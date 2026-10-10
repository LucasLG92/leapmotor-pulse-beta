package com.lucas.pulse.nativebridge;

/** Pure decisions shared by the refresh worker and JVM tests. */
final class NativeWidgetRefreshPolicy {
    private NativeWidgetRefreshPolicy() {}

    static boolean shouldAcceptSnapshot(boolean consentEnabled, boolean credentialCurrent,
            long requestGeneration, long currentGeneration) {
        return consentEnabled && credentialCurrent && requestGeneration >= 0L
                && requestGeneration == currentGeneration;
    }

    static boolean shouldRetireCredential(int httpStatus) {
        return httpStatus == 401 || httpStatus == 403 || httpStatus == 404;
    }
}
