package com.lucas.pulse.nativebridge;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class NativeRuntimeConfigTest {
    private static final String API = "https://pulse.example.test";
    private static final String ISSUER = "https://id.example.test/tenant";
    private static final String AUTH = ISSUER + "/oauth2/authorize";
    private static final String TOKEN = ISSUER + "/oauth2/token";
    private static final String CLIENT = "pulse-mobile-public";
    private static final String AUDIENCE = "https://pulse.example.test/native";
    private static final String CALLBACK = API + "/native/oauth/callback";

    @Test public void emptyOrPartialConfigStaysClosed() {
        assertNull(config("", ISSUER, AUTH, TOKEN, CLIENT, AUDIENCE, CALLBACK));
        assertNull(config(API, ISSUER, AUTH, TOKEN, CLIENT, "", CALLBACK));
        assertNull(config(API, ISSUER, AUTH, TOKEN, " ", AUDIENCE, CALLBACK));
    }

    @Test public void acceptsOnlyCompletePublicHttpsEndpoints() {
        NativeRuntimeConfig config = config(API, ISSUER, AUTH, TOKEN, CLIENT, AUDIENCE, CALLBACK);
        assertNotNull(config);
        assertTrue(config.isConfigured());
        assertTrue(config.isOAuthEndpoint(AUTH));
        assertTrue(config.isOAuthEndpoint(TOKEN));
        assertFalse(config.isOAuthEndpoint(TOKEN + "/redirect"));
        assertFalse(config.toString().contains(CLIENT));
        assertFalse(config.toString().contains(AUDIENCE));
    }

    @Test public void rejectsInsecureOrMisdirectedEndpoints() {
        assertNull(config("http://pulse.example.test", ISSUER, AUTH, TOKEN, CLIENT, AUDIENCE,
                "http://pulse.example.test/native/oauth/callback"));
        assertNull(config(API, ISSUER, AUTH, "https://other.example.test/token", CLIENT, AUDIENCE, CALLBACK));
        assertNull(config(API, ISSUER, "https://other.example.test/authorize", TOKEN, CLIENT, AUDIENCE, CALLBACK));
        assertNull(config(API, ISSUER, ISSUER + "/../authorize", TOKEN, CLIENT, AUDIENCE, CALLBACK));
        assertNull(config(API + "/path", ISSUER, AUTH, TOKEN, CLIENT, AUDIENCE,
                API + "/path/native/oauth/callback"));
        assertNull(config(API, ISSUER, AUTH, TOKEN, CLIENT, AUDIENCE, API + "/wrong/callback"));
        assertNull(config(API, ISSUER, AUTH + "?next=https://elsewhere", TOKEN, CLIENT, AUDIENCE, CALLBACK));
        assertNull(config(API, ISSUER, AUTH, TOKEN, "bad\nclient", AUDIENCE, CALLBACK));
    }

    private static NativeRuntimeConfig config(String api, String issuer, String auth, String token,
            String client, String audience, String callback) {
        return NativeRuntimeConfig.fromValues(api, issuer, auth, token, client, audience, callback);
    }
}
