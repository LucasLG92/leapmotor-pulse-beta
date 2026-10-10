package com.lucas.pulse.nativebridge;

import com.lucas.pulse.BuildConfig;

import java.net.URI;
import java.net.URISyntaxException;
import java.util.Locale;

/** Validated, build-time public endpoints for the native OAuth client. */
public final class NativeRuntimeConfig {
    private static final int MAX_PUBLIC_VALUE = 512;

    public final String apiOrigin;
    public final String issuer;
    public final String authorizationEndpoint;
    public final String tokenEndpoint;
    public final String clientId;
    public final String audience;
    public final String callback;
    private final URI apiUri;
    private final URI issuerUri;
    private final URI authorizationUri;
    private final URI tokenUri;

    private NativeRuntimeConfig(
            String apiOrigin,
            String issuer,
            String authorizationEndpoint,
            String tokenEndpoint,
            String clientId,
            String audience,
            String callback,
            URI apiUri,
            URI issuerUri,
            URI authorizationUri,
            URI tokenUri) {
        this.apiOrigin = apiOrigin;
        this.issuer = issuer;
        this.authorizationEndpoint = authorizationEndpoint;
        this.tokenEndpoint = tokenEndpoint;
        this.clientId = clientId;
        this.audience = audience;
        this.callback = callback;
        this.apiUri = apiUri;
        this.issuerUri = issuerUri;
        this.authorizationUri = authorizationUri;
        this.tokenUri = tokenUri;
    }

    public static NativeRuntimeConfig fromBuildConfig() {
        return fromValues(
                BuildConfig.PULSE_API_ORIGIN,
                BuildConfig.PULSE_OIDC_ISSUER,
                BuildConfig.PULSE_AUTHORIZATION_ENDPOINT,
                BuildConfig.PULSE_TOKEN_ENDPOINT,
                BuildConfig.PULSE_NATIVE_CLIENT_ID,
                BuildConfig.PULSE_NATIVE_AUDIENCE,
                BuildConfig.PULSE_CALLBACK);
    }

    /** Returns a closed configuration when any public operator value is absent or invalid. */
    public static NativeRuntimeConfig fromValues(
            String apiOrigin,
            String issuer,
            String authorizationEndpoint,
            String tokenEndpoint,
            String clientId,
            String audience,
            String callback) {
        if (!shortValue(clientId) || !shortValue(audience)) return null;
        URI api = strictHttps(apiOrigin, true);
        URI oidcIssuer = strictHttps(issuer, false);
        URI authorization = strictHttps(authorizationEndpoint, false);
        URI token = strictHttps(tokenEndpoint, false);
        URI redirect = strictHttps(callback, false);
        if (api == null || oidcIssuer == null || authorization == null || token == null || redirect == null) {
            return null;
        }
        String expectedCallback = apiOrigin + "/native/oauth/callback";
        if (!expectedCallback.equals(callback)) return null;
        if (!sameAuthority(oidcIssuer, authorization) || !sameAuthority(oidcIssuer, token)) return null;
        String issuerPath = normalizedPath(oidcIssuer.getRawPath());
        String authPath = normalizedPath(authorization.getRawPath());
        String tokenPath = normalizedPath(token.getRawPath());
        String prefix = issuerPath.isEmpty() ? "/" : issuerPath + "/";
        if (!authPath.startsWith(prefix) || !tokenPath.startsWith(prefix)) return null;
        if (authPath.equals(issuerPath) || tokenPath.equals(issuerPath)) return null;
        return new NativeRuntimeConfig(
                apiOrigin, issuer, authorizationEndpoint, tokenEndpoint, clientId, audience, callback,
                api, oidcIssuer, authorization, token);
    }

    public boolean isConfigured() {
        return apiUri != null && issuerUri != null && authorizationUri != null && tokenUri != null;
    }

    public boolean isOAuthEndpoint(String value) {
        URI candidate = strictHttps(value, false);
        return candidate != null && (sameUri(candidate, authorizationUri) || sameUri(candidate, tokenUri));
    }

    public URI apiUri() { return apiUri; }
    public URI issuerUri() { return issuerUri; }
    public URI authorizationUri() { return authorizationUri; }
    public URI tokenUri() { return tokenUri; }

    @Override public String toString() {
        return "NativeRuntimeConfig{configured=" + isConfigured() + ", endpoints=[redacted]}";
    }

    private static boolean shortValue(String value) {
        return value != null && !NativeText.isBlank(value) && value.length() <= MAX_PUBLIC_VALUE
                && value.chars().noneMatch(character -> Character.isISOControl(character) || Character.isWhitespace(character));
    }

    private static URI strictHttps(String raw, boolean originOnly) {
        if (raw == null || NativeText.isBlank(raw) || raw.length() > MAX_PUBLIC_VALUE
                || raw.chars().anyMatch(Character::isISOControl) || raw.contains("\\")) return null;
        try {
            URI uri = new URI(raw);
            if (!"https".equalsIgnoreCase(uri.getScheme()) || uri.getHost() == null
                    || uri.getRawUserInfo() != null || uri.getRawQuery() != null || uri.getRawFragment() != null
                    || !uri.normalize().equals(uri) || NativeText.isBlank(uri.getHost())) return null;
            if (uri.getPort() == 0 || uri.getPort() > 65535) return null;
            String path = uri.getRawPath() == null ? "" : uri.getRawPath();
            if (path.contains("%") || path.contains("//")) return null;
            if (originOnly && !path.isEmpty()) return null;
            return uri;
        } catch (URISyntaxException | IllegalArgumentException ignored) {
            return null;
        }
    }

    private static boolean sameAuthority(URI left, URI right) {
        return left != null && right != null
                && "https".equalsIgnoreCase(left.getScheme())
                && "https".equalsIgnoreCase(right.getScheme())
                && left.getHost().toLowerCase(Locale.ROOT).equals(right.getHost().toLowerCase(Locale.ROOT))
                && effectivePort(left) == effectivePort(right);
    }

    private static boolean sameUri(URI left, URI right) {
        return left != null && right != null && left.equals(right);
    }

    private static int effectivePort(URI uri) {
        return uri.getPort() < 0 ? 443 : uri.getPort();
    }

    private static String normalizedPath(String path) {
        if (path == null || path.isEmpty() || "/".equals(path)) return "";
        int end = path.length();
        while (end > 0 && path.charAt(end - 1) == '/') end--;
        return path.substring(0, end);
    }
}
