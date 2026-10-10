import Foundation

/// La cookie BFF y el CSRF solo viven en memoria y usan tiempo monotónico.
final class NativeSessionState {
    struct Snapshot {
        let cookie: String
        let csrf: String
        let expiresAtUptime: TimeInterval
        let generation: UInt64
    }

    private let lock = NSLock()
    private var cookie: String?
    private var csrf: String?
    private var expiresAtUptime: TimeInterval = 0
    private var currentGeneration: UInt64 = 0

    var generation: UInt64 {
        lock.lock()
        defer { lock.unlock() }
        expireIfNeeded(now: ProcessInfo.processInfo.systemUptime)
        return currentGeneration
    }

    var isAuthenticated: Bool {
        snapshot() != nil
    }

    func snapshot(now: TimeInterval = ProcessInfo.processInfo.systemUptime) -> Snapshot? {
        lock.lock()
        defer { lock.unlock() }
        expireIfNeeded(now: now)
        guard let cookie, let csrf else { return nil }
        return Snapshot(cookie: cookie, csrf: csrf,
                        expiresAtUptime: expiresAtUptime, generation: currentGeneration)
    }

    func install(cookie newCookie: String, csrf newCSRF: String, ttlSeconds: Int,
                 expectedGeneration: UInt64, now: TimeInterval = ProcessInfo.processInfo.systemUptime) -> Bool {
        guard (1...1_800).contains(ttlSeconds), Self.validCookie(newCookie), Self.validCSRF(newCSRF),
              now.isFinite, now >= 0 else { return false }
        lock.lock()
        defer { lock.unlock() }
        expireIfNeeded(now: now)
        guard expectedGeneration == currentGeneration else { return false }
        advanceGeneration()
        cookie = newCookie
        csrf = newCSRF
        expiresAtUptime = now + TimeInterval(ttlSeconds)
        return true
    }

    func updateCSRF(_ value: String, expectedGeneration: UInt64,
                    now: TimeInterval = ProcessInfo.processInfo.systemUptime) -> Bool {
        guard Self.validCSRF(value) else { return false }
        lock.lock()
        defer { lock.unlock() }
        expireIfNeeded(now: now)
        guard cookie != nil, expectedGeneration == currentGeneration else { return false }
        csrf = value
        return true
    }

    @discardableResult
    func invalidate() -> UInt64 {
        lock.lock()
        defer { lock.unlock() }
        cookie = nil
        csrf = nil
        expiresAtUptime = 0
        advanceGeneration()
        return currentGeneration
    }

    func isCurrent(_ expectedGeneration: UInt64,
                   now: TimeInterval = ProcessInfo.processInfo.systemUptime) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        expireIfNeeded(now: now)
        return cookie != nil && expectedGeneration == currentGeneration
    }

    static func validCookie(_ value: String) -> Bool {
        value.range(of: "^[A-Za-z0-9_-]{43}$", options: .regularExpression) != nil
    }

    static func validCSRF(_ value: String) -> Bool {
        value.range(of: "^[A-Za-z0-9_-]{32,128}$", options: .regularExpression) != nil
    }

    private func expireIfNeeded(now: TimeInterval) {
        guard cookie != nil else { return }
        guard !now.isFinite || now < 0 || now >= expiresAtUptime else { return }
        cookie = nil
        csrf = nil
        expiresAtUptime = 0
        advanceGeneration()
    }

    private func advanceGeneration() {
        currentGeneration = currentGeneration == UInt64.max ? 1 : currentGeneration + 1
    }
}
