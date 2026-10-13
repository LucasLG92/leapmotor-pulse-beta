package com.lucas.pulse.nativebridge;

import java.util.HashMap;
import java.util.HashSet;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.Arrays;
import java.util.Collections;
import java.util.regex.Pattern;

/** Exact native UI API allowlist. OAuth and native bearer grant routes are intentionally absent. */
public final class NativeApiPolicy {
    private static final Pattern DECIMAL = Pattern.compile("[0-9]{1,9}");
    private static final Set<String> PAGE_QUERY = Collections.unmodifiableSet(
            new HashSet<>(Arrays.asList("limit", "offset")));

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
                || !rawPath.startsWith("/api/") || rawPath.startsWith("//")
                || rawPath.indexOf('#') >= 0 || rawPath.indexOf('\\') >= 0
                || rawPath.indexOf('%') >= 0 || rawPath.indexOf(':') >= 0
                || rawPath.chars().anyMatch(Character::isISOControl)) return false;

        int queryAt = rawPath.indexOf('?');
        String path = queryAt < 0 ? rawPath : rawPath.substring(0, queryAt);
        String query = queryAt < 0 ? "" : rawPath.substring(queryAt + 1);
        if (queryAt >= 0 && query.isEmpty()) return false;
        if (path.isEmpty() || path.endsWith("/") || path.contains("//")) return false;
        Map<String, String> parameters = parseQuery(query, "/api/charger/pricing-history".equals(path));
        if (parameters == null) return false;

        if (is(method, "GET", path, "/api/session", "/api/state", "/api/telemetry",
                "/api/commands/history", "/api/widget-profiles", "/api/native-capabilities", "/api/export", "/api/charger", "/api/navigation/status")) {
            return parameters.isEmpty();
        }
        if (is(method, "GET", path, "/api/charges", "/api/telemetry/history")) {
            return boundedPageQuery(parameters);
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

        if (is(method, "POST", path, "/api/charges/quote", "/api/charges/requests", "/api/charges", "/api/demo", "/api/commands",
                "/api/protection/event", "/api/maintenance", "/api/import", "/api/widget-profiles",
                "/api/auth/logout", "/api/navigation/places", "/api/navigation/chargers", "/api/navigation/tiles")) return parameters.isEmpty();
        if ("POST".equals(method) && path.matches("/api/widget-profiles/[a-f0-9]{32}/pairings")) {
            return parameters.isEmpty();
        }
        if (is(method, "PUT", path, "/api/settings", "/api/protection", "/api/integrations/abrp")) return parameters.isEmpty();
        if ("PUT".equals(method) && path.matches("/api/charges/[a-f0-9]{32}")) return parameters.isEmpty();
        if ("DELETE".equals(method) && path.matches("/api/charges/[a-f0-9]{32}")) return parameters.isEmpty();
        if (path.matches("/api/maintenance/[a-f0-9]{32}")
                && ("PUT".equals(method) || "DELETE".equals(method))) return parameters.isEmpty();
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

    private static boolean validDecimal(String value) {
        return value != null && DECIMAL.matcher(value).matches();
    }

    private static Map<String, String> parseQuery(String query, boolean pricingHistory) {
        Map<String, String> result = new HashMap<>();
        if (query.isEmpty()) return result;
        if (query.length() > 128 || query.startsWith("&") || query.endsWith("&")) return null;
        for (String pair : query.split("&", -1)) {
            int equals = pair.indexOf('=');
            if (equals <= 0 || equals == pair.length() - 1 || pair.indexOf('=', equals + 1) >= 0) return null;
            String key = pair.substring(0, equals);
            String value = pair.substring(equals + 1);
            boolean validValue = pricingHistory && "session_id".equals(key)
                    ? value.matches("[a-f0-9]{32}") : validDecimal(value);
            if (!key.matches("[a-z_]{1,24}") || !validValue || result.putIfAbsent(key, value) != null) {
                return null;
            }
        }
        return result;
    }
}
