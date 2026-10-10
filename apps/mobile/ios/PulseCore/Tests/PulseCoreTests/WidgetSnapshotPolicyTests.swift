import XCTest
@testable import PulseCore

final class WidgetSnapshotPolicyTests: XCTestCase {
    private let profileID = "0123456789abcdef0123456789abcdef"
    private let now = ISO8601DateFormatter().date(from: "2026-10-08T10:00:00Z")!

    func testKeepsOnlySelectedSafeFieldsAndDiscardsUnknownPayload() throws {
        let input = try payload(values: [
            "battery_pct": 82,
            "range_km": 240.5,
            "charging": false,
            "vin": "must-not-persist",
            "latitude": 40.4,
            "longitude": -3.7,
        ], extraRoot: ["owner_id": "private-owner", "access_token": "never-cache"])
        let snapshot = try sanitize(input)
        let encoded = String(decoding: try JSONEncoder().encode(snapshot), as: UTF8.self).lowercased()

        XCTAssertEqual(snapshot.profileID, profileID)
        XCTAssertEqual(snapshot.maxAgeMinutes, 15)
        XCTAssertEqual(snapshot.fields, ["battery_pct", "range_km", "charging"])
        XCTAssertEqual(snapshot.batteryPercent, 82)
        XCTAssertEqual(snapshot.rangeKilometers, 240.5)
        XCTAssertEqual(snapshot.charging, false)
        XCTAssertFalse(encoded.contains("vin"))
        XCTAssertFalse(encoded.contains("latitude"))
        XCTAssertFalse(encoded.contains("longitude"))
        XCTAssertFalse(encoded.contains("owner_id"))
        XCTAssertFalse(encoded.contains("access_token"))
        XCTAssertFalse(encoded.contains("generated_at"))
    }

    func testRejectsWrongIdentitySchemaFieldsAndNaiveTimes() throws {
        XCTAssertThrowsError(try sanitize(try payload(profile: ["id": "ffffffffffffffffffffffffffffffff"])))
        XCTAssertThrowsError(try sanitize(try payload(schemaVersion: true)))
        XCTAssertThrowsError(try sanitize(try payload(revision: 0)))
        XCTAssertThrowsError(try sanitize(try payload(fields: ["battery_pct", "battery_pct"])))
        XCTAssertThrowsError(try sanitize(try payload(maxAgeMinutes: 121)))
        XCTAssertThrowsError(try sanitize(try payload(observedAt: "2026-10-08T09:59:00")))
        XCTAssertThrowsError(try sanitize(try payload(physicalFreshness: "fresh")))
        XCTAssertThrowsError(try sanitize(try payload(status: "current")))
    }

    func testRejectsDuplicateKeysAndUnboundedDeadlines() throws {
        let ordinary = String(decoding: try payload(), as: UTF8.self)
        var duplicate = ordinary
        duplicate.insert(contentsOf: "\"schema_version\":1,\"schema\\u005fversion\":1,", at: duplicate.index(after: duplicate.startIndex))
        XCTAssertThrowsError(try sanitize(Data(duplicate.utf8)))

        XCTAssertThrowsError(try sanitize(try payload(validUntil: "2026-10-08T10:16:00Z")))
        XCTAssertThrowsError(try sanitize(try payload(
            observedAt: "2026-10-08T09:00:00Z", validUntil: "2026-10-08T09:16:00Z"
        )))
        XCTAssertThrowsError(try sanitize(try payload(
            maxAgeMinutes: 120, validUntil: "2026-10-08T12:00:01Z"
        )))
    }

    func testFutureObservationsOverClockSkewAreRejected() throws {
        XCTAssertThrowsError(try sanitize(try payload(
            observedAt: "2026-10-08T10:01:01Z", validUntil: "2026-10-08T10:10:00Z"
        )))
    }

    func testAcceptsOffsetTimestampAndRedactsPoisonedValueTypes() throws {
        let snapshot = try sanitize(try payload(
            generatedAt: "2026-10-08T12:00:00+02:00",
            serverReceivedAt: "2026-10-08T11:59:30+02:00",
            observedAt: "2026-10-08T11:59:30+02:00",
            values: ["battery_pct": true, "range_km": 5_001, "charging": 1]
        ))

        XCTAssertNil(snapshot.batteryPercent)
        XCTAssertNil(snapshot.rangeKilometers)
        XCTAssertNil(snapshot.charging)
        XCTAssertNotNil(snapshot.observedAt)
    }

