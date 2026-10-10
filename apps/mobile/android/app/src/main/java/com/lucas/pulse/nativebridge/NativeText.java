package com.lucas.pulse.nativebridge;

/** String checks kept compatible with the app's minimum Android API level. */
final class NativeText {
    private NativeText() {}

    static boolean isBlank(String value) {
        if (value == null || value.isEmpty()) return true;
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            if (!Character.isWhitespace(character) && !Character.isSpaceChar(character)) return false;
        }
        return true;
    }

    static boolean startsWithJsonObject(String value) {
        if (value == null) return false;
        int index = 0;
        while (index < value.length()) {
            char character = value.charAt(index);
            if (character != ' ' && character != '\t' && character != '\r' && character != '\n') break;
            index++;
        }
        return index < value.length() && value.charAt(index) == '{';
    }
}
