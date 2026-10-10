package com.lucas.pulse.widget

import java.time.OffsetDateTime
import java.time.Duration
import java.time.Instant
import java.time.format.DateTimeParseException

/** Values safe for the renderer; placeholder states never carry telemetry. */
data class WidgetDisplay(
    val profileName: String? = null,
    val appearance: String = "system",
    val kind: String = "vehicle",
    val source: String? = null,
    val status: String = "setup",
    val physicalFreshness: String = "unknown",
    val selectedFields: Set<String> = emptySet(),
    val selectedFieldOrder: List<String> = emptyList(),
    val observedAtMillis: Long? = null,
    val energyWindowStartMillis: Long? = null,
    val energyWindowEndMillis: Long? = null,
    val validUntilMillis: Long? = null,
    val batteryPct: Double? = null,
    val rangeKm: Double? = null,
    val charging: Boolean? = null,
    val locked: Boolean? = null,
    val energyKwh: Double? = null,
    val energyCostEur: Double? = null,
    val demo: Boolean = false,
) {
    companion object {
        fun setup() = WidgetDisplay()
        fun locked() = WidgetDisplay(status = "locked")
        fun disabled() = WidgetDisplay(status = "disabled")
    }
}

/** Pure validation and expiry rules shared by the encrypted cache and JVM tests. */
internal object WidgetPolicy {
    const val MAX_TTL_MILLIS = 2 * 60 * 60 * 1000L
    const val CLOCK_SKEW_MILLIS = 60 * 1000L
    const val MAX_SELECTED_FIELDS = 3
    private const val MAX_CLOCK_DRIFT_MILLIS = 5 * 1000L
    private const val FRESH_WINDOW_MILLIS = 5 * 60 * 1000L
    private const val MIN_PROFILE_AGE_MILLIS = 15 * 60 * 1000L
    private const val MAX_PROFILE_AGE_MILLIS = MAX_TTL_MILLIS
    private const val ENERGY_WINDOW_MILLIS = 30L * 24L * 60L * 60L * 1000L
    private const val ENERGY_WINDOW_TOLERANCE_MILLIS = 1_000L
    private const val ENERGY_GENERATION_TOLERANCE_MILLIS = 60_000L

    private val legacyFields = setOf("battery_pct", "range_km", "charging")
    private val vehicleFields = legacyFields + "locked"
    private val energyFields = setOf("energy_kwh", "energy_cost_eur")
    private val allowedFields = vehicleFields + energyFields
    private val allowedAppearances = setOf("system", "light", "dark")
    private val legacySources = setOf("demo", "telemetry", "unavailable")
    private val vehicleSources = legacySources
    private val allowedSources = vehicleSources + "manual_energy"
    private val allowedStatuses = setOf("fresh", "stale", "unknown", "unavailable")
    private val utcTimestamp = Regex("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\\.[0-9]+)?Z$")

    @ConsistentCopyVisibility
    data class SafeSnapshot internal constructor(
        val profileName: String?,
        val appearance: String,
        val kind: String,
        val source: String,
        val status: String,
        val selectedFields: List<String>,
        val energyWindowStartMillis: Long?,
        val energyWindowEndMillis: Long?,
        val privacyMasked: Boolean,
        val maxAgeMillis: Long,
        val observedAtMillis: Long?,
        val validUntilMillis: Long?,
        val valuesValidUntilMillis: Long?,
        val cacheUntilMillis: Long,
        val receiptWallMillis: Long,
        val receiptElapsedMillis: Long,
        val receiptBootCount: Int?,
        val values: Map<String, Any>,
    )

