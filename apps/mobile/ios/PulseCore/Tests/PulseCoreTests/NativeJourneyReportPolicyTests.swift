import Foundation
import XCTest
@testable import PulseCore

final class NativeJourneyReportPolicyTests: XCTestCase {
    func testBridgeDaysAcceptsExactNumbersIndependentOfNSNumberRepresentation() {
        for days in [30, 90, 365] {
            XCTAssertEqual(NativeJourneyReportPolicy.requestedDays(arguments: ["days": NSNumber(value: days)]), days)
            XCTAssertEqual(NativeJourneyReportPolicy.requestedDays(arguments: ["days": NSNumber(value: Double(days))]), days)
        }
        let invalid: [Any] = ["30", true, false, 30.5, Double.nan, Double.infinity, 1e100, 0]
        for value in invalid {
            XCTAssertNil(NativeJourneyReportPolicy.requestedDays(arguments: ["days": value]))
        }
        XCTAssertNil(NativeJourneyReportPolicy.requestedDays(arguments: [:]))
        XCTAssertNil(NativeJourneyReportPolicy.requestedDays(arguments: ["days": 30, "path": "synthetic"]))
    }

    func testAcceptsZeroNullAndTinyValuesAndRebuildsOnlyContractFields() throws {
        var value = report()
        var totals = value["totals"] as! [String: Any]
        totals["count"] = 1
        totals["distance_km"] = 0.000001
        totals["energy_kwh"] = 0.000002
        totals["known_distance_count"] = 1
        totals["known_energy_count"] = 1
        totals["paired_count"] = 1
        totals["paired_distance_km"] = 0.000001
        totals["paired_energy_kwh"] = 0.000002
        totals["consumption_kwh_100km"] = 200.0
        value["totals"] = totals

        let month = monthlyTotals(monthKey: "2026-01")
        value["months"] = [month]
        let sanitized = try NativeJourneyReportPolicy.sanitizedJSON(
            data: encode(value), expectedDays: 30
        )
        let decoded = try XCTUnwrap(JSONSerialization.jsonObject(with: sanitized) as? [String: Any])
        XCTAssertEqual(Set(decoded.keys), ["source", "coverage", "period", "totals", "months"])
        let cleanTotals = try XCTUnwrap(decoded["totals"] as? [String: Any])
        XCTAssertEqual((cleanTotals["count"] as? NSNumber)?.intValue, 1)
        let distance = try XCTUnwrap(cleanTotals["distance_km"] as? NSNumber).doubleValue
        let energy = try XCTUnwrap(cleanTotals["energy_kwh"] as? NSNumber).doubleValue
        XCTAssertEqual(distance, 0.000001, accuracy: 0.0000000001)
        XCTAssertEqual(energy, 0.000002, accuracy: 0.0000000001)
        XCTAssertEqual((cleanTotals["consumption_kwh_100km"] as? NSNumber)?.doubleValue, 200)
        let cleanMonth = try XCTUnwrap((decoded["months"] as? [[String: Any]])?.first)
        XCTAssertTrue(cleanMonth["consumption_kwh_100km"] is NSNull)
        XCTAssertEqual((cleanMonth["count"] as? NSNumber)?.intValue, 0)
    }

    func testRejectsUnknownFieldsAndDuplicateKeys() throws {
        var value = report()
        var period = value["period"] as! [String: Any]
        period["timezone_offset"] = 0
        value["period"] = period
        XCTAssertThrowsError(try NativeJourneyReportPolicy.sanitizedJSON(
            data: encode(value), expectedDays: 30
        ))

        let valid = String(decoding: encode(report()), as: UTF8.self)
        let duplicated = "{\"source\":\"manual\",\"source\":\"manual\"," + String(valid.dropFirst())
        XCTAssertThrowsError(try NativeJourneyReportPolicy.sanitizedJSON(
            data: Data(duplicated.utf8), expectedDays: 30
        ))
    }

