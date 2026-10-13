package com.lucas.pulse.nativebridge;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;

import org.junit.Test;

public final class NativeJourneyReportTransportTest {
    @Test public void strictContentTypeRequiresPresentJsonHeader() {
        assertTrue(NativeHttpClient.hasStrictJsonContentType("application/json"));
        assertTrue(NativeHttpClient.hasStrictJsonContentType("Application/JSON; charset=UTF-8"));
        for (String value : new String[]{null, "", "  ", "text/plain", "application/jsonp"}) {
            assertFalse(NativeHttpClient.hasStrictJsonContentType(value));
        }
    }

    @Test public void reportBodyAcceptsTheExactUtf8ByteLimit() throws IOException {
        byte[] expected = new byte[NativeJourneyReportPolicy.MAX_BODY_BYTES];
        Arrays.fill(expected, (byte) 'x');
        assertArrayEquals(expected, NativeHttpClient.readJourneyReportBody(new ByteArrayInputStream(expected)));
    }

    @Test public void reportBodyRejectsOneByteOverTheLimit() throws IOException {
        byte[] oversized = new byte[NativeJourneyReportPolicy.MAX_BODY_BYTES + 1];
        try {
            NativeHttpClient.readJourneyReportBody(new ByteArrayInputStream(oversized));
            fail("Se esperaba rechazo del cuerpo sobredimensionado.");
        } catch (IOException expected) {
            assertTrue(expected.getMessage() != null);
        }
    }

    @Test public void reportByteLimitIsAppliedToUtf8RatherThanCharacterCount() {
        String multibyte = "€".repeat(NativeJourneyReportPolicy.MAX_BODY_BYTES / 3 + 1);
        assertFalse(NativeJourneyReportPolicy.bodyWithinLimit(multibyte));
        assertTrue(NativeJourneyReportPolicy.bodyWithinLimit("a".repeat(NativeJourneyReportPolicy.MAX_BODY_BYTES)));
    }

    @Test public void reportDecoderPreservesValidUtf8() throws CharacterCodingException {
        String expected = "{\"text\":\"café\",\"zero\":0,\"tiny\":0.0000006,\"nullable\":null}";
        assertTrue(expected.equals(NativeHttpClient.decodeJourneyReportBody(
                expected.getBytes(StandardCharsets.UTF_8))));
    }

    @Test public void reportDecoderRejectsMalformedOrUnmappableUtf8() {
        byte[][] invalid = {
                {(byte) 0xc3, 0x28},                  // malformed continuation
                {(byte) 0xc3},                       // truncated sequence
                {(byte) 0xc0, (byte) 0xaf},           // overlong encoding
                {(byte) 0xed, (byte) 0xa0, (byte) 0x80}, // encoded surrogate
                {(byte) 0xf4, (byte) 0x90, (byte) 0x80, (byte) 0x80} // above U+10FFFF
        };
        for (byte[] bytes : invalid) {
            try {
                NativeHttpClient.decodeJourneyReportBody(bytes);
                fail("Se esperaba rechazo de UTF-8 malformado.");
            } catch (CharacterCodingException expected) {
                // Expected: report-only decoding never replaces malformed bytes.
            }
        }
    }
}