    /**
     * Accepts only the versioned Pulse snapshot contract and produces a new,
     * allowlisted record. The input map is never retained.
     */
    fun sanitizeSnapshot(
        snapshot: Map<String, Any?>,
        nowWallMillis: Long,
        nowElapsedMillis: Long,
        receiptBootCount: Int? = null,
    ): SafeSnapshot? {
        if (nowWallMillis <= 0L || nowElapsedMillis < 0L) return null
        if (receiptBootCount != null && receiptBootCount < 0) return null
        val schemaVersion = exactLong(snapshot["schema_version"]) ?: return null
        if (schemaVersion !in 1L..2L) return null

        val profile = snapshot["profile"] as? Map<*, *> ?: return null
        val selectedFields = fields(profile["fields"]) ?: return null
        val appearance = profile["appearance"] as? String ?: return null
        if (appearance !in allowedAppearances) return null
        val maxAgeMinutes = exactLong(profile["max_age_minutes"]) ?: return null
        if (maxAgeMinutes !in 15L..120L) return null
        val maxAgeMillis = maxAgeMinutes * 60_000L
        val privacyMode = profile["privacy_mode"] as? String ?: return null
        if (privacyMode != "masked" && privacyMode != "visible") return null

        val source = snapshot["source"] as? String ?: return null
        if (source !in allowedSources) return null
        val kind = when (schemaVersion) {
            1L -> {
                // A V1 payload predates both new families and the car-lock field.
                if (source !in legacySources || selectedFields.any { it !in legacyFields }) return null
                "vehicle"
            }
            2L -> {
                if (!snapshot.containsKey("kind") || !snapshot.containsKey("energy_window")) return null
                val declaredKind = snapshot["kind"] as? String ?: return null
                when (declaredKind) {
                    "vehicle" -> {
                        if (source !in vehicleSources || selectedFields.any { it !in vehicleFields }) return null
                        if (snapshot["energy_window"] != null) return null
                    }
                    "energy" -> {
                        if (source != "manual_energy" || selectedFields.any { it !in energyFields }) return null
                    }
                    else -> return null
                }
                declaredKind
            }
            else -> return null
        }
        if (snapshot["physical_freshness"] != "unknown") return null
        var status = snapshot["temporal_status"] as? String ?: return null
        if (status !in allowedStatuses) return null

        val profileName = safeProfileName(profile["name"])
        val rawValues = snapshot["values"] as? Map<*, *> ?: return null
        val observedAt = parseInstant(snapshot["observed_at"])
        if (observedAt != null && observedAt > safeAdd(nowWallMillis, CLOCK_SKEW_MILLIS)) return null
        val energyWindow = if (kind == "energy") {
            validateEnergySnapshot(snapshot, selectedFields, rawValues, status, privacyMode, nowWallMillis)
                ?: return null
        } else {
            null
        }
        var validUntil: Long? = null
        var valuesValidUntil: Long? = null
        var values = emptyMap<String, Any>()
        val privacyMasked = privacyMode == "masked"

        if (source == "unavailable") status = "unavailable"
        if (status == "fresh" || status == "stale") {
            val serverDeadline = parseInstant(snapshot["valid_until"])
            if (observedAt == null || serverDeadline == null) {
                status = "unknown"
            } else {
                val age = ageMillis(nowWallMillis, observedAt)
                if (age >= maxAgeMillis || serverDeadline <= nowWallMillis) {
                    status = "unavailable"
                } else {
                    val safeDeadline = minOf(
                        serverDeadline,
                        safeAdd(observedAt, maxAgeMillis),
                        safeAdd(nowWallMillis, maxAgeMillis),
                        safeAdd(nowWallMillis, MAX_TTL_MILLIS),
                    )
                    if (safeDeadline <= nowWallMillis) {
                        status = "unavailable"
                    } else {
                        validUntil = safeDeadline
                        if (source != "unavailable" && !privacyMasked) {
                            valuesValidUntil = safeDeadline
                            values = selectedFields.mapNotNull { field ->
                                normalizeValue(field, rawValues[field])?.let { field to it }
                            }.toMap()
                            val locallyStale = age > FRESH_WINDOW_MILLIS
                            if (kind == "vehicle" && locallyStale && status == "fresh") status = "stale"
                        }
                    }
                }
            }
        }

        // Unknown/unavailable data is retained only as a bounded status summary.
        val cacheUntil = (validUntil ?: safeAdd(
            nowWallMillis,
            minOf(maxAgeMillis, MAX_TTL_MILLIS),
        )).coerceAtMost(safeAdd(nowWallMillis, MAX_TTL_MILLIS))

        return SafeSnapshot(
            profileName = profileName,
            appearance = appearance,
            kind = kind,
            source = source,
            status = status,
            selectedFields = selectedFields,
            energyWindowStartMillis = energyWindow?.startedAtMillis,
            energyWindowEndMillis = energyWindow?.endedAtMillis,
            privacyMasked = privacyMasked,
            maxAgeMillis = maxAgeMillis,
            observedAtMillis = observedAt,
            validUntilMillis = validUntil,
            valuesValidUntilMillis = valuesValidUntil,
            cacheUntilMillis = cacheUntil,
            receiptWallMillis = nowWallMillis,
            receiptElapsedMillis = nowElapsedMillis,
            receiptBootCount = receiptBootCount,
            values = values,
        )
    }

