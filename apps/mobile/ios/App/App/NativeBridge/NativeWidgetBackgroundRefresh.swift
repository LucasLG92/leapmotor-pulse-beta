import BackgroundTasks
import Foundation
import PulseCore
import WidgetKit

/// Best-effort background snapshot refresh. WidgetKit still controls when timelines render.
enum NativeWidgetBackgroundRefresh {
    static let taskIdentifier = "com.lucas.pulse.widget.refresh"
    private static let minimumInterval: TimeInterval = 30 * 60

    static func register() {
        _ = BGTaskScheduler.shared.register(forTaskWithIdentifier: taskIdentifier, using: nil) { task in
            guard let refreshTask = task as? BGAppRefreshTask else {
                task.setTaskCompleted(success: false)
                return
            }
            run(refreshTask)
        }
    }

    static func scheduleIfEnabled() {
        guard let config = PulseNativePlugin.readRuntimeConfig(),
              let groupID = PulseNativePlugin.readAppGroup(),
              let cache = PulseWidgetCache(groupID: groupID) else {
            BGTaskScheduler.shared.cancel(taskRequestWithIdentifier: taskIdentifier)
            return
        }
        let credentials = NativeWidgetCredentialStore()
        guard credentials.cacheEnabled(apiOrigin: config.apiOrigin),
              let credential = credentials.credential(apiOrigin: config.apiOrigin),
              credentials.isCurrent(credential, apiOrigin: config.apiOrigin) else {
            if clearUnavailableSnapshot(credentials, config: config, cache: cache) {
                BGTaskScheduler.shared.cancel(taskRequestWithIdentifier: taskIdentifier)
            }
            return
        }

        let request = BGAppRefreshTaskRequest(identifier: taskIdentifier)
        request.earliestBeginDate = Date(timeIntervalSinceNow: minimumInterval)
        do { try BGTaskScheduler.shared.submit(request) }
        catch { /* La planificación depende del sistema; se vuelve a solicitar al siguiente background. */ }
    }

    private static func run(_ task: BGAppRefreshTask) {
        guard let config = PulseNativePlugin.readRuntimeConfig(),
              let groupID = PulseNativePlugin.readAppGroup(),
              let cache = PulseWidgetCache(groupID: groupID) else {
            scheduleIfEnabled()
            task.setTaskCompleted(success: true)
            return
        }
        let credentials = NativeWidgetCredentialStore()
        guard credentials.cacheEnabled(apiOrigin: config.apiOrigin),
              // Shares the request generation with the foreground refresher.
              let credential = try? credentials.advanceGeneration(apiOrigin: config.apiOrigin),
              credentials.isCurrent(credential, apiOrigin: config.apiOrigin) else {
            _ = clearUnavailableSnapshot(credentials, config: config, cache: cache)
            scheduleIfEnabled()
            task.setTaskCompleted(success: true)
            return
        }
        NativeWidgetRefreshOperation(task: task, config: config, cache: cache,
                                     credentials: credentials, credential: credential).start()
    }

    private static func clearUnavailableSnapshot(_ credentials: NativeWidgetCredentialStore,
                                                config: NativeRuntimeConfig, cache: PulseWidgetCache) -> Bool {
        do {
            let cleared = try credentials.clearSnapshotIfUnavailable(apiOrigin: config.apiOrigin) {
                try cache.clear()
            }
            if cleared { WidgetCenter.shared.reloadTimelines(ofKind: "PulseWidget") }
            return cleared
        } catch { return false }
    }
}

private final class NativeWidgetRefreshOperation {
    private let lock = NSLock()
    private var task: BGAppRefreshTask?
    private let config: NativeRuntimeConfig
    private let cache: PulseWidgetCache
    private let credentials: NativeWidgetCredentialStore
    private let credential: NativeWidgetCredentialStore.Credential
    private let http: NativeHTTPClient
    private var request: NativeHTTPRequestHandle?
    private var completed = false

    init(task: BGAppRefreshTask, config: NativeRuntimeConfig, cache: PulseWidgetCache,
         credentials: NativeWidgetCredentialStore, credential: NativeWidgetCredentialStore.Credential) {
        self.task = task
        self.config = config
        self.cache = cache
        self.credentials = credentials
        self.credential = credential
        http = NativeHTTPClient(config: config)
    }

