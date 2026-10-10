package com.lucas.pulse.nativebridge;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class NativeRequestPolicyTest {
    @Test public void publicNavigationReadsAreRetryableWithoutWeakeningWrites() {
        for (String path : new String[]{"/api/navigation/places", "/api/navigation/chargers", "/api/navigation/tiles"}) {
            assertFalse(NativeRequestPolicy.mutationOutcomeUnknown("POST", path, false, false));
            assertTrue(NativeRequestPolicy.mutationOutcomeUnknown("PUT", path, false, false));
            assertTrue(NativeRequestPolicy.mutationOutcomeUnknown("POST", path + "?x=1", false, false));
        }
        assertTrue(NativeRequestPolicy.mutationOutcomeUnknown("POST", "/api/charges", false, false));
    }

    @Test public void uncertainMutationNeverLooksLikeAConfirmedHttpFailure() {
        assertTrue(NativeRequestPolicy.mutationOutcomeUnknown("POST", "/api/charges", true, false));
        assertTrue(NativeRequestPolicy.mutationOutcomeUnknown("PUT", "/api/charges", false, true));
        assertTrue(NativeRequestPolicy.mutationOutcomeUnknown("DELETE", "/api/charges", false, false));
        assertFalse(NativeRequestPolicy.mutationOutcomeUnknown("GET", "/api/charges", true, false));
        assertFalse(NativeRequestPolicy.mutationOutcomeUnknown("POST", "/api/charges", true, true));
    }

    @Test public void rendererSeesOnlyTheNativeCsrfMarkerForSessionBootstrap() {
        assertEquals("native-session", NativeRequestPolicy.RENDERER_CSRF_MARKER);
        assertFalse(NativeSessionState.validCsrf(NativeRequestPolicy.RENDERER_CSRF_MARKER));
        assertTrue(NativeRequestPolicy.shouldMaskSessionCsrf("GET", "/api/session", 200));
        assertFalse(NativeRequestPolicy.shouldMaskSessionCsrf("GET", "/api/state", 200));
        assertFalse(NativeRequestPolicy.shouldMaskSessionCsrf("POST", "/api/session", 200));
        assertFalse(NativeRequestPolicy.shouldMaskSessionCsrf("GET", "/api/session", 401));
    }
}
