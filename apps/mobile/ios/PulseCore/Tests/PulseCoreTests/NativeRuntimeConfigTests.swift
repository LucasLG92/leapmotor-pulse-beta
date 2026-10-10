import XCTest
@testable import PulseCore

final class NativeRuntimeConfigTests: XCTestCase {
    private let api = "https://pulse.example.test"
    private let issuer = "https://id.example.test/tenant"
    private let authorization = "https://id.example.test/tenant/oauth2/authorize"
    private let token = "https://id.example.test/tenant/oauth2/token"
    private let clientID = "pulse-mobile-public"
    private let audience = "https://pulse.example.test/native"

    func testAcceptsOnlyConfiguredHTTPSEndpointsAndExactCallback() throws {
        let config = try XCTUnwrap(makeConfig())

        XCTAssertEqual(config.apiOrigin, URL(string: api))
        XCTAssertEqual(config.issuer, URL(string: issuer))
        XCTAssertEqual(config.authorizationEndpoint, URL(string: authorization))
        XCTAssertEqual(config.tokenEndpoint, URL(string: token))
        XCTAssertEqual(config.callback, URL(string: api + "/native/oauth/callback"))
        XCTAssertEqual(config.clientID, clientID)
        XCTAssertEqual(config.audience, audience)
        XCTAssertTrue(config.isOAuthEndpoint(authorization))
        XCTAssertTrue(config.isOAuthEndpoint(token))
        XCTAssertFalse(config.isOAuthEndpoint(token + "/redirect"))
    }

    func testRejectsInsecureOrCredentialBearingURLs() {
        XCTAssertNil(makeConfig(apiOrigin: "http://pulse.example.test"))
        XCTAssertNil(makeConfig(issuer: "https://user@id.example.test/tenant"))
        XCTAssertNil(makeConfig(tokenEndpoint: "https://id.example.test:0/tenant/oauth2/token"))
        XCTAssertNil(makeConfig(authorizationEndpoint: "https://id.example.test/tenant/oauth2/authorize?next=https://elsewhere.test"))
        XCTAssertNil(makeConfig(tokenEndpoint: "https://id.example.test/tenant/%2e%2e/token"))
    }

    func testRejectsOriginConfusionAndEndpointsOutsideIssuer() {
        XCTAssertNil(makeConfig(callback: "https://pulse.example.test.attacker.test/native/oauth/callback"))
        XCTAssertNil(makeConfig(callback: "https://pulse.example.test/native/oauth/callback/extra"))
        XCTAssertNil(makeConfig(authorizationEndpoint: "https://attacker.test/tenant/oauth2/authorize"))
        XCTAssertNil(makeConfig(authorizationEndpoint: "https://id.example.test/other/authorize"))
        XCTAssertNil(makeConfig(tokenEndpoint: "https://id.example.test:444/tenant/oauth2/token"))
        XCTAssertNil(makeConfig(apiOrigin: api + "/nested"))
    }

    func testRejectsMalformedPublicValuesAndTraversal() {
        XCTAssertNil(makeConfig(clientID: "client with spaces"))
        XCTAssertNil(makeConfig(audience: ""))
        XCTAssertNil(makeConfig(issuer: "https://id.example.test/tenant/../other"))
        XCTAssertNil(makeConfig(authorizationEndpoint: "https://id.example.test/tenantish/authorize"))
    }

    private func makeConfig(
        apiOrigin: String? = nil,
        issuer: String? = nil,
        authorizationEndpoint: String? = nil,
        tokenEndpoint: String? = nil,
        clientID: String? = nil,
        audience: String? = nil,
        callback: String? = nil
    ) -> NativeRuntimeConfig? {
        NativeRuntimeConfig(
            apiOrigin: apiOrigin ?? api,
            issuer: issuer ?? self.issuer,
            authorizationEndpoint: authorizationEndpoint ?? authorization,
            tokenEndpoint: tokenEndpoint ?? token,
            clientID: clientID ?? self.clientID,
            audience: audience ?? self.audience,
            callback: callback ?? api + "/native/oauth/callback"
        )
    }
}
