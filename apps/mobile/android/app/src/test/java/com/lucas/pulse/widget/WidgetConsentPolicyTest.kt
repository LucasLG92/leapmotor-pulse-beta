package com.lucas.pulse.widget

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class WidgetConsentPolicyTest {
    @Test
    fun oldTruePreferenceWithoutKeystoreMarkerDoesNotEnableAfterRestart() {
        assertFalse(WidgetConsentPolicy.isEnabled(preferenceEnabled = true, markerPresent = false, blockedInProcess = false))
    }

    @Test
    fun failedPreferenceRevocationStillDisablesAfterMarkerRemovalAndRestart() {
        assertFalse(WidgetConsentPolicy.disableSucceeded(
            preferenceCommitted = false,
            markerRemoved = true,
            snapshotPurged = true,
        ))
        // A new process has no in-memory block; the absent marker still prevents stale true from opting in.
        assertFalse(WidgetConsentPolicy.isEnabled(preferenceEnabled = true, markerPresent = false, blockedInProcess = false))
    }

    @Test
    fun markerCreationFailureCannotCompleteOptIn() {
        assertFalse(WidgetConsentPolicy.enableSucceeded(preferenceCommitted = true, markerCreated = false))
        assertFalse(WidgetConsentPolicy.isEnabled(preferenceEnabled = true, markerPresent = false, blockedInProcess = true))
    }

    @Test
    fun preferenceCommitFailureCannotCompleteOptIn() {
        assertFalse(WidgetConsentPolicy.enableSucceeded(preferenceCommitted = false, markerCreated = true))
    }

    @Test
    fun markerDeletionFailureIsAnErrorAndProcessRemainsDisabled() {
        assertFalse(WidgetConsentPolicy.disableSucceeded(
            preferenceCommitted = true,
            markerRemoved = false,
            snapshotPurged = true,
        ))
        assertFalse(WidgetConsentPolicy.isEnabled(preferenceEnabled = true, markerPresent = true, blockedInProcess = true))
    }

    @Test
    fun independentRevocationFailuresAreNeverReportedAsSuccess() {
        assertFalse(WidgetConsentPolicy.disableSucceeded(
            preferenceCommitted = false,
            markerRemoved = false,
            snapshotPurged = false,
        ))
        assertFalse(WidgetConsentPolicy.isEnabled(preferenceEnabled = true, markerPresent = true, blockedInProcess = true))
    }

    @Test
    fun persistedFalsePreferenceDisablesAfterRestartEvenIfMarkerRemovalFailed() {
        assertFalse(WidgetConsentPolicy.disableSucceeded(
            preferenceCommitted = true,
            markerRemoved = false,
            snapshotPurged = true,
        ))
        assertFalse(WidgetConsentPolicy.isEnabled(preferenceEnabled = false, markerPresent = true, blockedInProcess = false))
    }

    @Test
    fun optInCompletesOnlyAfterPreferenceAndMarkerPersist() {
        assertTrue(WidgetConsentPolicy.enableSucceeded(preferenceCommitted = true, markerCreated = true))
        assertTrue(WidgetConsentPolicy.isEnabled(preferenceEnabled = true, markerPresent = true, blockedInProcess = false))
    }
}
