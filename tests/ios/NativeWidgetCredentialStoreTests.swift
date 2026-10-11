import Foundation
import Security
import XCTest
@testable import WidgetCredentialStore

/// All SecItem operations are replaced. This harness never reads the runner's Keychain.
private final class FakeKeychain: NativeWidgetKeychainAccess {
    var data: Data?
    var readError: OSStatus?
    var deletionError: OSStatus = errSecSuccess
    var writes = 0
    var deletes = 0
    var lastAccessibility: String?

    init(data: Data? = nil) { self.data = data }

    func copyMatching(_ query: CFDictionary) -> (OSStatus, CFTypeRef?) {
        if let readError { return (readError, nil) }
        guard let data else { return (errSecItemNotFound, nil) }
        return (errSecSuccess, data as NSData)
    }

    func add(_ attributes: CFDictionary) -> OSStatus {
        if data != nil { return errSecDuplicateItem }
        return save(attributes)
    }

    func update(_ query: CFDictionary, attributes: CFDictionary) -> OSStatus {
        guard data != nil else { return errSecItemNotFound }
        return save(attributes)
    }

    func delete(_ query: CFDictionary) -> OSStatus {
        deletes += 1
        guard deletionError == errSecSuccess else { return deletionError }
        data = nil
        return errSecSuccess
    }

    private func save(_ attributes: CFDictionary) -> OSStatus {
        let values = attributes as NSDictionary
        guard let payload = values[kSecValueData as String] as? Data else { return errSecParam }
        writes += 1
        data = payload
        lastAccessibility = values[kSecAttrAccessible as String] as? String
        return errSecSuccess
    }
}

final class NativeWidgetCredentialStoreTests: XCTestCase {
    private let origin = URL(string: "https://pulse.example.test")!

    private func record(enabled: Bool = true, version: Int = 1) throws -> Data {
        try JSONSerialization.data(withJSONObject: [
            "version": version,
            "installationID": String(repeating: "i", count: 43),
            "grantID": String(repeating: "a", count: 32),
            "profileID": String(repeating: "b", count: 32),
            "widgetToken": "pw_" + String(repeating: "t", count: 43),
            "apiOrigin": origin.absoluteString,
            "expiresAt": Date(timeIntervalSinceNow: 3_600).timeIntervalSinceReferenceDate,
            "cacheEnabled": enabled,
            "generation": 7
        ], options: [.sortedKeys])
    }

    private func assertReadFailure(_ operation: () throws -> Void, status: OSStatus,
                                   file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertThrowsError(try operation(), file: file, line: line) { error in
            guard case NativeWidgetCredentialError.keychain(let actual) = error else {
                XCTFail("Expected a Keychain read error", file: file, line: line)
                return
            }
            XCTAssertEqual(actual, status, file: file, line: line)
        }
    }

    func testUnavailableReadRejectsConsentMutationsWithoutWrites() throws {
        for status in [errSecInteractionNotAllowed, errSecNotAvailable] {
            let original = try record()
            let fake = FakeKeychain(data: original)
            let store = NativeWidgetCredentialStore(keychain: fake)
            fake.readError = status
            assertReadFailure({ try store.setCacheEnabled(false, apiOrigin: origin) }, status: status)
            assertReadFailure({ try store.disableCache() }, status: status)
            assertReadFailure({ try store.clearGrant() }, status: status)
            XCTAssertEqual(fake.writes, 0)
            XCTAssertEqual(fake.deletes, 0)
            XCTAssertEqual(fake.data, original)
        }
    }

    func testUnavailableReadDoesNotReplaceInstallationOrGrant() throws {
        let original = try record()
        let fake = FakeKeychain(data: original)
        let store = NativeWidgetCredentialStore(keychain: fake)
        fake.readError = errSecInteractionNotAllowed
        assertReadFailure({ _ = try store.installationID() }, status: errSecInteractionNotAllowed)
        assertReadFailure({
            _ = try store.storeGrant(grantID: String(repeating: "c", count: 32),
                profileID: String(repeating: "d", count: 32),
                widgetToken: "pw_" + String(repeating: "u", count: 43),
                apiOrigin: origin, expiresAt: Date(timeIntervalSinceNow: 3_600))
        }, status: errSecInteractionNotAllowed)
        XCTAssertEqual(fake.data, original)
        XCTAssertEqual(fake.writes, 0)
    }

