import CoreFoundation
import Foundation
import PulseCore

/// One cancellable SSE lease. Only a validated revision can leave this type.
final class NativeEventStream: NSObject, URLSessionDataDelegate, URLSessionTaskDelegate {
    static let maximumLineBytes = 8 * 1_024
    private let lock = NSLock()
    private let config: NativeRuntimeConfig
    private let sessionSnapshot: NativeSessionState.Snapshot
    private let subscriptionID: String
    private let isCurrent: () -> Bool
    private let onRevision: (Int64, String) -> Void
    private let onError: (String) -> Void
    private var networkSession: URLSession?
    private var task: URLSessionDataTask?
    private var responseAccepted = false
    private var line = Data()
    private var dataField: Data?
    private var cancelledByOwner = false
    private var finished = false

    init(config: NativeRuntimeConfig, session: NativeSessionState.Snapshot, subscriptionID: String,
         isCurrent: @escaping () -> Bool,
         onRevision: @escaping (Int64, String) -> Void,
         onError: @escaping (String) -> Void) {
        self.config = config
        self.sessionSnapshot = session
        self.subscriptionID = subscriptionID
        self.isCurrent = isCurrent
        self.onRevision = onRevision
        self.onError = onError
        super.init()
    }

    func start() {
        guard NativeAPIPolicy.allowsEvents(path: "/api/events", method: "GET"),
              NativeSessionState.validCookie(sessionSnapshot.cookie),
              let url = URL(string: "/api/events", relativeTo: config.apiOrigin)?.absoluteURL else {
            fail()
            return
        }
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 60)
        request.httpMethod = "GET"
        request.httpShouldHandleCookies = false
        request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        request.setValue("no-store", forHTTPHeaderField: "Cache-Control")
        request.setValue("no-cache", forHTTPHeaderField: "Pragma")
        request.setValue("__Host-pulse_session=\(sessionSnapshot.cookie)", forHTTPHeaderField: "Cookie")

        let configuration = URLSessionConfiguration.ephemeral
        configuration.httpCookieStorage = nil
        configuration.httpShouldSetCookies = false
        configuration.urlCredentialStorage = nil
        configuration.urlCache = nil
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        configuration.timeoutIntervalForRequest = 60
        configuration.waitsForConnectivity = false
        let session = URLSession(configuration: configuration, delegate: self, delegateQueue: nil)
        lock.lock()
        guard !finished, !cancelledByOwner else { lock.unlock(); session.invalidateAndCancel(); return }
        networkSession = session
        task = session.dataTask(with: request)
        let task = self.task
        lock.unlock()
        task?.resume()
    }

    func cancel() {
        lock.lock()
        cancelledByOwner = true
        let task = self.task
        let session = networkSession
        self.task = nil
        lock.unlock()
        task?.cancel()
        session?.invalidateAndCancel()
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask,
                    didReceive response: URLResponse,
                    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        guard let response = response as? HTTPURLResponse,
              response.statusCode == 200,
              response.value(forHTTPHeaderField: "Content-Type")?
                .split(separator: ";", maxSplits: 1).first.map({
                    $0.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
                }) == "text/event-stream" else {
            completionHandler(.cancel)
            fail()
            return
        }
        lock.lock()
        responseAccepted = true
        lock.unlock()
        completionHandler(.allow)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive chunk: Data) {
        guard isCurrent() else { cancel(); return }
        var revisions: [Int64] = []
        var malformed = false
        lock.lock()
        guard !finished, !cancelledByOwner, responseAccepted else { lock.unlock(); return }
        for byte in chunk {
            if byte == 0x0A {
                var completeLine = line
                if completeLine.last == 0x0D { completeLine.removeLast() }
                line.removeAll(keepingCapacity: true)
                if completeLine.isEmpty {
                    if let field = dataField, let revision = Self.revision(from: field) {
                        revisions.append(revision)
                    }
                    dataField = nil
                } else if completeLine.first == 0x3A {
                    continue
                } else if completeLine.starts(with: Data("data:".utf8)) {
                    guard dataField == nil else { malformed = true; break }
                    var value = Data(completeLine.dropFirst(5))
                    if value.first == 0x20 { value.removeFirst() }
                    dataField = value
                }
            } else {
                guard line.count < Self.maximumLineBytes else { malformed = true; break }
                line.append(byte)
            }
        }
        lock.unlock()
        guard !malformed else { fail(); return }
        for revision in revisions where isCurrent() {
            onRevision(revision, subscriptionID)
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        lock.lock()
        let shouldReport = !finished && !cancelledByOwner
        finished = true
        self.task = nil
        let networkSession = self.networkSession
        self.networkSession = nil
        lock.unlock()
        networkSession?.finishTasksAndInvalidate()
        if shouldReport { onError(subscriptionID) }
    }

    func urlSession(_ session: URLSession, didBecomeInvalidWithError error: Error?) {
        lock.lock()
        if networkSession === session { networkSession = nil }
        lock.unlock()
    }

    private func fail() {
        lock.lock()
        guard !finished, !cancelledByOwner else { lock.unlock(); return }
        finished = true
        let task = self.task
        self.task = nil
        let session = networkSession
        networkSession = nil
        lock.unlock()
        task?.cancel()
        session?.invalidateAndCancel()
        onError(subscriptionID)
    }

    private static func revision(from data: Data) -> Int64? {
        guard data.count <= maximumLineBytes,
              let object = try? JSONSerialization.jsonObject(with: data),
              let dictionary = object as? [String: Any],
              let number = dictionary["revision"] as? NSNumber,
              CFGetTypeID(number) != CFBooleanGetTypeID() else { return nil }
        let value = number.doubleValue
        guard value.isFinite, value >= 0, value <= 9_007_199_254_740_991,
              value.rounded(.towardZero) == value else { return nil }
        return Int64(value)
    }
}
