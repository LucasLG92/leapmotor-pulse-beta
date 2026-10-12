# Analítica de energía y cobertura observada

`Store.energy_analytics(days=30)` devuelve una lectura agregada en una sola transacción SQLite de solo lectura. `days` admite enteros de 1 a 3650; la ventana es inclusiva y UTC: `[now - days, now]`. Los meses de `months` son todos los meses UTC que intersectan la ventana, incluidos los que no tienen cargas.

## Contrato

```text
{
  window_start: ISO UTC,
  window_end: ISO UTC,
  bucket_timezone: "UTC",
  manual: ChargeAggregate,
  demo: ChargeAggregate,
  months: [{month: "YYYY-MM", manual: ChargeAggregate, demo: ChargeAggregate}],
  observations: ObservationCoverage,
  estimates: {
    consumption_kwh_100km: null,
    reason_code: "energy_not_observed"
  }
}

ChargeAggregate = {
  sessions: integer,
  kwh: number,
  cost_eur: number,
  effective_tariff_eur_kwh: number | null
}

ObservationCoverage = {
  sample_count: integer,
  observed_count: integer,
  missing_observation_count: integer,
  future_timestamp_count: integer,
  outside_window_count: integer,
  retention_limit: 1000,
  non_monotonic_count: integer,
  duplicate_time_count: integer,
  gap_count: integer,
  max_gap_seconds: number | null,
  covered_seconds: number,
  coverage_percent: number | null,
  first_observed_at: ISO UTC | null,
  last_observed_at: ISO UTC | null,
  odometer_delta_km: number | null,
  eligible_interval_count: integer,
  validation: "not_qualified"
}
```

Las cargas se agregan con `WHERE started_at >= ? AND started_at <= ?` y `GROUP BY substr(started_at, 1, 7)` por tabla. Los índices existentes sobre `started_at` acotan el recorrido. La tarifa efectiva es el coste agregado dividido por los kWh agregados, no la media de tarifas por sesión. Si no hay sesiones, los contadores y sumas son cero y la tarifa es `null`. Las cargas manuales y demo nunca se mezclan; los datos demo solo se consultan mientras la demo está habilitada.

## Cobertura de observaciones

Se seleccionan como máximo los 1000 snapshots retenidos más recientes según el orden local de ingestión (`rowid`); después se procesan desde el más antiguo al más reciente. `sample_count`, `missing_observation_count` y los contadores de timestamps consideran toda esa cohorte, aunque su recepción o periodo observado quede fuera de la ventana. `observed_count` solo cuenta timestamps `observed_at` válidos dentro de la ventana. Los timestamps anteriores se cuentan en `outside_window_count`; cualquier timestamp posterior al final se cuenta en `future_timestamp_count` y se excluye, aunque el cálculo de calidad temporal tolere hasta 60 segundos de desfase. La recepción del proveedor y la recepción local nunca sustituyen `observed_at`.

Un timestamp igual al mayor observado anterior se cuenta como duplicado; uno menor se cuenta como no monotónico. Ambos se excluyen como puntos de intervalo y no hacen retroceder el ancla cronológica. Un intervalo solo es elegible si `0 < dt <= 900` segundos y no contiene un timestamp futuro fuera de la ventana. Los intervalos mayores incrementan `gap_count`; no se interpola el hueco. `covered_seconds` suma la unión de intervalos aceptables dentro de la ventana para evitar doble conteo. `coverage_percent` es `null` sin observaciones en ventana; en otro caso es la cobertura dividida por la duración de ventana.

El odómetro solo contribuye en intervalos elegibles con ambas lecturas conocidas y finitas, delta no negativo y velocidad implícita no superior a 250 km/h. Reset, retroceso y salto se excluyen. Si ningún intervalo aporta lecturas válidas, el delta es `null`; un delta válido de cero se conserva como `0`.

La cobertura describe la distribución temporal de snapshots, no una validación física de señales: `validation` permanece en `not_qualified`. El historial de observaciones está limitado a 1000 muestras y puede no cubrir la ventana completa; `retention_limit` expone ese límite. No se deducen viajes, consumo real, energía del vehículo ni eficiencia a partir del SOC o la autonomía.
