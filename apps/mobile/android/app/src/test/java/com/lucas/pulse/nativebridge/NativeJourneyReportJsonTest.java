package com.lucas.pulse.nativebridge;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class NativeJourneyReportJsonTest {
    @Test public void rejectsDeepInputBeforeRecursiveJsonParsingAndEnforcesDepthBoundary() {
        String depthLimit = "{\"x\":" + "[".repeat(NativeJourneyReportJson.MAX_DEPTH - 1)
                + "0" + "]".repeat(NativeJourneyReportJson.MAX_DEPTH - 1) + "}";
        assertTrue(NativeJourneyReportJson.isStrictObject(depthLimit));

        String overLimit = "{\"x\":" + "[".repeat(NativeJourneyReportJson.MAX_DEPTH)
                + "0" + "]".repeat(NativeJourneyReportJson.MAX_DEPTH) + "}";
        assertFalse(NativeJourneyReportJson.isStrictObject(overLimit));

        String deeplyNested = "{\"x\":" + "[".repeat(30_000) + "0" + "]".repeat(30_000) + "}";
        assertTrue(deeplyNested.length() < NativeJourneyReportPolicy.MAX_BODY_BYTES);
        assertFalse(NativeJourneyReportJson.isStrictObject(deeplyNested));
    }

    @Test public void rejectsDuplicateKeysAfterJsonEscapeDecoding() {
        assertFalse(NativeJourneyReportJson.isStrictObject("{\"x\":1,\"x\":2}"));
        String escapedDuplicate = "{\"x\":1,\"" + (char) 92 + "u0078\":2}";
        assertFalse(NativeJourneyReportJson.isStrictObject(escapedDuplicate));
        String unicodeDuplicate = "{\"é\":1,\"" + (char) 92 + "u00e9\":2}";
        assertFalse(NativeJourneyReportJson.isStrictObject(unicodeDuplicate));
    }

    @Test public void acceptsJsonNullZeroTinyAmountsAndEscapedStrings() {
        String valid = "{\"nullable\":null,\"zero\":0,\"tiny\":0.0000006,\"text\":\"a\\n\\u00e9\"}";
        assertTrue(NativeJourneyReportJson.isStrictObject(valid));
    }

    @Test public void rejectsNonJsonGrammarAndTrailingContent() {
        String[] invalid = {
                "{'x':1}",
                "{x:1}",
                "{\"x\":NaN}",
                "{\"x\":Infinity}",
                "{\"x\":01}",
                "{\"x\":1.}",
                "{\"x\":1e}",
                "{\"x\":1,}",
                "{\"x\":/*comment*/1}",
                "{\"x\":1} trailing",
                "{\"x\":1}{\"y\":2}",
                "{\"x\":\"bad\\qescape\"}",
                "{\"x\":\"unclosed}"
        };
        for (String json : invalid) assertFalse(json, NativeJourneyReportJson.isStrictObject(json));
    }
}
