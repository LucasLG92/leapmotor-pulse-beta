import XCTest
@testable import PulseCore

final class NativeAPIPolicyTests: XCTestCase {
    private let id = "0123456789abcdef0123456789abcdef"

    func testNavigationAllowsOnlyExactConsentPostRoutes() {
        XCTAssertTrue(NativeAPIPolicy.allows(path: "/api/navigation/status", method: "GET"))
        XCTAssertFalse(NativeAPIPolicy.allows(path: "/api/navigation/status?", method: "GET"))
        XCTAssertFalse(NativeAPIPolicy.allows(path: "/api/navigation/status", method: "POST"))
        for path in ["/api/navigation/places", "/api/navigation/chargers", "/api/navigation/tiles"] {
            XCTAssertTrue(NativeAPIPolicy.allows(path: path, method: "POST"))
            for method in ["GET", "PUT", "DELETE"] {
                XCTAssertFalse(NativeAPIPolicy.allows(path: path, method: method))
            }
            XCTAssertFalse(NativeAPIPolicy.allows(path: path + "?latitude=1", method: "POST"))
            XCTAssertFalse(NativeAPIPolicy.allows(path: path + "/extra", method: "POST"))
            XCTAssertFalse(NativeRequestPolicy.mutationOutcomeUnknown(
                method: "POST", path: path, responseReceived: false, sessionCurrent: false
            ))
            XCTAssertTrue(NativeRequestPolicy.mutationOutcomeUnknown(
                method: "POST", path: path + "?x=1", responseReceived: false, sessionCurrent: false
            ))
        }
    }

    func testChargerIsReadOnlyAndBounded() {
        XCTAssertTrue(NativeAPIPolicy.allows(path: "/api/charger", method: "GET"))
        XCTAssertTrue(NativeAPIPolicy.allows(path: "/api/charger/sessions?limit=20&offset=1000", method: "GET"))
        for path in ["/api/charger/sessions?offset=1001", "/api/charger?refresh=1",
                     "/api/charger/sessions?limit=20&limit=10"] {
            XCTAssertFalse(NativeAPIPolicy.allows(path: path, method: "GET"))
        }
        XCTAssertFalse(NativeAPIPolicy.allows(path: "/api/charger", method: "POST"))
        XCTAssertFalse(NativeAPIPolicy.allows(path: "/api/charger/sessions", method: "DELETE"))
    }

    func testAllowsExistingUIRoutesAndBoundedQueries() {
        XCTAssertTrue(NativeAPIPolicy.allows(path: "/api/session", method: "GET"))
        XCTAssertTrue(NativeAPIPolicy.allows(path: "/api/state", method: "GET"))
        XCTAssertTrue(NativeAPIPolicy.allows(path: "/api/charges?limit=50&offset=0", method: "GET"))
        XCTAssertTrue(NativeAPIPolicy.allows(path: "/api/telemetry/history?offset=200", method: "GET"))
        XCTAssertTrue(NativeAPIPolicy.allows(path: "/api/analytics/energy?days=90", method: "GET"))
        XCTAssertTrue(NativeAPIPolicy.allows(path: "/api/charges/quote", method: "POST"))
        XCTAssertTrue(NativeAPIPolicy.allows(path: "/api/settings", method: "PUT"))
        XCTAssertTrue(NativeAPIPolicy.allows(path: "/api/widget-profiles/\(id)/pairings", method: "POST"))
        XCTAssertTrue(NativeAPIPolicy.allows(
            path: "/api/widget-profiles/\(id)/native-grants/\(id)", method: "DELETE"
        ))
        XCTAssertTrue(NativeAPIPolicy.allowsEvents(path: "/api/events", method: "GET"))
    }

    func testPricingHistoryRequiresExactSessionAndBoundedReadOnlyPage() {
        let route = "/api/charger/pricing-history?session_id=\(id)"
        XCTAssertTrue(NativeAPIPolicy.allows(path: route, method: "GET"))
        XCTAssertTrue(NativeAPIPolicy.allows(path: route + "&limit=20&offset=2048", method: "GET"))
        for suffix in ["&limit=21", "&limit=0", "&offset=2049", "&offset=-1",
                       "&extra=1", "&session_id=\(id)", "&limit=10&limit=20"] {
            XCTAssertFalse(NativeAPIPolicy.allows(path: route + suffix, method: "GET"))
        }
        for value in [String(id.dropFirst()), id.uppercased(), "%30" + String(id.dropFirst()), "../" + id] {
            XCTAssertFalse(NativeAPIPolicy.allows(path: "/api/charger/pricing-history?session_id=\(value)", method: "GET"))
        }
        XCTAssertFalse(NativeAPIPolicy.allows(path: "/api/charger/pricing-history", method: "GET"))
        XCTAssertFalse(NativeAPIPolicy.allows(path: "/api/charger/sessions?session_id=\(id)", method: "GET"))
        for method in ["POST", "PUT", "DELETE"] {
            XCTAssertFalse(NativeAPIPolicy.allows(path: route, method: method))
        }
    }

