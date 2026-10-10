import Foundation
import Security

/// App-only Keychain state. The App Group receives snapshots, never this record.
final class NativeWidgetCredentialStore {
    struct Credential {
        let installationID: String
        let grantID: String
        let profileID: String
        let widgetToken: String
        let apiOrigin: String
        let expiresAt: Date
        let generation: UInt64
    }

    private struct Record: Codable {
        var version = 1
        var installationID: String
        var grantID: String?
        var profileID: String?
        var widgetToken: String?
        var apiOrigin: String?
        var expiresAt: Date?
        var cacheEnabled = false
        var generation: UInt64 = 0
    }

    private static let lock = NSLock()
    private let service: String
    private let account = "widget-credentials"

    init() {
        service = "\(Bundle.main.bundleIdentifier ?? "com.lucas.pulse").native-widget.v1"
    }

    func installationID() throws -> String {
        Self.lock.lock()
        defer { Self.lock.unlock() }
        if var record = readRecord(), Self.validInstallationID(record.installationID) {
            if Self.hasGrant(record), Self.validCredentialRecord(record, now: Date()) {
                return record.installationID
            }
            if Self.hasGrant(record) {
                Self.clearGrant(from: &record)
                try writeRecord(record)
            }
            return record.installationID
        }
        let record = Record(installationID: try Self.randomInstallationID())
        try writeRecord(record)
        return record.installationID
    }

    func credential(apiOrigin: URL, now: Date = Date()) -> Credential? {
        Self.lock.lock()
        defer { Self.lock.unlock() }
        guard var record = readRecord(), Self.validInstallationID(record.installationID) else { return nil }
        guard Self.hasGrant(record), Self.validCredentialRecord(record, now: now),
              record.apiOrigin == apiOrigin.absoluteString,
              let grantID = record.grantID, let profileID = record.profileID,
              let widgetToken = record.widgetToken, let expiresAt = record.expiresAt else {
            if Self.hasGrant(record) || record.cacheEnabled {
                Self.clearGrant(from: &record)
                try? writeRecord(record)
            }
            return nil
        }
        return Credential(installationID: record.installationID, grantID: grantID,
                          profileID: profileID, widgetToken: widgetToken,
                          apiOrigin: apiOrigin.absoluteString, expiresAt: expiresAt,
                          generation: record.generation)
    }

    func isCurrent(_ expected: Credential, apiOrigin: URL, now: Date = Date()) -> Bool {
        Self.lock.lock()
        defer { Self.lock.unlock() }
        guard let record = readRecord() else { return false }
        return Self.matches(expected, record: record, apiOrigin: apiOrigin, now: now)
    }

    /// Holds the credential lock through a protected cache write so consent changes
    /// cannot race an already completed network response into the App Group.
    func withCurrent(_ expected: Credential, apiOrigin: URL, requireCacheEnabled: Bool,
                     now: Date = Date(), perform: () throws -> Void) throws -> Bool {
        Self.lock.lock()
        defer { Self.lock.unlock() }
        guard let record = readRecord(),
              Self.matches(expected, record: record, apiOrigin: apiOrigin, now: now),
              !requireCacheEnabled || record.cacheEnabled else { return false }
        try perform()
        return true
    }

    /// A stale preflight must not erase a snapshot authorized by a newer request.
    @discardableResult
    func clearSnapshotIfUnavailable(apiOrigin: URL, perform: () throws -> Void) throws -> Bool {
        Self.lock.lock()
        defer { Self.lock.unlock() }
        if let record = readRecord(), record.cacheEnabled,
           Self.validCredentialRecord(record, now: Date()),
           record.apiOrigin == apiOrigin.absoluteString { return false }
        try perform()
        return true
    }

    /// Invalidates outstanding snapshot requests without unlinking the current grant.
    func advanceGeneration(apiOrigin: URL, now: Date = Date()) throws -> Credential? {
        Self.lock.lock()
        defer { Self.lock.unlock() }
        guard var record = readRecord(), Self.hasGrant(record),
              Self.validCredentialRecord(record, now: now),
              record.apiOrigin == apiOrigin.absoluteString,
              let grantID = record.grantID, let profileID = record.profileID,
              let widgetToken = record.widgetToken, let expiresAt = record.expiresAt else { return nil }
        record.generation = Self.next(record.generation)
        try writeRecord(record)
        return Credential(installationID: record.installationID, grantID: grantID,
                          profileID: profileID, widgetToken: widgetToken,
                          apiOrigin: apiOrigin.absoluteString, expiresAt: expiresAt,
                          generation: record.generation)
    }

