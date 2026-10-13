package com.lucas.pulse.nativebridge;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.junit.Test;

public final class NativeJourneyReportPolicyTest {
    @Test public void acceptsOnlyTheExactDaysArgumentAndIntegerPeriods() {
        assertEquals(Integer.valueOf(30), NativeJourneyReportPolicy.requestedDays(1, "days", 30));
        assertEquals(Integer.valueOf(90), NativeJourneyReportPolicy.requestedDays(1, "days", 90L));
        assertEquals(Integer.valueOf(365), NativeJourneyReportPolicy.requestedDays(1, "days", 365));
        assertNull(NativeJourneyReportPolicy.requestedDays(2, "days", 30));
        assertNull(NativeJourneyReportPolicy.requestedDays(1, "extra", 30));
        assertNull(NativeJourneyReportPolicy.requestedDays(1, "days", "30"));
        assertNull(NativeJourneyReportPolicy.requestedDays(1, "days", 30.0));
        assertNull(NativeJourneyReportPolicy.requestedDays(1, "days", true));
        assertNull(NativeJourneyReportPolicy.requestedDays(1, "days", 31));
    }

    @Test public void serializesOnlyValidatedFieldsAndPreservesZeroNullAndSmallAmounts() {
        Map<String, Object> totals = totals(2, 0.0000006, 0.0000006, 1, 1, 1,
                0.0000006, 0.0000006, 100.0);
        Map<String, Object> report = report(30, totals, List.of(
                month("2026-09", totals(1, 0.0000006, 0.0000006, 1, 1, 1,
                        0.0000006, 0.0000006, 100.0)),
                month("2026-10", totals(1, 0.0, 0.0, 0, 0, 0, 0.0, 0.0, null))));

        String json = NativeJourneyReportPolicy.validateAndSerialize(report, 30);

        assertNotNull(json);
        assertTrue(json.startsWith("{\"source\":\"manual\",\"coverage\":\"unknown\""));
        assertTrue(json.contains("\"consumption_kwh_100km\":null"));
        assertTrue(json.contains("\"distance_km\":0.0"));
        assertTrue(json.contains("\"distance_km\":6.0E-7"));
        assertFalse(json.contains("owner_id"));
        assertFalse(json.contains("journey_id"));
    }

    @Test public void rejectsUnknownKeysAndIncorrectSourceOrCoverage() {
        Map<String, Object> report = report(30, totals(0, 0.0, 0.0, 0, 0, 0, 0.0, 0.0, null),
                List.of(month("2026-10", totals(0, 0.0, 0.0, 0, 0, 0, 0.0, 0.0, null))));
        report.put("owner_id", "private");
        assertNull(NativeJourneyReportPolicy.validateAndSerialize(report, 30));
        report.remove("owner_id");
        report.put("coverage", "complete");
        assertNull(NativeJourneyReportPolicy.validateAndSerialize(report, 30));
    }

    @Test public void rejectsBooleanCoercionNonFiniteAndInvalidCountRelationships() {
        Map<String, Object> report = baseReport(30);
        Map<String, Object> totals = mutableTotals(0, 0.0, 0.0, 0, 0, 0, 0.0, 0.0, null);
        report.put("totals", totals);
        totals.put("count", true);
        assertNull(NativeJourneyReportPolicy.validateAndSerialize(report, 30));

        totals.put("count", 0);
        totals.put("distance_km", "0");
        assertNull(NativeJourneyReportPolicy.validateAndSerialize(report, 30));

        totals.put("distance_km", Double.NaN);
        assertNull(NativeJourneyReportPolicy.validateAndSerialize(report, 30));

        totals.put("distance_km", 0.0);
        totals.put("known_distance_count", 1);
        assertNull(NativeJourneyReportPolicy.validateAndSerialize(report, 30));
    }

    @Test public void rejectsPeriodCoercionNonUtcInvalidDateAndNonIncreasingWindow() {
        Map<String, Object> report = baseReport(30);
        @SuppressWarnings("unchecked")
        Map<String, Object> period = (Map<String, Object>) report.get("period");
        period.put("days", "30");
        assertNull(NativeJourneyReportPolicy.validateAndSerialize(report, 30));

        period.put("days", 30);
        period.put("started_at", "2026-09-13T12:00:00+01:00");
        assertNull(NativeJourneyReportPolicy.validateAndSerialize(report, 30));

        period.put("started_at", "2026-09-31T12:00:00Z");
        assertNull(NativeJourneyReportPolicy.validateAndSerialize(report, 30));

        period.put("started_at", Instant.parse("2026-10-13T12:00:00Z").minus(Duration.ofDays(29)).toString());
        assertNotNull(NativeJourneyReportPolicy.validateAndSerialize(report, 30));

        period.put("ended_at", period.get("started_at"));
        assertNull(NativeJourneyReportPolicy.validateAndSerialize(report, 30));
    }

    @Test public void rejectsCountsAboveBoundAndRatiosBelowZero() {
        Map<String, Object> report = baseReport(30);
        @SuppressWarnings("unchecked")
        Map<String, Object> totals = (Map<String, Object>) report.get("totals");
        totals.put("count", 100_001);
        assertNull(NativeJourneyReportPolicy.validateAndSerialize(report, 30));
        totals.put("count", 0);
        totals.put("consumption_kwh_100km", -0.1);
        assertNull(NativeJourneyReportPolicy.validateAndSerialize(report, 30));
    }

