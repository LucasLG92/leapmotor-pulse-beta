import CoreFoundation
import Foundation

public enum WidgetSnapshotError: Error, Equatable, Sendable {
    case invalidSnapshot
}

/// Parses the versioned server contract into a small, renderer-safe cache record.
public enum WidgetSnapshotPolicy {
    private static let legacyVehicleFields: Set<String> = ["battery_pct", "range_km", "charging"]
    private static let vehicleFields = legacyVehicleFields.union(["locked"])
    private static let energyFields: Set<String> = ["energy_kwh", "energy_cost_eur"]
    private static let allowedAppearances: Set<String> = ["system", "light", "dark"]
    private static let allowedPrivacyModes: Set<String> = ["masked", "visible"]
    private static let legacyVehicleSources: Set<String> = ["demo", "telemetry", "unavailable"]
    private static let allowedStatuses: Set<String> = ["fresh", "stale", "unknown", "unavailable"]
    private static let maximumAgeMinutes = 120
    private static let maximumClockSkew: TimeInterval = 60
    private static let maximumTTL: TimeInterval = 2 * 60 * 60
    private static let energyWindowSeconds: TimeInterval = 30 * 24 * 60 * 60
    private static let energyTolerance: TimeInterval = 1

    /// Rejects malformed or unsafe responses; only selected, typed fields enter the returned value.
    public static func sanitize(
        data: Data,
        expectedProfileID: String,
        now: Date
    ) throws -> CachedWidgetSnapshot {
        guard data.count <= 64 * 1_024, isFinite(now), isProfileID(expectedProfileID) else {
            throw WidgetSnapshotError.invalidSnapshot
        }
        var keyScanner = JSONKeyScanner(data: data)
        guard keyScanner.hasUniqueObjectKeys(),
              let decodedRoot = try? JSONSerialization.jsonObject(with: data),
              let root = decodedRoot as? [String: Any],
              let schemaVersion = exactInteger(root["schema_version"]),
              schemaVersion == 1 || schemaVersion == 2,
              let profile = root["profile"] as? [String: Any],
              let profileID = profile["id"] as? String, profileID == expectedProfileID,
              let rawName = profile["name"] as? String,
              let profileName = safeProfileName(rawName),
              let revision = exactInteger(profile["revision"]), revision >= 1,
              let fields = parseFields(profile["fields"]),
              let appearance = profile["appearance"] as? String, allowedAppearances.contains(appearance),
              let privacyMode = profile["privacy_mode"] as? String, allowedPrivacyModes.contains(privacyMode),
              let maxAgeMinutes = exactInteger(profile["max_age_minutes"]), (15...maximumAgeMinutes).contains(maxAgeMinutes),
              let source = root["source"] as? String,
              let physicalFreshness = root["physical_freshness"] as? String, physicalFreshness == "unknown",
              let status = root["temporal_status"] as? String, allowedStatuses.contains(status),
              let reasonCode = root["reason_code"] as? String, isSafeReasonCode(reasonCode),
              let rawValues = root["values"] as? [String: Any],
              root.keys.contains("server_received_at"), root.keys.contains("observed_at"),
              root.keys.contains("valid_until")
        else { throw WidgetSnapshotError.invalidSnapshot }

        let kind: String
        let energyWindow: WidgetEnergyWindow?
        if schemaVersion == 1 {
            // V1 is closed to the original vehicle fields and origins. V12 metadata is V2-only.
            guard !root.keys.contains("kind"), !root.keys.contains("energy_window"),
                  fields.allSatisfy({ legacyVehicleFields.contains($0) }),
                  legacyVehicleSources.contains(source)
            else { throw WidgetSnapshotError.invalidSnapshot }
            kind = "vehicle"
            energyWindow = nil
        } else {
            guard root.keys.contains("kind"), root.keys.contains("energy_window"),
                  let rawKind = root["kind"] as? String
            else { throw WidgetSnapshotError.invalidSnapshot }
            switch rawKind {
            case "vehicle":
                guard fields.allSatisfy({ vehicleFields.contains($0) }),
                      legacyVehicleSources.contains(source),
                      root["energy_window"] is NSNull
                else { throw WidgetSnapshotError.invalidSnapshot }
                kind = rawKind
                energyWindow = nil
            case "energy":
                guard fields.allSatisfy({ energyFields.contains($0) }),
                      source == "manual_energy",
                      let parsedWindow = parseEnergyWindow(root["energy_window"])
                else { throw WidgetSnapshotError.invalidSnapshot }
                kind = rawKind
                energyWindow = parsedWindow
            default:
                throw WidgetSnapshotError.invalidSnapshot
            }
        }

        guard let generatedAt = parseTimestamp(root["generated_at"]),
              let receivedAtField = parseNullableTimestamp(root["server_received_at"]),
              let observedAtField = parseNullableTimestamp(root["observed_at"]),
              let validUntilField = parseNullableTimestamp(root["valid_until"])
        else { throw WidgetSnapshotError.invalidSnapshot }
        let serverReceivedAt = receivedAtField
        let observedAt = observedAtField
        let validUntil = validUntilField

        if let observedAt {
            guard observedAt <= now.addingTimeInterval(maximumClockSkew) else {
                throw WidgetSnapshotError.invalidSnapshot
            }
        }
        if let validUntil {
            guard let observedAt,
                  validUntil > observedAt,
                  validUntil <= observedAt.addingTimeInterval(TimeInterval(maxAgeMinutes * 60)),
                  validUntil <= now.addingTimeInterval(min(TimeInterval(maxAgeMinutes * 60), maximumTTL))
            else { throw WidgetSnapshotError.invalidSnapshot }
        }
        if status == "fresh" || status == "stale" {
            guard observedAt != nil, validUntil != nil, source != "unavailable" else {
                throw WidgetSnapshotError.invalidSnapshot
            }
        } else if validUntil != nil {
            throw WidgetSnapshotError.invalidSnapshot
        }
        if source == "unavailable" && status == "fresh" {
            throw WidgetSnapshotError.invalidSnapshot
        }

        if kind == "energy" {
            guard let calculatedAt = observedAt,
                  let serverReceivedAt,
                  let energyWindow,
                  serverReceivedAt == calculatedAt,
                  energyWindow.endedAt == calculatedAt,
                  abs(energyWindow.startedAt.timeIntervalSince(calculatedAt.addingTimeInterval(-energyWindowSeconds))) <= energyTolerance,
                  generatedAt.timeIntervalSince(energyWindow.endedAt) >= -energyTolerance,
                  generatedAt.timeIntervalSince(energyWindow.endedAt) <= maximumClockSkew,
                  calculatedAt <= now.addingTimeInterval(maximumClockSkew),
                  generatedAt <= now.addingTimeInterval(maximumClockSkew),
                  status == "fresh" || status == "unavailable"
            else { throw WidgetSnapshotError.invalidSnapshot }

            if status == "fresh" {
                guard reasonCode == "energy_calculated", validUntil != nil else {
                    throw WidgetSnapshotError.invalidSnapshot
                }
            } else if validUntil != nil {
                throw WidgetSnapshotError.invalidSnapshot
            }
        }

        var batteryPercent: Double?
        var rangeKilometers: Double?
        var charging: Bool?
        var locked: Bool?
        var energyKilowattHours: Double?
        var energyCostEuros: Double?
        let canExposeValues = privacyMode == "visible"
            && source != "unavailable"
            && (status == "fresh" || status == "stale")
            && observedAt != nil
            && validUntil.map { $0 > now } == true

        for field in fields {
            guard rawValues.keys.contains(field), let rawValue = rawValues[field] else {
                throw WidgetSnapshotError.invalidSnapshot
            }

            if kind == "energy" {
                if status == "unavailable" {
                    guard rawValue is NSNull else { throw WidgetSnapshotError.invalidSnapshot }
                    continue
                }
                if rawValue is NSNull {
                    // The API returns nulls for masked profiles even when the aggregate is fresh.
                    guard privacyMode == "masked" else { throw WidgetSnapshotError.invalidSnapshot }
                    continue
                }
                switch field {
                case "energy_kwh":
                    guard let number = boundedNumber(rawValue, range: 0...1_000_000_000) else {
                        throw WidgetSnapshotError.invalidSnapshot
                    }
                    if canExposeValues { energyKilowattHours = number }
                case "energy_cost_eur":
                    guard let number = boundedNumber(rawValue, range: -1_000_000_000...1_000_000_000) else {
                        throw WidgetSnapshotError.invalidSnapshot
                    }
                    if canExposeValues { energyCostEuros = number }
                default:
                    throw WidgetSnapshotError.invalidSnapshot
                }
                continue
            }

            if field == "locked" {
                if rawValue is NSNull { continue }
                guard let value = strictJSONBoolean(rawValue) else {
                    throw WidgetSnapshotError.invalidSnapshot
                }
                if canExposeValues { locked = value }
                continue
            }

            guard canExposeValues, !(rawValue is NSNull) else { continue }
            switch field {
            case "battery_pct":
                batteryPercent = boundedNumber(rawValue, range: 0...100)
            case "range_km":
                rangeKilometers = boundedNumber(rawValue, range: 0...5_000)
            case "charging":
                charging = strictJSONBoolean(rawValue)
            default:
                throw WidgetSnapshotError.invalidSnapshot
            }
        }

        return CachedWidgetSnapshot(
            profileID: profileID,
            profileName: profileName,
            revision: revision,
            fields: fields,
            appearance: appearance,
            privacyMode: privacyMode,
            source: source,
            observedAt: observedAt,
            validUntil: validUntil,
            batteryPercent: batteryPercent,
            rangeKilometers: rangeKilometers,
            charging: charging,
            maxAgeMinutes: maxAgeMinutes,
            locked: locked,
            energyKilowattHours: energyKilowattHours,
            energyCostEuros: energyCostEuros,
            kind: kind,
            energyWindow: energyWindow,
            generatedAt: generatedAt
        )
    }

