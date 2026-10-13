import CoreFoundation
import Foundation

public enum NativeJourneyReportPolicyError: Error, Equatable {
    case invalidReport
}

/// Validates and rebuilds the deliberately small manual-journey report contract.
public enum NativeJourneyReportPolicy {
    public static let maximumBodyBytes = 64 * 1_024

    public static func requestedDays(arguments: [String: Any]) -> Int? {
        guard Set(arguments.keys) == ["days"], let number = arguments["days"] as? NSNumber,
              CFGetTypeID(number) != CFBooleanGetTypeID(), number.doubleValue.isFinite,
              [30.0, 90.0, 365.0].contains(number.doubleValue)
        else { return nil }
        return Int(number.doubleValue)
    }

    private static let totalsKeys: Set<String> = [
        "count", "distance_km", "energy_kwh", "known_distance_count", "known_energy_count",
        "paired_count", "paired_distance_km", "paired_energy_kwh", "consumption_kwh_100km"
    ]
    private static let monthKeys = totalsKeys.union(["month_key"])

    public static func sanitizedJSON(data: Data, expectedDays: Int) throws -> Data {
        guard [30, 90, 365].contains(expectedDays),
              data.count <= maximumBodyBytes,
              String(data: data, encoding: .utf8) != nil
        else { throw NativeJourneyReportPolicyError.invalidReport }

        var scanner = JourneyJSONKeyScanner(data: data)
        guard scanner.hasUniqueObjectKeys(),
              let decoded = try? JSONSerialization.jsonObject(with: data),
              let root = decoded as? [String: Any],
              keys(of: root) == ["source", "coverage", "period", "totals", "months"],
              root["source"] as? String == "manual",
              root["coverage"] as? String == "unknown",
              let period = root["period"] as? [String: Any],
              keys(of: period) == ["started_at", "ended_at", "days", "timezone"],
              let startedText = period["started_at"] as? String,
              let endedText = period["ended_at"] as? String,
              let started = utcDate(startedText),
              let ended = utcDate(endedText),
              let days = exactInteger(period["days"]), days == expectedDays,
              period["timezone"] as? String == "UTC"
        else { throw NativeJourneyReportPolicyError.invalidReport }

        let interval = ended.timeIntervalSince(started)
        guard interval > 0,
              let totals = parseTotals(root["totals"], allowedKeys: totalsKeys),
              let rawMonths = root["months"] as? [Any], rawMonths.count <= 13
        else { throw NativeJourneyReportPolicyError.invalidReport }

        var months: [[String: Any]] = []
        var previousMonth: String?
        for rawMonth in rawMonths {
            guard let object = rawMonth as? [String: Any],
                  keys(of: object) == monthKeys,
                  let month = object["month_key"] as? String,
                  month.range(of: "^[0-9]{4}-(0[1-9]|1[0-2])$", options: .regularExpression) != nil,
                  Int(month.prefix(4)).map({ $0 > 0 }) == true,
                  previousMonth.map({ $0 < month }) ?? true,
                  let parsed = parseTotals(object, allowedKeys: monthKeys)
            else { throw NativeJourneyReportPolicyError.invalidReport }
            previousMonth = month
            months.append(parsed)
        }

        let rebuilt: [String: Any] = [
            "source": "manual",
            "coverage": "unknown",
            "period": [
                "started_at": startedText,
                "ended_at": endedText,
                "days": days,
                "timezone": "UTC"
            ],
            "totals": totals,
            "months": months
        ]
        guard let sanitized = try? JSONSerialization.data(withJSONObject: rebuilt, options: [.sortedKeys]),
              sanitized.count <= maximumBodyBytes
        else { throw NativeJourneyReportPolicyError.invalidReport }
        return sanitized
    }

