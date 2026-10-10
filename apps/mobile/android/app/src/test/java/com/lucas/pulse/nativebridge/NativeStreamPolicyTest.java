package com.lucas.pulse.nativebridge;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class NativeStreamPolicyTest {
    @Test public void onlyCurrentSubscriberIsNotifiedWhenItsSessionExpires() {
        assertTrue(NativeStreamPolicy.shouldReportSessionFailure(true, false));
        assertFalse(NativeStreamPolicy.shouldReportSessionFailure(false, false));
        assertFalse(NativeStreamPolicy.shouldReportSessionFailure(true, true));
    }
}