    /// Revalidates a decoded cache value before rendering and removes masked values defensively.
    public static func visibleSnapshot(_ cached: CachedWidgetSnapshot, now: Date) -> CachedWidgetSnapshot? {
        guard isFinite(now), isProfileID(cached.profileID),
              let profileName = safeProfileName(cached.profileName),
              cached.revision >= 1,
              let fields = parseFields(cached.fields),
              allowedAppearances.contains(cached.appearance),
              allowedPrivacyModes.contains(cached.privacyMode),
              (15...maximumAgeMinutes).contains(cached.maxAgeMinutes),
              familyIsCoherent(kind: cached.kind, fields: fields, source: cached.source, energyWindow: cached.energyWindow)
        else { return nil }

        if let generatedAt = cached.generatedAt, !isFinite(generatedAt) { return nil }
        if let observedAt = cached.observedAt {
            guard isFinite(observedAt), observedAt <= now.addingTimeInterval(maximumClockSkew) else { return nil }
            if now >= observedAt.addingTimeInterval(TimeInterval(cached.maxAgeMinutes * 60)) { return nil }
        }
        if let validUntil = cached.validUntil {
            guard isFinite(validUntil), let observedAt = cached.observedAt,
                  validUntil > observedAt,
                  validUntil <= observedAt.addingTimeInterval(TimeInterval(cached.maxAgeMinutes * 60)),
                  validUntil <= now.addingTimeInterval(min(TimeInterval(cached.maxAgeMinutes * 60), maximumTTL)),
                  validUntil > now
            else { return nil }
        }

        if cached.kind == "energy" {
            guard let calculatedAt = cached.observedAt,
                  let generatedAt = cached.generatedAt,
                  let energyWindow = cached.energyWindow,
                  energyWindowIsValid(energyWindow, calculatedAt: calculatedAt, generatedAt: generatedAt, now: now)
            else { return nil }
            if cached.validUntil == nil,
               cached.energyKilowattHours != nil || cached.energyCostEuros != nil {
                return nil
            }
            if cached.validUntil != nil && cached.privacyMode == "visible" {
                guard fields.contains("energy_kwh") == (cached.energyKilowattHours != nil),
                      fields.contains("energy_cost_eur") == (cached.energyCostEuros != nil)
                else { return nil }
            }
        }

        guard isValid(cached.batteryPercent, field: "battery_pct", fields: fields, range: 0...100),
              isValid(cached.rangeKilometers, field: "range_km", fields: fields, range: 0...5_000),
              isValid(cached.charging, field: "charging", fields: fields),
              isValid(cached.locked, field: "locked", fields: fields),
              isValid(cached.energyKilowattHours, field: "energy_kwh", fields: fields, range: 0...1_000_000_000),
              isValid(cached.energyCostEuros, field: "energy_cost_eur", fields: fields, range: -1_000_000_000...1_000_000_000)
        else { return nil }

        let mayExposeValues = cached.privacyMode == "visible"
            && cached.source != "unavailable"
            && cached.observedAt != nil
            && cached.validUntil != nil
        if !mayExposeValues && cached.privacyMode == "visible"
            && (cached.batteryPercent != nil || cached.rangeKilometers != nil || cached.charging != nil
                || cached.locked != nil || cached.energyKilowattHours != nil || cached.energyCostEuros != nil) {
            return nil
        }

        return CachedWidgetSnapshot(
            profileID: cached.profileID,
            profileName: profileName,
            revision: cached.revision,
            fields: fields,
            appearance: cached.appearance,
            privacyMode: cached.privacyMode,
            source: cached.source,
            observedAt: cached.observedAt,
            validUntil: cached.validUntil,
            batteryPercent: mayExposeValues ? cached.batteryPercent : nil,
            rangeKilometers: mayExposeValues ? cached.rangeKilometers : nil,
            charging: mayExposeValues ? cached.charging : nil,
            maxAgeMinutes: cached.maxAgeMinutes,
            locked: mayExposeValues ? cached.locked : nil,
            energyKilowattHours: mayExposeValues ? cached.energyKilowattHours : nil,
            energyCostEuros: mayExposeValues ? cached.energyCostEuros : nil,
            kind: cached.kind,
            energyWindow: cached.energyWindow,
            generatedAt: cached.generatedAt
        )
    }

