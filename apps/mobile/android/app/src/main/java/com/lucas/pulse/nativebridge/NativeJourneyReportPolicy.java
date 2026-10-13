package com.lucas.pulse.nativebridge;

import java.time.Instant;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/** Closed, privacy-preserving projection of the aggregate manual journey report. */
final class NativeJourneyReportPolicy {
    static final int MAX_BODY_BYTES = 64 * 1024;
    private static final int MAX_COUNT = 100_000;
    private static final Pattern UTC_INSTANT = Pattern.compile(
            "[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\\.[0-9]{1,9})?Z");
    private static final Pattern MONTH_KEY = Pattern.compile("[0-9]{4}-(?:0[1-9]|1[0-2])");
    private static final Set<String> REPORT_KEYS = keys("source", "coverage", "period", "totals", "months");
    private static final Set<String> PERIOD_KEYS = keys("started_at", "ended_at", "days", "timezone");
    private static final Set<String> TOTAL_KEYS = keys("count", "distance_km", "energy_kwh",
            "known_distance_count", "known_energy_count", "paired_count", "paired_distance_km",
            "paired_energy_kwh", "consumption_kwh_100km");
    private static final Set<String> MONTH_KEYS = keys("month_key", "count", "distance_km", "energy_kwh",
            "known_distance_count", "known_energy_count", "paired_count", "paired_distance_km",
            "paired_energy_kwh", "consumption_kwh_100km");
    private static final Pattern JSON_NUMBER = Pattern.compile(
            "-?(?:0|[1-9][0-9]*)(?:\\.[0-9]+)?(?:[eE][+-]?[0-9]+)?");

    private NativeJourneyReportPolicy() { }

    static boolean bodyWithinLimit(String body) {
        return body != null && body.getBytes(java.nio.charset.StandardCharsets.UTF_8).length <= MAX_BODY_BYTES;
    }

    static Integer requestedDays(int argumentCount, String soleArgumentKey, Object value) {
        if (argumentCount != 1 || !"days".equals(soleArgumentKey)) return null;
        Long days = integer(value);
        if (days == null || (days != 30 && days != 90 && days != 365)) return null;
        return days.intValue();
    }

    /** Returns a newly serialized projection, or null when any field is outside the closed contract. */
    static String validateAndSerialize(Object value, int requestedDays) {
        if (!isAllowedDays(requestedDays)) return null;
        Map<?, ?> report = asMap(value);
        if (!hasExactKeys(report, REPORT_KEYS)
                || !"manual".equals(report.get("source"))
                || !"unknown".equals(report.get("coverage"))) return null;

        Period period = readPeriod(report.get("period"), requestedDays);
        Totals totals = readTotals(report.get("totals"), false);
        List<?> rawMonths = asList(report.get("months"));
        if (period == null || totals == null || rawMonths == null || rawMonths.isEmpty() || rawMonths.size() > 13) {
            return null;
        }

        List<Totals> months = new ArrayList<>(rawMonths.size());
        String previousKey = null;
        for (Object rawMonth : rawMonths) {
            Totals month = readTotals(rawMonth, true);
            if (month == null || (previousKey != null && previousKey.compareTo(month.monthKey) >= 0)) return null;
            previousKey = month.monthKey;
            months.add(month);
        }

        StringBuilder json = new StringBuilder(512);
        json.append("{\"source\":\"manual\",\"coverage\":\"unknown\",\"period\":{");
        json.append("\"started_at\":"); appendQuoted(json, period.startedAt);
        json.append(",\"ended_at\":"); appendQuoted(json, period.endedAt);
        json.append(",\"days\":").append(period.days).append(",\"timezone\":\"UTC\"},\"totals\":");
        appendTotals(json, totals, false);
        json.append(",\"months\":[");
        for (int index = 0; index < months.size(); index++) {
            if (index > 0) json.append(',');
            appendTotals(json, months.get(index), true);
        }
        return json.append("]}").toString();
    }

    private static Period readPeriod(Object value, int requestedDays) {
        Map<?, ?> period = asMap(value);
        if (!hasExactKeys(period, PERIOD_KEYS)
                || !"UTC".equals(period.get("timezone"))) return null;
        Long days = integer(period.get("days"));
        if (days == null || days != requestedDays) return null;
        String startedAt = utcInstant(period.get("started_at"));
        String endedAt = utcInstant(period.get("ended_at"));
        if (startedAt == null || endedAt == null) return null;
        try {
            Instant start = Instant.parse(startedAt);
            Instant end = Instant.parse(endedAt);
            if (!end.isAfter(start)) return null;
        } catch (DateTimeParseException | ArithmeticException ignored) {
            return null;
        }
        return new Period(startedAt, endedAt, requestedDays);
    }

