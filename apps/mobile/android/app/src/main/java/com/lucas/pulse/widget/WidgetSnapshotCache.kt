package com.lucas.pulse.widget

import android.app.KeyguardManager
import android.content.Context
import android.os.Build
import android.os.SystemClock
import android.os.UserManager
import android.provider.Settings
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.AtomicFile
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.File
import java.nio.charset.StandardCharsets
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Opt-in, encrypted, no-backup storage for the small allowlisted widget view.
 * This class never performs network or OAuth work and never retains the input JSON.
 */
class WidgetSnapshotCache(context: Context) {
    private val appContext = context.applicationContext

    fun setEnabled(enabled: Boolean) {
        synchronized(CACHE_LOCK) {
            if (enabled) enableLocked() else disableLocked()
        }
    }

    fun isEnabled(): Boolean = try {
        WidgetConsentPolicy.isEnabled(
            preferenceEnabled = preferences().getBoolean(ENABLED_KEY, false),
            markerPresent = consentMarkerPresent(),
            blockedInProcess = consentFailClosedInProcess,
        )
    } catch (_: Exception) {
        false
    }

    /** Deletes the snapshot and its key; the user's cache opt-in is preserved. */
    fun clear() {
        synchronized(CACHE_LOCK) {
            if (!purgeSnapshotLocked()) throw IllegalStateException(CACHE_CLEAR_FAILED)
        }
    }

    fun disableAndClear() {
        setEnabled(false)
    }

    /**
     * Accepts a Pulse WidgetSnapshot JSONObject, strips it to its safe subset,
     * and stores only ciphertext. Invalid input clears the previous snapshot.
     */
    fun accept(snapshot: JSONObject): Boolean = synchronized(CACHE_LOCK) {
        if (!isEnabled() || !isDeviceUnlocked()) return@synchronized false
        try {
            val nowWall = System.currentTimeMillis()
            val nowElapsed = SystemClock.elapsedRealtime()
            val sanitized = WidgetPolicy.sanitizeSnapshot(
                snapshotToMap(snapshot),
                nowWall,
                nowElapsed,
                currentBootCount(),
            )
            if (sanitized == null) {
                purgeSnapshotLocked()
                return@synchronized false
            }
            val plain = encodeRecord(sanitized)
            val encrypted = encrypt(plain) ?: run {
                purgeSnapshotLocked()
                return@synchronized false
            }
            val saved = writeEncryptedLocked(encrypted)
            if (!saved) purgeSnapshotLocked()
            saved
        } catch (_: Exception) {
            purgeSnapshotLocked()
            false
        }
    }

    /** Returns only a validated display model; locked, expired or corrupt data fails closed. */
    fun display(): WidgetDisplay = synchronized(CACHE_LOCK) {
        if (!isEnabled()) return@synchronized WidgetDisplay.disabled()
        if (!isDeviceUnlocked()) return@synchronized WidgetDisplay.locked()
        try {
            val encrypted = readEncryptedLocked() ?: return@synchronized WidgetDisplay.setup()
            val plain = decrypt(encrypted) ?: run {
                purgeSnapshotLocked()
                return@synchronized WidgetDisplay.setup()
            }
            val record = JSONObject(String(plain, StandardCharsets.UTF_8))
            val restored = WidgetPolicy.restoreRecord(recordToMap(record))
            val safeDisplay = restored?.let {
                WidgetPolicy.display(it, System.currentTimeMillis(), SystemClock.elapsedRealtime(), currentBootCount())
            }
            if (safeDisplay == null) {
                purgeSnapshotLocked()
                WidgetDisplay.setup()
            } else {
                safeDisplay
            }
        } catch (_: Exception) {
            purgeSnapshotLocked()
            WidgetDisplay.setup()
        }
    }