    func testMaskedUnavailableAndExpiredValuesNeverReachRenderer() throws {
        let masked = try sanitize(try payload(privacyMode: "masked"))
        let maskedVisible = try XCTUnwrap(WidgetSnapshotPolicy.visibleSnapshot(masked, now: now))
        XCTAssertNil(maskedVisible.batteryPercent)
        XCTAssertNil(maskedVisible.rangeKilometers)
        XCTAssertNil(maskedVisible.charging)

        let unavailable = try sanitize(try payload(
            source: "unavailable", status: "unknown", observedAt: NSNull(), validUntil: NSNull()
        ))
        XCTAssertNil(unavailable.batteryPercent)
        XCTAssertNil(unavailable.rangeKilometers)
        XCTAssertNil(unavailable.charging)

        let expired = try sanitize(try payload(
            status: "stale",
            observedAt: "2026-10-08T09:30:00Z",
            validUntil: "2026-10-08T09:44:00Z"
        ))
        XCTAssertNil(expired.batteryPercent)
        XCTAssertNil(WidgetSnapshotPolicy.visibleSnapshot(expired, now: now))
    }

    func testVisibleSnapshotRejectsExpiredAndPoisonedCacheValues() throws {
        let safe = try sanitize(try payload(fields: ["battery_pct"], values: ["battery_pct": 82]))
        XCTAssertNil(WidgetSnapshotPolicy.visibleSnapshot(safe, now: date("2026-10-08T10:10:00Z")))

        let expiredWithoutDeadline = CachedWidgetSnapshot(
            profileID: profileID,
            profileName: "B10",
            revision: 2,
            fields: ["battery_pct"],
            appearance: "system",
            privacyMode: "visible",
            source: "telemetry",
            observedAt: date("2026-10-08T09:30:00Z"),
            validUntil: nil,
            batteryPercent: nil,
            rangeKilometers: nil,
            charging: nil,
            maxAgeMinutes: 15
        )
        XCTAssertNil(WidgetSnapshotPolicy.visibleSnapshot(expiredWithoutDeadline, now: now))

        let poisoned = CachedWidgetSnapshot(
            profileID: profileID,
            profileName: "B10",
            revision: 2,
            fields: ["battery_pct"],
            appearance: "system",
            privacyMode: "visible",
            source: "telemetry",
            observedAt: safe.observedAt,
            validUntil: safe.validUntil,
            batteryPercent: 82,
            rangeKilometers: 80,
            charging: nil
        )
        XCTAssertNil(WidgetSnapshotPolicy.visibleSnapshot(poisoned, now: now))

        let extendedDeadline = CachedWidgetSnapshot(
            profileID: profileID,
            profileName: "B10",
            revision: 2,
            fields: ["battery_pct"],
            appearance: "system",
            privacyMode: "visible",
            source: "telemetry",
            observedAt: safe.observedAt,
            validUntil: date("2026-10-08T10:15:00Z"),
            batteryPercent: 82,
            rangeKilometers: nil,
            charging: nil,
            maxAgeMinutes: 15
        )
        XCTAssertNil(WidgetSnapshotPolicy.visibleSnapshot(extendedDeadline, now: now))

        let invalidAge = CachedWidgetSnapshot(
            profileID: profileID,
            profileName: "B10",
            revision: 2,
            fields: ["battery_pct"],
            appearance: "system",
            privacyMode: "visible",
            source: "telemetry",
            observedAt: safe.observedAt,
            validUntil: safe.validUntil,
            batteryPercent: 82,
            rangeKilometers: nil,
            charging: nil,
            maxAgeMinutes: 14
        )
        XCTAssertNil(WidgetSnapshotPolicy.visibleSnapshot(invalidAge, now: now))

        let maskedPoison = CachedWidgetSnapshot(
            profileID: profileID,
            profileName: "B10",
            revision: 2,
            fields: ["battery_pct"],
            appearance: "system",
            privacyMode: "masked",
            source: "telemetry",
            observedAt: safe.observedAt,
            validUntil: safe.validUntil,
            batteryPercent: 82,
            rangeKilometers: nil,
            charging: nil
        )
        XCTAssertNil(try XCTUnwrap(WidgetSnapshotPolicy.visibleSnapshot(maskedPoison, now: now)).batteryPercent)
    }

