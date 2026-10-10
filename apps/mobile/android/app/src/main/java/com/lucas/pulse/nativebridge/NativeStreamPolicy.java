package com.lucas.pulse.nativebridge;

/** Pure decision for reporting a session failure to the current SSE subscriber. */
final class NativeStreamPolicy {
    private NativeStreamPolicy() {}

    static boolean shouldReportSessionFailure(boolean subscriptionCurrent, boolean sessionCurrent) {
        return subscriptionCurrent && !sessionCurrent;
    }
}
