package com.lucas.pulse.nativebridge;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.util.Locale;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;

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

    @Test public void requestCancellationIsRestrictedToReadsAndExactNavigationQueries() {
        assertTrue(NativeApiPolicy.isCancellableRead("/api/state", "GET"));
        assertTrue(NativeApiPolicy.isCancellableRead("/api/charges?limit=50", "GET"));
        for (String path : new String[]{"/api/navigation/places", "/api/navigation/chargers", "/api/navigation/tiles"}) {
            assertTrue(NativeApiPolicy.isCancellableRead(path, "POST"));
            assertFalse(NativeApiPolicy.isCancellableRead(path + "?x=1", "POST"));
        }
        for (String path : new String[]{"/api/charges", "/api/commands", "/api/charges/requests"}) {
            assertTrue(NativeApiPolicy.allows(path, "POST"));
            assertFalse(NativeApiPolicy.isCancellableRead(path, "POST"));
        }
        assertFalse(NativeApiPolicy.isCancellableRead("/api/navigation/favorites", "POST"));
        assertFalse(NativeApiPolicy.isCancellableRead("/api/state", "PUT"));
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
        for (String value : new String[]{ID.substring(1), ID.toUpperCase(), "%2530" + ID.substring(1), "../" + ID}) {
            assertFalse(NativeApiPolicy.allows("/api/charger/pricing-history?session_id=" + value, "GET"));
        }
        assertTrue(NativeApiPolicy.allows("/api/charger/pricing-history?session_id=%30" + ID.substring(1), "GET"));
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

    @Test public void manualJourneysHaveExactReadAndMutationRoutes() {
        assertTrue(NativeApiPolicy.allows("/api/journeys", "GET"));
        assertTrue(NativeApiPolicy.allows("/api/journeys?limit=100&offset=0", "GET"));
        assertTrue(NativeApiPolicy.allows("/api/journeys?limit=1&offset=1000000", "GET"));
        assertTrue(NativeApiPolicy.allows("/api/journeys/report", "GET"));
        for (String days : new String[]{"30", "90", "365"}) {
            assertTrue(NativeApiPolicy.allows("/api/journeys/report?days=" + days, "GET"));
        }
        assertTrue(NativeApiPolicy.allows("/api/journeys/requests", "POST"));
        assertTrue(NativeApiPolicy.allows("/api/journeys", "POST"));
        assertTrue(NativeApiPolicy.allows("/api/journeys/" + ID, "PUT"));
        assertTrue(NativeApiPolicy.allows("/api/journeys/" + ID, "DELETE"));

        for (String path : new String[]{"/api/journeys?limit=0", "/api/journeys?limit=101",
                "/api/journeys?offset=-1", "/api/journeys?offset=1000001",
                "/api/journeys?limit=1.0", "/api/journeys?limit=1&limit=2",
                "/api/journeys?limit=1&other=1", "/api/journeys?offset=+1",
                "/api/journeys/report?days=7", "/api/journeys/report?days=30&days=90",
                "/api/journeys/report?days=30&extra=1", "/api/journeys/report?DAYS=30",
                "/API/journeys", "/api/journeys/" + ID + "?x=1",
                "/api/journeys/" + ID.toUpperCase(Locale.ROOT), "/api/journeys/../" + ID,
                "/api/journeys/0123456789abcdef0123456789abcdeg",
                "/api/journeys/%2e%2e/" + ID, "/api/journeys/" + ID + "/extra"}) {
            assertFalse(path, NativeApiPolicy.allows(path, path.contains("/report")
                    || path.equals("/api/journeys") || path.startsWith("/api/journeys?") ? "GET" : "PUT"));
        }
        for (String method : new String[]{"GET", "PUT", "DELETE", "PATCH"}) {
            assertFalse(NativeApiPolicy.allows("/api/journeys/requests", method));
        }
        for (String method : new String[]{"PUT", "DELETE", "PATCH"}) {
            assertFalse(NativeApiPolicy.allows("/api/journeys", method));
        }
        assertFalse(NativeApiPolicy.allows("/api/journeys/requests?x=1", "POST"));
        assertFalse(NativeApiPolicy.allows("/api/journeys?x=1", "POST"));
        assertFalse(NativeApiPolicy.allows("/api/journeys/" + ID + "?x=1", "DELETE"));
        assertFalse(NativeApiPolicy.allows("/api/journeys/" + ID, "GET"));
        assertFalse(NativeApiPolicy.allows("/api/journeys/" + ID, "POST"));
        assertFalse(NativeApiPolicy.allows("/api/journeys", "get"));
        assertFalse(NativeApiPolicy.allows("/api/journeys", "post"));
    }

    @Test public void manualJourneyFiltersDecodeOnceAndEnforceUnicodeAndControlBounds() {
        String longSearch = URLEncoder.encode("🚙".repeat(100), StandardCharsets.UTF_8);
        String longTag = URLEncoder.encode("🛠".repeat(32), StandardCharsets.UTF_8);
        assertTrue(NativeApiPolicy.allows("/api/journeys?search=" + longSearch + "&tag=" + longTag
                + "&limit=100&offset=1000000", "GET"));
        assertTrue(NativeApiPolicy.allows("/api/journeys?search=viaje+interurbano&tag=%C3%A9", "GET"));
        assertTrue(NativeApiPolicy.allows("/api/journeys?search=%23ruta", "GET"));
        assertTrue(NativeApiPolicy.allows("/api/journeys?search=%2526literal", "GET"));
        assertTrue(NativeApiPolicy.allows("/api/journeys?search=", "GET"));
        assertTrue(NativeApiPolicy.allows("/api/journeys?%6cimit=%31", "GET"));

        for (String path : new String[]{
                "/api/journeys?search=" + URLEncoder.encode("🚙".repeat(101), StandardCharsets.UTF_8),
                "/api/journeys?tag=" + URLEncoder.encode("🛠".repeat(33), StandardCharsets.UTF_8),
                "/api/journeys?search=%00", "/api/journeys?search=%7f", "/api/journeys?tag=%C2%80",
                "/api/journeys?search=%E2%28%A1", "/api/journeys?search=%ZZ", "/api/journeys?search=%",
                "/api/journeys?limit=1&%6cimit=2", "/api/journeys?%78=1",
                "/api/journeys?offset=2147483648", "/api/journeys?search=bad#fragment",
                "/api/journeys?search=%2526&search=again"}) {
            assertFalse(path, NativeApiPolicy.allows(path, "GET"));
        }
        assertFalse(NativeApiPolicy.allows("/api/journeys?limit=%2531", "GET"));
        assertFalse(NativeApiPolicy.allows("/api/journeys/document", "GET"));
        assertFalse(NativeApiPolicy.allows("/api/journeys/document?format=csv", "GET"));
    }

    @Test public void maintenancePlansHaveExactRoutesAndBoundedReadFilters() {
        String route = "/api/maintenance-plans";
        assertTrue(NativeApiPolicy.allows(route, "GET"));
        assertTrue(NativeApiPolicy.allows(route + "?limit=1&offset=1000000&odometer_km=0", "GET"));
        assertTrue(NativeApiPolicy.allows(route + "?limit=100&odometer_km=2000000", "GET"));
        assertTrue(NativeApiPolicy.allows(route + "?%6fdometer_km=%32%30%30%30%30%30%30", "GET"));
        assertTrue(NativeApiPolicy.allows(route, "POST"));
        assertTrue(NativeApiPolicy.allows(route + "/" + ID, "PUT"));
        assertTrue(NativeApiPolicy.allows(route + "/" + ID, "DELETE"));
        assertTrue(NativeApiPolicy.allows(route + "/" + ID + "/complete", "POST"));

        for (String path : new String[]{route + "?limit=0", route + "?limit=101", route + "?offset=1000001",
                route + "?odometer_km=-1", route + "?odometer_km=2000001", route + "?odometer_km=2147483648",
                route + "?odometer_km=1.5", route + "?odometer_km=1&odometer_km=2",
                route + "?odometer_km=1&%6fdometer_km=2", route + "?refresh=1",
                route + "/" + ID.toUpperCase(Locale.ROOT), route + "/../" + ID,
                route + "/" + ID + "/complete/extra", route + "/%30" + ID.substring(1)}) {
            assertFalse(path, NativeApiPolicy.allows(path, path.equals(route + "/" + ID + "/complete/extra")
                    ? "POST" : path.equals(route + "/../" + ID) || path.contains("?") ? "GET" : "PUT"));
        }
        for (String method : new String[]{"PUT", "DELETE", "PATCH"}) {
            assertFalse(NativeApiPolicy.allows(route, method));
        }
        for (String method : new String[]{"GET", "POST", "PATCH"}) {
            assertFalse(NativeApiPolicy.allows(route + "/" + ID, method));
        }
        for (String method : new String[]{"GET", "PUT", "DELETE", "PATCH"}) {
            assertFalse(NativeApiPolicy.allows(route + "/" + ID + "/complete", method));
        }
        assertFalse(NativeApiPolicy.allows(route + "?limit=1", "POST"));
        assertFalse(NativeApiPolicy.allows(route + "/" + ID + "?x=1", "DELETE"));
    }

    @Test public void navigationFavoritesAndEnergyRoutesHaveExactMethods() {
        String favorites = "/api/navigation/favorites";
        assertTrue(NativeApiPolicy.allows(favorites, "GET"));
        assertTrue(NativeApiPolicy.allows(favorites, "POST"));
        assertTrue(NativeApiPolicy.allows(favorites + "/" + ID, "DELETE"));
        assertFalse(NativeApiPolicy.allows(favorites + "/" + ID, "GET"));
        assertFalse(NativeApiPolicy.allows(favorites + "/" + ID.toUpperCase(Locale.ROOT), "DELETE"));
        for (String path : new String[]{favorites + "?x=1", favorites + "/" + ID + "?x=1"}) {
            for (String method : new String[]{"GET", "POST", "PUT", "DELETE"}) {
                assertFalse(path + " " + method, NativeApiPolicy.allows(path, method));
            }
        }
        for (String method : new String[]{"PUT", "DELETE", "PATCH"}) {
            assertFalse(NativeApiPolicy.allows(favorites, method));
        }

        String comparison = "/api/energy/comparison";
        assertTrue(NativeApiPolicy.allows(comparison, "GET"));
        for (String days : new String[]{"30", "90", "365"}) {
            assertTrue(NativeApiPolicy.allows(comparison + "?days=" + days, "GET"));
        }
        assertTrue(NativeApiPolicy.allows(comparison + "?days=%39%30", "GET"));
        for (String path : new String[]{comparison + "?days=7", comparison + "?days=90&days=30",
                comparison + "?days=%2533%2530", comparison + "?days=30&unknown=1"}) {
            assertFalse(path, NativeApiPolicy.allows(path, "GET"));
        }
        for (String method : new String[]{"POST", "PUT", "DELETE", "PATCH"}) {
            assertFalse(NativeApiPolicy.allows(comparison, method));
        }
        assertTrue(NativeApiPolicy.allows("/api/energy/plan", "POST"));
        for (String method : new String[]{"GET", "PUT", "DELETE", "PATCH"}) {
            assertFalse(NativeApiPolicy.allows("/api/energy/plan", method));
        }
        assertFalse(NativeApiPolicy.allows("/api/energy/plan?days=90", "POST"));
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