    private fun snapshotToMap(snapshot: JSONObject): Map<String, Any?> {
        val profileJson = snapshot.optJSONObject("profile")
        val rawFields = profileJson?.optJSONArray("fields")
        val fields = if (rawFields == null) {
            null
        } else if (rawFields.length() > MAX_SELECTED_FIELDS) {
            listOf(INVALID_FIELD_SENTINEL)
        } else {
            List(rawFields.length()) { index -> rawFields.opt(index).jsonValue() }
        }
        val selectedNames = fields?.filterIsInstance<String>()?.filter { it in ALLOWED_FIELDS }.orEmpty()
        val rawValues = snapshot.optJSONObject("values")
        val values = rawValues?.let { source ->
            selectedNames.associateWith { field -> source.opt(field).jsonValue() }
        }
        val profile = linkedMapOf<String, Any?>()
        if (profileJson != null) {
            profile["name"] = profileJson.opt("name").jsonValue()
            profile["fields"] = fields
            profile["appearance"] = profileJson.opt("appearance").jsonValue()
            profile["max_age_minutes"] = profileJson.opt("max_age_minutes").jsonValue()
            profile["privacy_mode"] = profileJson.opt("privacy_mode").jsonValue()
        }
        val projected = linkedMapOf<String, Any?>(
            "schema_version" to snapshot.opt("schema_version").jsonValue(),
            "profile" to profile.takeIf { profileJson != null },
            "source" to snapshot.opt("source").jsonValue(),
            "physical_freshness" to snapshot.opt("physical_freshness").jsonValue(),
            "temporal_status" to snapshot.opt("temporal_status").jsonValue(),
            "values" to values,
        )
        if (snapshot.has("kind")) projected["kind"] = snapshot.opt("kind").jsonValue()
        if (snapshot.has("energy_window")) {
            val rawWindow = snapshot.opt("energy_window")
            projected["energy_window"] = if (rawWindow is JSONObject) {
                linkedMapOf(
                    "started_at" to rawWindow.opt("started_at").jsonValue(),
                    "ended_at" to rawWindow.opt("ended_at").jsonValue(),
                )
            } else {
                rawWindow.jsonValue()
            }
        }
        for (key in listOf("observed_at", "valid_until", "generated_at", "server_received_at", "reason_code")) {
            if (snapshot.has(key)) projected[key] = snapshot.opt(key).jsonValue()
        }
        return projected
    }

    private fun encodeRecord(snapshot: WidgetPolicy.SafeSnapshot): ByteArray {
        val fields = JSONArray()
        snapshot.selectedFields.forEach(fields::put)
        val values = JSONObject()
        snapshot.values.forEach { (key, value) -> values.put(key, value) }
        val record = JSONObject()
            .put("version", RECORD_VERSION)
            .put("profile_name", snapshot.profileName ?: JSONObject.NULL)
            .put("appearance", snapshot.appearance)
            .put("kind", snapshot.kind)
            .put("source", snapshot.source)
            .put("status", snapshot.status)
            .put("fields", fields)
            .put("energy_window_start_ms", snapshot.energyWindowStartMillis ?: JSONObject.NULL)
            .put("energy_window_end_ms", snapshot.energyWindowEndMillis ?: JSONObject.NULL)
            .put("privacy_masked", snapshot.privacyMasked)
            .put("max_age_ms", snapshot.maxAgeMillis)
            .put("observed_at_ms", snapshot.observedAtMillis ?: JSONObject.NULL)
            .put("valid_until_ms", snapshot.validUntilMillis ?: JSONObject.NULL)
            .put("values_valid_until_ms", snapshot.valuesValidUntilMillis ?: JSONObject.NULL)
            .put("cache_until_ms", snapshot.cacheUntilMillis)
            .put("receipt_wall_ms", snapshot.receiptWallMillis)
            .put("receipt_elapsed_ms", snapshot.receiptElapsedMillis)
            .put("receipt_boot_count", snapshot.receiptBootCount ?: JSONObject.NULL)
            .put("values", values)
        return record.toString().toByteArray(StandardCharsets.UTF_8)
    }

