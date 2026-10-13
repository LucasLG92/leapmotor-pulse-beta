package com.lucas.pulse.nativebridge;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.Proxy;
import java.net.URI;
import java.net.URL;
import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;

import javax.net.ssl.HttpsURLConnection;

/** Fixed-origin HTTPS transport. It never follows redirects or exposes headers to JavaScript. */
final class NativeHttpClient {
    static final int MAX_BODY_BYTES = 5 * 1024 * 1024;
    private static final int MAX_BOOTSTRAP_BYTES = 64 * 1024;
    private static final int CONNECT_TIMEOUT_MS = 3_000;
    private static final int READ_TIMEOUT_MS = 8_000;
    private static final String COOKIE_NAME = "__Host-pulse_session";
    private static final Pattern COOKIE_HEADER = Pattern.compile("__Host-pulse_session=([A-Za-z0-9_-]{43})");
    private static final Pattern WIDGET_TOKEN = Pattern.compile("pw_[A-Za-z0-9_-]{43}");
    private static final Pattern GRANT_ID = Pattern.compile("[a-f0-9]{32}");

    private final NativeRuntimeConfig config;
    private final ConnectionOpener connectionOpener;

    NativeHttpClient(NativeRuntimeConfig config) {
        this(config, url -> (HttpsURLConnection) url.openConnection(Proxy.NO_PROXY));
    }

    NativeHttpClient(NativeRuntimeConfig config, ConnectionOpener connectionOpener) {
        this.config = config;
        this.connectionOpener = connectionOpener;
    }

    NativeResponse request(String method, String rawPath, String body, NativeSessionState.SessionSnapshot session)
            throws NativeTransportException {
        return request(method, rawPath, body, session, null);
    }

    NativeResponse request(String method, String rawPath, String body, NativeSessionState.SessionSnapshot session,
            RequestCancellation cancellation) throws NativeTransportException {
        if (!NativeApiPolicy.allows(rawPath, method)) throw NativeTransportException.invalidRequest();
        if (cancellation != null && !NativeApiPolicy.isCancellableRead(rawPath, method)) {
            throw NativeTransportException.invalidRequest();
        }
        if (cancellation != null && cancellation.isCancelled()) throw NativeTransportException.cancelled();
        byte[] requestBytes = requestJson(method, body);
        if (session == null) throw NativeTransportException.unauthenticated();
        if (isMutation(method) && !NativeSessionState.validCsrf(session.csrf)) {
            throw NativeTransportException.unauthenticated();
        }
        try {
            Exchange response = exchange(method, rawPath, requestBytes, session.cookie,
                    isMutation(method) ? session.csrf : null, null, MAX_BODY_BYTES, false, false, cancellation);
            return publicResponse(response);
        } catch (Exception ignored) {
            if (cancellation != null && cancellation.isCancelled()) throw NativeTransportException.cancelled();
            throw NativeTransportException.transport();
        }
    }

    /** Purpose-bound aggregate report read with a strict header and body limit. */
    NativeResponse requestManualJourneyReport(int days, NativeSessionState.SessionSnapshot session)
            throws NativeTransportException {
        if ((days != 30 && days != 90 && days != 365)
                || !NativeApiPolicy.allows("/api/journeys/report?days=" + days, "GET")) {
            throw NativeTransportException.invalidRequest();
        }
        if (session == null || !NativeSessionState.validCookie(session.cookie)) {
            throw NativeTransportException.unauthenticated();
        }
        try {
            String path = "/api/journeys/report?days=" + days;
            Exchange response = exchange("GET", path, null, session.cookie, null, null,
                    NativeJourneyReportPolicy.MAX_BODY_BYTES, true, true);
            return new NativeResponse(response.status, response.contentType, response.body);
        } catch (NativeTransportException error) {
            throw error;
        } catch (ResponseTooLargeException ignored) {
            throw NativeTransportException.response();
        } catch (Exception ignored) {
            throw NativeTransportException.transport();
        }
    }