    /// Retires a grant only if the response belongs to the exact credential used.
    func clearGrant(ifCurrent expected: Credential, apiOrigin: URL, now: Date = Date(),
                    afterClear: () throws -> Void = {}) throws -> Bool {
        Self.lock.lock()
        defer { Self.lock.unlock() }
        guard var record = readRecord(),
              Self.matches(expected, record: record, apiOrigin: apiOrigin, now: now) else { return false }
        Self.clearGrant(from: &record)
        try writeRecord(record)
        try afterClear()
        return true
    }

    func cacheEnabled(apiOrigin: URL, now: Date = Date()) -> Bool {
        Self.lock.lock()
        defer { Self.lock.unlock() }
        guard var record = readRecord() else { return false }
        if Self.hasGrant(record), !Self.validCredentialRecord(record, now: now) {
            Self.clearGrant(from: &record)
            try? writeRecord(record)
            return false
        }
        return record.cacheEnabled && record.apiOrigin == apiOrigin.absoluteString && Self.hasGrant(record)
    }

    func storeGrant(grantID: String, profileID: String, widgetToken: String,
                    apiOrigin: URL, expiresAt: Date, now: Date = Date()) throws -> Credential {
        guard Self.validID(grantID), Self.validID(profileID), Self.validWidgetToken(widgetToken),
              expiresAt > now, expiresAt.timeIntervalSince(now) <= 30 * 24 * 60 * 60 else {
            throw NativeWidgetCredentialError.invalidCredential
        }
        Self.lock.lock()
        defer { Self.lock.unlock() }
        var record = try (readRecord() ?? Record(installationID: Self.randomInstallationID()))
        if !Self.validInstallationID(record.installationID) {
            record = Record(installationID: try Self.randomInstallationID())
        }
        record.grantID = grantID
        record.profileID = profileID
        record.widgetToken = widgetToken
        record.apiOrigin = apiOrigin.absoluteString
        record.expiresAt = expiresAt
        record.generation = Self.next(record.generation)
        try writeRecord(record)
        return Credential(installationID: record.installationID, grantID: grantID,
                          profileID: profileID, widgetToken: widgetToken,
                          apiOrigin: apiOrigin.absoluteString, expiresAt: expiresAt,
                          generation: record.generation)
    }

    func setCacheEnabled(_ enabled: Bool, apiOrigin: URL) throws {
        Self.lock.lock()
        defer { Self.lock.unlock() }
        guard var record = readRecord() else {
            if enabled { throw NativeWidgetCredentialError.notLinked }
            return
        }
        if enabled {
            guard Self.hasGrant(record), Self.validCredentialRecord(record, now: Date()),
                  record.apiOrigin == apiOrigin.absoluteString else {
                throw NativeWidgetCredentialError.notLinked
            }
        }
        guard record.cacheEnabled != enabled else { return }
        record.cacheEnabled = enabled
        record.generation = Self.next(record.generation)
        try writeRecord(record)
    }

    func disableCache() throws {
        Self.lock.lock()
        defer { Self.lock.unlock() }
        guard var record = readRecord(), record.cacheEnabled else { return }
        record.cacheEnabled = false
        record.generation = Self.next(record.generation)
        try writeRecord(record)
    }

    func disableCache(ifCurrent expected: Credential, apiOrigin: URL, now: Date = Date(),
                      afterDisable: () throws -> Void = {}) throws -> Bool {
        Self.lock.lock()
        defer { Self.lock.unlock() }
        guard var record = readRecord(),
              Self.matches(expected, record: record, apiOrigin: apiOrigin, now: now) else { return false }
        if record.cacheEnabled {
            record.cacheEnabled = false
            record.generation = Self.next(record.generation)
            try writeRecord(record)
        }
        try afterDisable()
        return true
    }

    /// Removes the grant and consent while preserving this installation's random pseudonym.
    func clearGrant() throws {
        Self.lock.lock()
        defer { Self.lock.unlock() }
        guard var record = readRecord() else { return }
        Self.clearGrant(from: &record)
        try writeRecord(record)
    }

