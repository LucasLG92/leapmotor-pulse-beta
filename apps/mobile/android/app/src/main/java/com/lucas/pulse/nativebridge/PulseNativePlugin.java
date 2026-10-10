package com.lucas.pulse.nativebridge;

import android.appwidget.AppWidgetManager;
import android.content.ComponentName;
import android.content.Intent;
import android.os.Build;
import android.os.SystemClock;
import android.util.Base64;

import androidx.activity.result.ActivityResult;

import com.getcapacitor.annotation.ActivityCallback;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.lucas.pulse.widget.PulseWidgetReceiver;
import com.lucas.pulse.widget.WidgetSnapshotCache;
import com.lucas.pulse.widget.WidgetUpdateBridge;

import net.openid.appauth.AppAuthConfiguration;
import net.openid.appauth.AuthorizationException;
import net.openid.appauth.AuthorizationRequest;
import net.openid.appauth.AuthorizationResponse;
import net.openid.appauth.AuthorizationService;
import net.openid.appauth.AuthorizationServiceConfiguration;
import net.openid.appauth.ResponseTypeValues;
import net.openid.appauth.TokenRequest;
import net.openid.appauth.TokenResponse;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.Set;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.Future;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;

import javax.net.ssl.HttpsURLConnection;

/** Capacitor bridge for the same Pulse UI, backed by private in-memory BFF credentials. */
@CapacitorPlugin(name = "PulseNative")
public final class PulseNativePlugin extends Plugin {
    private static final long MAX_SAFE_JS_INTEGER = 9_007_199_254_740_991L;
    private static final int MAX_SSE_LINE_BYTES = 8 * 1024;
    private static final String BUSY_JSON = "{\"detail\":\"Hay demasiadas solicitudes. Reintenta.\"}";

    private final Object authLock = new Object();
    private final Object streamLock = new Object();
    private final NativeSessionState sessions = new NativeSessionState();
    private ThreadPoolExecutor io;
    private NativeRuntimeConfig runtimeConfig;
    private NativeHttpClient http;
    private NativeWidgetCredentialVault widgetVault;
    private WidgetSnapshotCache widgetCache;
    private PendingAuth pendingAuth;

    private long latestSubscriptionNumber;
    private long streamGeneration;
    private String activeSubscriptionId;
    private boolean eventsDesired;
    private boolean eventsPaused;
    private Future<?> eventFuture;
    private HttpsURLConnection eventConnection;

    @Override public void load() {
        runtimeConfig = NativeRuntimeConfig.fromBuildConfig();
        if (runtimeConfig != null) http = new NativeHttpClient(runtimeConfig);
        widgetVault = new NativeWidgetCredentialVault(getContext());
        widgetCache = new WidgetSnapshotCache(getContext());
        ThreadFactory factory = command -> {
            Thread thread = new Thread(() -> {
                try { command.run(); } catch (Throwable ignored) { }
            }, "pulse-native-io");
            thread.setDaemon(true);
            return thread;
        };
        io = new ThreadPoolExecutor(2, 2, 0L, TimeUnit.MILLISECONDS,
                new ArrayBlockingQueue<>(8), factory, new ThreadPoolExecutor.AbortPolicy());
        scheduleWidgetRefreshIfReady();
    }

    @PluginMethod public void status(PluginCall call) {
        if (!submit(call, "status", () -> {
            NativeRuntimeConfig config = runtimeConfig;
            boolean configured = config != null && config.isConfigured();
            boolean authenticated = sessions.authenticated(SystemClock.elapsedRealtime());
            boolean linked = false;
            boolean cacheEnabled;
            boolean updateWidget = false;
            synchronized (NativeWidgetOperations.LOCK) {
                if (configured && widgetVault.credentialExpired(config.apiOrigin)) {
                    NativeWidgetOperations.retireLocked(getContext(), widgetVault, widgetCache);
                    updateWidget = true;
                }
                NativeWidgetCredentialVault.NativeWidgetCredential credential =
                        configured ? widgetVault.credential(config.apiOrigin) : null;
                cacheEnabled = widgetCache.isEnabled();
                if (cacheEnabled && credential == null) {
                    NativeWidgetOperations.retireLocked(getContext(), widgetVault, widgetCache);
                    cacheEnabled = false;
                    updateWidget = true;
                }
                linked = configured && credential != null;
            }
            if (updateWidget) requestWidgetUpdate();
            JSObject result = new JSObject();
            result.put("configured", configured);
            result.put("authenticated", authenticated);
            result.put("widgetLinked", linked);
            result.put("widgetCacheEnabled", cacheEnabled);
            call.resolve(result);
        })) call.reject("Hay demasiadas solicitudes. Reintenta.", "NATIVE_BUSY");
    }

    @PluginMethod public void login(PluginCall call) {
        startAuthorization(call, NativeAuthTransaction.Purpose.APP, null, null);
    }