    /** Validates the decrypted record again before any value reaches Glance. */
    fun restoreRecord(record: Map<String, Any?>): SafeSnapshot? {
        val recordVersion = exactLong(record["version"]) ?: return null
        if (recordVersion !in 2L..3L) return null
        val name = safeProfileName(record["profile_name"])
        val appearance = record["appearance"] as? String ?: return null
        if (appearance !in allowedAppearances) return null
        val source = record["source"] as? String ?: return null
        val status = record["status"] as? String ?: return null
        if (source !in allowedSources || status !in allowedStatuses) return null
        val fields = fields(record["fields"]) ?: return null

        val kind: String
        val energyWindowStart: Long?
        val energyWindowEnd: Long?
        if (recordVersion == 2L) {
            // V2 cache records have no family metadata, so migrate only the original car contract.
            if (source !in legacySources || fields.any { it !in legacyFields }) return null
            if (record.containsKey("kind") || record.containsKey("energy_window_start_ms") ||
                record.containsKey("energy_window_end_ms")
            ) return null
            kind = "vehicle"
            energyWindowStart = null
            energyWindowEnd = null
        } else {
            if (!record.containsKey("kind") || !record.containsKey("energy_window_start_ms") ||
                !record.containsKey("energy_window_end_ms")
            ) return null
            kind = record["kind"] as? String ?: return null
            energyWindowStart = nullableExactLong(record["energy_window_start_ms"]) ?: run {
                if (record["energy_window_start_ms"] != null) return null
                null
            }
            energyWindowEnd = nullableExactLong(record["energy_window_end_ms"]) ?: run {
                if (record["energy_window_end_ms"] != null) return null
                null
            }
            when (kind) {
                "vehicle" -> {
                    if (source !in vehicleSources || fields.any { it !in vehicleFields }) return null
                    if (energyWindowStart != null || energyWindowEnd != null) return null
                }
                "energy" -> {
                    if (source != "manual_energy" || fields.any { it !in energyFields }) return null
                    if (energyWindowStart == null || energyWindowEnd == null) return null
                }
                else -> return null
            }
        }

        val masked = record["privacy_masked"] as? Boolean ?: return null
        val maxAgeMillis = exactLong(record["max_age_ms"]) ?: return null
        if (maxAgeMillis !in MIN_PROFILE_AGE_MILLIS..MAX_PROFILE_AGE_MILLIS || maxAgeMillis % 60_000L != 0L) {
            return null
        }
        val receiptWall = exactLong(record["receipt_wall_ms"]) ?: return null
        val receiptElapsed = exactLong(record["receipt_elapsed_ms"]) ?: return null
        if (receiptElapsed < 0L) return null
        val receiptBootCount = nullableExactLong(record["receipt_boot_count"])?.let {
            if (it !in 0L..Int.MAX_VALUE.toLong()) return null
            it.toInt()
        } ?: run {
            if (record["receipt_boot_count"] != null) return null
            null
        }
        val cacheUntil = exactLong(record["cache_until_ms"]) ?: return null
        if (receiptWall <= 0L || receiptElapsed < 0L || cacheUntil <= receiptWall) return null
        if (cacheUntil > safeAdd(receiptWall, minOf(maxAgeMillis, MAX_TTL_MILLIS))) return null

        val observedAt = nullableExactLong(record["observed_at_ms"]) ?: run {
            if (record["observed_at_ms"] != null) return null
            null
        }
        if (observedAt != null && observedAt > safeAdd(receiptWall, CLOCK_SKEW_MILLIS)) return null
        if (kind == "energy") {
            if (observedAt == null || energyWindowEnd != observedAt) return null
            if (!isThirtyDayWindow(energyWindowStart!!, energyWindowEnd!!)) return null
            if (energyWindowEnd > safeAdd(receiptWall, CLOCK_SKEW_MILLIS)) return null
            if (status !in setOf("fresh", "unavailable")) return null
        }
        val validUntil = nullableExactLong(record["valid_until_ms"]) ?: run {
            if (record["valid_until_ms"] != null) return null
            null
        }
        if (kind == "energy" && status == "fresh" && validUntil == null) return null
        if (kind == "energy" && status == "unavailable" && validUntil != null) return null
        if (validUntil != null) {
            if (observedAt == null || source == "unavailable" || status !in setOf("fresh", "stale")) return null
            val upperBound = minOf(
                safeAdd(observedAt, maxAgeMillis),
                safeAdd(receiptWall, maxAgeMillis),
                safeAdd(receiptWall, MAX_TTL_MILLIS),
            )
            if (validUntil <= receiptWall || validUntil > upperBound || cacheUntil != validUntil) return null
        }
        val valuesValidUntil = nullableExactLong(record["values_valid_until_ms"]) ?: run {
            if (record["values_valid_until_ms"] != null) return null
            null
        }
        if (valuesValidUntil != null && (masked || valuesValidUntil != validUntil)) return null

        val rawValues = record["values"] as? Map<*, *> ?: return null
        val values = linkedMapOf<String, Any>()
        for ((key, value) in rawValues) {
            if (key !is String || key !in fields || value == null) return null
            val normalized = normalizeValue(key, value) ?: return null
            values[key] = normalized
        }
        if (kind == "energy" && !masked && status == "fresh" && !values.keys.containsAll(fields)) return null
        if ((masked || valuesValidUntil == null || status !in setOf("fresh", "stale") || source == "unavailable") && values.isNotEmpty()) {
            return null
        }
        if (status == "fresh" || status == "stale") {
            if (observedAt == null && values.isNotEmpty()) return null
        }
        return SafeSnapshot(
            profileName = name,
            appearance = appearance,
            kind = kind,
            source = source,
            status = if (source == "unavailable") "unavailable" else status,
            selectedFields = fields,
            energyWindowStartMillis = energyWindowStart,
            energyWindowEndMillis = energyWindowEnd,
            privacyMasked = masked,
            maxAgeMillis = maxAgeMillis,
            observedAtMillis = observedAt,
            validUntilMillis = validUntil,
            valuesValidUntilMillis = valuesValidUntil,
            cacheUntilMillis = cacheUntil,
            receiptWallMillis = receiptWall,
            receiptElapsedMillis = receiptElapsed,
            receiptBootCount = receiptBootCount,
            values = values,
        )
    }