    private static Totals readTotals(Object value, boolean monthValue) {
        Map<?, ?> totals = asMap(value);
        if (!hasExactKeys(totals, monthValue ? MONTH_KEYS : TOTAL_KEYS)) return null;
        String monthKey = null;
        if (monthValue) {
            Object rawKey = totals.get("month_key");
            if (!(rawKey instanceof String) || !MONTH_KEY.matcher((String) rawKey).matches()
                    || "0000-".equals(((String) rawKey).substring(0, 5))) return null;
            monthKey = (String) rawKey;
        }

        Long count = boundedCount(totals.get("count"));
        Long knownDistance = boundedCount(totals.get("known_distance_count"));
        Long knownEnergy = boundedCount(totals.get("known_energy_count"));
        Long paired = boundedCount(totals.get("paired_count"));
        String distance = nonnegativeNumber(totals.get("distance_km"));
        String energy = nonnegativeNumber(totals.get("energy_kwh"));
        String pairedDistance = nonnegativeNumber(totals.get("paired_distance_km"));
        String pairedEnergy = nonnegativeNumber(totals.get("paired_energy_kwh"));
        Object rawConsumption = totals.get("consumption_kwh_100km");
        String consumption = rawConsumption == null ? null : nonnegativeNumber(rawConsumption);
        if (count == null || knownDistance == null || knownEnergy == null || paired == null
                || distance == null || energy == null || pairedDistance == null || pairedEnergy == null
                || (rawConsumption != null && consumption == null) || knownDistance > count || knownEnergy > count
                || paired > knownDistance || paired > knownEnergy) return null;
        return new Totals(monthKey, count, distance, energy, knownDistance, knownEnergy, paired,
                pairedDistance, pairedEnergy, consumption);
    }

    private static void appendTotals(StringBuilder json, Totals totals, boolean includeMonth) {
        json.append('{');
        if (includeMonth) {
            json.append("\"month_key\":"); appendQuoted(json, totals.monthKey); json.append(',');
        }
        json.append("\"count\":").append(totals.count)
                .append(",\"distance_km\":").append(totals.distance)
                .append(",\"energy_kwh\":").append(totals.energy)
                .append(",\"known_distance_count\":").append(totals.knownDistance)
                .append(",\"known_energy_count\":").append(totals.knownEnergy)
                .append(",\"paired_count\":").append(totals.paired)
                .append(",\"paired_distance_km\":").append(totals.pairedDistance)
                .append(",\"paired_energy_kwh\":").append(totals.pairedEnergy)
                .append(",\"consumption_kwh_100km\":");
        if (totals.consumption == null) json.append("null");
        else json.append(totals.consumption);
        json.append('}');
    }

    private static String utcInstant(Object value) {
        if (!(value instanceof String)) return null;
        String text = (String) value;
        if (text.length() > 64 || !UTC_INSTANT.matcher(text).matches()
                || text.startsWith("0000-")) return null;
        try {
            Instant.parse(text);
            return text;
        } catch (DateTimeParseException ignored) {
            return null;
        }
    }

    private static Long boundedCount(Object value) {
        Long number = integer(value);
        return number != null && number >= 0 && number <= MAX_COUNT ? number : null;
    }

    private static Long integer(Object value) {
        if (!(value instanceof Byte || value instanceof Short || value instanceof Integer || value instanceof Long)) {
            return null;
        }
        return ((Number) value).longValue();
    }

    private static String nonnegativeNumber(Object value) {
        if (!(value instanceof Number) || value instanceof Boolean) return null;
        Number number = (Number) value;
        double asDouble = number.doubleValue();
        String text = number.toString();
        if (!Double.isFinite(asDouble) || asDouble < 0 || !JSON_NUMBER.matcher(text).matches()) return null;
        return text;
    }

    private static boolean isAllowedDays(int days) {
        return days == 30 || days == 90 || days == 365;
    }

    private static Map<?, ?> asMap(Object value) {
        return value instanceof Map<?, ?> ? (Map<?, ?>) value : null;
    }

    private static List<?> asList(Object value) {
        return value instanceof List<?> ? (List<?>) value : null;
    }

    private static boolean hasExactKeys(Map<?, ?> value, Set<String> expected) {
        return value != null && value.size() == expected.size() && value.keySet().equals(expected);
    }

    private static Set<String> keys(String... values) {
        return new HashSet<>(Arrays.asList(values));
    }

    private static void appendQuoted(StringBuilder json, String value) {
        json.append('"');
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            switch (character) {
                case '"': json.append("\\\""); break;
                case '\\': json.append("\\\\"); break;
                case '\b': json.append("\\b"); break;
                case '\f': json.append("\\f"); break;
                case '\n': json.append("\\n"); break;
                case '\r': json.append("\\r"); break;
                case '\t': json.append("\\t"); break;
                default:
                    if (character < 0x20) json.append(String.format("\\u%04x", (int) character));
                    else json.append(character);
            }
        }
        json.append('"');
    }

    private static final class Period {
        final String startedAt;
        final String endedAt;
        final int days;

        Period(String startedAt, String endedAt, int days) {
            this.startedAt = startedAt;
            this.endedAt = endedAt;
            this.days = days;
        }
    }

    private static final class Totals {
        final String monthKey;
        final long count;
        final String distance;
        final String energy;
        final long knownDistance;
        final long knownEnergy;
        final long paired;
        final String pairedDistance;
        final String pairedEnergy;
        final String consumption;

        Totals(String monthKey, long count, String distance, String energy, long knownDistance,
                long knownEnergy, long paired, String pairedDistance, String pairedEnergy, String consumption) {
            this.monthKey = monthKey;
            this.count = count;
            this.distance = distance;
            this.energy = energy;
            this.knownDistance = knownDistance;
            this.knownEnergy = knownEnergy;
            this.paired = paired;
            this.pairedDistance = pairedDistance;
            this.pairedEnergy = pairedEnergy;
            this.consumption = consumption;
        }
    }
}