    private static func parseFields(_ value: Any?) -> [String]? {
        guard let rawFields = value as? [Any], (1...3).contains(rawFields.count) else { return nil }
        var result: [String] = []
        for item in rawFields {
            guard let field = item as? String,
                  vehicleFields.contains(field) || energyFields.contains(field),
                  !result.contains(field)
            else { return nil }
            result.append(field)
        }
        return result
    }

    private static func parseEnergyWindow(_ value: Any?) -> WidgetEnergyWindow? {
        guard let object = value as? [String: Any], object.count == 2,
              let startedText = object["started_at"] as? String,
              let endedText = object["ended_at"] as? String,
              let startedAt = parseUTCTimestamp(startedText),
              let endedAt = parseUTCTimestamp(endedText)
        else { return nil }
        return WidgetEnergyWindow(startedAt: startedAt, endedAt: endedAt)
    }

    private static func energyWindowIsValid(
        _ window: WidgetEnergyWindow,
        calculatedAt: Date,
        generatedAt: Date,
        now: Date
    ) -> Bool {
        guard isFinite(window.startedAt), isFinite(window.endedAt),
              window.endedAt == calculatedAt,
              abs(window.startedAt.timeIntervalSince(calculatedAt.addingTimeInterval(-energyWindowSeconds))) <= energyTolerance,
              generatedAt.timeIntervalSince(window.endedAt) >= -energyTolerance,
              generatedAt.timeIntervalSince(window.endedAt) <= maximumClockSkew,
              calculatedAt <= now.addingTimeInterval(maximumClockSkew),
              generatedAt <= now.addingTimeInterval(maximumClockSkew)
        else { return false }
        return true
    }

