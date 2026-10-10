package com.lucas.pulse.widget

import java.time.Instant
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class WidgetPolicyTest {
    @Test
    fun acceptsOnlySelectedAllowlistedValues() {
        val safe = sanitize(snapshot(fields = listOf("battery_pct"), values = mapOf(
            "battery_pct" to 82,
            "range_km" to 240,
            "charging" to true,
        )))
        val display = display(safe, NOW + 1_000L, ELAPSED + 1_000L)!!

        assertEquals(setOf("battery_pct"), display.selectedFields)
        assertEquals(82.0, display.batteryPct!!, 0.0)
        assertNull(display.rangeKm)
        assertNull(display.charging)
    }

    @Test
    fun rejectsUnsupportedSchemaVersion() {
        assertNull(reject(snapshot(schemaVersion = 3)))
    }

    @Test
    fun rejectsUnknownSource() {
        assertNull(reject(snapshot(source = "manufacturer")))
    }

    @Test
    fun rejectsUnsupportedAndDuplicateFields() {
        assertNull(reject(snapshot(fields = listOf("battery_pct", "vin"))))
        assertNull(reject(snapshot(fields = listOf("battery_pct", "battery_pct"))))
    }

    @Test
    fun rejectsEmptyAndOversizedFieldSelection() {
        assertNull(reject(snapshot(fields = emptyList())))
        assertNull(reject(snapshot(fields = listOf("battery_pct", "range_km", "charging", "vin"))))
    }

    @Test
    fun acceptsLegacyV1OnlyForOriginalVehicleFieldsAndSources() {
        val legacy = sanitize(snapshot(fields = listOf("battery_pct", "range_km", "charging")))
        assertEquals("vehicle", legacy.kind)
        assertNull(legacy.energyWindowStartMillis)
        assertNull(legacy.energyWindowEndMillis)
        assertNull(reject(snapshot(fields = listOf("battery_pct", "locked"))))
        assertNull(reject(snapshot(source = "manual_energy")))
    }

    @Test
    fun v2RequiresKindAndWindowAndCapsSelectionAtThreeFields() {
        val missingWindow = vehicleV2Snapshot().toMutableMap().apply { remove("energy_window") }
        val missingKind = vehicleV2Snapshot().toMutableMap().apply { remove("kind") }
        val tooMany = vehicleV2Snapshot(fields = listOf("battery_pct", "range_km", "charging", "locked"))

        assertNull(reject(missingWindow))
        assertNull(reject(missingKind))
        assertNull(reject(tooMany))
    }

    @Test
    fun vehicleLockIsStrictBooleanAndDistinctFromPhoneLock() {
        val valid = sanitize(vehicleV2Snapshot(
            fields = listOf("battery_pct", "locked"),
            values = mapOf("battery_pct" to 82, "locked" to false),
        ))
        val display = display(valid, NOW + 1_000L, ELAPSED + 1_000L)!!
        val poisoned = sanitize(vehicleV2Snapshot(
            fields = listOf("locked"),
            values = mapOf("locked" to 1),
        ))

        assertEquals(false, display.locked)
        assertNull(display(poisoned, NOW + 1_000L, ELAPSED + 1_000L)?.locked)
        assertEquals("locked", WidgetDisplay.locked().status)
    }

    @Test
    fun energyV2SanitizesValuesAndRetainsTheThirtyDayWindow() {
        val safe = sanitize(energyV2Snapshot(values = mapOf(
            "energy_kwh" to 123.456,
            "energy_cost_eur" to -12.34,
        )))
        val display = display(safe, NOW + 1_000L, ELAPSED + 1_000L)!!

        assertEquals("energy", safe.kind)
        assertEquals("manual_energy", safe.source)
        assertEquals(NOW - 30_000L - THIRTY_DAYS, safe.energyWindowStartMillis)
        assertEquals(NOW - 30_000L, safe.energyWindowEndMillis)
        assertEquals(123.456, display.energyKwh!!, 0.0)
        assertEquals(-12.34, display.energyCostEur!!, 0.0)
        assertEquals(listOf("energy_kwh", "energy_cost_eur"), display.selectedFieldOrder)
        assertEquals("unknown", display.physicalFreshness)
    }

    @Test
    fun energyRejectsMixedFamiliesWrongSourcesAndInconsistentDates() {
        val mixed = energyV2Snapshot(fields = listOf("energy_kwh", "battery_pct"))
        val wrongSource = energyV2Snapshot(source = "telemetry")
        val wrongEnd = energyV2Snapshot().toMutableMap().apply {
            put("observed_at", iso(NOW - 20_000L))
        }
        val wrongReceived = energyV2Snapshot().toMutableMap().apply {
            put("server_received_at", iso(NOW - 20_000L))
        }
        val missingReason = energyV2Snapshot().toMutableMap().apply { remove("reason_code") }

        assertNull(reject(mixed))
        assertNull(reject(wrongSource))
        assertNull(reject(wrongEnd))
        assertNull(reject(wrongReceived))
        assertNull(reject(missingReason))
    }

    @Test
    fun energyRequiresUtcThirtyDayWindowAndBoundedGenerationTime() {
        val badWindow = energyV2Snapshot().toMutableMap().apply {
            put("energy_window", mapOf("started_at" to iso(NOW - THIRTY_DAYS - 2_000L), "ended_at" to iso(NOW - 30_000L)))
        }
        val offsetTimestamp = energyV2Snapshot().toMutableMap().apply {
            put("generated_at", "2026-10-08T12:00:00+02:00")
        }
        val tooOldGenerated = energyV2Snapshot().toMutableMap().apply {
            put("generated_at", iso(NOW - 90_001L))
        }
        val tooFarFuture = energyV2Snapshot().toMutableMap().apply {
            put("generated_at", iso(NOW + 60_001L))
        }
        val windowAfterGenerated = energyV2Snapshot().toMutableMap().apply {
            put("generated_at", iso(NOW - 32_000L))
        }

        assertNull(reject(badWindow))
        assertNull(reject(offsetTimestamp))
        assertNull(reject(tooOldGenerated))
        assertNull(reject(tooFarFuture))
        assertNull(reject(windowAfterGenerated))
    }

    @Test
    fun energyBoundsAllowNegativeCostButRejectInvalidOrBooleanAmounts() {
        assertEquals(-1_000_000_000.0, sanitize(energyV2Snapshot(
            fields = listOf("energy_cost_eur"), values = mapOf("energy_cost_eur" to -1_000_000_000.0),
        )).values["energy_cost_eur"])
        assertNull(reject(energyV2Snapshot(fields = listOf("energy_kwh"), values = mapOf("energy_kwh" to -0.01))))
        assertNull(reject(energyV2Snapshot(fields = listOf("energy_kwh"), values = mapOf("energy_kwh" to 1_000_000_001.0))))
        assertNull(reject(energyV2Snapshot(fields = listOf("energy_cost_eur"), values = mapOf("energy_cost_eur" to 1_000_000_001.0))))
        assertNull(reject(energyV2Snapshot(fields = listOf("energy_cost_eur"), values = mapOf("energy_cost_eur" to -1_000_000_001.0))))
        assertNull(reject(energyV2Snapshot(fields = listOf("energy_kwh"), values = mapOf("energy_kwh" to true))))
        assertNull(reject(energyV2Snapshot(fields = listOf("energy_kwh"), values = mapOf("energy_kwh" to Double.NaN))))
    }

    @Test
    fun cacheV3RoundTripsOnlySanitizedEnergyProjection() {
        val safe = sanitize(energyV2Snapshot(values = mapOf(
            "energy_kwh" to 15.321,
            "energy_cost_eur" to -2.75,
        )))
        val restored = WidgetPolicy.restoreRecord(record(safe))!!
        val display = display(restored, NOW + 1_000L, ELAPSED + 1_000L)!!

        assertEquals("energy", restored.kind)
        assertEquals(safe.energyWindowStartMillis, restored.energyWindowStartMillis)
        assertEquals(safe.energyWindowEndMillis, restored.energyWindowEndMillis)
        assertEquals(15.321, display.energyKwh!!, 0.0)
        assertEquals(-2.75, display.energyCostEur!!, 0.0)
    }

    @Test
    fun unavailableEnergyDoesNotInventZeroAndMaskedEnergyNeverPersistsValues() {
        val unavailable = energyV2Snapshot(
            status = "unavailable",
            reasonCode = "no_energy_sessions",
            validUntil = null,
            values = mapOf("energy_kwh" to null, "energy_cost_eur" to null),
        )
        val unavailableSafe = sanitize(unavailable)
        assertTrue(unavailableSafe.values.isEmpty())
        assertNull(display(unavailableSafe, NOW + 1_000L, ELAPSED + 1_000L)?.energyKwh)
        assertNull(reject(energyV2Snapshot(
            status = "unavailable",
            reasonCode = "no_energy_sessions",
            validUntil = null,
            values = mapOf("energy_kwh" to 0, "energy_cost_eur" to 0),
        )))

        val masked = sanitize(energyV2Snapshot(privacyMode = "masked"))
        val restored = WidgetPolicy.restoreRecord(record(masked))!!
        val maskedDisplay = display(restored, NOW + 1_000L, ELAPSED + 1_000L)!!
        assertTrue(masked.values.isEmpty())
        assertTrue(restored.values.isEmpty())
        assertNull(maskedDisplay.energyKwh)
        assertNull(maskedDisplay.energyCostEur)
        assertEquals("masked", maskedDisplay.status)
    }

    @Test
    fun cacheV2MigratesOnlyLegacyVehicleFieldsAndSources() {
        val legacyVehicle = record(sanitize(snapshot(fields = listOf("battery_pct", "charging"))), version = 2)
        val migrated = WidgetPolicy.restoreRecord(legacyVehicle)!!
        assertEquals("vehicle", migrated.kind)

        val legacyEnergy = record(sanitize(energyV2Snapshot()), version = 2)
        val newCarField = record(sanitize(vehicleV2Snapshot(fields = listOf("locked"))), version = 2)
        assertNull(WidgetPolicy.restoreRecord(legacyEnergy))
        assertNull(WidgetPolicy.restoreRecord(newCarField))
    }

    @Test
    fun rejectsProfileAgeOutsideContract() {
        assertNull(reject(snapshot(maxAgeMinutes = 14)))
        assertNull(reject(snapshot(maxAgeMinutes = 121)))
        assertNull(reject(snapshot(maxAgeMinutes = 15.5)))
    }

    @Test
    fun rejectsUnknownPrivacyMode() {
        assertNull(reject(snapshot(privacyMode = "private")))
    }

    @Test
    fun maskedProfileNeverRetainsOrDisplaysValues() {
        val safe = sanitize(snapshot(appearance = "dark", privacyMode = "masked", values = mapOf(
            "battery_pct" to 91,
            "charging" to true,
        )))

        assertTrue(safe.privacyMasked)
        assertTrue(safe.values.isEmpty())
        val display = display(safe, NOW + 1_000L, ELAPSED + 1_000L)!!
        assertEquals("dark", display.appearance)
        assertEquals("masked", display.status)
        assertNull(display.batteryPct)
        assertEquals(safe.validUntilMillis, display.validUntilMillis)
    }

    @Test
    fun outOfRangeBatteryAndRangeAreDropped() {
        val safe = sanitize(snapshot(fields = listOf("battery_pct", "range_km"), values = mapOf(
            "battery_pct" to 100.1,
            "range_km" to 5_001,
        )))
        val display = display(safe, NOW + 1_000L, ELAPSED + 1_000L)!!

        assertNull(display.batteryPct)
        assertNull(display.rangeKm)
    }

    @Test
    fun wrongValueTypesAreDropped() {
        val safe = sanitize(snapshot(fields = listOf("battery_pct", "charging"), values = mapOf(
            "battery_pct" to "82",
            "charging" to 1,
        )))
        val display = display(safe, NOW + 1_000L, ELAPSED + 1_000L)!!

        assertNull(display.batteryPct)
        assertNull(display.charging)
    }

    @Test
    fun rejectsPhysicalFreshnessClaim() {
        assertNull(reject(snapshot(physicalFreshness = "fresh")))
    }

    @Test
    fun rejectsMalformedValuesContainer() {
        val malformed = snapshot().toMutableMap()
        malformed["values"] = listOf(82, 240)

        assertNull(reject(malformed))
    }

    @Test
    fun unavailableSourceCannotExposeValues() {
        val safe = sanitize(snapshot(source = "unavailable", values = mapOf("battery_pct" to 42)))
        val display = display(safe, NOW + 1_000L, ELAPSED + 1_000L)!!

        assertEquals("unavailable", display.status)
        assertNull(display.batteryPct)
    }

    @Test
    fun missingOrMalformedObservationForcesUnknownAndHidesValues() {
        val missing = sanitize(snapshot(observedAt = null, values = mapOf("battery_pct" to 42)))
        val malformed = sanitize(snapshot(observedAt = "yesterday", values = mapOf("battery_pct" to 42)))

        assertEquals("unknown", missing.status)
        assertTrue(missing.values.isEmpty())
        assertEquals("unknown", malformed.status)
        assertTrue(malformed.values.isEmpty())
    }

    @Test
    fun observationMoreThanSixtySecondsInFutureIsRejected() {
        assertNull(reject(snapshot(
            observedAt = iso(NOW + 60_001L),
            validUntil = iso(NOW + 10 * MINUTE),
        )))
    }

    @Test
    fun serverDeadlineIsClampedToProfileAge() {
        val observedAt = NOW - 2 * MINUTE
        val safe = sanitize(snapshot(
            observedAt = iso(observedAt),
            validUntil = iso(NOW + 90 * MINUTE),
            maxAgeMinutes = 15,
        ))

        assertEquals(observedAt + 15 * MINUTE, safe.valuesValidUntilMillis)
        assertEquals(safe.valuesValidUntilMillis, safe.cacheUntilMillis)
    }

    @Test
    fun restoredAllowlistedRecordPreservesOnlySafeDisplayValues() {
        val safe = sanitize(snapshot(fields = listOf("battery_pct", "charging")))
        val restored = WidgetPolicy.restoreRecord(record(safe))!!
        val display = display(restored, NOW + 1_000L, ELAPSED + 1_000L)!!

        assertEquals(setOf("battery_pct", "charging"), display.selectedFields)
        assertEquals(82.0, display.batteryPct!!, 0.0)
        assertEquals(false, display.charging)
        assertEquals("B10", display.profileName)
        assertEquals(safe.valuesValidUntilMillis, display.validUntilMillis)
    }

    @Test
    fun appearanceIsAllowlistedAndSurvivesRestoreWithoutChangingFieldSelection() {
        for (appearance in listOf("system", "light", "dark")) {
            val safe = sanitize(snapshot(appearance = appearance, fields = listOf("battery_pct")))
            val restored = WidgetPolicy.restoreRecord(record(safe))!!
            val display = display(restored, NOW + 1_000L, ELAPSED + 1_000L)!!

            assertEquals(appearance, display.appearance)
            assertEquals(setOf("battery_pct"), display.selectedFields)
            assertEquals(82.0, display.batteryPct!!, 0.0)
            assertEquals(safe.cacheUntilMillis, restored.cacheUntilMillis)
        }
    }

    @Test
    fun invalidOrMissingAppearanceIsRejected() {
        assertNull(reject(snapshot(appearance = "automatic")))
        assertNull(reject(snapshot(appearance = null)))
        assertNull(reject(snapshot(appearance = 1)))

        val record = record(sanitize(snapshot())).toMutableMap().apply { put("appearance", "automatic") }
        assertNull(WidgetPolicy.restoreRecord(record))
    }

    @Test
    fun oldCachedRecordWithoutAppearanceIsRejectedWithoutMigration() {
        val oldRecord = record(sanitize(snapshot())).toMutableMap().apply {
            put("version", 1)
            remove("appearance")
        }

        assertNull(WidgetPolicy.restoreRecord(oldRecord))
    }

    @Test
    fun placeholderDisplayDefaultsToSystemAppearance() {
        assertEquals("system", WidgetDisplay.setup().appearance)
        assertEquals("system", WidgetDisplay.locked().appearance)
        assertEquals("system", WidgetDisplay.disabled().appearance)
    }

    @Test
    fun expiredObservationNeverKeepsValues() {
        val observedAt = NOW - 15 * MINUTE
        val safe = sanitize(snapshot(
            observedAt = iso(observedAt),
            validUntil = iso(NOW + 1 * MINUTE),
            maxAgeMinutes = 15,
            values = mapOf("battery_pct" to 42),
        ))

        assertEquals("unavailable", safe.status)
        assertTrue(safe.values.isEmpty())
    }

    @Test
    fun temporarilyStaleObservationIsLabelledStale() {
        val safe = sanitize(snapshot(observedAt = iso(NOW - 6 * MINUTE)))
        val display = display(safe, NOW + 1_000L, ELAPSED + 1_000L)!!

        assertEquals("stale", display.status)
        assertEquals("unknown", display.physicalFreshness)
    }

    @Test
    fun valuesDisappearAtValidUntil() {
        val safe = sanitize(snapshot(appearance = "light", validUntil = iso(NOW + 2 * MINUTE)))

        assertNull(display(safe, NOW + 2 * MINUTE, ELAPSED + 2 * MINUTE))
    }

    @Test
    fun wallClockRollbackFailsClosed() {
        val safe = sanitize(snapshot())

        assertNull(display(safe, NOW - 1L, ELAPSED + 10_000L))
    }

    @Test
    fun elapsedClockResetAfterRebootFailsClosed() {
        val safe = sanitize(snapshot())

        assertNull(display(safe, NOW + 10_000L, 100L))
    }

    @Test
    fun bootCountChangeAfterRebootFailsClosed() {
        val safe = WidgetPolicy.sanitizeSnapshot(snapshot(), NOW, ELAPSED, receiptBootCount = BOOT_COUNT)!!

        assertNull(display(safe, NOW + 1_000L, ELAPSED + 1_000L, currentBootCount = BOOT_COUNT + 1))
    }

    @Test
    fun unknownBootCountOnEitherSideFailsClosed() {
        val knownReceipt = sanitize(snapshot())
        val unknownReceipt = sanitize(snapshot(), receiptBootCount = null)

        assertFalse(WidgetPolicy.receiptClockIsValid(knownReceipt, NOW + 1_000L, ELAPSED + 1_000L, null))
        assertFalse(WidgetPolicy.receiptClockIsValid(unknownReceipt, NOW + 1_000L, ELAPSED + 1_000L, null))
        assertFalse(WidgetPolicy.receiptClockIsValid(unknownReceipt, NOW + 1_000L, ELAPSED + 1_000L, BOOT_COUNT))
        assertNull(display(unknownReceipt, NOW + 1_000L, ELAPSED + 1_000L, BOOT_COUNT))
    }

    @Test
    fun divergentWallAndMonotonicClocksFailClosed() {
        val safe = sanitize(snapshot())

        assertNull(display(safe, NOW + 2 * MINUTE, ELAPSED + 30_000L))
    }

    @Test
    fun demoIsExplicitInDisplayModel() {
        val safe = sanitize(snapshot(source = "demo"))

        assertTrue(display(safe, NOW + 1_000L, ELAPSED + 1_000L)!!.demo)
    }

    @Test
    fun restoredRecordRejectsValuesForUnselectedFields() {
        val safe = sanitize(snapshot(fields = listOf("battery_pct")))
        val record = record(safe).toMutableMap()
        record["values"] = mapOf("range_km" to 180.0)

        assertNull(WidgetPolicy.restoreRecord(record))
    }

    @Test
    fun profileNameIsOptionalAndControlTextIsDiscarded() {
        assertNull(sanitize(snapshot(profileName = "name\nwith control")).profileName)
        assertNull(sanitize(snapshot(profileName = 7)).profileName)
    }

    private fun display(
        snapshot: WidgetPolicy.SafeSnapshot,
        nowWallMillis: Long,
        nowElapsedMillis: Long,
        currentBootCount: Int? = BOOT_COUNT,
    ): WidgetDisplay? = WidgetPolicy.display(snapshot, nowWallMillis, nowElapsedMillis, currentBootCount)

    private fun sanitize(
        input: Map<String, Any?>,
        receiptBootCount: Int? = BOOT_COUNT,
    ): WidgetPolicy.SafeSnapshot =
        WidgetPolicy.sanitizeSnapshot(input, NOW, ELAPSED, receiptBootCount)!!

    private fun reject(input: Map<String, Any?>): WidgetPolicy.SafeSnapshot? =
        WidgetPolicy.sanitizeSnapshot(input, NOW, ELAPSED, BOOT_COUNT)

    private fun snapshot(
        schemaVersion: Any? = 1,
        fields: List<Any?> = listOf("battery_pct", "range_km", "charging"),
        appearance: Any? = "system",
        maxAgeMinutes: Any? = 15,
        privacyMode: Any? = "visible",
        source: Any? = "telemetry",
        status: Any? = "fresh",
        physicalFreshness: Any? = "unknown",
        observedAt: Any? = iso(NOW - 30_000L),
        validUntil: Any? = iso(NOW + 10 * MINUTE),
        profileName: Any? = "B10",
        values: Map<String, Any?> = mapOf("battery_pct" to 82, "range_km" to 240.5, "charging" to false),
    ): Map<String, Any?> = mapOf(
        "schema_version" to schemaVersion,
        "profile" to mapOf(
            "name" to profileName,
            "fields" to fields,
            "appearance" to appearance,
            "max_age_minutes" to maxAgeMinutes,
            "privacy_mode" to privacyMode,
        ),
        "source" to source,
        "physical_freshness" to physicalFreshness,
        "temporal_status" to status,
        "observed_at" to observedAt,
        "valid_until" to validUntil,
        "values" to values,
    )

    private fun vehicleV2Snapshot(
        fields: List<Any?> = listOf("battery_pct", "range_km", "charging"),
        source: Any? = "telemetry",
        values: Map<String, Any?> = mapOf("battery_pct" to 82, "range_km" to 240.5, "charging" to false),
    ): Map<String, Any?> = snapshot(fields = fields, source = source, values = values).toMutableMap().apply {
        put("schema_version", 2)
        put("kind", "vehicle")
        put("energy_window", null)
    }

    private fun energyV2Snapshot(
        fields: List<Any?> = listOf("energy_kwh", "energy_cost_eur"),
        source: Any? = "manual_energy",
        status: Any? = "fresh",
        reasonCode: Any? = "energy_calculated",
        privacyMode: Any? = "visible",
        observedAt: Any? = iso(NOW - 30_000L),
        generatedAt: Any? = iso(NOW),
        validUntil: Any? = iso(NOW + 10 * MINUTE),
        values: Map<String, Any?> = mapOf("energy_kwh" to 80.123, "energy_cost_eur" to 4.56),
    ): Map<String, Any?> = snapshot(
        fields = fields,
        privacyMode = privacyMode,
        source = source,
        status = status,
        observedAt = observedAt,
        validUntil = validUntil,
        values = values,
    ).toMutableMap().apply {
        put("schema_version", 2)
        put("kind", "energy")
        put("energy_window", mapOf(
            "started_at" to iso(NOW - 30_000L - THIRTY_DAYS),
            "ended_at" to iso(NOW - 30_000L),
        ))
        put("generated_at", generatedAt)
        put("server_received_at", iso(NOW - 30_000L))
        put("reason_code", reasonCode)
    }

    private fun record(snapshot: WidgetPolicy.SafeSnapshot, version: Int = 3): Map<String, Any?> =
        linkedMapOf<String, Any?>(
            "version" to version,
            "profile_name" to snapshot.profileName,
            "appearance" to snapshot.appearance,
            "source" to snapshot.source,
            "status" to snapshot.status,
            "fields" to snapshot.selectedFields,
            "privacy_masked" to snapshot.privacyMasked,
            "max_age_ms" to snapshot.maxAgeMillis,
            "observed_at_ms" to snapshot.observedAtMillis,
            "valid_until_ms" to snapshot.validUntilMillis,
            "values_valid_until_ms" to snapshot.valuesValidUntilMillis,
            "cache_until_ms" to snapshot.cacheUntilMillis,
            "receipt_wall_ms" to snapshot.receiptWallMillis,
            "receipt_elapsed_ms" to snapshot.receiptElapsedMillis,
            "receipt_boot_count" to snapshot.receiptBootCount,
            "values" to snapshot.values,
        ).apply {
            if (version == 3) {
                put("kind", snapshot.kind)
                put("energy_window_start_ms", snapshot.energyWindowStartMillis)
                put("energy_window_end_ms", snapshot.energyWindowEndMillis)
            }
        }

    private fun iso(epochMillis: Long): String = Instant.ofEpochMilli(epochMillis).toString()

    private companion object {
        val NOW: Long = Instant.parse("2026-10-08T10:00:00Z").toEpochMilli()
        const val ELAPSED = 50_000L
        const val BOOT_COUNT = 4
        const val MINUTE = 60_000L
        const val THIRTY_DAYS = 30L * 24L * 60L * 60L * 1_000L
    }
}