    @Test public void requiresSortedUniqueMonthKeysAndAtMostThirteenMonths() {
        Map<String, Object> report = baseReport(30);
        List<Object> months = new ArrayList<>();
        months.add(month("2026-10", totals(0, 0.0, 0.0, 0, 0, 0, 0.0, 0.0, null)));
        months.add(month("2026-09", totals(0, 0.0, 0.0, 0, 0, 0, 0.0, 0.0, null)));
        report.put("months", months);
        assertNull(NativeJourneyReportPolicy.validateAndSerialize(report, 30));

        months.clear();
        for (int index = 1; index <= 14; index++) {
            months.add(month(String.format("2026-%02d", index <= 12 ? index : index - 12),
                    totals(0, 0.0, 0.0, 0, 0, 0, 0.0, 0.0, null)));
        }
        assertNull(NativeJourneyReportPolicy.validateAndSerialize(report, 30));
    }

    @Test public void reportBodyLimitUsesUtf8Bytes() {
        assertTrue(NativeJourneyReportPolicy.bodyWithinLimit("a".repeat(NativeJourneyReportPolicy.MAX_BODY_BYTES)));
        assertFalse(NativeJourneyReportPolicy.bodyWithinLimit(
                "a".repeat(NativeJourneyReportPolicy.MAX_BODY_BYTES + 1)));
        assertFalse(NativeJourneyReportPolicy.bodyWithinLimit(
                "€".repeat(NativeJourneyReportPolicy.MAX_BODY_BYTES / 3 + 1)));
    }

    @Test public void operationStateIsSingleFlightAndRejectsOldSessionCallbacks() {
        NativeJourneyReportState state = new NativeJourneyReportState();
        NativeJourneyReportState.Operation first = state.begin(30, 4L);
        assertNotNull(first);
        assertNull(state.begin(90, 4L));
        assertTrue(state.isCurrent(first, 4L));
        assertFalse(state.isCurrent(first, 5L));
        assertTrue(state.finish(first));

        NativeJourneyReportState.Operation second = state.begin(90, 5L);
        assertNotNull(second);
        assertFalse(state.isCurrent(first));
        assertFalse(state.finish(first));
        assertTrue(state.isCurrent(second, 5L));
    }

    @Test public void logoutRetiresPendingAndDestroyPreventsLaterStarts() {
        NativeJourneyReportState state = new NativeJourneyReportState();
        NativeJourneyReportState.Operation pending = state.begin(365, 8L);
        assertNotNull(pending);
        assertTrue(state.retire() == pending);
        assertFalse(state.isCurrent(pending));

        NativeJourneyReportState.Operation afterLogout = state.begin(30, 9L);
        assertNotNull(afterLogout);
        assertTrue(state.destroy() == afterLogout);
        assertNull(state.begin(30, 10L));
    }

    private static Map<String, Object> baseReport(int days) {
        Map<String, Object> zero = totals(0, 0.0, 0.0, 0, 0, 0, 0.0, 0.0, null);
        return report(days, zero, List.of(month("2026-10", zero)));
    }

    private static Map<String, Object> report(int days, Map<String, Object> totals, List<?> months) {
        Instant end = Instant.parse("2026-10-13T12:00:00.123456Z");
        Map<String, Object> period = new LinkedHashMap<>();
        period.put("started_at", end.minus(Duration.ofDays(days)).toString());
        period.put("ended_at", end.toString());
        period.put("days", days);
        period.put("timezone", "UTC");
        Map<String, Object> report = new LinkedHashMap<>();
        report.put("source", "manual");
        report.put("coverage", "unknown");
        report.put("period", period);
        report.put("totals", totals);
        report.put("months", months);
        return report;
    }

    private static Map<String, Object> totals(long count, Number distance, Number energy,
            long knownDistance, long knownEnergy, long paired, Number pairedDistance,
            Number pairedEnergy, Number consumption) {
        Map<String, Object> totals = mutableTotals(count, distance, energy, knownDistance, knownEnergy,
                paired, pairedDistance, pairedEnergy, consumption);
        return totals;
    }

    private static Map<String, Object> mutableTotals(long count, Number distance, Number energy,
            long knownDistance, long knownEnergy, long paired, Number pairedDistance,
            Number pairedEnergy, Number consumption) {
        Map<String, Object> totals = new LinkedHashMap<>();
        totals.put("count", count);
        totals.put("distance_km", distance);
        totals.put("energy_kwh", energy);
        totals.put("known_distance_count", knownDistance);
        totals.put("known_energy_count", knownEnergy);
        totals.put("paired_count", paired);
        totals.put("paired_distance_km", pairedDistance);
        totals.put("paired_energy_kwh", pairedEnergy);
        totals.put("consumption_kwh_100km", consumption);
        return totals;
    }

    private static Map<String, Object> month(String monthKey, Map<String, Object> totals) {
        Map<String, Object> month = new LinkedHashMap<>();
        month.put("month_key", monthKey);
        month.putAll(totals);
        return month;
    }
}