    private static func familyIsCoherent(
        kind: String,
        fields: [String],
        source: String,
        energyWindow: WidgetEnergyWindow?
    ) -> Bool {
        switch kind {
        case "vehicle":
            return energyWindow == nil
                && legacyVehicleSources.contains(source)
                && fields.allSatisfy({ vehicleFields.contains($0) })
        case "energy":
            return energyWindow != nil
                && source == "manual_energy"
                && fields.allSatisfy({ energyFields.contains($0) })
        default:
            return false
        }
    }

    private static func safeProfileName(_ value: String) -> String? {
        let name = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty, name.utf16.count <= 60,
              !name.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) })
        else { return nil }
        return name
    }

    private static func isSafeReasonCode(_ value: String) -> Bool {
        !value.isEmpty && value.utf8.count <= 64
            && value.utf8.allSatisfy { (97...122).contains($0) || (48...57).contains($0) || $0 == 95 }
    }

    private static func isProfileID(_ value: String) -> Bool {
        value.utf8.count == 32
            && value.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) }
    }

    private static func exactInteger(_ value: Any?) -> Int? {
        guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID() else { return nil }
        return Int(number.stringValue)
    }

    private static func boundedNumber(_ value: Any, range: ClosedRange<Double>) -> Double? {
        guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID() else { return nil }
        let parsed = number.doubleValue
        return parsed.isFinite && range.contains(parsed) ? parsed : nil
    }

    private static func strictJSONBoolean(_ value: Any) -> Bool? {
        guard let number = value as? NSNumber, CFGetTypeID(number) == CFBooleanGetTypeID() else { return nil }
        return number.boolValue
    }

    private static func parseNullableTimestamp(_ value: Any?) -> Date?? {
        guard let value else { return nil }
        if value is NSNull { return .some(nil) }
        guard let text = value as? String, let date = parseTimestamp(text) else { return nil }
        return .some(.some(date))
    }

    private static func parseUTCTimestamp(_ value: String) -> Date? {
        guard value.hasSuffix("Z") else { return nil }
        return parseTimestamp(value)
    }

    private static func parseTimestamp(_ value: Any?) -> Date? {
        guard let text = value as? String,
              text.range(of: #"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})$"#, options: .regularExpression) != nil
        else { return nil }

        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: text), isFinite(date) { return date }
        formatter.formatOptions = [.withInternetDateTime]
        guard let date = formatter.date(from: text), isFinite(date) else { return nil }
        return date
    }

    private static func isFinite(_ date: Date) -> Bool {
        date.timeIntervalSince1970.isFinite
    }

    private static func isValid(_ value: Double?, field: String, fields: [String], range: ClosedRange<Double>) -> Bool {
        guard let value else { return true }
        return fields.contains(field) && value.isFinite && range.contains(value)
    }

    private static func isValid(_ value: Bool?, field: String, fields: [String]) -> Bool {
        value == nil || fields.contains(field)
    }
}