    HttpsURLConnection openEventStream(NativeSessionState.SessionSnapshot session) throws NativeTransportException {
        if (!NativeApiPolicy.allowsEvents("/api/events", "GET") || session == null
                || !NativeSessionState.validCookie(session.cookie)) throw NativeTransportException.unauthenticated();
        HttpsURLConnection connection = null;
        try {
            URL url = new URI(config.apiOrigin + "/api/events").toURL();
            connection = (HttpsURLConnection) url.openConnection(Proxy.NO_PROXY);
            connection.setInstanceFollowRedirects(false);
            connection.setConnectTimeout(CONNECT_TIMEOUT_MS);
            connection.setReadTimeout(60_000);
            connection.setUseCaches(false);
            connection.setRequestMethod("GET");
            connection.setRequestProperty("Accept", "text/event-stream");
            connection.setRequestProperty("Cache-Control", "no-store");
            connection.setRequestProperty("Pragma", "no-cache");
            connection.setRequestProperty("Cookie", COOKIE_NAME + "=" + session.cookie);
            return connection;
        } catch (Exception ignored) {
            if (connection != null) connection.disconnect();
            throw NativeTransportException.transport();
        }
    }

    static boolean isEventStreamResponse(HttpsURLConnection connection) throws IOException {
        return connection != null && connection.getResponseCode() == 200
                && "text/event-stream".equals(normalizedContentType(connection.getContentType()));
    }

    BootstrapResult bootstrap(String accessToken) throws NativeTransportException {
        if (accessToken == null || accessToken.length() > 4096 || NativeText.isBlank(accessToken)) {
            throw NativeTransportException.unauthenticated();
        }
        try {
            Exchange bootstrap = exchange("POST", "/api/native/app-sessions", "{}".getBytes(StandardCharsets.UTF_8),
                    null, null, accessToken, MAX_BOOTSTRAP_BYTES);
            if (bootstrap.status != 200 || !isJson(bootstrap.contentType)) throw NativeTransportException.bootstrap();
            JSONObject body = parseJsonObject(bootstrap.body);
            if (!body.has("ok") || !body.optBoolean("ok", false)
                    || !body.has("expires_in") || !isJsonInteger(body, "expires_in")) {
                throw NativeTransportException.bootstrap();
            }
            int ttl = body.getInt("expires_in");
            if (ttl < 1 || ttl > NativeSessionState.MAX_TTL_SECONDS) throw NativeTransportException.bootstrap();
            String sid = parseSessionCookie(bootstrap.headers, ttl);

            Exchange session = exchange("GET", "/api/session", null, sid, null, null, MAX_BOOTSTRAP_BYTES);
            if (session.status != 200 || !isJson(session.contentType)) throw NativeTransportException.bootstrap();
            JSONObject sessionBody = parseJsonObject(session.body);
            if (!sessionBody.optBoolean("authenticated", false)) throw NativeTransportException.bootstrap();
            String csrf = sessionBody.optString("csrf_token", null);
            if (!NativeSessionState.validCsrf(csrf)) throw NativeTransportException.bootstrap();
            JSONObject user = sessionBody.optJSONObject("user");
            if (user == null || NativeText.isBlank(user.optString("name", ""))) throw NativeTransportException.bootstrap();
            return new BootstrapResult(sid, csrf, ttl);
        } catch (NativeTransportException error) {
            throw error;
        } catch (Exception ignored) {
            throw NativeTransportException.bootstrap();
        }
    }

    void logout(BootstrapResult bootstrap) {
        if (bootstrap == null || !NativeSessionState.validCookie(bootstrap.cookie)
                || !NativeSessionState.validCsrf(bootstrap.csrf)) return;
        try {
            exchange("POST", "/api/auth/logout", "{}".getBytes(StandardCharsets.UTF_8),
                    bootstrap.cookie, bootstrap.csrf, null, 64 * 1024);
        } catch (Exception ignored) { }
    }

    void logout(NativeSessionState.SessionSnapshot snapshot) {
        if (snapshot == null || !NativeSessionState.validCookie(snapshot.cookie)
                || !NativeSessionState.validCsrf(snapshot.csrf)) return;
        try {
            exchange("POST", "/api/auth/logout", "{}".getBytes(StandardCharsets.UTF_8),
                    snapshot.cookie, snapshot.csrf, null, 64 * 1024);
        } catch (Exception ignored) { }
    }