    /// Explicit unlink/logout also removes the installation pseudonym.
    func clearAll() throws {
        Self.lock.lock()
        defer { Self.lock.unlock() }
        let status = SecItemDelete(baseQuery as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw NativeWidgetCredentialError.keychain(status)
        }
    }

    private var baseQuery: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }

    private func readRecord() -> Record? {
        var query = baseQuery
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        guard status == errSecSuccess, let data = result as? Data,
              data.count <= 8_192, let record = try? JSONDecoder().decode(Record.self, from: data),
              record.version == 1, Self.validInstallationID(record.installationID) else { return nil }
        return record
    }

    private func writeRecord(_ record: Record) throws {
        guard Self.validInstallationID(record.installationID),
              let data = try? JSONEncoder().encode(record), data.count <= 8_192 else {
            throw NativeWidgetCredentialError.invalidCredential
        }
        var query = baseQuery
        query[kSecValueData as String] = data
        query[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        let addStatus = SecItemAdd(query as CFDictionary, nil)
        guard addStatus == errSecDuplicateItem else {
            guard addStatus == errSecSuccess else { throw NativeWidgetCredentialError.keychain(addStatus) }
            return
        }
        let update: [String: Any] = [kSecValueData as String: data,
                      kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly]
        let updateStatus = SecItemUpdate(baseQuery as CFDictionary, update as CFDictionary)
        guard updateStatus == errSecSuccess else { throw NativeWidgetCredentialError.keychain(updateStatus) }
    }

    private static func hasGrant(_ record: Record) -> Bool {
        record.grantID != nil || record.profileID != nil || record.widgetToken != nil
            || record.apiOrigin != nil || record.expiresAt != nil
    }

    private static func matches(_ expected: Credential, record: Record, apiOrigin: URL, now: Date) -> Bool {
        guard Self.hasGrant(record), Self.validCredentialRecord(record, now: now),
              expected.expiresAt > now,
              record.generation == expected.generation,
              record.installationID == expected.installationID,
              record.grantID == expected.grantID,
              record.profileID == expected.profileID,
              record.widgetToken == expected.widgetToken,
              record.apiOrigin == apiOrigin.absoluteString,
              expected.apiOrigin == apiOrigin.absoluteString else { return false }
        return true
    }

    private static func validCredentialRecord(_ record: Record, now: Date) -> Bool {
        guard let grantID = record.grantID, let profileID = record.profileID,
              let token = record.widgetToken, let origin = record.apiOrigin,
              let expiresAt = record.expiresAt else { return false }
        return validID(grantID) && validID(profileID) && validWidgetToken(token)
            && !origin.isEmpty && origin.utf8.count <= 512
            && expiresAt > now && expiresAt.timeIntervalSince(now) <= 30 * 24 * 60 * 60
    }

    private static func clearGrant(from record: inout Record) {
        record.grantID = nil
        record.profileID = nil
        record.widgetToken = nil
        record.apiOrigin = nil
        record.expiresAt = nil
        record.cacheEnabled = false
        record.generation = next(record.generation)
    }

    private static func next(_ value: UInt64) -> UInt64 {
        value == UInt64.max ? 1 : value + 1
    }

    private static func validID(_ value: String) -> Bool {
        value.range(of: "^[a-f0-9]{32}$", options: .regularExpression) != nil
    }

    private static func validInstallationID(_ value: String) -> Bool {
        value.range(of: "^[A-Za-z0-9_-]{43}$", options: .regularExpression) != nil
    }

    private static func validWidgetToken(_ value: String) -> Bool {
        value.range(of: "^pw_[A-Za-z0-9_-]{43}$", options: .regularExpression) != nil
    }

    private static func randomInstallationID() throws -> String {
        var bytes = [UInt8](repeating: 0, count: 32)
        let status = bytes.withUnsafeMutableBytes { buffer in
            SecRandomCopyBytes(kSecRandomDefault, buffer.count, buffer.baseAddress!)
        }
        guard status == errSecSuccess else { throw NativeWidgetCredentialError.randomUnavailable }
        return Data(bytes).base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
}

enum NativeWidgetCredentialError: Error {
    case invalidCredential
    case notLinked
    case randomUnavailable
    case keychain(OSStatus)
}