    func testUnavailableReadDoesNotClearSnapshotOrRunProtectedMutation() throws {
        let fake = FakeKeychain(data: try record())
        let store = NativeWidgetCredentialStore(keychain: fake)
        let credential = try XCTUnwrap(store.credential(apiOrigin: origin))
        fake.readError = errSecInteractionNotAllowed
        var invoked = false
        assertReadFailure({
            _ = try store.clearSnapshotIfUnavailable(apiOrigin: origin) { invoked = true }
        }, status: errSecInteractionNotAllowed)
        assertReadFailure({
            _ = try store.withCurrent(credential, apiOrigin: origin, requireCacheEnabled: true) { invoked = true }
        }, status: errSecInteractionNotAllowed)
        assertReadFailure({ _ = try store.advanceGeneration(apiOrigin: origin) }, status: errSecInteractionNotAllowed)
        assertReadFailure({
            _ = try store.clearGrant(ifCurrent: credential, apiOrigin: origin) { invoked = true }
        }, status: errSecInteractionNotAllowed)
        assertReadFailure({
            _ = try store.disableCache(ifCurrent: credential, apiOrigin: origin) { invoked = true }
        }, status: errSecInteractionNotAllowed)
        XCTAssertFalse(invoked)
        XCTAssertEqual(fake.writes, 0)
    }

    func testUnavailableReadGettersFailClosed() throws {
        let fake = FakeKeychain(data: try record())
        let store = NativeWidgetCredentialStore(keychain: fake)
        let credential = try XCTUnwrap(store.credential(apiOrigin: origin))
        fake.readError = errSecInteractionNotAllowed
        XCTAssertNil(store.credential(apiOrigin: origin))
        XCTAssertFalse(store.cacheEnabled(apiOrigin: origin))
        XCTAssertFalse(store.isCurrent(credential, apiOrigin: origin))
        XCTAssertEqual(fake.writes, 0)
    }

    func testMissingRecordPreservesIdempotentDisableAndClear() throws {
        let fake = FakeKeychain()
        let store = NativeWidgetCredentialStore(keychain: fake)
        try store.setCacheEnabled(false, apiOrigin: origin)
        try store.disableCache()
        try store.clearGrant()
        XCTAssertEqual(fake.writes, 0)
        XCTAssertNil(fake.data)
        var cleared = false
        XCTAssertTrue(try store.clearSnapshotIfUnavailable(apiOrigin: origin) { cleared = true })
        XCTAssertTrue(cleared)
    }

    func testCorruptOrUnsupportedRecordCannotBecomeAbsence() throws {
        for data in [Data("not-json".utf8), Data(repeating: 0, count: 8_193), try record(version: 2)] {
            let fake = FakeKeychain(data: data)
            let store = NativeWidgetCredentialStore(keychain: fake)
            XCTAssertThrowsError(try store.setCacheEnabled(false, apiOrigin: origin))
            XCTAssertThrowsError(try store.installationID())
            var cleared = false
            XCTAssertThrowsError(try store.clearSnapshotIfUnavailable(apiOrigin: origin) { cleared = true })
            XCTAssertFalse(cleared)
            XCTAssertNil(store.credential(apiOrigin: origin))
            XCTAssertFalse(store.cacheEnabled(apiOrigin: origin))
            XCTAssertEqual(fake.data, data)
            XCTAssertEqual(fake.writes, 0)
        }
    }

    func testSuccessfulDisablePersistsConsentAndGeneration() throws {
        let fake = FakeKeychain(data: try record())
        let store = NativeWidgetCredentialStore(keychain: fake)
        let before = try XCTUnwrap(store.credential(apiOrigin: origin))
        try store.setCacheEnabled(false, apiOrigin: origin)
        XCTAssertFalse(store.cacheEnabled(apiOrigin: origin))
        let after = try XCTUnwrap(store.credential(apiOrigin: origin))
        XCTAssertEqual(after.generation, before.generation + 1)
        XCTAssertEqual(after.grantID, before.grantID)
        XCTAssertEqual(after.widgetToken, before.widgetToken)
        XCTAssertEqual(fake.writes, 1)
        XCTAssertEqual(fake.lastAccessibility, kSecAttrAccessibleWhenUnlockedThisDeviceOnly as String)
    }

    func testSuccessfulActiveRecordProtectsSnapshot() throws {
        let fake = FakeKeychain(data: try record())
        let store = NativeWidgetCredentialStore(keychain: fake)
        var cleared = false
        XCTAssertFalse(try store.clearSnapshotIfUnavailable(apiOrigin: origin) { cleared = true })
        XCTAssertFalse(cleared)
        XCTAssertEqual(fake.writes, 0)
    }

    func testExplicitClearPropagatesDeleteFailure() throws {
        let fake = FakeKeychain(data: try record())
        let original = fake.data
        let store = NativeWidgetCredentialStore(keychain: fake)
        fake.deletionError = errSecInteractionNotAllowed
        assertReadFailure({ try store.clearAll() }, status: errSecInteractionNotAllowed)
        XCTAssertEqual(fake.data, original)
        XCTAssertEqual(fake.deletes, 1)
    }

}
