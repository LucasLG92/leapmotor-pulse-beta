package com.lucas.pulse.nativebridge;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;

/** One-use in-memory OAuth state bound to a purpose, elapsed deadline and auth generation. */
public final class NativeAuthTransaction {
    public enum Purpose { APP, WIDGET }

    public static final long MAX_AGE_MILLIS = 5 * 60 * 1000L;
    private static final SecureRandom RANDOM = new SecureRandom();
    private final Purpose purpose;
    private final String state;
    private final long startedAtElapsedMillis;
    private final long authGeneration;
    private boolean consumed;

    private NativeAuthTransaction(Purpose purpose, String state, long startedAtElapsedMillis, long authGeneration) {
        this.purpose = purpose;
        this.state = state;
        this.startedAtElapsedMillis = startedAtElapsedMillis;
        this.authGeneration = authGeneration;
    }

    public static NativeAuthTransaction begin(Purpose purpose, long elapsedMillis, long authGeneration) {
        if (purpose == null || elapsedMillis < 0 || authGeneration < 0) throw new IllegalArgumentException();
        byte[] entropy = new byte[32];
        RANDOM.nextBytes(entropy);
        return new NativeAuthTransaction(purpose,
                Base64Url.encode(entropy), elapsedMillis, authGeneration);
    }

    /** The OAuth state is intentionally exposed only for constructing the in-memory AppAuth request. */
    public String stateForRequest() { return state; }

    public synchronized boolean consume(String returnedState, Purpose expectedPurpose,
            long nowElapsedMillis, long currentAuthGeneration) {
        if (consumed || returnedState == null || expectedPurpose != purpose || nowElapsedMillis < startedAtElapsedMillis
                || nowElapsedMillis - startedAtElapsedMillis > MAX_AGE_MILLIS
                || currentAuthGeneration != authGeneration || returnedState.length() != state.length()) return false;
        boolean matches = MessageDigest.isEqual(
                state.getBytes(StandardCharsets.US_ASCII), returnedState.getBytes(StandardCharsets.US_ASCII));
        if (!matches) return false;
        consumed = true;
        return true;
    }

    @Override public String toString() {
        return "NativeAuthTransaction{purpose=" + purpose + ", state=[redacted], consumed=" + consumed + "}";
    }
}