    WidgetGrantResult registerWidget(String accessToken, String pairingCode, String installationId,
            String deviceName) throws NativeTransportException {
        if (accessToken == null || accessToken.length() > 4096 || NativeText.isBlank(accessToken)
                || pairingCode == null || !pairingCode.matches("[A-Za-z0-9_-]{43}")
                || installationId == null || !installationId.matches("[A-Za-z0-9_-]{43}")
                || NativeText.isBlank(deviceName) || deviceName.length() > 60) {
            throw NativeTransportException.invalidRequest();
        }
        try {
            JSONObject payload = new JSONObject().put("pairing_code", pairingCode)
                    .put("installation_id", installationId).put("device_name", deviceName);
            Exchange response = exchange("POST", "/api/native/widget-grants",
                    payload.toString().getBytes(StandardCharsets.UTF_8), null, null, accessToken, MAX_BOOTSTRAP_BYTES);
            if (response.status != 200 || !isJson(response.contentType)) throw NativeTransportException.widget();
            JSONObject json = parseJsonObject(response.body);
            JSONObject grant = json.optJSONObject("grant");
            if (grant == null) throw NativeTransportException.widget();
            String grantId = grant.optString("id", null);
            String widgetToken = json.optString("widget_token", null);
            String expiry = grant.optString("expires_at", null);
            if (grantId == null || !GRANT_ID.matcher(grantId).matches() || widgetToken == null
                    || !WIDGET_TOKEN.matcher(widgetToken).matches() || expiry == null) {
                throw NativeTransportException.widget();
            }
            long expiresAt = Instant.from(OffsetDateTime.parse(expiry)).toEpochMilli();
            long now = System.currentTimeMillis();
            if (expiresAt <= now || expiresAt - now > 31L * 24 * 60 * 60 * 1000) {
                throw NativeTransportException.widget();
            }
            return new WidgetGrantResult(grantId, widgetToken, expiresAt);
        } catch (NativeTransportException error) {
            throw error;
        } catch (Exception ignored) {
            throw NativeTransportException.widget();
        }
    }

    JSONObject widgetSnapshot(NativeWidgetCredentialVault.NativeWidgetCredential credential)
            throws NativeTransportException {
        if (!validCredential(credential)) throw NativeTransportException.widget();
        try {
            Exchange response = exchange("GET", "/api/native/widget-grants/" + credential.grantId + "/snapshot",
                    null, null, null, credential.widgetToken, MAX_BODY_BYTES, true);
            if (response.status != 200) throw NativeTransportException.widgetStatus(response.status);
            if (!isJson(response.contentType)) throw NativeTransportException.response();
            return parseJsonObject(response.body);
        } catch (NativeTransportException error) {
            throw error;
        } catch (Exception ignored) {
            throw NativeTransportException.widget();
        }
    }

    void revokeWidget(NativeWidgetCredentialVault.NativeWidgetCredential credential) {
        if (!validCredential(credential)) return;
        revokeWidget(credential.grantId, credential.widgetToken);
    }

    void revokeWidget(String grantId, String widgetToken) {
        if (grantId == null || !GRANT_ID.matcher(grantId).matches() || widgetToken == null
                || !WIDGET_TOKEN.matcher(widgetToken).matches()) return;
        try {
            String path = "/api/native/widget-grants/" + grantId;
            exchange("DELETE", path, "{}".getBytes(StandardCharsets.UTF_8), null, null, widgetToken, 64 * 1024);
        } catch (Exception ignored) { }
    }

    private Exchange exchange(String method, String path, byte[] body, String cookie, String csrf,
            String bearer, int responseLimit) throws Exception {
        return exchange(method, path, body, cookie, csrf, bearer, responseLimit, false);
    }

    /** Can return a definitive non-200 status without reading an untrusted error body. */
    private Exchange exchange(String method, String path, byte[] body, String cookie, String csrf,
            String bearer, int responseLimit, boolean skipNon200Body) throws Exception {
        return exchange(method, path, body, cookie, csrf, bearer, responseLimit, skipNon200Body, false);
    }

    private Exchange exchange(String method, String path, byte[] body, String cookie, String csrf,
            String bearer, int responseLimit, boolean skipNon200Body, boolean requireJsonOn200) throws Exception {
        return exchange(method, path, body, cookie, csrf, bearer, responseLimit,
                skipNon200Body, requireJsonOn200, null);
    }