    private fun recordToMap(record: JSONObject): Map<String, Any?> {
        val rawFields = record.optJSONArray("fields")
        val fields: List<Any?>? = if (rawFields == null) {
            null
        } else if (rawFields.length() > MAX_SELECTED_FIELDS) {
            listOf(INVALID_FIELD_SENTINEL)
        } else {
            List(rawFields.length()) { index -> rawFields.opt(index).jsonValue() }
        }
        val selectedNames = fields?.filterIsInstance<String>()?.filter { it in ALLOWED_FIELDS }.orEmpty()
        val valuesObject = record.optJSONObject("values")
        val values = linkedMapOf<String, Any?>()
        if (valuesObject != null) {
            val keys = valuesObject.keys()
            while (keys.hasNext()) {
                val key = keys.next()
                if (key !in selectedNames) return emptyMap()
                values[key] = valuesObject.opt(key).jsonValue()
            }
        }
        val mapped = linkedMapOf<String, Any?>(
            "version" to record.opt("version").jsonValue(),
            "profile_name" to record.opt("profile_name").jsonValue(),
            "appearance" to record.opt("appearance").jsonValue(),
            "source" to record.opt("source").jsonValue(),
            "status" to record.opt("status").jsonValue(),
            "fields" to fields,
            "privacy_masked" to record.opt("privacy_masked").jsonValue(),
            "max_age_ms" to record.opt("max_age_ms").jsonValue(),
            "observed_at_ms" to record.opt("observed_at_ms").jsonValue(),
            "valid_until_ms" to record.opt("valid_until_ms").jsonValue(),
            "values_valid_until_ms" to record.opt("values_valid_until_ms").jsonValue(),
            "cache_until_ms" to record.opt("cache_until_ms").jsonValue(),
            "receipt_wall_ms" to record.opt("receipt_wall_ms").jsonValue(),
            "receipt_elapsed_ms" to record.opt("receipt_elapsed_ms").jsonValue(),
            "receipt_boot_count" to record.opt("receipt_boot_count").jsonValue(),
            "values" to values,
        )
        if (record.has("kind")) mapped["kind"] = record.opt("kind").jsonValue()
        if (record.has("energy_window_start_ms")) {
            mapped["energy_window_start_ms"] = record.opt("energy_window_start_ms").jsonValue()
        }
        if (record.has("energy_window_end_ms")) {
            mapped["energy_window_end_ms"] = record.opt("energy_window_end_ms").jsonValue()
        }
        return mapped
    }

