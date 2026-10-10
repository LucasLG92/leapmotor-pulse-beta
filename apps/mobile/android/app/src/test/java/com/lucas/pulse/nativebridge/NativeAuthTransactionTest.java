package com.lucas.pulse.nativebridge;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class NativeAuthTransactionTest {
    @Test public void base64UrlEncodingIsUnpaddedAndUrlSafe() {
        assertEquals("-_8", Base64Url.encode(new byte[] {(byte) 0xfb, (byte) 0xff}));
        assertEquals("AA", Base64Url.encode(new byte[] {0}));
    }

    @Test public void textChecksCoverWhitespaceWithoutRelaxingJsonTopLevel() {
        assertTrue(NativeText.isBlank(" \t\n\u2003"));
        assertFalse(NativeText.isBlank(" x "));
        assertTrue(NativeText.startsWithJsonObject(" \r\n{}"));
        assertFalse(NativeText.startsWithJsonObject("[{}]"));
    }

    @Test public void stateIsRandomOneUsePurposeAndGenerationBound() {
        NativeAuthTransaction first = NativeAuthTransaction.begin(NativeAuthTransaction.Purpose.APP, 100L, 3L);
        NativeAuthTransaction second = NativeAuthTransaction.begin(NativeAuthTransaction.Purpose.APP, 100L, 3L);
        String state = first.stateForRequest();
        assertTrue(state.matches("[A-Za-z0-9_-]{43}"));
        assertNotEquals(state, second.stateForRequest());
        assertFalse(first.consume(state, NativeAuthTransaction.Purpose.WIDGET, 101L, 3L));
        assertFalse(first.consume("wrong", NativeAuthTransaction.Purpose.APP, 101L, 3L));
        assertFalse(first.consume(state, NativeAuthTransaction.Purpose.APP, 101L, 4L));
        assertTrue(first.consume(state, NativeAuthTransaction.Purpose.APP, 101L, 3L));
        assertFalse(first.consume(state, NativeAuthTransaction.Purpose.APP, 101L, 3L));
        assertFalse(first.toString().contains(state));
    }

    @Test public void rejectsExpiredOrReversedMonotonicTime() {
        NativeAuthTransaction transaction = NativeAuthTransaction.begin(NativeAuthTransaction.Purpose.WIDGET, 10L, 1L);
        String state = transaction.stateForRequest();
        assertFalse(transaction.consume(state, NativeAuthTransaction.Purpose.WIDGET,
                10L + NativeAuthTransaction.MAX_AGE_MILLIS + 1L, 1L));
        assertFalse(transaction.consume(state, NativeAuthTransaction.Purpose.WIDGET, 9L, 1L));
        assertTrue(transaction.consume(state, NativeAuthTransaction.Purpose.WIDGET,
                10L + NativeAuthTransaction.MAX_AGE_MILLIS, 1L));
    }
}
