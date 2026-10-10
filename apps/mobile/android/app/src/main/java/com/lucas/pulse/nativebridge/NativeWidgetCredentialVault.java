package com.lucas.pulse.nativebridge;

import android.content.Context;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.AtomicFile;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.security.SecureRandom;
import java.util.regex.Pattern;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/** Encrypted, no-backup storage for the widget grant only; never exposed to the renderer. */
public final class NativeWidgetCredentialVault {
    private static final String KEY_ALIAS = "pulse.native.widget.credentials.v1";
    private static final byte[] MAGIC = new byte[] {'P', 'N', 'W', '1'};
    private static final int NONCE_BYTES = 12;
    private static final int TAG_BITS = 128;
    private static final int MAX_FILE_BYTES = 8192;
    private static final Pattern ID = Pattern.compile("[a-f0-9]{32}");
    private static final Pattern OPAQUE_TOKEN = Pattern.compile("pw_[A-Za-z0-9_-]{43}");
    private static final Pattern INSTALLATION_ID = Pattern.compile("[A-Za-z0-9_-]{43}");
    private static final Object LOCK = new Object();

    private final Context context;
    private final AtomicFile file;

    public NativeWidgetCredentialVault(Context context) {
        this.context = context.getApplicationContext();
        this.file = new AtomicFile(new File(this.context.getNoBackupFilesDir(), "native-widget-credential.bin"));
    }

    /** Gets or creates a 256-bit random, encrypted installation identifier. */
    public String installationId() {
        synchronized (LOCK) {
            Record record = readLocked();
            if (record != null && validInstallation(record.installationId)) return record.installationId;
            String value = randomUrlToken();
            return writeLocked(new Record(value, null, null, 0L, null, 0L)) ? value : null;
        }
    }

    public boolean store(String apiOrigin, String grantId, String widgetToken, long expiresAtEpochMillis) {
        if (!validOrigin(apiOrigin) || grantId == null || !ID.matcher(grantId).matches()
                || widgetToken == null || !OPAQUE_TOKEN.matcher(widgetToken).matches()
                || expiresAtEpochMillis <= System.currentTimeMillis()
                || expiresAtEpochMillis - System.currentTimeMillis() > 366L * 24 * 60 * 60 * 1000) return false;
        synchronized (LOCK) {
            Record existing = readLocked();
            String installationId = existing != null && validInstallation(existing.installationId)
                    ? existing.installationId : randomUrlToken();
            long generation = existing == null || existing.generation == Long.MAX_VALUE
                    ? 1L : existing.generation + 1L;
            return writeLocked(new Record(installationId, grantId, widgetToken, expiresAtEpochMillis, apiOrigin, generation));
        }
    }

    /** Returns a credential only when its origin binding and server expiry remain valid. */
    public NativeWidgetCredential credential(String expectedApiOrigin) {
        synchronized (LOCK) {
            Record record = readLocked();
            if (record == null || !validOrigin(expectedApiOrigin)
                    || record.grantId == null || record.widgetToken == null
                    || !expectedApiOrigin.equals(record.apiOrigin)) return null;
            if (record.expiresAtEpochMillis <= System.currentTimeMillis()) return null;
            if (!ID.matcher(record.grantId).matches() || !OPAQUE_TOKEN.matcher(record.widgetToken).matches()
                    || !validInstallation(record.installationId)) {
                clearLocked();
                return null;
            }
            return new NativeWidgetCredential(record.grantId, record.widgetToken,
                    record.expiresAtEpochMillis, record.apiOrigin, record.generation);
        }
    }

    /** Detects expiry before credential() returns null, so callers can revoke consent and cached data too. */
    public boolean credentialExpired(String expectedApiOrigin) {
        synchronized (LOCK) {
            Record record = readLocked();
            return record != null && validOrigin(expectedApiOrigin)
                    && expectedApiOrigin.equals(record.apiOrigin)
                    && record.grantId != null && record.widgetToken != null
                    && record.expiresAtEpochMillis > 0L
                    && record.expiresAtEpochMillis <= System.currentTimeMillis();
        }
    }

    /** Rechecks the full encrypted record after a network request before its snapshot can be accepted. */
    public boolean isCurrent(NativeWidgetCredential expected, String expectedApiOrigin) {
        if (expected == null || !validOrigin(expectedApiOrigin)) return false;
        synchronized (LOCK) {
            Record record = readLocked();
            return record != null && expectedApiOrigin.equals(record.apiOrigin)
                    && expectedApiOrigin.equals(expected.apiOrigin)
                    && expected.expiresAtEpochMillis > System.currentTimeMillis()
                    && record.expiresAtEpochMillis == expected.expiresAtEpochMillis
                    && record.generation == expected.generation
                    && record.grantId != null && record.grantId.equals(expected.grantId)
                    && record.widgetToken != null && record.widgetToken.equals(expected.widgetToken);
        }
    }