    func testAbrpConsentOnlyAllowsExactPutRoute() {
        XCTAssertTrue(NativeAPIPolicy.allows(path: "/api/integrations/abrp", method: "PUT"))
        for method in ["POST", "GET", "DELETE"] {
            XCTAssertFalse(NativeAPIPolicy.allows(path: "/api/integrations/abrp", method: method))
        }
        for path in ["/api/integrations/abrp?enabled=true", "/api/integrations/abrp/extra"] {
            XCTAssertFalse(NativeAPIPolicy.allows(path: path, method: "PUT"))
        }
    }

    func testObservedHistoryIsReadOnlyAndBounded() {
        XCTAssertTrue(NativeAPIPolicy.allows(path: "/api/protection/observations?limit=20&offset=1000", method: "GET"))
        for path in ["/api/protection/observations?limit=101", "/api/protection/observations?offset=1001",
                     "/api/protection/observations?offset=-1", "/api/protection/observations?limit=20&limit=10",
                     "/api/protection/observations?refresh=1", "/api/protection/observations/extra"] {
            XCTAssertFalse(NativeAPIPolicy.allows(path: path, method: "GET"), path)
        }
        XCTAssertFalse(NativeAPIPolicy.allows(path: "/api/protection/observations", method: "POST"))
        XCTAssertFalse(NativeAPIPolicy.allows(path: "/api/protection/observations", method: "DELETE"))
    }

    func testRejectsTraversalAbsoluteOriginsQueriesAndPrivateRoutes() {
        for path in [
            "https://pulse.example.test/api/state",
            "//pulse.example.test/api/state",
            "/api/../api/state",
            "/api/state%2fadmin",
            "/api/state\\admin",
            "/api/charges?limit=5&limit=10",
            "/api/charges?limit=101",
            "/api/charges?limit=5&other=1",
            "/api/state?",
            "/api/analytics/energy?days=7",
            "/api/native/widget-grants",
            "/api/auth/login",
            "/api/commands/demo",
        ] {
            XCTAssertFalse(NativeAPIPolicy.allows(path: path, method: "GET"), path)
        }
        XCTAssertFalse(NativeAPIPolicy.allows(path: "/api/state", method: "get"))
        XCTAssertFalse(NativeAPIPolicy.allowsEvents(path: "/api/events?cursor=secret", method: "GET"))
    }
}

final class NativeRequestPolicyTests: XCTestCase {
    func testTokenFormPreservesPlusAndReservedCharacters() throws {
        let encoded = try XCTUnwrap(NativeRequestPolicy.formEncoded([
            ("code", "A+B /?&=ñ"), ("client_id", "Pulse+mobile"), ("empty", "")
        ]))
        XCTAssertEqual(String(decoding: encoded, as: UTF8.self),
                       "code=A%2BB%20%2F%3F%26%3D%C3%B1&client_id=Pulse%2Bmobile&empty=")
    }

    func testUnknownMutationOutcomeRequiresAnUncertainMutation() {
        XCTAssertEqual(NativeRequestPolicy.csrfMarker, "native-session")
        XCTAssertTrue(NativeRequestPolicy.mutationOutcomeUnknown(
            method: "POST", responseReceived: false, sessionCurrent: true
        ))
        XCTAssertTrue(NativeRequestPolicy.mutationOutcomeUnknown(
            method: "DELETE", responseReceived: true, sessionCurrent: false
        ))
        XCTAssertFalse(NativeRequestPolicy.mutationOutcomeUnknown(
            method: "GET", responseReceived: false, sessionCurrent: false
        ))
        XCTAssertFalse(NativeRequestPolicy.mutationOutcomeUnknown(
            method: "PUT", responseReceived: true, sessionCurrent: true
        ))
        XCTAssertFalse(NativeRequestPolicy.mutationOutcomeUnknown(
            method: "post", responseReceived: false, sessionCurrent: true
        ))
    }
}
