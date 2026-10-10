import Foundation
import PulseCore

struct NativeHTTPResponse {
    let status: Int
    let contentType: String
    let body: Data
    let headers: [AnyHashable: Any]
}

enum NativeHTTPError: Error {
    case transport
    case invalidResponse
    case responseTooLarge
}

final class NativeHTTPRequestHandle {
    private let task: URLSessionDataTask

    fileprivate init(task: URLSessionDataTask) {
        self.task = task
    }

    func cancel() {
        task.cancel()
    }
}

/// Fixed-origin ephemeral transport. It owns cookie handling and never follows redirects.
final class NativeHTTPClient {
    static let maximumBodyBytes = 5 * 1_024 * 1_024
    static let maximumOAuthBytes = 64 * 1_024
    static let maximumRequestBytes = 5 * 1_024 * 1_024
    static let requestTimeout: TimeInterval = 8

    private let config: NativeRuntimeConfig

    init(config: NativeRuntimeConfig) {
        self.config = config
    }

    @discardableResult
    func sendAPI(method: String, path: String, body: Data?, session: NativeSessionState.Snapshot?,
                 limit: Int = NativeHTTPClient.maximumBodyBytes,
                 completion: @escaping (Result<NativeHTTPResponse, NativeHTTPError>) -> Void) -> NativeHTTPRequestHandle? {
        guard NativeAPIPolicy.allows(path: path, method: method),
              let url = apiURL(for: path),
              let session,
              NativeSessionState.validCookie(session.cookie) else {
            completion(.failure(.invalidResponse))
            return nil
        }
        let csrf: String? = ["POST", "PUT", "DELETE"].contains(method) ? session.csrf : nil
        if let csrf, !NativeSessionState.validCSRF(csrf) {
            completion(.failure(.invalidResponse))
            return nil
        }
        return send(url: url, method: method, body: body, cookie: session.cookie, csrf: csrf,
                    bearer: nil, contentType: body == nil ? nil : "application/json",
                    accept: "application/json", limit: limit,
                    timeout: Self.requestTimeout, completion: completion)
    }

    @discardableResult
    func sendBootstrap(accessToken: String,
                       completion: @escaping (Result<NativeHTTPResponse, NativeHTTPError>) -> Void) -> NativeHTTPRequestHandle? {
        guard validBearer(accessToken), let url = apiURL(for: "/api/native/app-sessions") else {
            completion(.failure(.invalidResponse))
            return nil
        }
        return send(url: url, method: "POST", body: Data("{}".utf8), cookie: nil, csrf: nil,
                    bearer: accessToken, contentType: "application/json", accept: "application/json",
                    limit: Self.maximumOAuthBytes, timeout: Self.requestTimeout, completion: completion)
    }

    @discardableResult
    func sendWidgetRegistration(accessToken: String, pairingCode: String, installationID: String,
                                deviceName: String,
                                completion: @escaping (Result<NativeHTTPResponse, NativeHTTPError>) -> Void) -> NativeHTTPRequestHandle? {
        guard validBearer(accessToken),
              pairingCode.range(of: "^[A-Za-z0-9_-]{43}$", options: .regularExpression) != nil,
              installationID.range(of: "^[A-Za-z0-9_-]{43}$", options: .regularExpression) != nil,
              let url = apiURL(for: "/api/native/widget-grants"),
              let body = try? JSONSerialization.data(withJSONObject: [
                "pairing_code": pairingCode,
                "installation_id": installationID,
                "device_name": deviceName
              ], options: [.sortedKeys]), body.count <= Self.maximumOAuthBytes else {
            completion(.failure(.invalidResponse))
            return nil
        }
        return send(url: url, method: "POST", body: body, cookie: nil, csrf: nil,
                    bearer: accessToken, contentType: "application/json", accept: "application/json",
                    limit: Self.maximumOAuthBytes, timeout: Self.requestTimeout, completion: completion)
    }