    /** Erases the grant while preserving the installation binding for a subsequent link. */
    public void clearGrant() {
        synchronized (LOCK) {
            Record record = readLocked();
            if (record == null || !validInstallation(record.installationId)) {
                clearLocked();
                ensureClearedLocked();
            } else {
                long generation = record.generation == Long.MAX_VALUE ? 1L : record.generation + 1L;
                if (!writeLocked(new Record(record.installationId, null, null, 0L, null, generation))) {
                    throw new IllegalStateException("Widget credentials could not be cleared.");
                }
            }
        }
    }

    /** Erases all widget credentials and the device pseudonym. */
    public void clear() {
        synchronized (LOCK) {
            clearLocked();
            ensureClearedLocked();
        }
    }

    private Record readLocked() {
        try {
            File base = file.getBaseFile();
            if (!base.isFile() || base.length() < MAGIC.length + 1 + NONCE_BYTES + 16
                    || base.length() > MAX_FILE_BYTES) return null;
            byte[] record = new byte[(int) base.length()];
            try (FileInputStream input = file.openRead()) {
                int offset = 0;
                while (offset < record.length) {
                    int read = input.read(record, offset, record.length - offset);
                    if (read < 0) return null;
                    offset += read;
                }
                if (input.read() != -1) return null;
            }
            for (int index = 0; index < MAGIC.length; index++) if (record[index] != MAGIC[index]) return null;
            int nonceLength = record[MAGIC.length] & 0xff;
            if (nonceLength != NONCE_BYTES) return null;
            int cipherOffset = MAGIC.length + 1 + nonceLength;
            if (record.length <= cipherOffset) return null;
            byte[] nonce = new byte[nonceLength];
            System.arraycopy(record, MAGIC.length + 1, nonce, 0, nonceLength);
            byte[] encrypted = new byte[record.length - cipherOffset];
            System.arraycopy(record, cipherOffset, encrypted, 0, encrypted.length);
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, encryptionKey(), new GCMParameterSpec(TAG_BITS, nonce));
            cipher.updateAAD(MAGIC);
            byte[] plain = cipher.doFinal(encrypted);
            JSONObject json = new JSONObject(new String(plain, StandardCharsets.UTF_8));
            if (json.getInt("version") != 1) return null;
            String installation = nullableString(json, "installation_id");
            String grant = nullableString(json, "grant_id");
            String token = nullableString(json, "widget_token");
            long expiry = json.optLong("expires_at_ms", 0L);
            String origin = nullableString(json, "api_origin");
            long generation = json.optLong("generation", 0L);
            if (!validInstallation(installation) || generation < 0L) return null;
            if (grant == null || token == null) {
                if (grant != null || token != null || expiry != 0L || origin != null) return null;
            } else if (!ID.matcher(grant).matches() || !OPAQUE_TOKEN.matcher(token).matches()
                    || !validOrigin(origin) || expiry <= 0L) {
                return null;
            }
            return new Record(installation, grant, token, expiry, origin, generation);
        } catch (Exception ignored) {
            clearFileLocked();
            return null;
        }
    }

    private boolean writeLocked(Record record) {
        if (record == null || !validInstallation(record.installationId)) return false;
        try {
            JSONObject json = new JSONObject()
                    .put("version", 1)
                    .put("installation_id", record.installationId)
                    .put("grant_id", record.grantId == null ? JSONObject.NULL : record.grantId)
                    .put("widget_token", record.widgetToken == null ? JSONObject.NULL : record.widgetToken)
                    .put("expires_at_ms", record.expiresAtEpochMillis)
                    .put("api_origin", record.apiOrigin == null ? JSONObject.NULL : record.apiOrigin)
                    .put("generation", record.generation);
            byte[] plain = json.toString().getBytes(StandardCharsets.UTF_8);
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.ENCRYPT_MODE, encryptionKey());
            cipher.updateAAD(MAGIC);
            byte[] encrypted = cipher.doFinal(plain);
            byte[] nonce = cipher.getIV();
            if (nonce == null || nonce.length != NONCE_BYTES) return false;
            ByteArrayOutputStream output = new ByteArrayOutputStream(MAGIC.length + 1 + nonce.length + encrypted.length);
            output.write(MAGIC);
            output.write(nonce.length);
            output.write(nonce);
            output.write(encrypted);
            byte[] bytes = output.toByteArray();
            if (bytes.length > MAX_FILE_BYTES) return false;
            File parent = file.getBaseFile().getParentFile();
            if (parent == null || (!parent.isDirectory() && !parent.mkdirs())) return false;
            java.io.FileOutputStream stream = file.startWrite();
            try {
                stream.write(bytes);
                file.finishWrite(stream);
                return true;
            } catch (Exception ignored) {
                file.failWrite(stream);
                return false;
            }
        } catch (Exception ignored) {
            return false;
        }
    }

    private SecretKey encryptionKey() throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        java.security.Key existing = store.getKey(KEY_ALIAS, null);
        if (existing instanceof SecretKey) return (SecretKey) existing;
        KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
        generator.init(new KeyGenParameterSpec.Builder(KEY_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .setRandomizedEncryptionRequired(true)
                .build());
        return generator.generateKey();
    }

    private void clearLocked() {
        clearFileLocked();
        try {
            KeyStore store = KeyStore.getInstance("AndroidKeyStore");
            store.load(null);
            if (store.containsAlias(KEY_ALIAS)) store.deleteEntry(KEY_ALIAS);
        } catch (Exception ignored) {
            // Best effort; the unreadable file remains inaccessible after the key is removed.
        }
    }

    private void ensureClearedLocked() {
        try {
            KeyStore store = KeyStore.getInstance("AndroidKeyStore");
            store.load(null);
            if (file.getBaseFile().exists() || store.containsAlias(KEY_ALIAS)) {
                throw new IllegalStateException("Widget credentials could not be cleared.");
            }
        } catch (IllegalStateException error) {
            throw error;
        } catch (Exception ignored) {
            throw new IllegalStateException("Widget credentials could not be cleared.");
        }
    }

    private void clearFileLocked() {
        try { file.delete(); } catch (Exception ignored) { }
    }

    private static String randomUrlToken() {
        byte[] random = new byte[32];
        new SecureRandom().nextBytes(random);
        return Base64Url.encode(random);
    }

    private static boolean validInstallation(String value) {
        return value != null && INSTALLATION_ID.matcher(value).matches();
    }

    private static String nullableString(JSONObject json, String key) {
        Object value = json.opt(key);
        return value instanceof String ? (String) value : null;
    }

    private static boolean validOrigin(String value) {
        if (value == null || value.length() > 300) return false;
        try {
            java.net.URI uri = new java.net.URI(value);
            return "https".equalsIgnoreCase(uri.getScheme()) && uri.getHost() != null
                    && uri.getRawUserInfo() == null && (uri.getRawPath() == null || uri.getRawPath().isEmpty())
                    && uri.getRawQuery() == null && uri.getRawFragment() == null
                    && (uri.getPort() == -1 || (uri.getPort() >= 1 && uri.getPort() <= 65535))
                    && uri.normalize().equals(uri);
        } catch (Exception ignored) {
            return false;
        }
    }

    private static final class Record {
        final String installationId;
        final String grantId;
        final String widgetToken;
        final long expiresAtEpochMillis;
        final String apiOrigin;
        final long generation;

        Record(String installationId, String grantId, String widgetToken, long expiresAtEpochMillis,
                String apiOrigin, long generation) {
            this.installationId = installationId;
            this.grantId = grantId;
            this.widgetToken = widgetToken;
            this.expiresAtEpochMillis = expiresAtEpochMillis;
            this.apiOrigin = apiOrigin;
            this.generation = generation;
        }
    }

    public static final class NativeWidgetCredential {
        public final String grantId;
        public final String widgetToken;
        public final long expiresAtEpochMillis;
        public final String apiOrigin;
        public final long generation;

        private NativeWidgetCredential(String grantId, String widgetToken, long expiresAtEpochMillis,
                String apiOrigin, long generation) {
            this.grantId = grantId;
            this.widgetToken = widgetToken;
            this.expiresAtEpochMillis = expiresAtEpochMillis;
            this.apiOrigin = apiOrigin;
            this.generation = generation;
        }

        @Override public String toString() {
            return "NativeWidgetCredential{grantId=[redacted], widgetToken=[redacted], expiresAtEpochMillis="
                    + expiresAtEpochMillis + ", apiOrigin=[redacted]}";
        }
    }
}
