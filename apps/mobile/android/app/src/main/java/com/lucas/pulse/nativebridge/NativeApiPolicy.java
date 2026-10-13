package com.lucas.pulse.nativebridge;

import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;
import java.nio.CharBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/** Exact native UI API allowlist. OAuth and native bearer grant routes are intentionally absent. */
public final class NativeApiPolicy {
    private static final Pattern DECIMAL = Pattern.compile("[0-9]{1,9}");
    private static final Set<String> PAGE_QUERY = Collections.unmodifiableSet(
            new HashSet<>(Arrays.asList("limit", "offset")));
    private static final Set<String> JOURNEY_QUERY = Collections.unmodifiableSet(
            new HashSet<>(Arrays.asList("limit", "offset", "search", "tag")));
    private static final Set<String> MAINTENANCE_PLAN_QUERY = Collections.unmodifiableSet(
            new HashSet<>(Arrays.asList("limit", "offset", "odometer_km")));

    private NativeApiPolicy() {}

    public static boolean allows(String rawPath, String method) {
        return validate(rawPath, method);
    }

    public static boolean allowsEvents(String rawPath, String method) {
        return "GET".equals(method) && "/api/events".equals(rawPath);
    }

    private static boolean validate(String rawPath, String method) {
        if (rawPath == null || method == null || rawPath.length() > 2048
                || !method.equals(method.toUpperCase(Locale.ROOT))
                || rawPath.indexOf('#') >= 0 || rawPath.indexOf('\\') >= 0
                || rawPath.chars().anyMatch(Character::isISOControl)) return false;

        int queryAt = rawPath.indexOf('?');
        String path = queryAt < 0 ? rawPath : rawPath.substring(0, queryAt);
        String query = queryAt < 0 ? "" : rawPath.substring(queryAt + 1);
        if (queryAt >= 0 && query.isEmpty()) return false;
        if (!path.startsWith("/api/") || path.startsWith("//") || path.indexOf('%') >= 0
                || path.indexOf(':') >= 0 || path.isEmpty() || path.endsWith("/")
                || path.contains("//")) return false;
        Map<String, String> parameters = parseQuery(query);
        if (parameters == null) return false;

        if (is(method, "GET", path, "/api/session", "/api/state", "/api/telemetry",
                "/api/commands/history", "/api/widget-profiles", "/api/native-capabilities", "/api/export", "/api/charger", "/api/navigation/status")) {
            return parameters.isEmpty();
        }
        if (is(method, "GET", path, "/api/charges", "/api/telemetry/history")) {
            return boundedPageQuery(parameters);
        }
        if ("GET".equals(method) && "/api/journeys".equals(path)) {
            return journeysQuery(parameters);
        }
        if ("GET".equals(method) && "/api/maintenance-plans".equals(path)) {
            return maintenancePlansQuery(parameters);
        }
        if ("GET".equals(method) && "/api/navigation/favorites".equals(path)) {
            return parameters.isEmpty();
        }
        if (is(method, "GET", path, "/api/charger/sessions", "/api/protection/observations")) {
            return boundedPageQuery(parameters) && (!parameters.containsKey("offset")
                    || Integer.parseInt(parameters.get("offset")) <= 1000);
        }
        if ("GET".equals(method) && "/api/analytics/energy".equals(path)) {
            return parameters.isEmpty() || (parameters.size() == 1 && validDecimal(parameters.get("days"))
                    && ("30".equals(parameters.get("days")) || "90".equals(parameters.get("days"))
                    || "365".equals(parameters.get("days"))));
        }
        if ("GET".equals(method) && "/api/journeys/report".equals(path)) {
            return parameters.isEmpty() || (parameters.size() == 1 && validDecimal(parameters.get("days"))
                    && ("30".equals(parameters.get("days")) || "90".equals(parameters.get("days"))
                    || "365".equals(parameters.get("days"))));
        }
        if ("GET".equals(method) && "/api/energy/comparison".equals(path)) {
            return optionalDaysQuery(parameters);
        }
        if ("GET".equals(method) && "/api/charger/pricing-history".equals(path)) {
            String session = parameters.get("session_id");
            if (session == null || !session.matches("[a-f0-9]{32}")) return false;
            Map<String, String> page = new HashMap<>(parameters);
            page.remove("session_id");
            return boundedPageQuery(page)
                    && (!page.containsKey("limit") || Integer.parseInt(page.get("limit")) <= 20)
                    && (!page.containsKey("offset") || Integer.parseInt(page.get("offset")) <= 2048);
        }
        if ("GET".equals(method) && path.matches("/api/widget-profiles/[a-f0-9]{32}/native-grants")) {
            return parameters.isEmpty();
        }

        if (is(method, "POST", path, "/api/charges/quote", "/api/charges/requests", "/api/charges", "/api/journeys/requests", "/api/journeys", "/api/demo", "/api/commands",
                "/api/protection/event", "/api/maintenance", "/api/import", "/api/widget-profiles",
                "/api/auth/logout", "/api/navigation/places", "/api/navigation/chargers", "/api/navigation/tiles",
                "/api/maintenance-plans", "/api/navigation/favorites", "/api/energy/plan")) return parameters.isEmpty();
        if ("POST".equals(method) && path.matches("/api/maintenance-plans/[a-f0-9]{32}/complete")) {
            return parameters.isEmpty();
        }
        if ("POST".equals(method) && path.matches("/api/widget-profiles/[a-f0-9]{32}/pairings")) {
            return parameters.isEmpty();
        }
        if (is(method, "PUT", path, "/api/settings", "/api/protection", "/api/integrations/abrp")) return parameters.isEmpty();
        if ("PUT".equals(method) && path.matches("/api/charges/[a-f0-9]{32}")) return parameters.isEmpty();
        if ("DELETE".equals(method) && path.matches("/api/charges/[a-f0-9]{32}")) return parameters.isEmpty();
        if (path.matches("/api/journeys/[a-f0-9]{32}")
                && ("PUT".equals(method) || "DELETE".equals(method))) return parameters.isEmpty();
        if (path.matches("/api/maintenance/[a-f0-9]{32}")
                && ("PUT".equals(method) || "DELETE".equals(method))) return parameters.isEmpty();
        if (path.matches("/api/maintenance-plans/[a-f0-9]{32}")
                && ("PUT".equals(method) || "DELETE".equals(method))) return parameters.isEmpty();
        if ("DELETE".equals(method) && path.matches("/api/navigation/favorites/[a-f0-9]{32}")) {
            return parameters.isEmpty();
        }
        if (path.matches("/api/widget-profiles/[a-f0-9]{32}")
                && ("PUT".equals(method) || "DELETE".equals(method))) return parameters.isEmpty();
        if ("DELETE".equals(method)
                && path.matches("/api/widget-profiles/[a-f0-9]{32}/native-grants/[a-f0-9]{32}")) {
            return parameters.isEmpty();
        }
        return false;
    }