    @PluginMethod public void request(PluginCall call) {
        String path = call.getString("path");
        String method = call.getString("method");
        String body = call.getString("body");
        if (path == null || method == null || !NativeApiPolicy.allows(path, method)) {
            resolveResponse(call, 400, "application/json", "{\"detail\":\"La solicitud nativa no es válida.\"}");
            return;
        }
        if ("POST".equals(method) && "/api/auth/logout".equals(path)) {
            performLogout(call, true);
            return;
        }
        NativeSessionState.SessionSnapshot captured = sessions.snapshot(SystemClock.elapsedRealtime());
        if (captured == null) {
            resolveResponse(call, 401, "application/json", "{\"detail\":\"Inicia sesión para continuar.\"}");
            return;
        }
        if (!submit(call, "request", () -> {
            if (!sessions.isCurrent(captured.generation, SystemClock.elapsedRealtime())) {
                resolveResponse(call, 401, "application/json", "{\"detail\":\"La sesión ha caducado. Vuelve a iniciar sesión.\"}");
                return;
            }
            try {
                NativeHttpClient.NativeResponse response = http.request(method, path, body, captured);
                if (!sessions.isCurrent(captured.generation, SystemClock.elapsedRealtime())) {
                    if (NativeRequestPolicy.mutationOutcomeUnknown(method, path, true, false)) {
                        call.reject("La sesión cambió mientras se completaba la operación.",
                                "NATIVE_MUTATION_OUTCOME_UNKNOWN");
                    } else {
                        resolveResponse(call, 401, "application/json", "{\"detail\":\"La sesión ha caducado. Vuelve a iniciar sesión.\"}");
                    }
                    return;
                }
                if (NativeRequestPolicy.shouldMaskSessionCsrf(method, path, response.status)) {
                    try {
                        JSONObject sessionBody = new JSONObject(response.body);
                        Object rawCsrf = sessionBody.opt("csrf_token");
                        if (!(rawCsrf instanceof String)
                                || !NativeSessionState.validCsrf((String) rawCsrf)) {
                            resolveResponse(call, 502, "application/json",
                                    "{\"detail\":\"Pulse devolvió una sesión no válida.\"}");
                            return;
                        }
                        if (!sessions.updateCsrf((String) rawCsrf, captured.generation,
                                SystemClock.elapsedRealtime())) {
                            resolveResponse(call, 401, "application/json",
                                    "{\"detail\":\"La sesión ha caducado. Vuelve a iniciar sesión.\"}");
                            return;
                        }
                        sessionBody.put("csrf_token", NativeRequestPolicy.RENDERER_CSRF_MARKER);
                        response = new NativeHttpClient.NativeResponse(
                                response.status, response.contentType, sessionBody.toString());
                    } catch (JSONException ignored) {
                        resolveResponse(call, 502, "application/json",
                                "{\"detail\":\"Pulse devolvió una sesión no válida.\"}");
                        return;
                    }
                }
                resolveResponse(call, response.status, response.contentType, response.body);
            } catch (NativeHttpClient.NativeTransportException error) {
                if (error.status == 0 && NativeRequestPolicy.mutationOutcomeUnknown(
                        method, path, false, sessions.isCurrent(captured.generation, SystemClock.elapsedRealtime()))) {
                    call.reject("La sesión cambió mientras se completaba la operación.",
                            "NATIVE_MUTATION_OUTCOME_UNKNOWN");
                } else if (error.status == 0) call.reject(error.safeMessage, "NATIVE_TRANSPORT");
                else resolveResponse(call, error.status, "application/json", safeDetailBody(error.safeMessage));
            } catch (Exception ignored) {
                call.reject("No se pudo completar la solicitud nativa.", "NATIVE_REQUEST");
            }
        })) resolveResponse(call, 429, "application/json", BUSY_JSON);
    }

    @PluginMethod public void logout(PluginCall call) {
        performLogout(call, false);
    }

    @PluginMethod public void linkWidget(PluginCall call) {
        NativeRuntimeConfig config = runtimeConfig;
        if (config == null || !config.isConfigured()) {
            call.reject("La app nativa no está configurada.", "NATIVE_NOT_CONFIGURED");
            return;
        }
        if (sessions.snapshot(SystemClock.elapsedRealtime()) == null) {
            call.reject("Inicia sesión antes de enlazar el widget.", "NATIVE_UNAUTHENTICATED");
            return;
        }
        String pairingCode = call.getString("pairingCode");
        String deviceName = call.getString("deviceName");
        if (pairingCode == null || !pairingCode.matches("[A-Za-z0-9_-]{43}")
                || deviceName == null || !validDeviceName(deviceName)) {
            call.reject("Los datos del widget no son válidos.", "NATIVE_WIDGET_INPUT");
            return;
        }
        // Capacitor retains PluginCall data while the system browser is open. Remove the
        // one-time pairing secret before it can enter a saved activity bundle.
        call.getData().remove("pairingCode");
        startAuthorization(call, NativeAuthTransaction.Purpose.WIDGET, pairingCode, deviceName.trim());
    }