    /** Null means the stored clock/TTL no longer supports displaying any snapshot. */
    fun display(
        snapshot: SafeSnapshot,
        nowWallMillis: Long,
        nowElapsedMillis: Long,
        currentBootCount: Int? = null,
    ): WidgetDisplay? {
        if (!receiptClockIsValid(snapshot, nowWallMillis, nowElapsedMillis, currentBootCount)) return null
        val elapsedSinceReceipt = nowElapsedMillis - snapshot.receiptElapsedMillis
        val elapsedCacheTtl = snapshot.cacheUntilMillis - snapshot.receiptWallMillis
        if (nowWallMillis >= snapshot.cacheUntilMillis || elapsedSinceReceipt >= elapsedCacheTtl) return null

        val validUntil = snapshot.valuesValidUntilMillis
        if (validUntil != null) {
            val elapsedValueTtl = validUntil - snapshot.receiptWallMillis
            if (nowWallMillis >= validUntil || elapsedSinceReceipt >= elapsedValueTtl) return null
        }

        val currentAge = snapshot.observedAtMillis?.let { ageMillis(nowWallMillis, it) }
        val status = when {
            snapshot.privacyMasked -> "masked"
            snapshot.source == "unavailable" -> "unavailable"
            currentAge != null && currentAge >= snapshot.maxAgeMillis -> "unavailable"
            snapshot.kind == "vehicle" && currentAge != null && currentAge > FRESH_WINDOW_MILLIS && snapshot.status == "fresh" -> "stale"
            else -> snapshot.status
        }
        val mayShowValues = !snapshot.privacyMasked &&
            snapshot.source != "unavailable" &&
            validUntil != null &&
            status in setOf("fresh", "stale")

        return WidgetDisplay(
            profileName = snapshot.profileName,
            appearance = snapshot.appearance,
            kind = snapshot.kind,
            source = snapshot.source,
            status = status,
            selectedFields = snapshot.selectedFields.toSet(),
            selectedFieldOrder = snapshot.selectedFields,
            observedAtMillis = snapshot.observedAtMillis,
            energyWindowStartMillis = snapshot.energyWindowStartMillis,
            energyWindowEndMillis = snapshot.energyWindowEndMillis,
            validUntilMillis = snapshot.validUntilMillis,
            batteryPct = if (mayShowValues) snapshot.values["battery_pct"] as? Double else null,
            rangeKm = if (mayShowValues) snapshot.values["range_km"] as? Double else null,
            charging = if (mayShowValues) snapshot.values["charging"] as? Boolean else null,
            locked = if (mayShowValues) snapshot.values["locked"] as? Boolean else null,
            energyKwh = if (mayShowValues) snapshot.values["energy_kwh"] as? Double else null,
            energyCostEur = if (mayShowValues) snapshot.values["energy_cost_eur"] as? Double else null,
            demo = snapshot.source == "demo",
        )
    }