    func testV2VehicleFamilyAndLockedAreStrict() throws {
        let vehicle = try sanitize(try payload(
            schemaVersion: 2,
            fields: ["battery_pct", "locked"],
            values: ["battery_pct": 82, "locked": true],
            extraRoot: ["kind": "vehicle", "energy_window": NSNull()]
        ))
        XCTAssertEqual(vehicle.kind, "vehicle")
        XCTAssertNil(vehicle.energyWindow)
        XCTAssertEqual(vehicle.locked, true)

        let open = try sanitize(try payload(
            schemaVersion: 2,
            fields: ["locked"],
            values: ["locked": false],
            extraRoot: ["kind": "vehicle", "energy_window": NSNull()]
        ))
        XCTAssertEqual(open.locked, false)

        let nullable = try sanitize(try payload(
            schemaVersion: 2,
            fields: ["locked"],
            values: ["locked": NSNull()],
            extraRoot: ["kind": "vehicle", "energy_window": NSNull()]
        ))
        XCTAssertNil(nullable.locked)

        XCTAssertThrowsError(try sanitize(try payload(
            schemaVersion: 2,
            fields: ["locked"],
            values: ["locked": 1],
            extraRoot: ["kind": "vehicle", "energy_window": NSNull()]
        )))
        XCTAssertThrowsError(try sanitize(try payload(
            schemaVersion: 2,
            fields: ["locked", "energy_kwh"],
            values: ["locked": true, "energy_kwh": 10],
            extraRoot: ["kind": "vehicle", "energy_window": NSNull()]
        )))
        XCTAssertThrowsError(try sanitize(try payload(
            schemaVersion: 2,
            fields: ["battery_pct"],
            source: "manual_energy",
            extraRoot: ["kind": "vehicle", "energy_window": NSNull()]
        )))
    }

    func testV1RemainsClosedToNewFieldsAndOrigins() throws {
        let legacy = try sanitize(try payload())
        XCTAssertEqual(legacy.kind, "vehicle")
        XCTAssertNil(legacy.energyWindow)
        XCTAssertEqual(legacy.fields, ["battery_pct", "range_km", "charging"])

        XCTAssertThrowsError(try sanitize(try payload(fields: ["locked"])))
        XCTAssertThrowsError(try sanitize(try payload(source: "manual_energy")))
        XCTAssertThrowsError(try sanitize(try payload(
            extraRoot: ["kind": "vehicle", "energy_window": NSNull()]
        )))
    }

    func testV2EnergyAcceptsThirtyDayWindowAndNegativeRecordedCost() throws {
        let snapshot = try sanitize(try energyPayload(values: [
            "energy_kwh": 12.345,
            "energy_cost_eur": -4.56,
        ]))

        XCTAssertEqual(snapshot.kind, "energy")
        XCTAssertEqual(snapshot.source, "manual_energy")
        XCTAssertEqual(snapshot.energyKilowattHours, 12.345)
        XCTAssertEqual(snapshot.energyCostEuros, -4.56)
        XCTAssertEqual(snapshot.observedAt, date("2026-10-08T09:59:30Z"))
        XCTAssertEqual(snapshot.generatedAt, date("2026-10-08T10:00:00Z"))
        XCTAssertEqual(snapshot.energyWindow?.startedAt, date("2026-09-08T09:59:30Z"))
        XCTAssertEqual(snapshot.energyWindow?.endedAt, snapshot.observedAt)
        XCTAssertNotNil(WidgetSnapshotPolicy.visibleSnapshot(snapshot, now: now))
    }

    func testV2EnergyBoundsAreInclusiveAndRejectBooleansOrOverflow() throws {
        let boundaries = try sanitize(try energyPayload(values: [
            "energy_kwh": 1_000_000_000,
            "energy_cost_eur": -1_000_000_000,
        ]))
        XCTAssertEqual(boundaries.energyKilowattHours, 1_000_000_000)
        XCTAssertEqual(boundaries.energyCostEuros, -1_000_000_000)

        for values in [
            ["energy_kwh": -0.001, "energy_cost_eur": 1] as [String: Any],
            ["energy_kwh": 1_000_000_001, "energy_cost_eur": 1],
            ["energy_kwh": true, "energy_cost_eur": 1],
            ["energy_kwh": 1, "energy_cost_eur": 1_000_000_001],
            ["energy_kwh": 1, "energy_cost_eur": false],
        ] {
            XCTAssertThrowsError(try sanitize(try energyPayload(values: values)))
        }
    }