    @PluginMethod public void setWidgetCache(PluginCall call) {
        Object value = call.getData().opt("enabled");
        if (!(value instanceof Boolean)) {
            call.reject("La preferencia de caché no es válida.", "NATIVE_WIDGET_INPUT");
            return;
        }
        boolean enabled = (Boolean) value;
        if (!submit(call, "widget cache", () -> {
            try {
                synchronized (NativeWidgetOperations.LOCK) {
                    NativeRuntimeConfig config = runtimeConfig;
                    if (enabled) {
                        if (config == null || !config.isConfigured()) {
                            throw NativeHttpClient.NativeTransportException.widget();
                        }
                        if (widgetVault.credentialExpired(config.apiOrigin)) {
                            NativeWidgetOperations.retireLocked(getContext(), widgetVault, widgetCache);
                            throw NativeHttpClient.NativeTransportException.widget();
                        }
                        if (widgetVault.credential(config.apiOrigin) == null) {
                            NativeWidgetOperations.retireLocked(getContext(), widgetVault, widgetCache);
                            throw NativeHttpClient.NativeTransportException.widget();
                        }
                    }
                    boolean wasEnabled = widgetCache.isEnabled();
                    if (wasEnabled != enabled) NativeWidgetOperations.advanceLocked();
                    widgetCache.setEnabled(enabled);
                    if (enabled) {
                        NativeWidgetRefreshScheduler.schedulePeriodic(getContext());
                        NativeWidgetRefreshScheduler.scheduleImmediate(getContext());
                    } else {
                        NativeWidgetRefreshScheduler.cancelAll(getContext());
                    }
                }
                call.resolve();
            } catch (Exception ignored) {
                synchronized (NativeWidgetOperations.LOCK) {
                    NativeWidgetOperations.advanceLocked();
                    try { widgetCache.disableAndClear(); } catch (Exception ignoredAgain) { }
                    NativeWidgetRefreshScheduler.cancelAll(getContext());
                }
                call.reject("No se pudo guardar la preferencia de caché.", "NATIVE_WIDGET_CACHE");
            } finally {
                requestWidgetUpdate();
            }
        })) call.reject("Hay demasiadas solicitudes. Reintenta.", "NATIVE_BUSY");
    }

    @PluginMethod public void clearWidget(PluginCall call) {
        boolean localClearSucceeded = true;
        NativeWidgetCredentialVault.NativeWidgetCredential credential;
        synchronized (NativeWidgetOperations.LOCK) {
            NativeWidgetOperations.advanceLocked();
            NativeRuntimeConfig config = runtimeConfig;
            credential = config == null ? null : widgetVault.credential(config.apiOrigin);
            try { widgetVault.clear(); } catch (Exception ignored) { localClearSucceeded = false; }
            try { widgetCache.disableAndClear(); } catch (Exception ignored) { localClearSucceeded = false; }
            NativeWidgetRefreshScheduler.cancelAll(getContext());
        }
        requestWidgetUpdate();
        final boolean finalLocalClearSucceeded = localClearSucceeded;
        if (!submit(call, "clear widget", () -> {
            if (credential != null && http != null) http.revokeWidget(credential);
            if (finalLocalClearSucceeded) call.resolve();
            else call.reject("No se pudo borrar la caché del widget.", "NATIVE_WIDGET_CACHE");
        })) {
            if (finalLocalClearSucceeded) call.resolve();
            else call.reject("No se pudo borrar la caché del widget.", "NATIVE_WIDGET_CACHE");
        }
    }

