package com.lucas.pulse.nativebridge;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class NativeWidgetRefreshPolicyTest {
    @Test public void snapshotCanBeAcceptedOnlyForTheCurrentOptedInGrantGeneration() {
        assertTrue(NativeWidgetRefreshPolicy.shouldAcceptSnapshot(true, true, 8L, 8L));
        assertFalse(NativeWidgetRefreshPolicy.shouldAcceptSnapshot(false, true, 8L, 8L));
        assertFalse(NativeWidgetRefreshPolicy.shouldAcceptSnapshot(true, false, 8L, 8L));
        assertFalse(NativeWidgetRefreshPolicy.shouldAcceptSnapshot(true, true, 8L, 9L));
        assertFalse(NativeWidgetRefreshPolicy.shouldAcceptSnapshot(true, true, -1L, -1L));
    }

    @Test public void onlyDefinitiveGrantRejectionsRetireTheCredential() {
        assertTrue(NativeWidgetRefreshPolicy.shouldRetireCredential(401));
        assertTrue(NativeWidgetRefreshPolicy.shouldRetireCredential(403));
        assertTrue(NativeWidgetRefreshPolicy.shouldRetireCredential(404));
        assertFalse(NativeWidgetRefreshPolicy.shouldRetireCredential(0));
        assertFalse(NativeWidgetRefreshPolicy.shouldRetireCredential(429));
        assertFalse(NativeWidgetRefreshPolicy.shouldRetireCredential(502));
    }
}