    fun receiptClockIsValid(
        snapshot: SafeSnapshot,
        nowWallMillis: Long,
        nowElapsedMillis: Long,
        currentBootCount: Int? = null,
    ): Boolean {
        if (nowWallMillis <= 0L || nowElapsedMillis < 0L) return false
        val receiptBootCount = snapshot.receiptBootCount ?: return false
        val observedBootCount = currentBootCount ?: return false
        if (receiptBootCount < 0 || observedBootCount < 0 || receiptBootCount != observedBootCount) return false
        if (nowWallMillis < snapshot.receiptWallMillis || nowElapsedMillis < snapshot.receiptElapsedMillis) return false
        val wallDelta = nowWallMillis - snapshot.receiptWallMillis
        val elapsedDelta = nowElapsedMillis - snapshot.receiptElapsedMillis
        val drift = if (wallDelta >= elapsedDelta) wallDelta - elapsedDelta else elapsedDelta - wallDelta
        return drift <= MAX_CLOCK_DRIFT_MILLIS
    }

    private fun fields(value: Any?): List<String>? {
        val raw = value as? List<*> ?: return null
        if (raw.isEmpty() || raw.size > MAX_SELECTED_FIELDS) return null
        if (raw.any { it !is String || it !in allowedFields }) return null
        val strings = raw.filterIsInstance<String>()
        if (strings.toSet().size != strings.size) return null
        return strings.toList()
    }

    private fun safeProfileName(value: Any?): String? {
        val name = value as? String ?: return null
        val cleaned = name.trim()
        if (cleaned.isEmpty() || cleaned.length > 60 || cleaned.any(Char::isISOControl)) return null
        return cleaned
    }

    private fun normalizeValue(field: String, value: Any?): Any? {
        if (value == null) return null
        return when (field) {
            "battery_pct" -> finiteNumber(value)?.takeIf { it in 0.0..100.0 }
            "range_km" -> finiteNumber(value)?.takeIf { it in 0.0..5000.0 }
            "charging", "locked" -> value as? Boolean
            "energy_kwh" -> finiteNumber(value)?.takeIf { it in 0.0..1_000_000_000.0 }
            "energy_cost_eur" -> finiteNumber(value)?.takeIf { it in -1_000_000_000.0..1_000_000_000.0 }
            else -> null
        }
    }

    private data class EnergyWindow(val startedAtMillis: Long, val endedAtMillis: Long)

    private fun validateEnergySnapshot(
        snapshot: Map<String, Any?>,
        selectedFields: List<String>,
        rawValues: Map<*, *>,
        status: String,
        privacyMode: String,
        nowWallMillis: Long,
    ): EnergyWindow? {
        if (!snapshot.containsKey("generated_at") || !snapshot.containsKey("server_received_at") ||
            !snapshot.containsKey("observed_at") || !snapshot.containsKey("valid_until") ||
            !snapshot.containsKey("reason_code")
        ) return null
        val window = snapshot["energy_window"] as? Map<*, *> ?: return null
        val startedAt = parseUtcInstant(window["started_at"]) ?: return null
        val endedAt = parseUtcInstant(window["ended_at"]) ?: return null
        val observedAt = parseUtcInstant(snapshot["observed_at"]) ?: return null
        val receivedAt = parseUtcInstant(snapshot["server_received_at"]) ?: return null
        val generatedAt = parseUtcInstant(snapshot["generated_at"]) ?: return null
        if (endedAt != observedAt || receivedAt != observedAt) return null
        val durationMillis = try {
            Duration.between(startedAt, endedAt).toMillis()
        } catch (_: ArithmeticException) {
            return null
        }
        if (durationMillis <= 0L || absDifference(durationMillis, ENERGY_WINDOW_MILLIS) > ENERGY_WINDOW_TOLERANCE_MILLIS) {
            return null
        }

        val now = try {
            Instant.ofEpochMilli(nowWallMillis)
        } catch (_: DateTimeParseException) {
            return null
        }
        if (generatedAt.isAfter(now.plusMillis(CLOCK_SKEW_MILLIS)) ||
            endedAt.isAfter(now.plusMillis(CLOCK_SKEW_MILLIS))
        ) return null
        val generationLag = try {
            Duration.between(endedAt, generatedAt).toMillis()
        } catch (_: ArithmeticException) {
            return null
        }
        if (generationLag < -1_000L || generationLag > ENERGY_GENERATION_TOLERANCE_MILLIS) return null

        val reasonCode = snapshot["reason_code"] as? String ?: return null
        when (status) {
            "fresh" -> {
                if (reasonCode != "energy_calculated" || parseUtcInstant(snapshot["valid_until"]) == null) return null
                if (privacyMode == "visible" && selectedFields.any { normalizeValue(it, rawValues[it]) == null }) {
                    return null
                }
            }
            "unavailable" -> {
                if (snapshot["valid_until"] != null || selectedFields.any { rawValues[it] != null }) return null
            }
            else -> return null
        }
        return try {
            EnergyWindow(startedAt.toEpochMilli(), endedAt.toEpochMilli())
        } catch (_: ArithmeticException) {
            null
        }
    }