    @PluginMethod public void pinWidget(PluginCall call) {
        boolean supported = false;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            AppWidgetManager manager = AppWidgetManager.getInstance(getContext());
            supported = manager.isRequestPinAppWidgetSupported();
            if (supported) {
                supported = manager.requestPinAppWidget(
                        new ComponentName(getContext(), PulseWidgetReceiver.class), null, null);
            }
        }
        JSObject result = new JSObject();
        result.put("supported", supported);
        call.resolve(result);
    }

    @PluginMethod public void startEvents(PluginCall call) {
        String subscriptionId = call.getString("subscriptionId");
        Long subscriptionNumber = parseSubscriptionId(subscriptionId);
        if (subscriptionNumber == null) {
            call.reject("La suscripción de eventos no es válida.", "NATIVE_EVENT_ID");
            return;
        }
        NativeSessionState.SessionSnapshot session = sessions.snapshot(SystemClock.elapsedRealtime());
        if (session == null || runtimeConfig == null || http == null) {
            call.reject("Inicia sesión para recibir actualizaciones.", "NATIVE_UNAUTHENTICATED");
            return;
        }
        synchronized (streamLock) {
            if (subscriptionNumber <= latestSubscriptionNumber) {
                call.reject("La suscripción de eventos ya ha caducado.", "NATIVE_EVENT_STALE");
                return;
            }
            latestSubscriptionNumber = subscriptionNumber;
            activeSubscriptionId = subscriptionId;
            eventsDesired = true;
            eventsPaused = false;
            stopStreamLocked();
            long generation = ++streamGeneration;
            try {
                eventFuture = io.submit(() -> runEventStream(subscriptionId, generation, session));
                call.resolve();
            } catch (RejectedExecutionException ignored) {
                activeSubscriptionId = null;
                eventsDesired = false;
                eventFuture = null;
                call.reject("Hay demasiadas solicitudes. Reintenta.", "NATIVE_BUSY");
            }
        }
    }

    @PluginMethod public void stopEvents(PluginCall call) {
        String subscriptionId = call.getString("subscriptionId");
        synchronized (streamLock) {
            if (subscriptionId != null && subscriptionId.equals(activeSubscriptionId)) {
                eventsDesired = false;
                eventsPaused = false;
                activeSubscriptionId = null;
                streamGeneration++;
                stopStreamLocked();
            }
        }
        call.resolve();
    }

    @ActivityCallback public void authorizationResult(PluginCall call, ActivityResult result) {
        PendingAuth pending;
        synchronized (authLock) { pending = pendingAuth; }
        if (pending == null || call == null) return;
        Intent data = result == null ? null : result.getData();
        AuthorizationException exception = data == null ? null : AuthorizationException.fromIntent(data);
        AuthorizationResponse response = data == null ? null : AuthorizationResponse.fromIntent(data);
        if (exception != null || response == null || !validAuthorizationResponse(pending, response)
                || !pending.transaction.consume(response.state, pending.purpose,
                SystemClock.elapsedRealtime(), sessions.generation(SystemClock.elapsedRealtime()))) {
            finishAuthorization(pending, "No se pudo completar la autenticación.", "NATIVE_AUTH_FAILED");
            return;
        }
        synchronized (authLock) {
            if (pendingAuth != pending || pending.exchangeStarted) {
                finishAuthorization(pending, "La autenticación ya no está activa.", "NATIVE_AUTH_STALE");
                return;
            }
            pending.exchangeStarted = true;
        }
        try {
            NativeAppAuthLogging.ensureSilent();
            TokenRequest tokenRequest = response.createTokenExchangeRequest();
            pending.service.performTokenRequest(tokenRequest, (tokenResponse, tokenError) -> {
                if (tokenError != null || tokenResponse == null || tokenResponse.accessToken == null
                        || NativeText.isBlank(tokenResponse.accessToken) || tokenResponse.accessToken.length() > 4096) {
                    finishAuthorization(pending, "No se pudo completar la autenticación.", "NATIVE_AUTH_FAILED");
                    return;
                }
                String accessToken = tokenResponse.accessToken;
                if (pending.purpose == NativeAuthTransaction.Purpose.APP) {
                    submitAuthWork(pending, () -> completeAppLogin(pending, accessToken));
                } else {
                    submitAuthWork(pending, () -> completeWidgetLink(pending, accessToken));
                }
            });
        } catch (Exception ignored) {
            finishAuthorization(pending, "No se pudo completar la autenticación.", "NATIVE_AUTH_FAILED");
        }
    }

    @Override protected void handleOnPause() {
        synchronized (streamLock) {
            if (eventsDesired && activeSubscriptionId != null) {
                eventsPaused = true;
                streamGeneration++;
                stopStreamLocked();
            }
        }
    }

    @Override protected void handleOnResume() {
        synchronized (streamLock) {
            if (eventsDesired && eventsPaused && activeSubscriptionId != null && http != null) {
                NativeSessionState.SessionSnapshot session = sessions.snapshot(SystemClock.elapsedRealtime());
                if (session == null) {
                    String expiredId = activeSubscriptionId;
                    eventsDesired = false;
                    activeSubscriptionId = null;
                    notifyStreamErrorLocked(expiredId);
                } else {
                    eventsPaused = false;
                    long generation = ++streamGeneration;
                    try {
                        String id = activeSubscriptionId;
                        eventFuture = io.submit(() -> runEventStream(id, generation, session));
                    } catch (RejectedExecutionException ignored) {
                        notifyStreamErrorLocked(activeSubscriptionId);
                    }
                }
            }
        }
        scheduleWidgetRefreshIfReady();
    }

    @Override protected void handleOnDestroy() {
        synchronized (streamLock) {
            eventsDesired = false;
            activeSubscriptionId = null;
            streamGeneration++;
            stopStreamLocked();
        }
        synchronized (authLock) {
            if (pendingAuth != null) {
                try { pendingAuth.service.dispose(); } catch (Exception ignored) { }
                pendingAuth = null;
            }
        }
        if (io != null) io.shutdownNow();
    }

    private void startAuthorization(PluginCall call, NativeAuthTransaction.Purpose purpose,
            String pairingCode, String deviceName) {
        NativeRuntimeConfig config = runtimeConfig;
        if (config == null || !config.isConfigured() || http == null) {
            call.reject("La app nativa no está configurada.", "NATIVE_NOT_CONFIGURED");
            return;
        }
        long now = SystemClock.elapsedRealtime();
        long authGeneration = sessions.generation(now);
        NativeSessionState.SessionSnapshot previousSession = purpose == NativeAuthTransaction.Purpose.APP
                ? sessions.snapshot(now) : null;
        long widgetOperationGeneration = -1L;
        if (purpose == NativeAuthTransaction.Purpose.WIDGET && !sessions.authenticated(now)) {
            call.reject("Inicia sesión antes de enlazar el widget.", "NATIVE_UNAUTHENTICATED");
            return;
        }
        if (purpose == NativeAuthTransaction.Purpose.WIDGET) {
            synchronized (NativeWidgetOperations.LOCK) {
                if (runtimeConfig != null && widgetVault.credentialExpired(runtimeConfig.apiOrigin)) {
                    NativeWidgetOperations.retireLocked(getContext(), widgetVault, widgetCache);
                    requestWidgetUpdate();
                }
                widgetOperationGeneration = NativeWidgetOperations.generationLocked();
            }
        }
        synchronized (authLock) {
            if (pendingAuth != null) {
                call.reject("Ya hay una autenticación en curso.", "NATIVE_AUTH_BUSY");
                return;
            }
            try {
                NativeAppAuthLogging.ensureSilent();
                NativeAuthTransaction transaction = NativeAuthTransaction.begin(purpose, now, authGeneration);
                AuthorizationServiceConfiguration serviceConfig = new AuthorizationServiceConfiguration(
                        android.net.Uri.parse(config.authorizationEndpoint), android.net.Uri.parse(config.tokenEndpoint));
                AppAuthConfiguration appAuthConfig = new AppAuthConfiguration.Builder()
                        .setConnectionBuilder(new NativeOAuthConnectionBuilder(config)).build();
                AuthorizationService service = new AuthorizationService(getContext().getApplicationContext(), appAuthConfig);
                String[] requestedScopes = purpose == NativeAuthTransaction.Purpose.APP
                        ? new String[] {"openid", "profile", "pulse.app.access"}
                        : new String[] {"openid", "pulse.widget.read"};
                AuthorizationRequest request = new AuthorizationRequest.Builder(
                        serviceConfig, config.clientId, ResponseTypeValues.CODE, android.net.Uri.parse(config.callback))
                        .setScopes(requestedScopes)
                        .setState(transaction.stateForRequest())
                        .setAdditionalParameters(Collections.singletonMap("audience", config.audience))
                        .build();
                if (request.codeVerifier == null || !"S256".equals(request.codeVerifierChallengeMethod)
                        || request.codeVerifier.length() < 43 || request.codeVerifier.length() > 128) {
                    service.dispose();
                    call.reject("La autenticación segura no está disponible.", "NATIVE_PKCE_UNAVAILABLE");
                    return;
                }
                PendingAuth pending = new PendingAuth(call, purpose, transaction, request, service,
                        pairingCode, deviceName, authGeneration, previousSession, widgetOperationGeneration);
                pendingAuth = pending;
                try {
                    Intent intent = service.getAuthorizationRequestIntent(request);
                    startActivityForResult(call, intent, "authorizationResult");
                } catch (Exception ignored) {
                    pendingAuth = null;
                    service.dispose();
                    call.reject("No se pudo abrir el acceso seguro.", "NATIVE_AUTH_BROWSER");
                }
            } catch (Exception ignored) {
                pendingAuth = null;
                call.reject("No se pudo iniciar la autenticación segura.", "NATIVE_AUTH_FAILED");
            }
        }
    }

    private boolean validAuthorizationResponse(PendingAuth pending, AuthorizationResponse response) {
        try {
            NativeRuntimeConfig config = runtimeConfig;
            AuthorizationRequest request = response.request;
            if (config == null || request == null || response.authorizationCode == null
                    || NativeText.isBlank(response.authorizationCode) || response.authorizationCode.length() > 4096
                    || !config.clientId.equals(request.clientId)
                    || !android.net.Uri.parse(config.callback).equals(request.redirectUri)
                    || !"code".equals(request.responseType)
                    || !pending.transaction.stateForRequest().equals(response.state)
                    || !"S256".equals(request.codeVerifierChallengeMethod)
                    || request.codeVerifier == null || request.codeVerifier.length() < 43
                    || !config.authorizationEndpoint.equals(request.configuration.authorizationEndpoint.toString())
                    || !config.tokenEndpoint.equals(request.configuration.tokenEndpoint.toString())) return false;
            Set<String> expected = pending.purpose == NativeAuthTransaction.Purpose.APP
                    ? new HashSet<>(Arrays.asList("openid", "profile", "pulse.app.access"))
                    : new HashSet<>(Arrays.asList("openid", "pulse.widget.read"));
            Set<String> actual = request.scope == null ? Collections.emptySet()
                    : new HashSet<>(Arrays.asList(request.scope.trim().split("\\s+")));
            return expected.equals(actual)
                    && config.audience.equals(request.additionalParameters.get("audience"));
        } catch (Exception ignored) {
            return false;
        }
    }

    private void completeAppLogin(PendingAuth pending, String accessToken) {
        try {
            if (!sessions.isGenerationCurrent(pending.authGeneration, SystemClock.elapsedRealtime())) {
                finishAuthorization(pending, "La autenticación ya no está activa.", "NATIVE_AUTH_STALE");
                return;
            }
            NativeHttpClient.BootstrapResult bootstrap = http.bootstrap(accessToken);
            if (!sessions.install(bootstrap.cookie, bootstrap.csrf, bootstrap.ttlSeconds,
                    SystemClock.elapsedRealtime(), pending.authGeneration)) {
                http.logout(bootstrap);
                finishAuthorization(pending, "La autenticación ya no está activa.", "NATIVE_AUTH_STALE");
                return;
            }
            if (pending.previousSession != null) http.logout(pending.previousSession);
            finishAuthorization(pending, null, null);
        } catch (NativeHttpClient.NativeTransportException ignored) {
            finishAuthorization(pending, "No se pudo iniciar sesión en Pulse.", "NATIVE_BOOTSTRAP_FAILED");
        } catch (Exception ignored) {
            finishAuthorization(pending, "No se pudo iniciar sesión en Pulse.", "NATIVE_BOOTSTRAP_FAILED");
        }
    }

    private void completeWidgetLink(PendingAuth pending, String accessToken) {
        NativeHttpClient.WidgetGrantResult grant = null;
        boolean newGrantStored = false;
        NativeWidgetCredentialVault.NativeWidgetCredential previousCredential = null;
        long linkGeneration = -1L;
        try {
            if (!sessions.isCurrent(pending.authGeneration, SystemClock.elapsedRealtime())) {
                finishAuthorization(pending, "La sesión de Pulse ha caducado.", "NATIVE_UNAUTHENTICATED");
                return;
            }
            final String installationId;
            final boolean cacheOptedIn;
            synchronized (NativeWidgetOperations.LOCK) {
                if (!NativeWidgetOperations.isGenerationCurrent(pending.widgetOperationGeneration)
                        || runtimeConfig == null || widgetVault.credentialExpired(runtimeConfig.apiOrigin)) {
                    finishAuthorization(pending, "El enlace del widget ya no está activo.", "NATIVE_AUTH_STALE");
                    return;
                }
                installationId = widgetVault.installationId();
                if (installationId == null) throw NativeHttpClient.NativeTransportException.widget();
                previousCredential = widgetVault.credential(runtimeConfig.apiOrigin);
                cacheOptedIn = widgetCache.isEnabled();
                NativeWidgetOperations.advanceLocked();
                linkGeneration = NativeWidgetOperations.generationLocked();
                if (cacheOptedIn) widgetCache.clear();
            }
            if (cacheOptedIn) requestWidgetUpdate();
            grant = http.registerWidget(accessToken, pending.pairingCode, installationId, pending.deviceName);
            boolean stale = !sessions.isCurrent(pending.authGeneration, SystemClock.elapsedRealtime());
            synchronized (NativeWidgetOperations.LOCK) {
                stale = stale || NativeWidgetOperations.generationLocked() != linkGeneration;
                if (!stale && !widgetVault.store(runtimeConfig.apiOrigin, grant.grantId,
                        grant.widgetToken, grant.expiresAtEpochMillis)) {
                    stale = true;
                }
                if (!stale) {
                    NativeWidgetCredentialVault.NativeWidgetCredential stored =
                            widgetVault.credential(runtimeConfig.apiOrigin);
                    if (stored == null) {
                        stale = true;
                    } else {
                        newGrantStored = true;
                        NativeWidgetOperations.advanceLocked();
                        if (widgetCache.isEnabled()) {
                            NativeWidgetRefreshScheduler.schedulePeriodic(getContext());
                            NativeWidgetRefreshScheduler.scheduleImmediate(getContext());
                        } else {
                            NativeWidgetRefreshScheduler.cancelAll(getContext());
                        }
                    }
                }
            }
            if (stale) {
                http.revokeWidget(grant.grantId, grant.widgetToken);
                scheduleWidgetRefreshIfReady();
                finishAuthorization(pending, "El enlace del widget ya no está activo.", "NATIVE_AUTH_STALE");
                requestWidgetUpdate();
                return;
            }
            if (previousCredential != null && !previousCredential.grantId.equals(grant.grantId)) {
                http.revokeWidget(previousCredential);
            }
            requestWidgetUpdate();
            finishAuthorization(pending, null, null);
        } catch (NativeHttpClient.NativeTransportException ignored) {
            if (grant != null && !newGrantStored) http.revokeWidget(grant.grantId, grant.widgetToken);
            scheduleWidgetRefreshIfReady();
            requestWidgetUpdate();
            finishAuthorization(pending, "No se pudo enlazar el widget. Comprueba el código.", "NATIVE_WIDGET_LINK_FAILED");
        } catch (Exception ignored) {
            if (grant != null && !newGrantStored) http.revokeWidget(grant.grantId, grant.widgetToken);
            scheduleWidgetRefreshIfReady();
            requestWidgetUpdate();
            finishAuthorization(pending, "No se pudo enlazar el widget. Comprueba el código.", "NATIVE_WIDGET_LINK_FAILED");
        }
    }

    private void submitAuthWork(PendingAuth pending, Runnable work) {
        try {
            io.execute(() -> {
                if (!isPending(pending)) return;
                try { work.run(); }
                catch (Throwable ignored) {
                    finishAuthorization(pending, "No se pudo completar la autenticación.", "NATIVE_AUTH_FAILED");
                }
            });
        } catch (RejectedExecutionException ignored) {
            finishAuthorization(pending, "Hay demasiadas solicitudes. Reintenta.", "NATIVE_BUSY");
        }
    }

    private boolean isPending(PendingAuth pending) {
        synchronized (authLock) { return pendingAuth == pending; }
    }

    private void finishAuthorization(PendingAuth pending, String message, String code) {
        synchronized (authLock) {
            if (pendingAuth != pending) return;
            pendingAuth = null;
        }
        try { pending.service.dispose(); } catch (Exception ignored) { }
        if (message == null) pending.call.resolve();
        else pending.call.reject(message, code);
    }

    private void performLogout(PluginCall call, boolean responseMode) {
        NativeSessionState.SessionSnapshot session = sessions.snapshot(SystemClock.elapsedRealtime());
        sessions.invalidate();
        boolean localClearSucceeded = true;
        NativeWidgetCredentialVault.NativeWidgetCredential credential;
        synchronized (NativeWidgetOperations.LOCK) {
            NativeWidgetOperations.advanceLocked();
            NativeRuntimeConfig config = runtimeConfig;
            credential = config == null ? null : widgetVault.credential(config.apiOrigin);
            try { widgetVault.clear(); } catch (Exception ignored) { localClearSucceeded = false; }
            try { widgetCache.disableAndClear(); } catch (Exception ignored) { localClearSucceeded = false; }
            NativeWidgetRefreshScheduler.cancelAll(getContext());
        }
        requestWidgetUpdate();
        cancelPendingAuthorization();
        stopAllEvents();
        final boolean finalLocalClearSucceeded = localClearSucceeded;
        Runnable cleanup = () -> {
            if (session != null && http != null) http.logout(session);
            if (credential != null && http != null) http.revokeWidget(credential);
            if (finalLocalClearSucceeded) {
                if (responseMode) resolveResponse(call, 200, "application/json", "{\"ok\":true}");
                else call.resolve();
            } else if (responseMode) {
                resolveResponse(call, 500, "application/json", "{\"detail\":\"No se pudo borrar la caché del widget.\"}");
            } else {
                call.reject("No se pudo borrar la caché del widget.", "NATIVE_WIDGET_CACHE");
            }
        };
        try {
            io.execute(() -> { try { cleanup.run(); } catch (Throwable ignored) { cleanupResult(call, responseMode, finalLocalClearSucceeded); } });
        } catch (RejectedExecutionException ignored) {
            cleanupResult(call, responseMode, finalLocalClearSucceeded);
        }
    }

    private void cleanupResult(PluginCall call, boolean responseMode, boolean localClearSucceeded) {
        if (responseMode) {
            if (localClearSucceeded) resolveResponse(call, 200, "application/json", "{\"ok\":true}");
            else resolveResponse(call, 500, "application/json", "{\"detail\":\"No se pudo borrar la caché del widget.\"}");
        } else if (localClearSucceeded) call.resolve();
        else call.reject("No se pudo borrar la caché del widget.", "NATIVE_WIDGET_CACHE");
    }

    private void cancelPendingAuthorization() {
        PendingAuth pending;
        synchronized (authLock) {
            pending = pendingAuth;
            pendingAuth = null;
        }
        if (pending != null) {
            try { pending.service.dispose(); } catch (Exception ignored) { }
            pending.call.reject("La autenticación se canceló.", "NATIVE_AUTH_CANCELLED");
        }
    }

    private void stopAllEvents() {
        synchronized (streamLock) {
            eventsDesired = false;
            eventsPaused = false;
            activeSubscriptionId = null;
            streamGeneration++;
            stopStreamLocked();
        }
    }

    private void runEventStream(String subscriptionId, long generation,
            NativeSessionState.SessionSnapshot capturedSession) {
        HttpsURLConnection connection = null;
        boolean failed = false;
        try {
            boolean subscriptionCurrent = isStreamCurrent(subscriptionId, generation);
            if (!subscriptionCurrent) return;
            boolean sessionCurrent = sessions.isCurrent(
                    capturedSession.generation, SystemClock.elapsedRealtime());
            if (!sessionCurrent) {
                failed = NativeStreamPolicy.shouldReportSessionFailure(subscriptionCurrent, false);
                return;
            }
            connection = http.openEventStream(capturedSession);
            synchronized (streamLock) {
                if (!isStreamCurrentLocked(subscriptionId, generation)) {
                    connection.disconnect();
                    return;
                }
                eventConnection = connection;
            }
            if (!NativeHttpClient.isEventStreamResponse(connection)) throw new IOException();
            try (InputStream input = connection.getInputStream()) {
                ByteArrayOutputStream data = null;
                while (isStreamCurrent(subscriptionId, generation)
                        && sessions.isCurrent(capturedSession.generation, SystemClock.elapsedRealtime())) {
                    String line = readLineBounded(input);
                    if (line == null) throw new IOException();
                    if (line.isEmpty()) {
                        if (data != null) dispatchRevision(subscriptionId, generation, data.toByteArray());
                        data = null;
                        continue;
                    }
                    if (line.startsWith(":")) continue;
                    if (line.startsWith("data:")) {
                        if (data != null) throw new IOException();
                        String value = line.substring(5);
                        if (value.startsWith(" ")) value = value.substring(1);
                        byte[] bytes = value.getBytes(StandardCharsets.UTF_8);
                        if (bytes.length > MAX_SSE_LINE_BYTES) throw new IOException();
                        data = new ByteArrayOutputStream(bytes.length);
                        data.write(bytes);
                    }
                }
                if (isStreamCurrent(subscriptionId, generation)) failed = true;
            }
        } catch (Exception ignored) {
            failed = true;
        } finally {
            if (connection != null) connection.disconnect();
            synchronized (streamLock) {
                if (eventConnection == connection && streamGeneration == generation) eventConnection = null;
                if (eventFuture != null && streamGeneration == generation) eventFuture = null;
                if (failed && isStreamCurrentLocked(subscriptionId, generation)) notifyStreamErrorLocked(subscriptionId);
            }
        }
    }

    private void dispatchRevision(String subscriptionId, long generation, byte[] data) {
        if (data.length > MAX_SSE_LINE_BYTES || !isStreamCurrent(subscriptionId, generation)) return;
        try {
            JSONObject payload = new JSONObject(new String(data, StandardCharsets.UTF_8));
            Object rawRevision = payload.opt("revision");
            if (!(rawRevision instanceof Number)) return;
            Number number = (Number) rawRevision;
            long revision = number.longValue();
            if (revision < 0 || revision > MAX_SAFE_JS_INTEGER || number.doubleValue() != (double) revision) return;
            JSObject event = new JSObject();
            event.put("revision", revision);
            event.put("subscriptionId", subscriptionId);
            notifyListeners("pulseRevision", event);
        } catch (JSONException ignored) { }
    }

    private boolean isStreamCurrent(String id, long generation) {
        synchronized (streamLock) { return isStreamCurrentLocked(id, generation); }
    }

    private boolean isStreamCurrentLocked(String id, long generation) {
        return eventsDesired && !eventsPaused && id != null && id.equals(activeSubscriptionId)
                && generation == streamGeneration;
    }

    private void notifyStreamErrorLocked(String id) {
        if (id == null) return;
        JSObject event = new JSObject();
        event.put("subscriptionId", id);
        notifyListeners("pulseStreamError", event);
    }

    private void stopStreamLocked() {
        if (eventConnection != null) {
            try { eventConnection.disconnect(); } catch (Exception ignored) { }
            eventConnection = null;
        }
        if (eventFuture != null) {
            eventFuture.cancel(true);
            eventFuture = null;
        }
    }

    private static String readLineBounded(InputStream input) throws IOException {
        ByteArrayOutputStream line = new ByteArrayOutputStream(128);
        while (true) {
            int value = input.read();
            if (value < 0) return line.size() == 0 ? null : line.toString(StandardCharsets.UTF_8.name());
            if (value == '\n') {
                byte[] bytes = line.toByteArray();
                int length = bytes.length > 0 && bytes[bytes.length - 1] == '\r' ? bytes.length - 1 : bytes.length;
                return new String(bytes, 0, length, StandardCharsets.UTF_8);
            }
            if (line.size() >= MAX_SSE_LINE_BYTES) throw new IOException();
            line.write(value);
        }
    }

    private boolean submit(PluginCall call, String name, Runnable work) {
        if (io == null) {
            call.reject("La app nativa no está disponible.", "NATIVE_UNAVAILABLE");
            return false;
        }
        try {
            io.execute(() -> {
                try { work.run(); } catch (Throwable ignored) {
                    call.reject("No se pudo completar la solicitud nativa.", "NATIVE_REQUEST");
                }
            });
            return true;
        } catch (RejectedExecutionException ignored) {
            return false;
        }
    }

    private static void resolveResponse(PluginCall call, int status, String contentType, String body) {
        JSObject result = new JSObject();
        result.put("status", status);
        result.put("contentType", contentType == null ? "application/json" : contentType);
        result.put("body", body == null ? "" : body);
        call.resolve(result);
    }

    private static String safeDetailBody(String detail) {
        try { return new JSONObject().put("detail", detail).toString(); }
        catch (JSONException ignored) { return "{\"detail\":\"No se pudo completar la solicitud.\"}"; }
    }

    private static Long parseSubscriptionId(String id) {
        if (id == null || !id.matches("[1-9][0-9]{0,15}")) return null;
        try {
            long value = Long.parseLong(id);
            return value <= MAX_SAFE_JS_INTEGER ? value : null;
        } catch (NumberFormatException ignored) {
            return null;
        }
    }

    private static boolean validDeviceName(String value) {
        String name = value.trim();
        if (name.isEmpty() || name.length() > 60) return false;
        for (int index = 0; index < name.length(); index++) {
            int type = Character.getType(name.charAt(index));
            if (Character.isISOControl(name.charAt(index)) || type == Character.FORMAT) return false;
        }
        return true;
    }

    private void requestWidgetUpdate() {
        try { WidgetUpdateBridge.updateAll(getContext()); } catch (Exception ignored) { }
    }

    private void scheduleWidgetRefreshIfReady() {
        if (io == null) return;
        try {
            io.execute(() -> {
                boolean update = false;
                synchronized (NativeWidgetOperations.LOCK) {
                    NativeRuntimeConfig config = runtimeConfig;
                    if (config == null || !config.isConfigured()) {
                        NativeWidgetOperations.retireLocked(getContext(), widgetVault, widgetCache);
                        update = true;
                    } else if (widgetVault.credentialExpired(config.apiOrigin)) {
                        NativeWidgetOperations.retireLocked(getContext(), widgetVault, widgetCache);
                        update = true;
                    } else {
                        NativeWidgetCredentialVault.NativeWidgetCredential credential =
                                widgetVault.credential(config.apiOrigin);
                        boolean enabled = widgetCache.isEnabled();
                        if (enabled && credential == null) {
                            NativeWidgetOperations.retireLocked(getContext(), widgetVault, widgetCache);
                            update = true;
                        } else if (enabled) {
                            NativeWidgetRefreshScheduler.schedulePeriodic(getContext());
                            NativeWidgetRefreshScheduler.scheduleImmediate(getContext());
                        } else {
                            NativeWidgetRefreshScheduler.cancelAll(getContext());
                        }
                    }
                }
                if (update) requestWidgetUpdate();
            });
        } catch (RejectedExecutionException ignored) {
            // The next resume or periodic request will re-check the same state.
        }
    }

    private static final class PendingAuth {
        final PluginCall call;
        final NativeAuthTransaction.Purpose purpose;
        final NativeAuthTransaction transaction;
        final AuthorizationRequest request;
        final AuthorizationService service;
        final String pairingCode;
        final String deviceName;
        final long authGeneration;
        final NativeSessionState.SessionSnapshot previousSession;
        final long widgetOperationGeneration;
        boolean exchangeStarted;

        PendingAuth(PluginCall call, NativeAuthTransaction.Purpose purpose, NativeAuthTransaction transaction,
                AuthorizationRequest request, AuthorizationService service, String pairingCode,
                String deviceName, long authGeneration, NativeSessionState.SessionSnapshot previousSession,
                long widgetOperationGeneration) {
            this.call = call;
            this.purpose = purpose;
            this.transaction = transaction;
            this.request = request;
            this.service = service;
            this.pairingCode = pairingCode;
            this.deviceName = deviceName;
            this.authGeneration = authGeneration;
            this.previousSession = previousSession;
            this.widgetOperationGeneration = widgetOperationGeneration;
        }

        @Override public String toString() {
            return "PendingAuth{purpose=" + purpose + ", state=[redacted], pairingCode=[redacted]}";
        }
    }
}
