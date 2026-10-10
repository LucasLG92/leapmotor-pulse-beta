package com.lucas.pulse.nativebridge;

import android.content.Context;

import androidx.work.Constraints;
import androidx.work.ExistingPeriodicWorkPolicy;
import androidx.work.ExistingWorkPolicy;
import androidx.work.NetworkType;
import androidx.work.OneTimeWorkRequest;
import androidx.work.PeriodicWorkRequest;
import androidx.work.WorkManager;

import java.util.concurrent.TimeUnit;

/** Unique, network-constrained scheduling for opt-in widget refreshes. */
final class NativeWidgetRefreshScheduler {
    private static final String PERIODIC_NAME = "pulse-native-widget-refresh-periodic";
    private static final String IMMEDIATE_NAME = "pulse-native-widget-refresh-immediate";
    private static final long PERIOD_MINUTES = 30L;

    private NativeWidgetRefreshScheduler() {}

    static void schedulePeriodic(Context context) {
        try {
            PeriodicWorkRequest request = new PeriodicWorkRequest.Builder(
                    NativeWidgetRefreshWorker.class, PERIOD_MINUTES, TimeUnit.MINUTES)
                    .setConstraints(connectedConstraint())
                    .build();
            WorkManager.getInstance(context.getApplicationContext()).enqueueUniquePeriodicWork(
                    PERIODIC_NAME, ExistingPeriodicWorkPolicy.KEEP, request);
        } catch (Exception ignored) {
            // Refresh is best-effort; the next app resume can schedule it again.
        }
    }

    static void scheduleImmediate(Context context) {
        try {
            OneTimeWorkRequest request = new OneTimeWorkRequest.Builder(NativeWidgetRefreshWorker.class)
                    .setConstraints(connectedConstraint())
                    .build();
            WorkManager.getInstance(context.getApplicationContext()).enqueueUniqueWork(
                    IMMEDIATE_NAME, ExistingWorkPolicy.KEEP, request);
        } catch (Exception ignored) {
            // Do not fail login or the explicit opt-in when background work is unavailable.
        }
    }

    static void cancelAll(Context context) {
        try {
            WorkManager manager = WorkManager.getInstance(context.getApplicationContext());
            manager.cancelUniqueWork(PERIODIC_NAME);
            manager.cancelUniqueWork(IMMEDIATE_NAME);
        } catch (Exception ignored) {
            // In-flight workers still re-check consent and generation before storing data.
        }
    }

    private static Constraints connectedConstraint() {
        return new Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build();
    }
}