    func testRejectsBooleanCoercionsAndInconsistentCounts() throws {
        var booleanCount = report()
        var totals = booleanCount["totals"] as! [String: Any]
        totals["count"] = true
        booleanCount["totals"] = totals
        XCTAssertThrowsError(try NativeJourneyReportPolicy.sanitizedJSON(
            data: encode(booleanCount), expectedDays: 30
        ))

        var inconsistent = report()
        totals = inconsistent["totals"] as! [String: Any]
        totals["count"] = 1
        totals["known_distance_count"] = 2
        inconsistent["totals"] = totals
        XCTAssertThrowsError(try NativeJourneyReportPolicy.sanitizedJSON(
            data: encode(inconsistent), expectedDays: 30
        ))

        let valid = String(decoding: encode(report()), as: UTF8.self)
        let floatCount = valid.replacingOccurrences(of: "\"count\":0", with: "\"count\":1.0")
        XCTAssertThrowsError(try NativeJourneyReportPolicy.sanitizedJSON(
            data: Data(floatCount.utf8), expectedDays: 30
        ))
    }

    func testRequiresUTCPeriodMatchingRequestedDaysAndExactDates() throws {
        var value = report()
        XCTAssertThrowsError(try NativeJourneyReportPolicy.sanitizedJSON(
            data: encode(value), expectedDays: 90
        ))

        var period = value["period"] as! [String: Any]
        period["started_at"] = "2026-02-30T00:00:00.000000Z"
        value["period"] = period
        XCTAssertThrowsError(try NativeJourneyReportPolicy.sanitizedJSON(
            data: encode(value), expectedDays: 30
        ))

        period["started_at"] = "2026-01-01T00:00:00.000000+00:00"
        period["ended_at"] = "2026-01-31T00:00:00.000000Z"
        period["days"] = true
        value["period"] = period
        XCTAssertThrowsError(try NativeJourneyReportPolicy.sanitizedJSON(
            data: encode(value), expectedDays: 30
        ))
    }

    func testAcceptsShorterWindowAndRejectsNonIncreasingPeriod() throws {
        var value = report()
        var period = value["period"] as! [String: Any]
        period["ended_at"] = "2026-01-02T00:00:00.000000Z"
        value["period"] = period
        XCTAssertNoThrow(try NativeJourneyReportPolicy.sanitizedJSON(data: encode(value), expectedDays: 30))
        period["ended_at"] = period["started_at"]
        value["period"] = period
        XCTAssertThrowsError(try NativeJourneyReportPolicy.sanitizedJSON(data: encode(value), expectedDays: 30))
    }

    func testRejectsUnorderedTooManyMonthsAndOversizedBody() throws {
        var value = report()
        value["months"] = [monthlyTotals(monthKey: "2026-02"), monthlyTotals(monthKey: "2026-01")]
        XCTAssertThrowsError(try NativeJourneyReportPolicy.sanitizedJSON(
            data: encode(value), expectedDays: 30
        ))

        var yearReport = report()
        var period = yearReport["period"] as! [String: Any]
        period["started_at"] = "2025-01-01T00:00:00.000000Z"
        period["ended_at"] = "2026-01-01T00:00:00.000000Z"
        period["days"] = 365
        yearReport["period"] = period
        yearReport["months"] = (0..<13).map { month in
            monthlyTotals(monthKey: String(format: "%04d-%02d", 2025 + month / 12, month % 12 + 1))
        }
        XCTAssertNoThrow(try NativeJourneyReportPolicy.sanitizedJSON(
            data: encode(yearReport), expectedDays: 365
        ))
        yearReport["months"] = (0..<14).map { month in
            monthlyTotals(monthKey: String(format: "%04d-%02d", 2025 + month / 12, month % 12 + 1))
        }
        XCTAssertThrowsError(try NativeJourneyReportPolicy.sanitizedJSON(
            data: encode(yearReport), expectedDays: 365
        ))

        let base = encode(report())
        var atLimit = base
        atLimit.append(Data(repeating: 0x20, count: NativeJourneyReportPolicy.maximumBodyBytes - base.count))
        XCTAssertNoThrow(try NativeJourneyReportPolicy.sanitizedJSON(data: atLimit, expectedDays: 30))
        atLimit.append(0x20)
        XCTAssertThrowsError(try NativeJourneyReportPolicy.sanitizedJSON(data: atLimit, expectedDays: 30))
    }

