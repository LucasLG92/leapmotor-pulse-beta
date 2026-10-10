import Foundation

/// Validated public endpoints for the native OAuth client.
public struct NativeRuntimeConfig: Sendable {
    public let apiOrigin: URL
    public let issuer: URL
    public let authorizationEndpoint: URL
    public let tokenEndpoint: URL
    public let callback: URL
    public let clientID: String
    public let audience: String

    public init?(
        apiOrigin: String,
        issuer: String,
        authorizationEndpoint: String,
        tokenEndpoint: String,
        clientID: String,
        audience: String,
        callback: String
    ) {
        guard Self.isShortPublicValue(clientID), Self.isShortPublicValue(audience),
              let api = Self.strictHTTPS(apiOrigin, originOnly: true),
              let issuerURL = Self.strictHTTPS(issuer, originOnly: false),
              let authorizationURL = Self.strictHTTPS(authorizationEndpoint, originOnly: false),
              let tokenURL = Self.strictHTTPS(tokenEndpoint, originOnly: false),
              let callbackURL = Self.strictHTTPS(callback, originOnly: false),
              callback == apiOrigin + "/native/oauth/callback",
              Self.sameAuthority(issuerURL, authorizationURL),
              Self.sameAuthority(issuerURL, tokenURL),
              Self.isUnderIssuerPath(authorizationURL, issuerURL),
              Self.isUnderIssuerPath(tokenURL, issuerURL)
        else { return nil }

        self.apiOrigin = api
        self.issuer = issuerURL
        self.authorizationEndpoint = authorizationURL
        self.tokenEndpoint = tokenURL
        self.callback = callbackURL
        self.clientID = clientID
        self.audience = audience
    }

    /// Matches only the two configured OAuth endpoints after the same strict URL checks.
    public func isOAuthEndpoint(_ value: String) -> Bool {
        guard let candidate = Self.strictHTTPS(value, originOnly: false) else { return false }
        return Self.sameURI(candidate, authorizationEndpoint) || Self.sameURI(candidate, tokenEndpoint)
    }

    private static let maximumPublicValueLength = 512

    private static func isShortPublicValue(_ value: String) -> Bool {
        !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && value.utf16.count <= maximumPublicValueLength
            && value.unicodeScalars.allSatisfy { !CharacterSet.whitespacesAndNewlines.contains($0)
                && !CharacterSet.controlCharacters.contains($0) }
    }

    private static func strictHTTPS(_ raw: String, originOnly: Bool) -> URL? {
        guard !raw.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              raw.utf16.count <= maximumPublicValueLength,
              !raw.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }),
              !raw.contains("\\"),
              let components = URLComponents(string: raw),
              components.scheme?.caseInsensitiveCompare("https") == .orderedSame,
              let host = components.host, !host.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              host.utf8.allSatisfy({ $0 < 128 }),
              !host.unicodeScalars.contains(where: { CharacterSet.whitespacesAndNewlines.contains($0)
                  || CharacterSet.controlCharacters.contains($0) }),
              components.user == nil, components.password == nil,
              components.percentEncodedQuery == nil, components.percentEncodedFragment == nil,
              let url = components.url,
              let authority = Self.rawAuthority(in: raw),
              !authority.contains("%"),
              Self.validPort(in: authority),
              components.port != 0,
              (components.port == nil || (1...65_535).contains(components.port!))
        else { return nil }

        let path = components.percentEncodedPath
        guard !path.contains("%"), !path.contains("//"),
              !path.split(separator: "/", omittingEmptySubsequences: false).contains(where: { $0 == "." || $0 == ".." })
        else { return nil }
        if originOnly && !path.isEmpty { return nil }
        return url
    }

    private static func rawAuthority(in value: String) -> Substring? {
        guard let separator = value.range(of: "://") else { return nil }
        let remainder = value[separator.upperBound...]
        let end = remainder.firstIndex(where: { $0 == "/" || $0 == "?" || $0 == "#" }) ?? remainder.endIndex
        let authority = remainder[..<end]
        return authority.isEmpty ? nil : authority
    }

    private static func validPort(in authority: Substring) -> Bool {
        let hostPort: Substring
        if authority.first == "[" {
            guard let closingBracket = authority.firstIndex(of: "]") else { return false }
            let suffix = authority[authority.index(after: closingBracket)...]
            if suffix.isEmpty { return true }
            guard suffix.first == ":" else { return false }
            hostPort = suffix.dropFirst()
        } else {
            let colons = authority.indices.filter { authority[$0] == ":" }
            guard colons.count <= 1 else { return false }
            guard let colon = colons.first else { return true }
            hostPort = authority[authority.index(after: colon)...]
        }
        guard !hostPort.isEmpty, hostPort.utf8.allSatisfy({ (48...57).contains($0) }),
              let port = Int(hostPort), port > 0, port <= 65_535
        else { return false }
        return true
    }

    private static func sameAuthority(_ left: URL, _ right: URL) -> Bool {
        guard let leftComponents = URLComponents(url: left, resolvingAgainstBaseURL: false),
              let rightComponents = URLComponents(url: right, resolvingAgainstBaseURL: false),
              let leftHost = leftComponents.host, let rightHost = rightComponents.host
        else { return false }
        return leftComponents.scheme?.lowercased() == "https"
            && rightComponents.scheme?.lowercased() == "https"
            && leftHost.lowercased() == rightHost.lowercased()
            && (leftComponents.port ?? 443) == (rightComponents.port ?? 443)
    }

    private static func isUnderIssuerPath(_ endpoint: URL, _ issuer: URL) -> Bool {
        guard let endpointPath = URLComponents(url: endpoint, resolvingAgainstBaseURL: false)?.percentEncodedPath,
              let issuerPath = URLComponents(url: issuer, resolvingAgainstBaseURL: false)?.percentEncodedPath
        else { return false }
        let normalizedIssuer = issuerPath == "/" ? "" : issuerPath.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        let normalizedEndpoint = endpointPath.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard !normalizedEndpoint.isEmpty, normalizedEndpoint != normalizedIssuer else { return false }
        let prefix = normalizedIssuer.isEmpty ? "" : normalizedIssuer + "/"
        return normalizedEndpoint.hasPrefix(prefix)
    }

    private static func sameURI(_ left: URL, _ right: URL) -> Bool {
        guard let leftComponents = URLComponents(url: left, resolvingAgainstBaseURL: false),
              let rightComponents = URLComponents(url: right, resolvingAgainstBaseURL: false)
        else { return false }
        return leftComponents.scheme?.lowercased() == rightComponents.scheme?.lowercased()
            && leftComponents.host?.lowercased() == rightComponents.host?.lowercased()
            && leftComponents.port == rightComponents.port
            && leftComponents.percentEncodedPath == rightComponents.percentEncodedPath
    }
}
