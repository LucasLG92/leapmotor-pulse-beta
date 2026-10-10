package com.lucas.pulse.nativebridge;

/** Minimal unpadded Base64URL encoder available on every supported Android API level. */
final class Base64Url {
    private static final char[] ALPHABET =
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_".toCharArray();

    private Base64Url() {}

    static String encode(byte[] bytes) {
        if (bytes == null) throw new IllegalArgumentException("bytes");
        StringBuilder result = new StringBuilder((bytes.length * 4 + 2) / 3);
        int index = 0;
        while (index + 3 <= bytes.length) {
            int value = ((bytes[index++] & 0xff) << 16)
                    | ((bytes[index++] & 0xff) << 8)
                    | (bytes[index++] & 0xff);
            result.append(ALPHABET[(value >>> 18) & 63]);
            result.append(ALPHABET[(value >>> 12) & 63]);
            result.append(ALPHABET[(value >>> 6) & 63]);
            result.append(ALPHABET[value & 63]);
        }
        int remaining = bytes.length - index;
        if (remaining == 1) {
            int value = (bytes[index] & 0xff) << 16;
            result.append(ALPHABET[(value >>> 18) & 63]);
            result.append(ALPHABET[(value >>> 12) & 63]);
        } else if (remaining == 2) {
            int value = ((bytes[index] & 0xff) << 16) | ((bytes[index + 1] & 0xff) << 8);
            result.append(ALPHABET[(value >>> 18) & 63]);
            result.append(ALPHABET[(value >>> 12) & 63]);
            result.append(ALPHABET[(value >>> 6) & 63]);
        }
        return result.toString();
    }
}