    func testTemporaryStoreUsesOneUUIDDirectoryAndRemovesOnlyThatDirectory() throws {
        let fileManager = FileManager.default
        let testRoot = fileManager.temporaryDirectory
            .appendingPathComponent("PulseNativeJourneyReportTests-\(UUID().uuidString)", isDirectory: true)
        let exportRoot = testRoot.appendingPathComponent("exports", isDirectory: true)
        try fileManager.createDirectory(at: exportRoot, withIntermediateDirectories: true)
        defer { try? fileManager.removeItem(at: testRoot) }

        let retained = exportRoot.appendingPathComponent("keep.txt")
        try Data("keep".utf8).write(to: retained)
        let store = NativeJourneyReportTemporaryStore(rootURL: exportRoot)
        #if os(iOS)
        let file = try store.createExport(data: Data("{}".utf8), days: 90)
        XCTAssertEqual(file.lastPathComponent, "pulse-viajes-90-dias.json")
        XCTAssertEqual(try Data(contentsOf: file), Data("{}".utf8))
        XCTAssertNotNil(UUID(uuidString: file.deletingLastPathComponent().lastPathComponent))

        try store.removeExport(at: file)
        XCTAssertFalse(fileManager.fileExists(atPath: file.deletingLastPathComponent().path))
        XCTAssertTrue(fileManager.fileExists(atPath: retained.path))
        #else
        XCTAssertThrowsError(try store.createExport(data: Data("{}".utf8), days: 90)) {
            XCTAssertEqual($0 as? NativeJourneyReportTemporaryStoreError, .unavailable)
        }
        let remainingEntries = try fileManager.contentsOfDirectory(atPath: exportRoot.path)
        XCTAssertEqual(Set(remainingEntries), Set(["keep.txt"]))
        XCTAssertTrue(fileManager.fileExists(atPath: retained.path))
        #endif
        XCTAssertThrowsError(try store.removeExport(at: retained))
    }

    func testStartupCleanupRemovesUUIDExportsAndKeepsUnownedEntries() throws {
        let fileManager = FileManager.default
        let root = fileManager.temporaryDirectory
            .appendingPathComponent("PulseNativeJourneyReportCleanup-\(UUID().uuidString)", isDirectory: true)
        try fileManager.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? fileManager.removeItem(at: root) }

        let stale = root.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try fileManager.createDirectory(at: stale, withIntermediateDirectories: false)
        try Data("private".utf8).write(to: stale.appendingPathComponent("pulse-viajes-30-dias.json"))
        let unowned = root.appendingPathComponent("keep", isDirectory: true)
        try fileManager.createDirectory(at: unowned, withIntermediateDirectories: false)

        try NativeJourneyReportTemporaryStore(rootURL: root).removeStaleExports()
        XCTAssertFalse(fileManager.fileExists(atPath: stale.path))
        XCTAssertTrue(fileManager.fileExists(atPath: unowned.path))
    }

    private func report() -> [String: Any] {
        [
            "source": "manual",
            "coverage": "unknown",
            "period": [
                "started_at": "2026-01-01T00:00:00.000000Z",
                "ended_at": "2026-01-31T00:00:00.000000Z",
                "days": 30,
                "timezone": "UTC"
            ],
            "totals": monthlyTotals(),
            "months": []
        ]
    }

    private func monthlyTotals(monthKey: String? = nil) -> [String: Any] {
        var result: [String: Any] = [
            "count": 0,
            "distance_km": 0.0,
            "energy_kwh": 0.0,
            "known_distance_count": 0,
            "known_energy_count": 0,
            "paired_count": 0,
            "paired_distance_km": 0.0,
            "paired_energy_kwh": 0.0,
            "consumption_kwh_100km": NSNull()
        ]
        if let monthKey { result["month_key"] = monthKey }
        return result
    }

    private func encode(_ object: [String: Any]) -> Data {
        try! JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
    }
}