    private static boolean is(String method, String expectedMethod, String path, String... paths) {
        if (!expectedMethod.equals(method)) return false;
        for (String expected : paths) if (expected.equals(path)) return true;
        return false;
    }

    private static boolean boundedPageQuery(Map<String, String> parameters) {
        if (parameters.isEmpty()) return true;
        if (!PAGE_QUERY.containsAll(parameters.keySet())) return false;
        try {
            if (parameters.containsKey("limit")) {
                if (!validDecimal(parameters.get("limit"))) return false;
                int limit = Integer.parseInt(parameters.get("limit"));
                if (limit < 1 || limit > 100) return false;
            }
            if (parameters.containsKey("offset")) {
                if (!validDecimal(parameters.get("offset"))) return false;
                int offset = Integer.parseInt(parameters.get("offset"));
                if (offset < 0 || offset > 1_000_000) return false;
            }
            return true;
        } catch (NumberFormatException ignored) {
            return false;
        }
    }

    private static boolean journeysQuery(Map<String, String> parameters) {
        if (!JOURNEY_QUERY.containsAll(parameters.keySet())) return false;
        Map<String, String> page = new HashMap<>(parameters);
        String search = page.remove("search");
        String tag = page.remove("tag");
        return boundedPageQuery(page) && validOptionalText(search, 100) && validOptionalText(tag, 32);
    }

