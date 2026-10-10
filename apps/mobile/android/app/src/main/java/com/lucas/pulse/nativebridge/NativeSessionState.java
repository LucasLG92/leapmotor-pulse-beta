package com.lucas.pulse.nativebridge;

import java.util.regex.Pattern;

/** BFF session held only in process memory and bounded by Android's monotonic clock. */
public final class NativeSessionState {
    public static final int MAX_TTL_SECONDS = 30 * 60;
    private static final Pattern COOKIE = Pattern.compile("[A-Za-z0-9_-]{43}");
    private static final Pattern CSRF = Pattern.compile("[A-Za-z0-9_-]{32,128}");

    private String cookie;
    private String csrf;
    private long expiresAtElapsedMillis;
    private long generation;

    public synchronized long generation(long nowElapsedMillis) {
        expireIfNeeded(nowElapsedMillis);
        return generation;
    }

    public synchronized boolean authenticated(long nowElapsedMillis) {
        expireIfNeeded(nowElapsedMillis);
        return cookie != null;
    }

    public synchronized SessionSnapshot snapshot(long nowElapsedMillis) {
        expireIfNeeded(nowElapsedMillis);
        if (cookie == null) return null;
        return new SessionSnapshot(cookie, csrf, expiresAtElapsedMillis, generation);
    }

    public synchronized boolean install(String cookieValue, String csrfValue, int ttlSeconds,
            long nowElapsedMillis, long expectedGeneration) {
        if (nowElapsedMillis < 0) return false;
        // A pending authorization must not revive a session that expired while the
        // system browser was open. Expiration advances the generation before compare.
        expireIfNeeded(nowElapsedMillis);
        if (expectedGeneration != generation || ttlSeconds < 1
                || ttlSeconds > MAX_TTL_SECONDS || !validCookie(cookieValue) || !validCsrf(csrfValue)) return false;
        long ttlMillis = ttlSeconds * 1000L;
        if (nowElapsedMillis > Long.MAX_VALUE - ttlMillis) return false;
        generation++;
        cookie = cookieValue;
        csrf = csrfValue;
        expiresAtElapsedMillis = nowElapsedMillis + ttlMillis;
        return true;
    }

    public synchronized boolean updateCsrf(String value, long expectedGeneration, long nowElapsedMillis) {
        expireIfNeeded(nowElapsedMillis);
        if (cookie == null || generation != expectedGeneration || !validCsrf(value)) return false;
        csrf = value;
        return true;
    }

    /** Immediately makes every captured request snapshot stale. */
    public synchronized long invalidate() {
        generation++;
        cookie = null;
        csrf = null;
        expiresAtElapsedMillis = 0;
        return generation;
    }

    public synchronized boolean isCurrent(long expectedGeneration, long nowElapsedMillis) {
        expireIfNeeded(nowElapsedMillis);
        return cookie != null && generation == expectedGeneration;
    }

    /** Checks an authorization epoch even when the user has no BFF cookie yet. */
    public synchronized boolean isGenerationCurrent(long expectedGeneration, long nowElapsedMillis) {
        expireIfNeeded(nowElapsedMillis);
        return generation == expectedGeneration;
    }

    private void expireIfNeeded(long nowElapsedMillis) {
        if (cookie != null && (nowElapsedMillis < 0 || nowElapsedMillis >= expiresAtElapsedMillis)) {
            cookie = null;
            csrf = null;
            expiresAtElapsedMillis = 0;
            generation++;
        }
    }

    public static boolean validCookie(String value) {
        return value != null && COOKIE.matcher(value).matches();
    }

    public static boolean validCsrf(String value) {
        return value != null && CSRF.matcher(value).matches();
    }

    public static final class SessionSnapshot {
        public final String cookie;
        public final String csrf;
        public final long expiresAtElapsedMillis;
        public final long generation;

        private SessionSnapshot(String cookie, String csrf, long expiresAtElapsedMillis, long generation) {
            this.cookie = cookie;
            this.csrf = csrf;
            this.expiresAtElapsedMillis = expiresAtElapsedMillis;
            this.generation = generation;
        }

        @Override public String toString() {
            return "SessionSnapshot{cookie=[redacted], csrf=[redacted], generation=" + generation + "}";
        }
    }
}