/// Detects duplicate JSON keys before JSONSerialization collapses them into a dictionary.
private struct JSONKeyScanner {
    private let bytes: [UInt8]
    private var index = 0

    init(data: Data) {
        bytes = Array(data)
    }

    mutating func hasUniqueObjectKeys() -> Bool {
        guard consumeValue(depth: 0) else { return false }
        skipWhitespace()
        return index == bytes.count
    }

    private mutating func consumeValue(depth: Int) -> Bool {
        guard depth <= 128 else { return false }
        skipWhitespace()
        guard index < bytes.count else { return false }
        switch bytes[index] {
        case 0x7B: return consumeObject(depth: depth + 1)
        case 0x5B: return consumeArray(depth: depth + 1)
        case 0x22: return consumeString() != nil
        default: return consumePrimitive()
        }
    }

    private mutating func consumeObject(depth: Int) -> Bool {
        index += 1
        skipWhitespace()
        if consume(0x7D) { return true }
        var keys = Set<String>()
        while true {
            skipWhitespace()
            guard let range = consumeString(),
                  let key = decodeJSONString(Data(bytes[range])), keys.insert(key).inserted
            else { return false }
            skipWhitespace()
            guard consume(0x3A), consumeValue(depth: depth) else { return false }
            skipWhitespace()
            if consume(0x7D) { return true }
            guard consume(0x2C) else { return false }
        }
    }

    private mutating func consumeArray(depth: Int) -> Bool {
        index += 1
        skipWhitespace()
        if consume(0x5D) { return true }
        while true {
            guard consumeValue(depth: depth) else { return false }
            skipWhitespace()
            if consume(0x5D) { return true }
            guard consume(0x2C) else { return false }
        }
    }

    private mutating func consumeString() -> Range<Int>? {
        guard consume(0x22) else { return nil }
        let start = index - 1
        while index < bytes.count {
            let byte = bytes[index]
            index += 1
            if byte == 0x5C {
                guard index < bytes.count else { return nil }
                index += 1
            } else if byte == 0x22 {
                return start..<index
            }
        }
        return nil
    }

    private mutating func consumePrimitive() -> Bool {
        let start = index
        while index < bytes.count, !isDelimiter(bytes[index]) { index += 1 }
        return index > start
    }

    private mutating func consume(_ byte: UInt8) -> Bool {
        guard index < bytes.count, bytes[index] == byte else { return false }
        index += 1
        return true
    }

    private mutating func skipWhitespace() {
        while index < bytes.count, [0x20, 0x09, 0x0A, 0x0D].contains(bytes[index]) { index += 1 }
    }

    private func isDelimiter(_ byte: UInt8) -> Bool {
        [0x20, 0x09, 0x0A, 0x0D, 0x2C, 0x5D, 0x7D].contains(byte)
    }

    private func decodeJSONString(_ data: Data) -> String? {
        (try? JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed])) as? String
    }
}
