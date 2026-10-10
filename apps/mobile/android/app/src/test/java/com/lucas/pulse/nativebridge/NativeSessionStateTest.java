package com.lucas.pulse.nativebridge;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class NativeSessionStateTest {
    private static final String COOKIE = "A".repeat(43);
    private static final String CSRF = "B".repeat(48);

    @Test public void sessionExpiresAtBoundedTtlAndSnapshotsRedactSecrets() {
        NativeSessionState state = new NativeSessionState();
        long generation = state.generation(100L);
        assertTrue(state.install(COOKIE, CSRF, NativeSessionState.MAX_TTL_SECONDS, 100L, generation));
        NativeSessionState.SessionSnapshot snapshot = state.snapshot(100L);
        assertNotNull(snapshot);
        assertEquals(COOKIE, snapshot.cookie);
        assertEquals(CSRF, snapshot.csrf);
        assertEquals(1_800_100L, snapshot.expiresAtElapsedMillis);
        assertFalse(snapshot.toString().contains(COOKIE));
        assertFalse(snapshot.toString().contains(CSRF));
        assertTrue(state.isCurrent(snapshot.generation, 1_800_099L));
        assertNull(state.snapshot(1_800_100L));
        assertFalse(state.isCurrent(snapshot.generation, 1_800_100L));
        assertFalse(state.authenticated(1_800_100L));
    }

    @Test public void ttlAndCookieValuesAreValidatedAndReplacementStalesOldRequests() {
        NativeSessionState state = new NativeSessionState();
        long generation = state.generation(0L);
        assertFalse(state.install(COOKIE, CSRF, NativeSessionState.MAX_TTL_SECONDS + 1, 0L, generation));
        assertFalse(state.install("bad-cookie", CSRF, 30, 0L, generation));
        assertFalse(state.install(COOKIE, "bad-csrf", 30, 0L, generation));
        assertTrue(state.install(COOKIE, CSRF, 60, 0L, generation));
        NativeSessionState.SessionSnapshot old = state.snapshot(1L);
        assertNotNull(old);
        assertTrue(state.install("C".repeat(43), "D".repeat(48), 60, 2L, old.generation));
        assertFalse(state.isCurrent(old.generation, 2L));
        assertTrue(state.isCurrent(state.snapshot(2L).generation, 2L));
    }

    @Test public void invalidationClearsSessionAndRejectsStaleGeneration() {
        NativeSessionState state = new NativeSessionState();
        long generation = state.generation(5L);
        assertTrue(state.install(COOKIE, CSRF, 60, 5L, generation));
        NativeSessionState.SessionSnapshot old = state.snapshot(5L);
        long invalidatedGeneration = state.invalidate();
        assertNull(state.snapshot(5L));
        assertFalse(state.isCurrent(old.generation, 5L));
        assertFalse(state.install(COOKIE, CSRF, 60, 5L, old.generation));
        assertTrue(state.install(COOKIE, CSRF, 60, 5L, invalidatedGeneration));
    }

    @Test public void firstLoginCanUseCurrentGenerationWithoutAnExistingCookie() {
        NativeSessionState state = new NativeSessionState();
        long pendingGeneration = state.generation(10L);

        assertFalse(state.isCurrent(pendingGeneration, 10L));
        assertTrue(state.isGenerationCurrent(pendingGeneration, 10L));
        assertTrue(state.install(COOKIE, CSRF, 60, 11L, pendingGeneration));
        assertTrue(state.authenticated(11L));
    }

    @Test public void logoutAndExpiryInvalidatePendingLoginBeforeInstall() {
        NativeSessionState cancelled = new NativeSessionState();
        long cancelledGeneration = cancelled.generation(10L);
        cancelled.invalidate();
        assertFalse(cancelled.isGenerationCurrent(cancelledGeneration, 11L));
        assertFalse(cancelled.install(COOKIE, CSRF, 60, 11L, cancelledGeneration));

        NativeSessionState expiring = new NativeSessionState();
        long initialGeneration = expiring.generation(0L);
        assertTrue(expiring.install(COOKIE, CSRF, 1, 0L, initialGeneration));
        long pendingReloginGeneration = expiring.generation(500L);
        assertFalse(expiring.install("C".repeat(43), "D".repeat(48), 60,
                1_000L, pendingReloginGeneration));
        assertFalse(expiring.isGenerationCurrent(pendingReloginGeneration, 1_000L));
        long freshGeneration = expiring.generation(1_000L);
        assertTrue(expiring.isGenerationCurrent(freshGeneration, 1_000L));
    }
}