    func testV2EnergyRejectsIncorrectWindowOrCalculationTimestamps() throws {
        XCTAssertThrowsError(try sanitize(try energyPayload(
            startedAt: "2026-09-08T09:59:28Z"
        )))
        XCTAssertThrowsError(try sanitize(try energyPayload(
            endedAt: "2026-10-08T09:59:31Z"
        )))
        XCTAssertThrowsError(try sanitize(try energyPayload(
            generatedAt: "2026-10-08T10:00:31Z"
        )))
        XCTAssertThrowsError(try sanitize(try energyPayload(
            startedAt: "2026-09-08T11:59:30+02:00"
        )))
        XCTAssertThrowsError(try sanitize(try energyPayload(
            serverReceivedAt: "2026-10-08T09:59:32Z"
        )))
        XCTAssertThrowsError(try sanitize(try energyPayload(
            calculatedAt: "2026-10-08T10:01:01Z",
            generatedAt: "2026-10-08T10:01:01Z",
            startedAt: "2026-09-08T10:01:01Z",
            endedAt: "2026-10-08T10:01:01Z"
        )))
    }

    func testEmptyAndMaskedEnergyNeverExposeValues() throws {
        let empty = try sanitize(try energyPayload(
            status: "unavailable",
            reasonCode: "energy_empty",
            validUntil: NSNull(),
            values: ["energy_kwh": NSNull(), "energy_cost_eur": NSNull()]
        ))
        let emptyVisible = try XCTUnwrap(WidgetSnapshotPolicy.visibleSnapshot(empty, now: now))
        XCTAssertNil(emptyVisible.energyKilowattHours)
        XCTAssertNil(emptyVisible.energyCostEuros)
        XCTAssertThrowsError(try sanitize(try energyPayload(
            status: "unavailable",
            reasonCode: "energy_empty",
            validUntil: NSNull(),
            values: ["energy_kwh": 0, "energy_cost_eur": NSNull()]
        )))
        XCTAssertThrowsError(try sanitize(try energyPayload(
            privacyMode: "visible",
            values: ["energy_kwh": NSNull(), "energy_cost_eur": NSNull()]
        )))

        let maskedNulls = try sanitize(try energyPayload(
            privacyMode: "masked",
            values: ["energy_kwh": NSNull(), "energy_cost_eur": NSNull()]
        ))
        let maskedNullProjection = try XCTUnwrap(WidgetSnapshotPolicy.visibleSnapshot(maskedNulls, now: now))
        XCTAssertEqual(maskedNullProjection.privacyMode, "masked")
        XCTAssertNil(maskedNullProjection.energyKilowattHours)
        XCTAssertNil(maskedNullProjection.energyCostEuros)

        let masked = try sanitize(try energyPayload(
            privacyMode: "masked",
            values: ["energy_kwh": 12.345, "energy_cost_eur": -4.56]
        ))
        let maskedVisible = try XCTUnwrap(WidgetSnapshotPolicy.visibleSnapshot(masked, now: now))
        XCTAssertNil(maskedVisible.energyKilowattHours)
        XCTAssertNil(maskedVisible.energyCostEuros)
        let projection = String(decoding: try JSONEncoder().encode(maskedVisible), as: UTF8.self)
        XCTAssertFalse(projection.contains("12.345"))
        XCTAssertFalse(projection.contains("-4.56"))
    }