    @discardableResult
    func sendWidgetSnapshot(grantID: String, token: String,
                            completion: @escaping (Result<NativeHTTPResponse, NativeHTTPError>) -> Void) -> NativeHTTPRequestHandle? {
        guard grantID.range(of: "^[a-f0-9]{32}$", options: .regularExpression) != nil,
              validWidgetToken(token),
              let url = apiURL(for: "/api/native/widget-grants/\(grantID)/snapshot") else {
            completion(.failure(.invalidResponse))
            return nil
        }
        return send(url: url, method: "GET", body: nil, cookie: nil, csrf: nil,
                    bearer: token, contentType: nil, accept: "application/json",
                    limit: Self.maximumOAuthBytes, timeout: Self.requestTimeout, completion: completion)
    }

    @discardableResult
    func sendWidgetRevocation(grantID: String, token: String,
                              completion: @escaping (Result<NativeHTTPResponse, NativeHTTPError>) -> Void) -> NativeHTTPRequestHandle? {
        guard grantID.range(of: "^[a-f0-9]{32}$", options: .regularExpression) != nil,
              validWidgetToken(token),
              let url = apiURL(for: "/api/native/widget-grants/\(grantID)") else {
            completion(.failure(.invalidResponse))
            return nil
        }
        return send(url: url, method: "DELETE", body: Data("{}".utf8), cookie: nil, csrf: nil,
                    bearer: token, contentType: "application/json", accept: "application/json",
                    limit: Self.maximumOAuthBytes, timeout: Self.requestTimeout, completion: completion)
    }

    @discardableResult
    func sendTokenRequest(code: String, redirectURI: URL, verifier: String,
                          completion: @escaping (Result<NativeHTTPResponse, NativeHTTPError>) -> Void) -> NativeHTTPRequestHandle? {
        guard code.count <= 4_096, !code.isEmpty,
              verifier.range(of: "^[A-Za-z0-9._~-]{43,128}$", options: .regularExpression) != nil,
              let body = formEncoded([
                ("grant_type", "authorization_code"),
                ("code", code),
                ("redirect_uri", redirectURI.absoluteString),
                ("client_id", config.clientID),
                ("code_verifier", verifier)
              ]) else {
            completion(.failure(.invalidResponse))
            return nil
        }
        return send(url: config.tokenEndpoint, method: "POST", body: body, cookie: nil, csrf: nil,
                    bearer: nil, contentType: "application/x-www-form-urlencoded", accept: "application/json",
                    limit: Self.maximumOAuthBytes, timeout: Self.requestTimeout, completion: completion)
    }

    func logout(_ snapshot: NativeSessionState.Snapshot) {
        guard let url = apiURL(for: "/api/auth/logout"),
              NativeSessionState.validCookie(snapshot.cookie), NativeSessionState.validCSRF(snapshot.csrf) else { return }
        _ = send(url: url, method: "POST", body: Data("{}".utf8), cookie: snapshot.cookie,
                 csrf: snapshot.csrf, bearer: nil, contentType: "application/json", accept: "application/json",
                 limit: Self.maximumOAuthBytes, timeout: Self.requestTimeout) { _ in }
    }

    private func send(url: URL, method: String, body: Data?, cookie: String?, csrf: String?, bearer: String?,
                      contentType: String?, accept: String, limit: Int, timeout: TimeInterval,
                      completion: @escaping (Result<NativeHTTPResponse, NativeHTTPError>) -> Void) -> NativeHTTPRequestHandle? {
        guard url.scheme?.lowercased() == "https", limit > 0,
              body.map({ $0.count <= Self.maximumRequestBytes }) ?? true,
              cookie.map(NativeSessionState.validCookie) ?? true,
              csrf.map(NativeSessionState.validCSRF) ?? true,
              bearer.map(validBearer) ?? true else {
            completion(.failure(.invalidResponse))
            return nil
        }

        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData,
                                 timeoutInterval: timeout)
        request.httpMethod = method
        request.httpShouldHandleCookies = false
        request.setValue(accept, forHTTPHeaderField: "Accept")
        request.setValue("no-store", forHTTPHeaderField: "Cache-Control")
        request.setValue("no-cache", forHTTPHeaderField: "Pragma")
        if url.path.hasPrefix("/api/navigation/") {
            request.setValue(config.apiOrigin.absoluteString.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + "/",
                             forHTTPHeaderField: "Referer")
        }
        if let contentType { request.setValue(contentType, forHTTPHeaderField: "Content-Type") }
        if let cookie { request.setValue("__Host-pulse_session=\(cookie)", forHTTPHeaderField: "Cookie") }
        if let csrf { request.setValue(csrf, forHTTPHeaderField: "X-Pulse-CSRF") }
        if let bearer { request.setValue("Bearer \(bearer)", forHTTPHeaderField: "Authorization") }
        request.httpBody = body

