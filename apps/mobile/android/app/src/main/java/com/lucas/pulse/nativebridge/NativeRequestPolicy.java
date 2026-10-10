package com.lucas.pulse.nativebridge;

/** Pure decision for requests whose mutation result cannot safely be delivered. */
final class NativeRequestPolicy {
    static final String RENDERER_CSRF_MARKER = "native-session";

    private NativeRequestPolicy() {}

    static boolean mutationOutcomeUnknown(String method, String path, boolean responseReceived, boolean sessionCurrent) {
        // These POSTs only read public map data; CSRF remains required by HTTP.
        if ("POST".equals(method) && ("/api/navigation/places".equals(path)
                || "/api/navigation/chargers".equals(path) || "/api/navigation/tiles".equals(path))) return false;
        boolean mutation = "POST".equals(method) || "PUT".equals(method) || "DELETE".equals(method);
        return mutation && (!responseReceived || !sessionCurrent);
    }

    static boolean shouldMaskSessionCsrf(String method, String path, int status) {
        return "GET".equals(method) && "/api/session".equals(path) && status == 200;
    }
}
