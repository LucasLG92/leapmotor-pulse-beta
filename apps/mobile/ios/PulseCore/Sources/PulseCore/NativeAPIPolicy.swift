import Foundation

/// Exact allowlist for native UI routes; OAuth and native bearer-grant routes stay excluded.
public enum NativeAPIPolicy {
    private static let pageQueries: Set<String> = ["limit", "offset"]

    public static func allows(path rawPath: String, method: String) -> Bool {
        guard rawPath.utf16.count <= 2_048,
              method == method.uppercased(),
              !rawPath.contains("#"), !rawPath.contains("\\"),
              !rawPath.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) })
        else { return false }

        let path: String
        let query: String
        if let marker = rawPath.firstIndex(of: "?") {
            path = String(rawPath[..<marker])
            query = String(rawPath[rawPath.index(after: marker)...])
            guard !query.isEmpty else { return false }
        } else {
            path = rawPath
            query = ""
        }
        guard path.hasPrefix("/api/"), !path.hasPrefix("//"), !path.contains("%"), !path.contains(":"),
              !path.isEmpty, !path.hasSuffix("/"), !path.contains("//"),
              let parameters = parseQuery(query)
        else { return false }

        if method == "GET" {
            if ["/api/session", "/api/state", "/api/telemetry", "/api/commands/history",
                "/api/widget-profiles", "/api/native-capabilities", "/api/export", "/api/charger", "/api/navigation/status"].contains(path) {
                return parameters.isEmpty
            }
            if ["/api/charges", "/api/telemetry/history"].contains(path) {
                return boundedPageQuery(parameters)
            }
            if path == "/api/journeys" {
                return journeysQuery(parameters)
            }
            if path == "/api/maintenance-plans" {
                return maintenancePlansQuery(parameters)
            }
            if path == "/api/navigation/favorites" {
                return parameters.isEmpty
            }
            if ["/api/charger/sessions", "/api/protection/observations"].contains(path) {
                guard boundedPageQuery(parameters) else { return false }
                return parameters["offset"].flatMap(Int.init).map { $0 <= 1000 } ?? true
            }
            if path == "/api/analytics/energy" {
                guard !parameters.isEmpty else { return true }
                guard parameters.count == 1, let days = parameters["days"], isDecimal(days) else { return false }
                return ["30", "90", "365"].contains(days)
            }
            if path == "/api/journeys/report" {
                guard !parameters.isEmpty else { return true }
                guard parameters.count == 1, let days = parameters["days"], isDecimal(days) else { return false }
                return ["30", "90", "365"].contains(days)
            }
            if path == "/api/energy/comparison" {
                return optionalDaysQuery(parameters)
            }
            if path == "/api/charger/pricing-history" {
                guard let session = parameters["session_id"], isID(session) else { return false }
                var page = parameters
                page.removeValue(forKey: "session_id")
                guard boundedPageQuery(page) else { return false }
                return (page["limit"].flatMap(Int.init).map { $0 <= 20 } ?? true)
                    && (page["offset"].flatMap(Int.init).map { $0 <= 2048 } ?? true)
            }
            if isProfileIDRoute(path, suffix: "/native-grants") { return parameters.isEmpty }
        }

        if method == "POST" {
            if ["/api/charges/quote", "/api/charges/requests", "/api/charges", "/api/journeys/requests", "/api/journeys", "/api/demo", "/api/commands",
                "/api/protection/event", "/api/maintenance", "/api/import", "/api/widget-profiles",
                "/api/auth/logout", "/api/navigation/places", "/api/navigation/chargers", "/api/navigation/tiles",
                "/api/maintenance-plans", "/api/navigation/favorites", "/api/energy/plan"].contains(path) {
                return parameters.isEmpty
            }
            if path.range(of: #"^/api/maintenance-plans/[a-f0-9]{32}/complete$"#, options: .regularExpression) != nil {
                return parameters.isEmpty
            }
            if isProfileIDRoute(path, suffix: "/pairings") { return parameters.isEmpty }
        }

        if method == "PUT" {
            if ["/api/settings", "/api/protection", "/api/integrations/abrp"].contains(path) { return parameters.isEmpty }
            if isSingleIDRoute(path, prefix: "/api/charges/")
                || isSingleIDRoute(path, prefix: "/api/journeys/")
                || isSingleIDRoute(path, prefix: "/api/widget-profiles/") {
                return parameters.isEmpty
            }
            if isSingleIDRoute(path, prefix: "/api/maintenance/") { return parameters.isEmpty }
            if isSingleIDRoute(path, prefix: "/api/maintenance-plans/") { return parameters.isEmpty }
        }

        if method == "DELETE" {
            if isSingleIDRoute(path, prefix: "/api/charges/")
                || isSingleIDRoute(path, prefix: "/api/journeys/")
                || isSingleIDRoute(path, prefix: "/api/maintenance/")
                || isSingleIDRoute(path, prefix: "/api/maintenance-plans/")
                || isSingleIDRoute(path, prefix: "/api/widget-profiles/") {
                return parameters.isEmpty
            }
            if isSingleIDRoute(path, prefix: "/api/navigation/favorites/") { return parameters.isEmpty }
            if isWidgetGrantRoute(path) { return parameters.isEmpty }
        }

        return false
    }

    public static func allowsEvents(path: String, method: String) -> Bool {
        method == "GET" && path == "/api/events"
    }

    private static func boundedPageQuery(_ parameters: [String: String]) -> Bool {
        guard pageQueries.isSuperset(of: parameters.keys) else { return false }
        if let limit = parameters["limit"] {
            guard isDecimal(limit), let value = Int(limit), (1...100).contains(value) else { return false }
        }
        if let offset = parameters["offset"] {
            guard isDecimal(offset), let value = Int(offset), (0...1_000_000).contains(value) else { return false }
        }
        return true
    }

    private static func journeysQuery(_ parameters: [String: String]) -> Bool {
        guard Set(["limit", "offset", "search", "tag"]).isSuperset(of: parameters.keys) else { return false }
        var page = parameters
        let search = page.removeValue(forKey: "search")
        let tag = page.removeValue(forKey: "tag")
        return boundedPageQuery(page) && validOptionalText(search, maximumScalars: 100)
            && validOptionalText(tag, maximumScalars: 32)
    }

    private static func maintenancePlansQuery(_ parameters: [String: String]) -> Bool {
        guard Set(["limit", "offset", "odometer_km"]).isSuperset(of: parameters.keys) else { return false }
        var page = parameters
        let odometer = page.removeValue(forKey: "odometer_km")
        guard boundedPageQuery(page) else { return false }
        guard let odometer else { return true }
        guard isDecimal(odometer), let value = Int(odometer) else { return false }
        return (0...2_000_000).contains(value)
    }

    private static func optionalDaysQuery(_ parameters: [String: String]) -> Bool {
        guard !parameters.isEmpty else { return true }
        guard parameters.count == 1, let days = parameters["days"], isDecimal(days) else { return false }
        return ["30", "90", "365"].contains(days)
    }

    private static func validOptionalText(_ value: String?, maximumScalars: Int) -> Bool {
        guard let value else { return true }
        return value.unicodeScalars.count <= maximumScalars
            && !value.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) })
    }

    private static func parseQuery(_ query: String) -> [String: String]? {
        guard !query.isEmpty else { return [:] }
        guard query.utf16.count <= 2_048, !query.hasPrefix("&"), !query.hasSuffix("&") else { return nil }
        var result: [String: String] = [:]
        for pair in query.split(separator: "&", omittingEmptySubsequences: false) {
            guard let equals = pair.firstIndex(of: "="),
                  equals != pair.startIndex,
                  pair[pair.index(after: equals)...].firstIndex(of: "=") == nil
            else { return nil }
            let rawKey = String(pair[..<equals])
            let rawValue = String(pair[pair.index(after: equals)...])
            guard let key = decodeQueryComponent(rawKey), let value = decodeQueryComponent(rawValue),
                  !key.isEmpty, result[key] == nil else { return nil }
            result[key] = value
        }
        return result
    }

    /// Decodes one query component once; literal '+' follows form-query semantics.
    private static func decodeQueryComponent(_ raw: String) -> String? {
        raw.replacingOccurrences(of: "+", with: " ").removingPercentEncoding
    }

    private static func isDecimal(_ value: String) -> Bool {
        !value.isEmpty && value.utf8.count <= 9 && value.utf8.allSatisfy { (48...57).contains($0) }
    }

    private static func isProfileIDRoute(_ path: String, suffix: String) -> Bool {
        guard path.hasPrefix("/api/widget-profiles/"), path.hasSuffix(suffix) else { return false }
        let start = path.index(path.startIndex, offsetBy: "/api/widget-profiles/".count)
        let end = path.index(path.endIndex, offsetBy: -suffix.count)
        guard start <= end else { return false }
        return isID(String(path[start..<end]))
    }

    private static func isSingleIDRoute(_ path: String, prefix: String) -> Bool {
        guard path.hasPrefix(prefix) else { return false }
        return isID(String(path.dropFirst(prefix.count)))
    }

    private static func isWidgetGrantRoute(_ path: String) -> Bool {
        let prefix = "/api/widget-profiles/"
        let infix = "/native-grants/"
        guard path.hasPrefix(prefix) else { return false }
        let remainder = path.dropFirst(prefix.count)
        guard let separator = remainder.range(of: infix) else { return false }
        return isID(String(remainder[..<separator.lowerBound]))
            && isID(String(remainder[separator.upperBound...]))
    }

    private static func isID(_ value: String) -> Bool {
        value.utf8.count == 32 && value.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) }
    }
}