    private fun parseUtcInstant(value: Any?): Instant? {
        val text = value as? String ?: return null
        if (!utcTimestamp.matches(text)) return null
        return try {
            Instant.parse(text)
        } catch (_: DateTimeParseException) {
            null
        }
    }

    private fun isThirtyDayWindow(startedAtMillis: Long, endedAtMillis: Long): Boolean {
        val durationMillis = try {
            Duration.between(Instant.ofEpochMilli(startedAtMillis), Instant.ofEpochMilli(endedAtMillis)).toMillis()
        } catch (_: ArithmeticException) {
            return false
        }
        return durationMillis > 0L && absDifference(durationMillis, ENERGY_WINDOW_MILLIS) <= ENERGY_WINDOW_TOLERANCE_MILLIS
    }

    private fun absDifference(left: Long, right: Long): Long =
        if (left >= right) left - right else right - left

    private fun finiteNumber(value: Any): Double? {
        val number = value as? Number ?: return null
        val parsed = number.toDouble()
        return parsed.takeIf { it.isFinite() }
    }

    private fun exactLong(value: Any?): Long? {
        val number = value as? Number ?: return null
        val parsed = number.toDouble()
        if (!parsed.isFinite() || parsed < Long.MIN_VALUE.toDouble() || parsed > Long.MAX_VALUE.toDouble()) return null
        val asLong = parsed.toLong()
        return asLong.takeIf { it.toDouble() == parsed }
    }

    private fun nullableExactLong(value: Any?): Long? = if (value == null) null else exactLong(value)

    private fun parseInstant(value: Any?): Long? {
        val text = value as? String ?: return null
        return try {
            OffsetDateTime.parse(text).toInstant().toEpochMilli()
        } catch (_: DateTimeParseException) {
            null
        } catch (_: ArithmeticException) {
            null
        }
    }

    private fun ageMillis(nowMillis: Long, observedAtMillis: Long): Long {
        if (observedAtMillis >= nowMillis) return 0L
        if (observedAtMillis < 0L && nowMillis > Long.MAX_VALUE + observedAtMillis) return Long.MAX_VALUE
        return nowMillis - observedAtMillis
    }

    private fun safeAdd(left: Long, right: Long): Long =
        if (right > 0L && left > Long.MAX_VALUE - right) Long.MAX_VALUE else left + right
}

/** Pure consent gate: persisted opt-in is insufficient without the separate Keystore marker. */
internal object WidgetConsentPolicy {
    fun isEnabled(preferenceEnabled: Boolean, markerPresent: Boolean, blockedInProcess: Boolean): Boolean =
        preferenceEnabled && markerPresent && !blockedInProcess

    fun enableSucceeded(preferenceCommitted: Boolean, markerCreated: Boolean): Boolean =
        preferenceCommitted && markerCreated

    fun disableSucceeded(
        preferenceCommitted: Boolean,
        markerRemoved: Boolean,
        snapshotPurged: Boolean,
    ): Boolean = preferenceCommitted && markerRemoved && snapshotPurged
}
