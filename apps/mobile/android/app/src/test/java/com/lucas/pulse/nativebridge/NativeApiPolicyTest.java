package com.lucas.pulse.nativebridge;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class NativeApiPolicyTest {
    private static final String ID = "0123456789abcdef0123456789abcdef";

    @Test public void navigationHasOnlyExactConsentPostRoutes() {
        assertTrue(NativeApiPolicy.allows("/api/navigation/status", "GET"));
        assertFalse(NativeApiPolicy.allows("/api/navigation/status", "POST"));
        for (String path : new String[]{"/api/navigation/places", "/api/navigation/chargers", "/api/navigation/tiles"}) {
            assertTrue(NativeApiPolicy.allows(path, "POST"));
            assertFalse(NativeApiPolicy.allows(path, "GET"));
            assertFalse(NativeApiPolicy.allows(path, "PUT"));
            assertFalse(NativeApiPolicy.allows(path + "?latitude=1", "POST"));
            assertFalse(NativeApiPolicy.allows(path + "/extra", "POST"));
        }
        assertFalse(NativeApiPolicy.allows("/api/navigation/status?", "GET"));
    }

    @Test public void chargerIsReadOnlyAndBounded() {
        assertTrue(NativeApiPolicy.allows("/api/charger", "GET"));
        assertTrue(NativeApiPolicy.allows("/api/charger/sessions?limit=20&offset=1000", "GET"));
        assertFalse(NativeApiPolicy.allows("/api/charger/sessions?offset=1001", "GET"));
        assertFalse(NativeApiPolicy.allows("/api/charger?refresh=1", "GET"));
        assertFalse(NativeApiPolicy.allows("/api/charger/sessions?limit=20&limit=10", "GET"));
        assertFalse(NativeApiPolicy.allows("/api/charger", "POST"));
        assertFalse(NativeApiPolicy.allows("/api/charger/sessions", "DELETE"));
    }

    @Test public void allowsUiRoutesAndBoundedQueryParameters() {
        assertTrue(NativeApiPolicy.allows("/api/session", "GET"));
        assertTrue(NativeApiPolicy.allows("/api/state", "GET"));
        assertTrue(NativeApiPolicy.allows("/api/charges?limit=50&offset=0", "GET"));
        assertTrue(NativeApiPolicy.allows("/api/charges?limit=50", "GET"));
        assertTrue(NativeApiPolicy.allows("/api/telemetry/history?offset=200", "GET"));
        assertTrue(NativeApiPolicy.allows("/api/analytics/energy?days=90", "GET"));
        assertTrue(NativeApiPolicy.allows("/api/charges/quote", "POST"));
        assertTrue(NativeApiPolicy.allows("/api/commands", "POST"));
        assertTrue(NativeApiPolicy.allows("/api/demo", "POST"));
        assertTrue(NativeApiPolicy.allows("/api/protection", "PUT"));
        assertTrue(NativeApiPolicy.allows("/api/settings", "PUT"));
        assertTrue(NativeApiPolicy.allows("/api/charges/" + ID, "PUT"));
        assertTrue(NativeApiPolicy.allows("/api/maintenance/" + ID, "DELETE"));
        assertTrue(NativeApiPolicy.allows("/api/settings", "PUT"));
        assertTrue(NativeApiPolicy.allows("/api/widget-profiles/" + ID + "/pairings", "POST"));
        assertTrue(NativeApiPolicy.allows("/api/widget-profiles/" + ID + "/native-grants/" + ID, "DELETE"));
        assertTrue(NativeApiPolicy.allowsEvents("/api/events", "GET"));
    }

    @Test public void chargeRequestReservationAllowsOnlyExactPostRoute() {
        assertTrue(NativeApiPolicy.allows("/api/charges/requests", "POST"));
        for (String method : new String[]{"GET", "PUT", "DELETE"}) {
            assertFalse(NativeApiPolicy.allows("/api/charges/requests", method));
        }
        assertFalse(NativeApiPolicy.allows("/api/charges/requests/extra", "POST"));
        assertFalse(NativeApiPolicy.allows("/api/charges/requests?source=1", "POST"));
    }

    @Test public void pricingHistoryIsPrivateReadOnlyWithAnExactSessionAndBoundedPage() {
        String route = "/api/charger/pricing-history?session_id=" + ID;
        assertTrue(NativeApiPolicy.allows(route, "GET"));
        assertTrue(NativeApiPolicy.allows(route + "&limit=20&offset=2048", "GET"));
        for (String suffix : new String[]{"&limit=21", "&limit=0", "&offset=2049",
                "&offset=-1", "&extra=1", "&session_id=" + ID, "&limit=10&limit=20"}) {
            assertFalse(NativeApiPolicy.allows(route + suffix, "GET"));
        }
        for (String value : new String[]{ID.substring(1), ID.toUpperCase(), "%30" + ID.substring(1), "../" + ID}) {
            assertFalse(NativeApiPolicy.allows("/api/charger/pricing-history?session_id=" + value, "GET"));
        }
        assertFalse(NativeApiPolicy.allows("/api/charger/pricing-history", "GET"));
        assertFalse(NativeApiPolicy.allows("/api/charger/sessions?session_id=" + ID, "GET"));
        for (String method : new String[]{"POST", "PUT", "DELETE"}) {
            assertFalse(NativeApiPolicy.allows(route, method));
        }
    }

    @Test public void abrpConsentOnlyAllowsExactPutRoute() {
        assertTrue(NativeApiPolicy.allows("/api/integrations/abrp", "PUT"));
        assertFalse(NativeApiPolicy.allows("/api/integrations/abrp", "POST"));
        assertFalse(NativeApiPolicy.allows("/api/integrations/abrp", "GET"));
        assertFalse(NativeApiPolicy.allows("/api/integrations/abrp?enabled=true", "PUT"));
        assertFalse(NativeApiPolicy.allows("/api/integrations/abrp/extra", "PUT"));
    }

    @Test public void observedHistoryIsReadOnlyAndBounded() {
        assertTrue(NativeApiPolicy.allows("/api/protection/observations?limit=20&offset=1000", "GET"));
        for (String path : new String[]{"/api/protection/observations?limit=101",
                "/api/protection/observations?offset=1001", "/api/protection/observations?offset=-1",
                "/api/protection/observations?limit=20&limit=10", "/api/protection/observations?refresh=1",
                "/api/protection/observations/extra"}) {
            assertFalse(NativeApiPolicy.allows(path, "GET"));
        }
        assertFalse(NativeApiPolicy.allows("/api/protection/observations", "POST"));
        assertFalse(NativeApiPolicy.allows("/api/protection/observations", "DELETE"));
    }

    @Test public void rejectsRoutesAndInputsOutsideExplicitAllowlist() {
        assertFalse(NativeApiPolicy.allows("https://pulse.example.test/api/state", "GET"));
        assertFalse(NativeApiPolicy.allows("//pulse.example.test/api/state", "GET"));
        assertFalse(NativeApiPolicy.allows("/api/state%2fadmin", "GET"));
        assertFalse(NativeApiPolicy.allows("/api/state\\admin", "GET"));
        assertFalse(NativeApiPolicy.allows("/api/charges?limit=101&offset=0", "GET"));
        assertFalse(NativeApiPolicy.allows("/api/charges?limit=5&limit=10", "GET"));
        assertFalse(NativeApiPolicy.allows("/api/charges?limit=5&other=1", "GET"));
        assertFalse(NativeApiPolicy.allows("/api/state?", "GET"));
        assertFalse(NativeApiPolicy.allows("/api/analytics/energy?days=7", "GET"));
        assertFalse(NativeApiPolicy.allows("/api/charges/not-an-id", "DELETE"));
        assertFalse(NativeApiPolicy.allows("/api/native/app-sessions", "POST"));
        assertFalse(NativeApiPolicy.allows("/api/native/widget-grants", "POST"));
        assertFalse(NativeApiPolicy.allows("/api/auth/login", "POST"));
        assertFalse(NativeApiPolicy.allows("/api/commands/demo", "POST"));
        assertFalse(NativeApiPolicy.allows("/api/settings/protection", "PUT"));
        assertFalse(NativeApiPolicy.allows("/native/oauth/callback", "GET"));
        assertFalse(NativeApiPolicy.allows("/api/state", "PATCH"));
        assertFalse(NativeApiPolicy.allowsEvents("/api/events?cursor=secret", "GET"));
    }
}