        let configuration = URLSessionConfiguration.ephemeral
        configuration.httpCookieStorage = nil
        configuration.httpShouldSetCookies = false
        configuration.urlCredentialStorage = nil
        configuration.urlCache = nil
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        configuration.timeoutIntervalForRequest = timeout
        configuration.timeoutIntervalForResource = timeout
        configuration.waitsForConnectivity = false
        let delegate = BoundedHTTPResponseDelegate(limit: limit, completion: completion)
        let session = URLSession(configuration: configuration, delegate: delegate, delegateQueue: nil)
        delegate.retain(session: session)
        let task = session.dataTask(with: request)
        let handle = NativeHTTPRequestHandle(task: task)
        task.resume()
        return handle
    }

    private func apiURL(for path: String) -> URL? {
        guard path.hasPrefix("/api/"),
              let url = URL(string: path, relativeTo: config.apiOrigin)?.absoluteURL,
              url.scheme?.lowercased() == "https",
              url.host?.lowercased() == config.apiOrigin.host?.lowercased(),
              url.port == config.apiOrigin.port else { return nil }
        return url
    }

    private func validBearer(_ value: String) -> Bool {
        !value.isEmpty && value.utf8.count <= 4_096
            && !value.unicodeScalars.contains(where: CharacterSet.controlCharacters.contains)
    }

    private func validWidgetToken(_ value: String) -> Bool {
        value.range(of: "^pw_[A-Za-z0-9_-]{43}$", options: .regularExpression) != nil
    }

    private func formEncoded(_ values: [(String, String)]) -> Data? {
        NativeRequestPolicy.formEncoded(values)
    }
}

private final class BoundedHTTPResponseDelegate: NSObject, URLSessionDataDelegate, URLSessionTaskDelegate {
    private let lock = NSLock()
    private let limit: Int
    private let completion: (Result<NativeHTTPResponse, NativeHTTPError>) -> Void
    private var response: HTTPURLResponse?
    private var data = Data()
    private var failure: NativeHTTPError?
    private var completed = false
    private var owningSession: URLSession?

    init(limit: Int, completion: @escaping (Result<NativeHTTPResponse, NativeHTTPError>) -> Void) {
        self.limit = limit
        self.completion = completion
        super.init()
    }

    func retain(session: URLSession) {
        lock.lock()
        owningSession = session
        lock.unlock()
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask,
                    didReceive response: URLResponse,
                    completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
        guard let http = response as? HTTPURLResponse else {
            fail(.invalidResponse)
            completionHandler(.cancel)
            return
        }
        lock.lock()
        self.response = http
        lock.unlock()
        completionHandler(.allow)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive chunk: Data) {
        lock.lock()
        guard !completed else { lock.unlock(); return }
        if chunk.count > limit - data.count {
            failure = .responseTooLarge
            lock.unlock()
            dataTask.cancel()
            return
        }
        data.append(chunk)
        lock.unlock()
    }

    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        lock.lock()
        guard !completed else { lock.unlock(); return }
        completed = true
        let result: Result<NativeHTTPResponse, NativeHTTPError>
        if let failure {
            result = .failure(failure)
        } else if error != nil {
            result = .failure(.transport)
        } else if let response {
            let contentType = response.value(forHTTPHeaderField: "Content-Type")?
                .split(separator: ";", maxSplits: 1).first.map { $0.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() } ?? ""
            result = .success(NativeHTTPResponse(status: response.statusCode,
                                                 contentType: contentType, body: data,
                                                 headers: response.allHeaderFields))
        } else {
            result = .failure(.invalidResponse)
        }
        let currentSession = owningSession
        lock.unlock()
        completion(result)
        currentSession?.finishTasksAndInvalidate()
    }

    func urlSession(_ session: URLSession, didBecomeInvalidWithError error: Error?) {
        lock.lock()
        owningSession = nil
        lock.unlock()
    }

    private func fail(_ error: NativeHTTPError) {
        lock.lock()
        guard !completed else { lock.unlock(); return }
        failure = error
        lock.unlock()
    }
}
