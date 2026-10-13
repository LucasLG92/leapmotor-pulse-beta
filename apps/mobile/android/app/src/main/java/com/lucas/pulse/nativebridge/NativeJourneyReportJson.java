package com.lucas.pulse.nativebridge;

import java.util.HashSet;
import java.util.Set;

/** Strict, depth-bounded JSON syntax gate for the native journey report parser. */
final class NativeJourneyReportJson {
    static final int MAX_DEPTH = 16;

    private NativeJourneyReportJson() { }

    static boolean isStrictObject(String json) {
        if (json == null || json.isEmpty()) return false;
        Scanner scanner = new Scanner(json);
        scanner.skipWhitespace();
        if (!scanner.parseObject(1)) return false;
        scanner.skipWhitespace();
        return scanner.atEnd();
    }

    private static final class Scanner {
        private final String input;
        private int offset;

        Scanner(String input) {
            this.input = input;
        }

        boolean parseObject(int depth) {
            if (depth > MAX_DEPTH || !consume('{')) return false;
            skipWhitespace();
            if (consume('}')) return true;

            Set<String> keys = new HashSet<>();
            while (true) {
                if (peek() != '"') return false;
                String key = parseString();
                if (key == null || !keys.add(key)) return false;
                skipWhitespace();
                if (!consume(':')) return false;
                skipWhitespace();
                if (!parseValue(depth)) return false;
                skipWhitespace();
                if (consume('}')) return true;
                if (!consume(',')) return false;
                skipWhitespace();
            }
        }

        private boolean parseArray(int depth) {
            if (depth > MAX_DEPTH || !consume('[')) return false;
            skipWhitespace();
            if (consume(']')) return true;
            while (true) {
                if (!parseValue(depth)) return false;
                skipWhitespace();
                if (consume(']')) return true;
                if (!consume(',')) return false;
                skipWhitespace();
            }
        }

        private boolean parseValue(int parentDepth) {
            char next = peek();
            if (next == '{') return parseObject(parentDepth + 1);
            if (next == '[') return parseArray(parentDepth + 1);
            if (next == '"') return parseString() != null;
            if (next == 't') return consumeLiteral("true");
            if (next == 'f') return consumeLiteral("false");
            if (next == 'n') return consumeLiteral("null");
            return parseNumber();
        }

        private String parseString() {
            if (!consume('"')) return null;
            StringBuilder decoded = new StringBuilder();
            while (!atEnd()) {
                char current = input.charAt(offset++);
                if (current == '"') return decoded.toString();
                if (current < 0x20) return null;
                if (current == '\\') {
                    if (atEnd()) return null;
                    char escape = input.charAt(offset++);
                    switch (escape) {
                        case '"': decoded.append('"'); break;
                        case '\\': decoded.append('\\'); break;
                        case '/': decoded.append('/'); break;
                        case 'b': decoded.append('\b'); break;
                        case 'f': decoded.append('\f'); break;
                        case 'n': decoded.append('\n'); break;
                        case 'r': decoded.append('\r'); break;
                        case 't': decoded.append('\t'); break;
                        case 'u':
                            Character first = readHexCodeUnit();
                            if (first == null) return null;
                            if (Character.isHighSurrogate(first)) {
                                if (offset + 2 > input.length() || input.charAt(offset) != '\\'
                                        || input.charAt(offset + 1) != 'u') return null;
                                offset += 2;
                                Character second = readHexCodeUnit();
                                if (second == null || !Character.isLowSurrogate(second)) return null;
                                decoded.append(first).append(second);
                            } else if (Character.isLowSurrogate(first)) {
                                return null;
                            } else {
                                decoded.append(first);
                            }
                            break;
                        default: return null;
                    }
                    continue;
                }
                if (Character.isHighSurrogate(current)) {
                    if (atEnd() || !Character.isLowSurrogate(input.charAt(offset))) return null;
                    decoded.append(current).append(input.charAt(offset++));
                } else if (Character.isLowSurrogate(current)) {
                    return null;
                } else {
                    decoded.append(current);
                }
            }
            return null;
        }

        private Character readHexCodeUnit() {
            if (offset + 4 > input.length()) return null;
            int value = 0;
            for (int index = 0; index < 4; index++) {
                int digit = hexDigit(input.charAt(offset++));
                if (digit < 0) return null;
                value = (value << 4) | digit;
            }
            return (char) value;
        }

        private static int hexDigit(char value) {
            if (value >= '0' && value <= '9') return value - '0';
            if (value >= 'a' && value <= 'f') return value - 'a' + 10;
            if (value >= 'A' && value <= 'F') return value - 'A' + 10;
            return -1;
        }

        private boolean parseNumber() {
            int start = offset;
            if (consume('-') && atEnd()) return false;
            if (consume('0')) {
                if (isDigit(peek())) return false;
            } else {
                if (!isOneToNine(peek())) return false;
                offset++;
                while (isDigit(peek())) offset++;
            }
            if (consume('.')) {
                if (!isDigit(peek())) return false;
                while (isDigit(peek())) offset++;
            }
            if (consume('e') || consume('E')) {
                if (!consume('+')) consume('-');
                if (!isDigit(peek())) return false;
                while (isDigit(peek())) offset++;
            }
            return offset > start;
        }

        private boolean consumeLiteral(String literal) {
            if (!input.regionMatches(offset, literal, 0, literal.length())) return false;
            offset += literal.length();
            return true;
        }

        private void skipWhitespace() {
            while (!atEnd()) {
                char current = input.charAt(offset);
                if (current != ' ' && current != '\t' && current != '\r' && current != '\n') return;
                offset++;
            }
        }

        private boolean consume(char expected) {
            if (peek() != expected) return false;
            offset++;
            return true;
        }

        private char peek() {
            return atEnd() ? '\0' : input.charAt(offset);
        }

        private boolean atEnd() {
            return offset >= input.length();
        }

        private static boolean isDigit(char value) {
            return value >= '0' && value <= '9';
        }

        private static boolean isOneToNine(char value) {
            return value >= '1' && value <= '9';
        }
    }
}