    private Exchange exchange(String method, String path, byte[] body, String cookie, String csrf,
            String bearer, int responseLimit, boolean skipNon200Body, boolean requireJsonOn200,
            RequestCancellation cancellation) throws Exception {
        URL url = new URI(config.apiOrigin + path).toURL();
        if (!"https".equalsIgnoreCase(url.getProtocol())) throw new IOException();
        HttpsURLConnection connection = connectionOpener.open(url);
        connection.setInstanceFollowRedirects(false);
        connection.setConnectTimeout(CONNECT_TIMEOUT_MS);
        connection.setReadTimeout(READ_TIMEOUT_MS);
        connection.setUseCaches(false);
        connection.setRequestMethod(method);
        connection.setRequestProperty("Accept", "application/json");
        connection.setRequestProperty("Cache-Control", "no-store");
        connection.setRequestProperty("Pragma", "no-cache");
        if (path.startsWith("/api/navigation/")) {
            connection.setRequestProperty("Referer", config.apiOrigin + "/");
        }
        if (body != null) {
            if (body.length > MAX_BODY_BYTES) throw new IOException();
            connection.setDoOutput(true);
            connection.setRequestProperty("Content-Type", "application/json");
            connection.setFixedLengthStreamingMode(body.length);
        }
        if (cookie != null) {
            if (!NativeSessionState.validCookie(cookie)) throw new IOException();
            connection.setRequestProperty("Cookie", COOKIE_NAME + "=" + cookie);
        }
        if (csrf != null) {
            if (!NativeSessionState.validCsrf(csrf)) throw new IOException();
            connection.setRequestProperty("X-Pulse-CSRF", csrf);
        }
        if (bearer != null) {
            if (bearer.length() > 4096 || NativeText.isBlank(bearer) || bearer.chars().anyMatch(Character::isISOControl)) {
                throw new IOException();
            }
            connection.setRequestProperty("Authorization", "Bearer " + bearer);
        }
        if (cancellation != null && !cancellation.attach(connection)) throw new IOException();
        try {
            ensureNotCancelled(cancellation);
            if (body != null) {
                try (OutputStream output = connection.getOutputStream()) {
                    output.write(body);
                }
            }
            ensureNotCancelled(cancellation);
            int status = connection.getResponseCode();
            String rawContentType = connection.getContentType();
            String contentType = normalizedContentType(rawContentType);
            if (requireJsonOn200 && status == 200 && !hasStrictJsonContentType(rawContentType)) {
                throw NativeTransportException.response();
            }
            byte[] bytes;
            if (skipNon200Body && status != 200) {
                bytes = new byte[0];
            } else {
                ensureNotCancelled(cancellation);
                InputStream stream = status >= 400 ? connection.getErrorStream() : connection.getInputStream();
                bytes = stream == null ? new byte[0] : requireJsonOn200 && status == 200
                        ? readJourneyReportBody(stream) : readBounded(stream, responseLimit);
            }
            String responseBody;
            if (requireJsonOn200 && status == 200) {
                try {
                    responseBody = decodeJourneyReportBody(bytes);
                } catch (CharacterCodingException ignored) {
                    throw NativeTransportException.response();
                }
            } else {
                responseBody = new String(bytes, StandardCharsets.UTF_8);
            }
            return new Exchange(status, contentType, responseBody, connection.getHeaderFields());
        } finally {
            if (cancellation != null) cancellation.detach(connection);
            connection.disconnect();
        }
    }

    private static void ensureNotCancelled(RequestCancellation cancellation) throws IOException {
        if (cancellation != null && cancellation.isCancelled()) throw new IOException();
    }

    private static byte[] requestJson(String method, String body) throws NativeTransportException {
        if ("GET".equals(method)) {
            if (body != null && !body.isEmpty()) throw NativeTransportException.invalidRequest();
            return null;
        }
        if (body == null || body.isEmpty() || body.length() > MAX_BODY_BYTES) {
            throw NativeTransportException.invalidRequest();
        }
        byte[] encoded = body.getBytes(StandardCharsets.UTF_8);
        if (encoded.length > MAX_BODY_BYTES) throw NativeTransportException.invalidRequest();
        try {
            if (!NativeText.startsWithJsonObject(body)) throw NativeTransportException.invalidRequest();
            new JSONObject(body);
            return encoded;
        } catch (JSONException error) {
            throw NativeTransportException.invalidRequest();
        }
    }

    private static boolean isMutation(String method) {
        return !"GET".equals(method);
    }

    private static byte[] readBounded(InputStream stream, int limit) throws IOException {
        return readBounded(stream, limit, false);
    }

