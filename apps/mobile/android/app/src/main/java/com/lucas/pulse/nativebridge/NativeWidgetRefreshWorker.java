package com.lucas.pulse.nativebridge;

import android.app.KeyguardManager;
import android.content.Context;
import android.os.UserManager;

import androidx.annotation.NonNull;
import androidx.work.Worker;
import androidx.work.WorkerParameters;

import com.lucas.pulse.widget.WidgetSnapshotCache;
import com.lucas.pulse.widget.WidgetUpdateBridge;

import org.json.JSONObject;

/** Refreshes only the explicitly opted-in encrypted widget snapshot. */
public final class NativeWidgetRefreshWorker extends Worker {
    public NativeWidgetRefreshWorker(@NonNull Context context, @NonNull WorkerParameters parameters) {
        super(context, parameters);
    }

    @NonNull @Override public Result doWork() {
        Context context = getApplicationContext();
        NativeRuntimeConfig config = NativeRuntimeConfig.fromBuildConfig();
        NativeWidgetCredentialVault vault = new NativeWidgetCredentialVault(context);
        WidgetSnapshotCache cache = new WidgetSnapshotCache(context);
        if (config == null || !config.isConfigured()) {
            synchronized (NativeWidgetOperations.LOCK) {
                NativeWidgetOperations.retireLocked(context, vault, cache);
            }
            updateWidget(context);
            return Result.success();
        }
        NativeWidgetCredentialVault.NativeWidgetCredential credential;
        long requestGeneration;
        boolean retired = false;
        synchronized (NativeWidgetOperations.LOCK) {
            if (!cache.isEnabled()) return Result.success();
            if (vault.credentialExpired(config.apiOrigin)) {
                NativeWidgetOperations.retireLocked(context, vault, cache);
                retired = true;
                credential = null;
                requestGeneration = -1L;
            } else {
                credential = vault.credential(config.apiOrigin);
                if (credential == null) {
                    NativeWidgetOperations.retireLocked(context, vault, cache);
                    retired = true;
                    requestGeneration = -1L;
                } else if (!isDeviceUnlocked(context)) {
                    // Locked-device state is temporary; preserve consent and the current TTL cache.
                    return Result.success();
                } else {
                    requestGeneration = NativeWidgetOperations.generationLocked();
                    if (!NativeWidgetOperations.beginRefreshLocked(requestGeneration)) return Result.success();
                }
            }
        }
        if (retired) {
            updateWidget(context);
            return Result.success();
        }

        try {
            NativeHttpClient http = new NativeHttpClient(config);
            JSONObject snapshot;
            try {
                snapshot = http.widgetSnapshot(credential);
            } catch (NativeHttpClient.NativeTransportException error) {
                if (NativeWidgetRefreshPolicy.shouldRetireCredential(error.status)) {
                    boolean stateRetired = false;
                    synchronized (NativeWidgetOperations.LOCK) {
                        if (NativeWidgetRefreshPolicy.shouldAcceptSnapshot(
                                cache.isEnabled(), vault.isCurrent(credential, config.apiOrigin),
                                requestGeneration, NativeWidgetOperations.generationLocked())) {
                            NativeWidgetOperations.retireLocked(context, vault, cache);
                            stateRetired = true;
                        }
                    }
                    if (stateRetired) updateWidget(context);
                }
                // A success result keeps periodic work scheduled; transient failures retry next period.
                return Result.success();
            } catch (Exception ignored) {
                return Result.success();
            }

            boolean shouldUpdate = false;
            synchronized (NativeWidgetOperations.LOCK) {
                if (!NativeWidgetRefreshPolicy.shouldAcceptSnapshot(
                        cache.isEnabled(), vault.isCurrent(credential, config.apiOrigin),
                        requestGeneration, NativeWidgetOperations.generationLocked())) {
                    return Result.success();
                }
                if (!isDeviceUnlocked(context)) {
                    // The credential may be fine; wait for a later periodic run after unlock.
                    return Result.success();
                }
                if (cache.accept(snapshot)) {
                    shouldUpdate = true;
                } else if (isDeviceUnlocked(context)) {
                    // accept() purges invalid/failed writes; ask Glance to render the closed state.
                    try { cache.clear(); } catch (Exception ignored) { }
                    shouldUpdate = true;
                }
            }
            if (shouldUpdate) updateWidget(context);
            return Result.success();
        } finally {
            NativeWidgetOperations.finishRefresh(requestGeneration);
        }
    }

    private static boolean isDeviceUnlocked(Context context) {
        try {
            KeyguardManager keyguard = (KeyguardManager) context.getSystemService(Context.KEYGUARD_SERVICE);
            UserManager user = (UserManager) context.getSystemService(Context.USER_SERVICE);
            return keyguard != null && user != null && user.isUserUnlocked() && !keyguard.isDeviceLocked();
        } catch (Exception ignored) {
            return false;
        }
    }

    private static void updateWidget(Context context) {
        try { WidgetUpdateBridge.updateAll(context); } catch (Exception ignored) { }
    }
}