    private static boolean maintenancePlansQuery(Map<String, String> parameters) {
        if (!MAINTENANCE_PLAN_QUERY.containsAll(parameters.keySet())) return false;
        Map<String, String> page = new HashMap<>(parameters);
        String odometer = page.remove("odometer_km");
        if (!boundedPageQuery(page)) return false;
        if (odometer == null) return true;
        if (!validDecimal(odometer)) return false;
        try {
            return Integer.parseInt(odometer) <= 2_000_000;
        } catch (NumberFormatException ignored) {
            return false;
        }
    }

    private static boolean optionalDaysQuery(Map<String, String> parameters) {
        if (parameters.isEmpty()) return true;
        String days = parameters.get("days");
        return parameters.size() == 1 && validDecimal(days)
                && ("30".equals(days) || "90".equals(days) || "365".equals(days));
    }

    private static boolean validOptionalText(String value, int maximumCodePoints) {
        return value == null || (value.codePointCount(0, value.length()) <= maximumCodePoints
                && value.codePoints().noneMatch(Character::isISOControl));
    }

    private static boolean validDecimal(String value) {
        return value != null && DECIMAL.matcher(value).matches();
    }

    private static Map<String, String> parseQuery(String query) {
        Map<String, String> result = new HashMap<>();
        if (query.isEmpty()) return result;
        if (query.length() > 2048 || query.startsWith("&") || query.endsWith("&")) return null;
        for (String pair : query.split("&", -1)) {
            int equals = pair.indexOf('=');
            if (equals <= 0 || pair.indexOf('=', equals + 1) >= 0) return null;
            String key = decodeQueryComponent(pair.substring(0, equals));
            String value = decodeQueryComponent(pair.substring(equals + 1));
            if (key == null || value == null || key.isEmpty() || result.putIfAbsent(key, value) != null) return null;
        }
        return result;
    }

    /** Decodes one query component using strict UTF-8, with form-query '+' semantics. */
    private static String decodeQueryComponent(String raw) {
        ByteArrayOutputStream bytes = new ByteArrayOutputStream(raw.length());
        for (int index = 0; index < raw.length();) {
            char current = raw.charAt(index);
            if (current == '+') {
                bytes.write(' ');
                index++;
            } else if (current == '%') {
                if (index + 2 >= raw.length()) return null;
                int high = asciiHex(raw.charAt(index + 1));
                int low = asciiHex(raw.charAt(index + 2));
                if (high < 0 || low < 0) return null;
                bytes.write((high << 4) | low);
                index += 3;
            } else {
                int codePoint = raw.codePointAt(index);
                int charCount = Character.charCount(codePoint);
                try {
                    ByteBuffer encoded = StandardCharsets.UTF_8.newEncoder()
                            .onMalformedInput(CodingErrorAction.REPORT)
                            .onUnmappableCharacter(CodingErrorAction.REPORT)
                            .encode(CharBuffer.wrap(raw, index, index + charCount));
                    while (encoded.hasRemaining()) bytes.write(encoded.get() & 0xff);
                } catch (CharacterCodingException ignored) {
                    return null;
                }
                index += charCount;
            }
        }
        try {
            return StandardCharsets.UTF_8.newDecoder()
                    .onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT)
                    .decode(ByteBuffer.wrap(bytes.toByteArray())).toString();
        } catch (CharacterCodingException ignored) {
            return null;
        }
    }

    private static int asciiHex(char value) {
        if (value >= '0' && value <= '9') return value - '0';
        if (value >= 'a' && value <= 'f') return value - 'a' + 10;
        if (value >= 'A' && value <= 'F') return value - 'A' + 10;
        return -1;
    }
}