    private fun encrypt(plain: ByteArray): EncryptedRecord? {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, encryptionKey())
        val encryptedBytes = cipher.doFinal(plain)
        if (encryptedBytes.size > MAX_CIPHERTEXT_BYTES) return null
        val nonce = cipher.iv ?: return null
        if (nonce.size != GCM_NONCE_BYTES) return null
        val output = ByteArrayOutputStream(MAGIC.size + 1 + nonce.size + encryptedBytes.size)
        output.write(MAGIC)
        output.write(nonce.size)
        output.write(nonce)
        output.write(encryptedBytes)
        return EncryptedRecord(output.toByteArray(), encryptedBytes.size)
    }

    private fun decrypt(record: EncryptedRecord): ByteArray? {
        val input = ByteArrayInputStream(record.bytes)
        val magic = ByteArray(MAGIC.size)
        if (input.read(magic) != MAGIC.size || !magic.contentEquals(MAGIC)) return null
        val nonceLength = input.read()
        if (nonceLength != GCM_NONCE_BYTES || input.available() <= nonceLength) return null
        val nonce = ByteArray(nonceLength)
        if (input.read(nonce) != nonceLength) return null
        val ciphertext = ByteArray(input.available())
        if (input.read(ciphertext) != ciphertext.size || ciphertext.size > MAX_CIPHERTEXT_BYTES) return null
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.DECRYPT_MODE, encryptionKey(), GCMParameterSpec(GCM_TAG_BITS, nonce))
        return cipher.doFinal(ciphertext)
    }

    private fun writeEncryptedLocked(record: EncryptedRecord): Boolean {
        if (record.ciphertextBytes > MAX_CIPHERTEXT_BYTES || record.bytes.size > MAX_FILE_BYTES) return false
        val file = atomicFile()
        return try {
            file.baseFile.parentFile?.mkdirs()
            val stream = file.startWrite()
            try {
                stream.write(record.bytes)
                file.finishWrite(stream)
                true
            } catch (error: Exception) {
                file.failWrite(stream)
                false
            }
        } catch (_: Exception) {
            false
        }
    }

    private fun readEncryptedLocked(): EncryptedRecord? {
        val file = atomicFile()
        val base = file.baseFile
        val backup = File(base.path + ".bak")
        if (!base.exists() && !backup.exists()) return null
        val atomicReadCandidate = if (backup.exists()) backup else base
        if (!atomicReadCandidate.isFile || atomicReadCandidate.length() !in MIN_FILE_BYTES.toLong()..MAX_FILE_BYTES.toLong()) {
            purgeSnapshotLocked()
            return null
        }
        return try {
            val bytes = file.openRead().use { readBounded(it, MAX_FILE_BYTES) }
            if (bytes.size !in MIN_FILE_BYTES..MAX_FILE_BYTES) {
                purgeSnapshotLocked()
                return null
            }
            val ciphertextBytes = bytes.size - MAGIC.size - 1 - GCM_NONCE_BYTES
            if (ciphertextBytes !in 16..MAX_CIPHERTEXT_BYTES) {
                purgeSnapshotLocked()
                return null
            }
            EncryptedRecord(bytes, ciphertextBytes)
        } catch (_: Exception) {
            purgeSnapshotLocked()
            null
        }
    }

    private fun encryptionKey(): SecretKey {
        val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        (keyStore.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }

        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
        val builder = KeyGenParameterSpec.Builder(
            KEY_ALIAS,
            KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
        ).setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setRandomizedEncryptionRequired(true)
            .setUserAuthenticationRequired(false)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) builder.setUnlockedDeviceRequired(true)
        generator.init(builder.build())
        return generator.generateKey()
    }

    private fun isDeviceUnlocked(): Boolean = try {
        val keyguard = appContext.getSystemService(Context.KEYGUARD_SERVICE) as? KeyguardManager
        val userManager = appContext.getSystemService(Context.USER_SERVICE) as? UserManager
        keyguard != null && userManager != null && userManager.isUserUnlocked && !keyguard.isDeviceLocked
    } catch (_: Exception) {
        false
    }

    private fun currentBootCount(): Int? = try {
        Settings.Global.getInt(appContext.contentResolver, Settings.Global.BOOT_COUNT, -1)
            .takeIf { it >= 0 }
    } catch (_: Exception) {
        null
    }

    private fun preferences() = appContext.getSharedPreferences(PREFERENCES_NAME, Context.MODE_PRIVATE)

    private fun atomicFile() = AtomicFile(File(appContext.noBackupFilesDir, SNAPSHOT_FILE_NAME))

    private fun enableLocked() {
        if (isEnabled()) return
        consentFailClosedInProcess = true

        // Remove any marker from a prior/partial attempt before committing a fresh opt-in.
        val markerRemoved = deleteConsentMarker()
        val snapshotPurged = purgeSnapshotLocked()
        if (!markerRemoved || !snapshotPurged) throw IllegalStateException(CONSENT_CHANGE_FAILED)

        val preferenceCommitted = writeEnabledPreference(true)
        if (!preferenceCommitted) {
            writeEnabledPreference(false)
            deleteConsentMarker()
            purgeSnapshotLocked()
            throw IllegalStateException(CONSENT_CHANGE_FAILED)
        }

        val markerCreated = createConsentMarker()
        if (!WidgetConsentPolicy.enableSucceeded(preferenceCommitted, markerCreated)) {
            writeEnabledPreference(false)
            deleteConsentMarker()
            purgeSnapshotLocked()
            throw IllegalStateException(CONSENT_CHANGE_FAILED)
        }

        consentFailClosedInProcess = false
    }

    private fun disableLocked() {
        // The in-process gate closes before any fallible persistence operation.
        consentFailClosedInProcess = true
        val preferenceCommitted = writeEnabledPreference(false)
        val markerRemoved = deleteConsentMarker()
        val snapshotPurged = purgeSnapshotLocked()
        if (!WidgetConsentPolicy.disableSucceeded(preferenceCommitted, markerRemoved, snapshotPurged)) {
            throw IllegalStateException(CONSENT_CHANGE_FAILED)
        }
    }

    private fun writeEnabledPreference(enabled: Boolean): Boolean = try {
        preferences().edit().putBoolean(ENABLED_KEY, enabled).commit()
    } catch (_: Exception) {
        false
    }

    private fun createConsentMarker(): Boolean {
        return try {
            val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
            if (keyStore.containsAlias(CONSENT_MARKER_ALIAS)) {
                false
            } else {
                val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
                val builder = KeyGenParameterSpec.Builder(
                    CONSENT_MARKER_ALIAS,
                    KeyProperties.PURPOSE_ENCRYPT,
                ).setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setRandomizedEncryptionRequired(true)
                    .setUserAuthenticationRequired(false)
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) builder.setUnlockedDeviceRequired(true)
                generator.init(builder.build())
                generator.generateKey()
                KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }.containsAlias(CONSENT_MARKER_ALIAS)
            }
        } catch (_: Exception) {
            false
        }
    }

    private fun deleteConsentMarker(): Boolean = try {
        val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        if (keyStore.containsAlias(CONSENT_MARKER_ALIAS)) keyStore.deleteEntry(CONSENT_MARKER_ALIAS)
        !keyStore.containsAlias(CONSENT_MARKER_ALIAS)
    } catch (_: Exception) {
        false
    }

    private fun consentMarkerPresent(): Boolean = try {
        KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }.containsAlias(CONSENT_MARKER_ALIAS)
    } catch (_: Exception) {
        false
    }

    private fun deleteSnapshotLocked(): Boolean {
        return try {
            val atomic = atomicFile()
            val base = atomic.baseFile
            atomic.delete()
            listOf(base, File(base.path + ".bak"), File(base.path + ".new")).none(File::exists)
        } catch (_: Exception) {
            false
        }
    }

    private fun deleteEncryptionKeyLocked(): Boolean = try {
        val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        if (keyStore.containsAlias(KEY_ALIAS)) keyStore.deleteEntry(KEY_ALIAS)
        !keyStore.containsAlias(KEY_ALIAS)
    } catch (_: Exception) {
        false
    }

    private fun purgeSnapshotLocked(): Boolean {
        val fileRemoved = deleteSnapshotLocked()
        val keyRemoved = deleteEncryptionKeyLocked()
        return fileRemoved && keyRemoved
    }

    private fun readBounded(input: java.io.InputStream, limit: Int): ByteArray {
        val output = ByteArrayOutputStream(minOf(limit, 1024))
        val buffer = ByteArray(minOf(limit, 1024))
        var total = 0
        while (true) {
            val count = input.read(buffer, 0, minOf(buffer.size, limit - total + 1))
            if (count < 0) break
            total += count
            if (total > limit) throw IllegalStateException("Widget cache entry exceeds limit")
            output.write(buffer, 0, count)
        }
        return output.toByteArray()
    }

    private fun Any?.jsonValue(): Any? = if (this === JSONObject.NULL) null else this

    private data class EncryptedRecord(val bytes: ByteArray, val ciphertextBytes: Int)

    private companion object {
        private const val PREFERENCES_NAME = "pulse_widget_preferences"
        private const val ENABLED_KEY = "snapshot_cache_enabled"
        private const val SNAPSHOT_FILE_NAME = "pulse-widget-snapshot.bin"
        private const val KEY_ALIAS = "com.lucas.pulse.widget.snapshot.v1"
        private const val CONSENT_MARKER_ALIAS = "com.lucas.pulse.widget.consent.v1"
        private const val CONSENT_CHANGE_FAILED = "No se pudo guardar el consentimiento del widget."
        private const val CACHE_CLEAR_FAILED = "No se pudo borrar la caché del widget."
        private const val ANDROID_KEYSTORE = "AndroidKeyStore"
        private const val TRANSFORMATION = "AES/GCM/NoPadding"
        // V3 adds kind and the optional energy window; V2 restores only legacy vehicle snapshots.
        private const val RECORD_VERSION = 3
        private const val GCM_NONCE_BYTES = 12
        private const val GCM_TAG_BITS = 128
        private const val MAX_CIPHERTEXT_BYTES = 16 * 1024
        private const val MIN_FILE_BYTES = 4 + 1 + GCM_NONCE_BYTES + 16
        private const val MAX_FILE_BYTES = 4 + 1 + GCM_NONCE_BYTES + MAX_CIPHERTEXT_BYTES
        private const val MAX_SELECTED_FIELDS = WidgetPolicy.MAX_SELECTED_FIELDS
        private const val INVALID_FIELD_SENTINEL = "__invalid_widget_field__"
        private val MAGIC = byteArrayOf('P'.code.toByte(), 'W'.code.toByte(), 'C'.code.toByte(), '1'.code.toByte())
        private val ALLOWED_FIELDS = setOf(
            "battery_pct", "range_km", "charging", "locked", "energy_kwh", "energy_cost_eur",
        )
        private val CACHE_LOCK = Any()
        @Volatile private var consentFailClosedInProcess = false
    }
}