    private static func parseTotals(_ raw: Any?, allowedKeys: Set<String>) -> [String: Any]? {
        guard let object = raw as? [String: Any], keys(of: object) == allowedKeys,
              let count = boundedCount(object["count"]),
              let knownDistance = boundedCount(object["known_distance_count"]), knownDistance <= count,
              let knownEnergy = boundedCount(object["known_energy_count"]), knownEnergy <= count,
              let paired = boundedCount(object["paired_count"]), paired <= knownDistance, paired <= knownEnergy,
              let distance = nonnegativeFinite(object["distance_km"]),
              let energy = nonnegativeFinite(object["energy_kwh"]),
              let pairedDistance = nonnegativeFinite(object["paired_distance_km"]),
              let pairedEnergy = nonnegativeFinite(object["paired_energy_kwh"]),
              let rawConsumption = object["consumption_kwh_100km"]
        else { return nil }

        let consumption: Any
        if rawConsumption is NSNull {
            consumption = NSNull()
        } else if let number = nonnegativeFinite(rawConsumption) {
            consumption = number
        } else {
            return nil
        }

        var result: [String: Any] = [
            "count": count,
            "distance_km": distance,
            "energy_kwh": energy,
            "known_distance_count": knownDistance,
            "known_energy_count": knownEnergy,
            "paired_count": paired,
            "paired_distance_km": pairedDistance,
            "paired_energy_kwh": pairedEnergy,
            "consumption_kwh_100km": consumption
        ]
        if allowedKeys.contains("month_key"), let month = object["month_key"] as? String {
            result["month_key"] = month
        }
        return result
    }

    private static func nonnegativeFinite(_ raw: Any?) -> Double? {
        guard let raw, let number = raw as? NSNumber,
              CFGetTypeID(number) != CFBooleanGetTypeID() else { return nil }
        let value = number.doubleValue
        return value.isFinite && value >= 0 ? value : nil
    }

    private static func boundedCount(_ raw: Any?) -> Int? {
        guard let raw, let number = raw as? NSNumber,
              CFGetTypeID(number) != CFBooleanGetTypeID(),
              isIntegerNumber(number),
              number.doubleValue.isFinite,
              (0...100_000).contains(number.doubleValue)
        else { return nil }
        return number.intValue
    }

    private static func exactInteger(_ raw: Any?) -> Int? {
        guard let raw, let number = raw as? NSNumber,
              CFGetTypeID(number) != CFBooleanGetTypeID(),
              isIntegerNumber(number),
              number.doubleValue.isFinite
        else { return nil }
        return Int(number.stringValue)
    }

    private static func isIntegerNumber(_ number: NSNumber) -> Bool {
        !["f", "d"].contains(String(cString: number.objCType))
    }

    private static func utcDate(_ value: String) -> Date? {
        guard value.range(
            of: "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]{1,6})?Z$",
            options: .regularExpression
        ) != nil else { return nil }

        let parts = value.prefix(19).split { $0 == "-" || $0 == "T" || $0 == ":" }
        let numbers = parts.compactMap { Int($0) }
        guard numbers.count == 6 else { return nil }
        let year = numbers[0], month = numbers[1], day = numbers[2]
        let hour = numbers[3], minute = numbers[4], second = numbers[5]
        guard (1...9999).contains(year), (1...12).contains(month),
              (0...23).contains(hour), (0...59).contains(minute), (0...59).contains(second)
        else { return nil }
        let leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0)
        let monthLengths = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
        guard (1...monthLengths[month - 1]).contains(day) else { return nil }
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(secondsFromGMT: 0)!
        guard let date = calendar.date(from: DateComponents(
            year: year, month: month, day: day, hour: hour, minute: minute, second: second
        )) else { return nil }
        let suffix = value.dropFirst(19).dropLast()
        let fraction = suffix.isEmpty ? 0 : Double("0" + suffix)!
        return date.addingTimeInterval(fraction)
    }

    private static func keys(of object: [String: Any]) -> Set<String> {
        Set(object.keys)
    }
}

/// Stores export copies below one app-owned cache directory and removes only UUID children it created.
public final class NativeJourneyReportTemporaryStore {
    private let fileManager: FileManager
    private let rootURL: URL?

    public convenience init() {
        self.init(fileManager: .default)
    }

    public init(rootURL: URL, fileManager: FileManager = .default) {
        self.fileManager = fileManager
        self.rootURL = rootURL.standardizedFileURL
    }

