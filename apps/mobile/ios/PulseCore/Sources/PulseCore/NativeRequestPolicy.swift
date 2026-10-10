import Foundation

/// Pure decisions and encoding used at the native transport boundary.
public enum NativeRequestPolicy {
    public static let csrfMarker = "native-session"

    /// A URL query encoder leaves '+' literal; form decoders interpret it as a space.
    public static func formEncoded(_ values: [(String, String)]) -> Data? {
        let unreserved = CharacterSet(charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
        var pairs: [String] = []
        for (key, value) in values {
            guard let encodedKey = key.addingPercentEncoding(withAllowedCharacters: unreserved),
                  let encodedValue = value.addingPercentEncoding(withAllowedCharacters: unreserved) else { return nil }
            pairs.append(encodedKey + "=" + encodedValue)
        }
        return Data(pairs.joined(separator: "&").utf8)
    }

    public static func mutationOutcomeUnknown(
        method: String,
        path: String? = nil,
        responseReceived: Bool,
        sessionCurrent: Bool
    ) -> Bool {
        if method == "POST", let path,
           ["/api/navigation/places", "/api/navigation/chargers", "/api/navigation/tiles"].contains(path) {
            return false
        }
        let isMutation = method == "POST" || method == "PUT" || method == "DELETE"
        return isMutation && (!responseReceived || !sessionCurrent)
    }
}