    func start() {
        lock.lock()
        guard let task, !completed else { lock.unlock(); return }
        task.expirationHandler = { [self] in cancel() }
        lock.unlock()

        NativeWidgetBackgroundRefresh.scheduleIfEnabled()
        lock.lock()
        let active = !completed
        lock.unlock()
        guard active else { return }
        guard credentials.cacheEnabled(apiOrigin: config.apiOrigin),
              credentials.isCurrent(credential, apiOrigin: config.apiOrigin) else {
            finish(success: true)
            return
        }

        let handle = http.sendWidgetSnapshot(grantID: credential.grantID, token: credential.widgetToken) { [self] result in
            handleResponse(result)
        }
        lock.lock()
        let shouldCancel = completed
        if !shouldCancel { request = handle }
        lock.unlock()
        if shouldCancel { handle?.cancel() }
    }

    private func handleResponse(_ result: Result<NativeHTTPResponse, NativeHTTPError>) {
        guard credentials.isCurrent(credential, apiOrigin: config.apiOrigin) else {
            finish(success: true)
            return
        }
        guard case .success(let response) = result else {
            // Preserve the last authorized snapshot only until its existing valid_until.
            finish(success: true)
            return
        }
        if [401, 403, 404].contains(response.status) {
            do {
                let cleared = try performWhileActive {
                    guard try credentials.clearGrant(
                        ifCurrent: credential, apiOrigin: config.apiOrigin,
                        afterClear: {
                            do { try cache.clear() } catch { }
                        }
                    ) else { return false }
                    WidgetCenter.shared.reloadTimelines(ofKind: "PulseWidget")
                    return true
                }
                if cleared == true { NativeWidgetBackgroundRefresh.scheduleIfEnabled() }
            } catch { }
            finish(success: true)
            return
        }
        guard response.status == 200, response.contentType == "application/json" else {
            if response.status == 200 { _ = clearSnapshotIfCurrent() }
            finish(success: true)
            return
        }

        let now = Date()
        guard let sanitized = try? WidgetSnapshotPolicy.sanitize(
            data: response.body, expectedProfileID: credential.profileID, now: now
        ), let visible = WidgetSnapshotPolicy.visibleSnapshot(sanitized, now: now) else {
            _ = clearSnapshotIfCurrent()
            finish(success: true)
            return
        }
        do {
            let wrote = try performWhileActive {
                try credentials.withCurrent(credential, apiOrigin: config.apiOrigin,
                                            requireCacheEnabled: true) {
                    try cache.write(snapshot: visible)
                }
            }
            if wrote == true { WidgetCenter.shared.reloadTimelines(ofKind: "PulseWidget") }
        } catch {
            do {
                let disabled = try performWhileActive {
                    guard try credentials.disableCache(
                        ifCurrent: credential, apiOrigin: config.apiOrigin,
                        afterDisable: {
                            do { try cache.clear() } catch { }
                        }
                    ) else { return false }
                    WidgetCenter.shared.reloadTimelines(ofKind: "PulseWidget")
                    return true
                }
                if disabled == true { NativeWidgetBackgroundRefresh.scheduleIfEnabled() }
            } catch { }
        }
        finish(success: true)
    }

    @discardableResult
    private func clearSnapshotIfCurrent() -> Bool {
        do {
            let cleared = try performWhileActive {
                try credentials.withCurrent(credential, apiOrigin: config.apiOrigin,
                                            requireCacheEnabled: true) {
                    try cache.clear()
                }
            }
            if cleared == true { WidgetCenter.shared.reloadTimelines(ofKind: "PulseWidget") }
            return cleared == true
        } catch { return false }
    }

    private func performWhileActive<T>(_ work: () throws -> T) rethrows -> T? {
        lock.lock()
        defer { lock.unlock() }
        guard !completed else { return nil }
        return try work()
    }

    private func cancel() {
        lock.lock()
        let request = self.request
        lock.unlock()
        request?.cancel()
        finish(success: false)
    }

    private func finish(success: Bool) {
        lock.lock()
        guard !completed else { lock.unlock(); return }
        completed = true
        let task = self.task
        self.task = nil
        request = nil
        lock.unlock()
        task?.expirationHandler = nil
        task?.setTaskCompleted(success: success)
    }
}