    func testCodableMigratesOnlyLegacyVehicleAndRoundTripsV2Projection() throws {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        let legacy = try decoder.decode(CachedWidgetSnapshot.self, from: legacyCacheData(
            fields: ["battery_pct", "range_km", "charging"], source: "telemetry"
        ))
        XCTAssertEqual(legacy.kind, "vehicle")
        XCTAssertNil(legacy.energyWindow)
        XCTAssertNil(legacy.generatedAt)
        XCTAssertThrowsError(try decoder.decode(CachedWidgetSnapshot.self, from: legacyCacheData(
            fields: ["energy_kwh"], source: "manual_energy"
        )))
        XCTAssertThrowsError(try decoder.decode(CachedWidgetSnapshot.self, from: legacyCacheData(
            fields: ["locked"], source: "telemetry"
        )))

        let energy = try sanitize(try energyPayload(values: [
            "energy_kwh": 12.345,
            "energy_cost_eur": -4.56,
        ], extraRoot: ["access_token": "never-cache", "owner_id": "private-owner", "vin": "never-persist"]))
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        let encoded = try encoder.encode(energy)
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: encoded) as? [String: Any])
        XCTAssertEqual(object["kind"] as? String, "energy")
        XCTAssertNotNil(object["energyWindow"] as? [String: Any])
        XCTAssertNotNil(object["generatedAt"] as? String)
        XCTAssertNil(object["access_token"])
        XCTAssertNil(object["owner_id"])
        XCTAssertNil(object["vin"])
        let roundTrip = try decoder.decode(CachedWidgetSnapshot.self, from: encoded)
        XCTAssertEqual(roundTrip, energy)
    }

    private func sanitize(_ data: Data) throws -> CachedWidgetSnapshot {
        try WidgetSnapshotPolicy.sanitize(data: data, expectedProfileID: profileID, now: now)
    }

    private func payload(
        schemaVersion: Any = 1,
        profileID: String? = nil,
        name: Any = "B10",
        revision: Any = 2,
        fields: [Any] = ["battery_pct", "range_km", "charging"],
        appearance: Any = "system",
        privacyMode: Any = "visible",
        maxAgeMinutes: Any = 15,
        source: Any = "telemetry",
        status: Any = "fresh",
        physicalFreshness: Any = "unknown",
        generatedAt: Any = "2026-10-08T10:00:00Z",
        serverReceivedAt: Any = "2026-10-08T09:59:30Z",
        observedAt: Any = "2026-10-08T09:59:30Z",
        validUntil: Any = "2026-10-08T10:10:00Z",
        reasonCode: Any = "observation_recent",
        values: [String: Any] = ["battery_pct": 82, "range_km": 240.5, "charging": false],
        extraRoot: [String: Any] = [:],
        profile: [String: Any] = [:]
    ) throws -> Data {
        var profileObject: [String: Any] = [
            "id": profileID ?? self.profileID,
            "name": name,
            "revision": revision,
            "fields": fields,
            "appearance": appearance,
            "privacy_mode": privacyMode,
            "max_age_minutes": maxAgeMinutes,
        ]
        profileObject.merge(profile) { _, replacement in replacement }
        var root: [String: Any] = [
            "schema_version": schemaVersion,
            "profile": profileObject,
            "source": source,
            "generated_at": generatedAt,
            "server_received_at": serverReceivedAt,
            "observed_at": observedAt,
            "physical_freshness": physicalFreshness,
            "temporal_status": status,
            "reason_code": reasonCode,
            "valid_until": validUntil,
            "values": values,
        ]
        root.merge(extraRoot) { _, replacement in replacement }
        return try JSONSerialization.data(withJSONObject: root)
    }

    private func energyPayload(
        fields: [Any] = ["energy_kwh", "energy_cost_eur"],
        privacyMode: Any = "visible",
        status: Any = "fresh",
        reasonCode: Any = "energy_calculated",
        calculatedAt: Any = "2026-10-08T09:59:30Z",
        generatedAt: Any = "2026-10-08T10:00:00Z",
        serverReceivedAt: Any? = nil,
        startedAt: Any = "2026-09-08T09:59:30Z",
        endedAt: Any = "2026-10-08T09:59:30Z",
        validUntil: Any = "2026-10-08T10:14:30Z",
        values: [String: Any] = ["energy_kwh": 12.345, "energy_cost_eur": -4.56],
        extraRoot: [String: Any] = [:]
    ) throws -> Data {
        var versionedMetadata: [String: Any] = [
            "kind": "energy",
            "energy_window": ["started_at": startedAt, "ended_at": endedAt],
        ]
        versionedMetadata.merge(extraRoot) { _, replacement in replacement }
        return try payload(
            schemaVersion: 2,
            fields: fields,
            privacyMode: privacyMode,
            maxAgeMinutes: 15,
            source: "manual_energy",
            status: status,
            generatedAt: generatedAt,
            serverReceivedAt: serverReceivedAt ?? calculatedAt,
            observedAt: calculatedAt,
            validUntil: validUntil,
            reasonCode: reasonCode,
            values: values,
            extraRoot: versionedMetadata
        )
    }

    private func legacyCacheData(fields: [String], source: String) throws -> Data {
        try JSONSerialization.data(withJSONObject: [
            "profileID": profileID,
            "profileName": "B10",
            "revision": 2,
            "fields": fields,
            "appearance": "system",
            "privacyMode": "visible",
            "source": source,
            "maxAgeMinutes": 15,
            "observedAt": "2026-10-08T09:59:30Z",
            "validUntil": "2026-10-08T10:10:00Z",
            "batteryPercent": 82,
            "rangeKilometers": 240.5,
            "charging": false,
        ])
    }

    private func date(_ value: String) -> Date {
        ISO8601DateFormatter().date(from: value)!
    }

}