    private init(fileManager: FileManager) {
        self.fileManager = fileManager
        self.rootURL = fileManager.urls(for: .cachesDirectory, in: .userDomainMask).first?
            .appendingPathComponent("PulseNativeReportExports", isDirectory: true).standardizedFileURL
    }

    public func createExport(data: Data, days: Int) throws -> URL {
        guard [30, 90, 365].contains(days), let rootURL else {
            throw NativeJourneyReportTemporaryStoreError.unavailable
        }
        #if os(iOS)
        var directory: URL?
        var createdDirectory = false
        do {
            try fileManager.createDirectory(at: rootURL, withIntermediateDirectories: true,
                                            attributes: [.protectionKey: FileProtectionType.complete])
            try protectAndExclude(rootURL)

            let exportDirectory = rootURL.appendingPathComponent(UUID().uuidString, isDirectory: true)
            directory = exportDirectory
            try fileManager.createDirectory(at: exportDirectory, withIntermediateDirectories: false,
                                            attributes: [.protectionKey: FileProtectionType.complete])
            createdDirectory = true
            try protectAndExclude(exportDirectory)

            let file = exportDirectory.appendingPathComponent("pulse-viajes-\(days)-dias.json", isDirectory: false)
            try data.write(to: file, options: [.atomic, .completeFileProtection])
            try protectAndExclude(file)
            return file
        } catch {
            if createdDirectory, let directory {
                try? fileManager.removeItem(at: directory)
            }
            throw NativeJourneyReportTemporaryStoreError.unavailable
        }
        #else
        // The protected export handoff is an iOS capability; never create an
        // unprotected substitute on platforms where those guarantees do not apply.
        throw NativeJourneyReportTemporaryStoreError.unavailable
        #endif
    }

    public func removeExport(at fileURL: URL) throws {
        guard let directory = ownedDirectory(for: fileURL) else {
            throw NativeJourneyReportTemporaryStoreError.invalidLocation
        }
        do {
            try fileManager.removeItem(at: directory)
        } catch {
            throw NativeJourneyReportTemporaryStoreError.unavailable
        }
    }

    public func removeStaleExports() throws {
        guard let rootURL, fileManager.fileExists(atPath: rootURL.path) else { return }
        let children: [URL]
        do {
            children = try fileManager.contentsOfDirectory(
                at: rootURL,
                includingPropertiesForKeys: [.isDirectoryKey, .isSymbolicLinkKey],
                options: [.skipsHiddenFiles]
            )
        } catch {
            throw NativeJourneyReportTemporaryStoreError.unavailable
        }

        for child in children {
            guard isUUIDDirectory(child),
                  let values = try? child.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey]),
                  values.isDirectory == true, values.isSymbolicLink != true
            else { continue }
            do {
                try fileManager.removeItem(at: child)
            } catch {
                throw NativeJourneyReportTemporaryStoreError.unavailable
            }
        }
    }

    private func ownedDirectory(for fileURL: URL) -> URL? {
        guard let rootURL else { return nil }
        let file = fileURL.standardizedFileURL
        let directory = file.deletingLastPathComponent()
        guard directory.deletingLastPathComponent().standardizedFileURL == rootURL,
              isUUIDDirectory(directory),
              ["pulse-viajes-30-dias.json", "pulse-viajes-90-dias.json", "pulse-viajes-365-dias.json"]
                .contains(file.lastPathComponent)
        else { return nil }
        return directory
    }

    private func isUUIDDirectory(_ url: URL) -> Bool {
        guard let uuid = UUID(uuidString: url.lastPathComponent) else { return false }
        return uuid.uuidString == url.lastPathComponent.uppercased()
    }

    #if os(iOS)
    private func protectAndExclude(_ url: URL) throws {
        try fileManager.setAttributes([.protectionKey: FileProtectionType.complete], ofItemAtPath: url.path)
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        var mutableURL = url
        try mutableURL.setResourceValues(values)
    }
    #endif
}

public enum NativeJourneyReportTemporaryStoreError: Error, Equatable {
    case unavailable
    case invalidLocation
}

private struct JourneyJSONKeyScanner {
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
        guard depth <= 32 else { return false }
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
