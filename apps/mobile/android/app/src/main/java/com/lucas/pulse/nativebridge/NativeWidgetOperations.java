package com.lucas.pulse.nativebridge;

import android.content.Context;

import com.lucas.pulse.widget.WidgetSnapshotCache;

/** Process-wide serialization point for grant/consent changes and snapshot acceptance. */
final class NativeWidgetOperations {
    static final Object LOCK = new Object();

    private static long generation;
    private static boolean refreshInFlight;
    private static long refreshGeneration = -1L;

    private NativeWidgetOperations() {}

    static long generation() {
        synchronized (LOCK) { return generation; }
    }

    static boolean isGenerationCurrent(long expectedGeneration) {
        synchronized (LOCK) { return generation == expectedGeneration; }
    }

    /** Must be called while holding LOCK. */
    static long generationLocked() { return generation; }

    /** Must be called while holding LOCK. Changes invalidate every in-flight result. */
    static long advanceLocked() {
        generation = generation == Long.MAX_VALUE ? 1L : generation + 1L;
        return generation;
    }

    /** Allows at most one refresh per widget state, even when periodic and one-time work overlap. */
    static boolean beginRefreshLocked(long expectedGeneration) {
        if (generation != expectedGeneration) return false;
        if (refreshInFlight && refreshGeneration == expectedGeneration) return false;
        refreshInFlight = true;
        refreshGeneration = expectedGeneration;
        return true;
    }

    static void finishRefresh(long requestGeneration) {
        synchronized (LOCK) {
            if (refreshInFlight && refreshGeneration == requestGeneration) {
                refreshInFlight = false;
                refreshGeneration = -1L;
            }
        }
    }

    /** Must be called while holding LOCK. A failed clear remains closed and is not logged. */
    static void retireLocked(Context context, NativeWidgetCredentialVault vault, WidgetSnapshotCache cache) {
        advanceLocked();
        try { vault.clear(); } catch (Exception ignored) { }
        try { cache.disableAndClear(); } catch (Exception ignored) { }
        NativeWidgetRefreshScheduler.cancelAll(context);
    }
}