    private static byte[] readBounded(InputStream stream, int limit, boolean reportResponse) throws IOException {
        try (InputStream input = stream; ByteArrayOutputStream output = new ByteArrayOutputStream(Math.min(8192, limit))) {
            byte[] buffer = new byte[8192];
            int count;
            while ((count = input.read(buffer)) != -1) {
                if (count > limit - output.size()) {
                    if (reportResponse) throw new ResponseTooLargeException();
                    throw new IOException();
                }
                output.write(buffer, 0, count);
            }
            return output.toByteArray();
        }
    }

    static byte[] readJourneyReportBody(InputStream stream) throws IOException {
        return readBounded(stream, NativeJourneyReportPolicy.MAX_BODY_BYTES, true);
    }

    static String decodeJourneyReportBody(byte[] body) throws CharacterCodingException {
        return StandardCharsets.UTF_8.newDecoder()
                .onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT)
                .decode(ByteBuffer.wrap(body)).toString();
    }

    static boolean hasStrictJsonContentType(String contentType) {
        return contentType != null && !contentType.trim().isEmpty() && isJson(normalizedContentType(contentType));
    }

    private static String parseSessionCookie(Map<String, List<String>> headers, int expectedTtl)
            throws NativeTransportException {
        String value = null;
        for (Map.Entry<String, List<String>> entry : headers.entrySet()) {
            if (entry.getKey() == null || !"set-cookie".equalsIgnoreCase(entry.getKey())) continue;
            List<String> values = entry.getValue();
            if (values == null || values.size() != 1 || value != null) throw NativeTransportException.bootstrap();
            value = values.get(0);
        }
        if (value == null || value.length() > 512) throw NativeTransportException.bootstrap();
        String[] parts = value.split(";", -1);
        if (parts.length < 4 || !COOKIE_HEADER.matcher(parts[0].trim()).matches()) {
            throw NativeTransportException.bootstrap();
        }
        boolean secure = false;
        boolean httpOnly = false;
        boolean path = false;
        boolean sameSite = false;
        boolean domain = false;
        boolean maxAge = false;
        for (int index = 1; index < parts.length; index++) {
            String attribute = parts[index].trim();
            if (attribute.equalsIgnoreCase("Secure")) secure = true;
            else if (attribute.equalsIgnoreCase("HttpOnly")) httpOnly = true;
            else if (attribute.equalsIgnoreCase("Path=/")) path = true;
            else if (attribute.equalsIgnoreCase("SameSite=lax")) sameSite = true;
            else if (attribute.toLowerCase(java.util.Locale.ROOT).startsWith("domain=")) domain = true;
            else if (attribute.toLowerCase(java.util.Locale.ROOT).startsWith("max-age=")) {
                if (maxAge) throw NativeTransportException.bootstrap();
                maxAge = true;
                try {
                    if (Integer.parseInt(attribute.substring(attribute.indexOf('=') + 1)) != expectedTtl) {
                        throw NativeTransportException.bootstrap();
                    }
                } catch (NumberFormatException ignored) {
                    throw NativeTransportException.bootstrap();
                }
            } else if (!attribute.isEmpty()) {
                throw NativeTransportException.bootstrap();
            }
        }
        if (!secure || !httpOnly || !path || !sameSite || domain || !maxAge) {
            throw NativeTransportException.bootstrap();
        }
        String pair = parts[0].trim();
        return pair.substring(COOKIE_NAME.length() + 1);
    }

    private static JSONObject parseJsonObject(String body) throws JSONException {
        if (body == null || body.length() > MAX_BODY_BYTES) throw new JSONException("invalid");
        return new JSONObject(body);
    }

    private static boolean isJsonInteger(JSONObject object, String key) {
        Object value = object.opt(key);
        return value instanceof Integer || value instanceof Long;
    }

    private static boolean isJson(String contentType) {
        return "application/json".equals(contentType);
    }

    private static String normalizedContentType(String contentType) {
        if (contentType == null) return "";
        int semicolon = contentType.indexOf(';');
        String mediaType = semicolon < 0 ? contentType : contentType.substring(0, semicolon);
        return mediaType.trim().toLowerCase(java.util.Locale.ROOT);
    }

    private boolean validCredential(NativeWidgetCredentialVault.NativeWidgetCredential credential) {
        return credential != null && credential.grantId != null && GRANT_ID.matcher(credential.grantId).matches()
                && credential.widgetToken != null && WIDGET_TOKEN.matcher(credential.widgetToken).matches()
                && credential.expiresAtEpochMillis > System.currentTimeMillis()
                && credential.apiOrigin != null && credential.apiOrigin.equals(config.apiOrigin);
    }

    private static NativeResponse publicResponse(Exchange exchange) throws NativeTransportException {
        if (!exchange.contentType.isEmpty() && !isJson(exchange.contentType)) {
            throw NativeTransportException.response();
        }
        if (exchange.status >= 300 && exchange.status < 400) {
            return new NativeResponse(exchange.status, "application/json", "{\"detail\":\"Recurso no disponible.\"}");
        }
        return new NativeResponse(exchange.status,
                exchange.contentType.isEmpty() ? "application/json" : exchange.contentType, exchange.body);
    }

    static final class BootstrapResult {
        final String cookie;
        final String csrf;
        final int ttlSeconds;

        BootstrapResult(String cookie, String csrf, int ttlSeconds) {
            this.cookie = cookie;
            this.csrf = csrf;
            this.ttlSeconds = ttlSeconds;
        }

        @Override public String toString() { return "BootstrapResult{cookie=[redacted], csrf=[redacted]}"; }
    }

    static final class WidgetGrantResult {
        final String grantId;
        final String widgetToken;
        final long expiresAtEpochMillis;

        WidgetGrantResult(String grantId, String widgetToken, long expiresAtEpochMillis) {
            this.grantId = grantId;
            this.widgetToken = widgetToken;
            this.expiresAtEpochMillis = expiresAtEpochMillis;
        }

        @Override public String toString() { return "WidgetGrantResult{grantId=[redacted], widgetToken=[redacted]}"; }
    }

    static final class NativeResponse {
        final int status;
        final String contentType;
        final String body;

        NativeResponse(int status, String contentType, String body) {
            this.status = status;
            this.contentType = contentType;
            this.body = body;
        }
    }

    interface ConnectionOpener {
        HttpsURLConnection open(URL url) throws IOException;
    }

    /** Owns only an in-flight UI read connection; cancellation before attach prevents dispatch. */
    static final class RequestCancellation {
        private HttpsURLConnection connection;
        private boolean cancelled;

        synchronized boolean isCancelled() { return cancelled; }

        boolean attach(HttpsURLConnection value) {
            synchronized (this) {
                if (!cancelled) {
                    connection = value;
                    return true;
                }
            }
            value.disconnect();
            return false;
        }

        void cancel() {
            HttpsURLConnection active;
            synchronized (this) {
                if (cancelled) return;
                cancelled = true;
                active = connection;
            }
            if (active != null) active.disconnect();
        }

        synchronized void detach(HttpsURLConnection value) {
            if (connection == value) connection = null;
        }
    }

    private static final class ResponseTooLargeException extends IOException {
        private ResponseTooLargeException() { super("Response exceeds its byte limit."); }
    }

    private static final class Exchange {
        final int status;
        final String contentType;
        final String body;
        final Map<String, List<String>> headers;

        Exchange(int status, String contentType, String body, Map<String, List<String>> headers) {
            this.status = status;
            this.contentType = contentType;
            this.body = body;
            this.headers = headers;
        }
    }

    static final class NativeTransportException extends Exception {
        final int status;
        final String safeMessage;

        private NativeTransportException(int status, String safeMessage) {
            super(safeMessage);
            this.status = status;
            this.safeMessage = safeMessage;
        }

        static NativeTransportException invalidRequest() { return new NativeTransportException(400, "La solicitud nativa no es válida."); }
        static NativeTransportException unauthenticated() { return new NativeTransportException(401, "Inicia sesión para continuar."); }
        static NativeTransportException transport() { return new NativeTransportException(0, "No se pudo contactar con Pulse."); }
        static NativeTransportException cancelled() { return new NativeTransportException(0, "La solicitud se canceló."); }
        static NativeTransportException bootstrap() { return new NativeTransportException(401, "No se pudo iniciar sesión en Pulse."); }
        static NativeTransportException widget() { return new NativeTransportException(400, "No se pudo enlazar el widget."); }
        static NativeTransportException widgetStatus(int status) {
            return new NativeTransportException(status, "No se pudo actualizar el widget.");
        }
        static NativeTransportException response() { return new NativeTransportException(502, "Pulse devolvió una respuesta no válida."); }
    }
}
