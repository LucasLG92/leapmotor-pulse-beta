# Collector y telemetría pasiva

## Propiedad y activación

`pulse.collector.Collector` ejecuta lecturas pasivas de un proveedor inyectado y persiste snapshots normalizados mediante el coordinator. Su constructor requiere explícitamente un objeto con `fetch_snapshot()` y un coordinator con `call()`. Construirlo no crea tareas ni inicia una lectura. Solo una llamada explícita a `start()` inicia el ciclo periódico; `request_refresh()` solicita una lectura manual. No existe singleton de collector ni lectura desde `GET /api/state`.

El ciclo usa el contrato:

```python
await coordinator.call("record_snapshot", snapshot, write=True)
```

El payload que sale del worker contiene solo `source`, `observed_at`, `provider_received_at`, `vehicle` y `field_sources`. El Store asigna el `id` persistente y `server_received_at` con su propio reloj. No se envían `model`, `freshness`, `capabilities`, respuesta SDK cruda, identidad del vehículo ni hora de servidor fabricada por el collector. `telemetry_latest()` expone el registro persistido con esos tiempos separados y los catorce campos enumerados más abajo.

## Hilo, exclusión y ciclo de vida

`fetch_snapshot()` es una llamada síncrona al proveedor que se envía a un `ThreadPoolExecutor` propio de un solo worker (`pulse-collector`). El coordinator conserva su executor separado y exclusivo de SQLite. No se reutiliza el executor de la base de datos para bloquear una lectura de proveedor.

Parámetros predeterminados: `interval_seconds=300`, `read_timeout_seconds=20` y reloj aware UTC. Se admite como máximo una lectura de proveedor en vuelo. Las llamadas concurrentes a `request_refresh()` comparten el mismo refresh task y, por tanto, la misma lectura y escritura. Un timeout del await no detiene el hilo Python: el snapshot que termine después del límite se descarta; hasta que el futuro de ese hilo finalice, cualquier nueva solicitud responde `busy` y no se encola otra lectura.

El ciclo periódico realiza una primera lectura después de `start()` y agenda las siguientes tras éxito o backoff. Un error incrementa el contador y usa backoff exponencial acotado: intervalo base duplicado por error, con máximo `max(interval_seconds, 3600)`. Una llamada explícita respeta `next_attempt_at`. Errores repetidos no disparan un ciclo de reintento rápido. `close()` cancela tareas y espera como máximo un segundo; no intenta terminar a la fuerza un thread bloqueado. Después de `close()` no se acepta ningún refresh.

`Collector.status()` contiene exclusivamente `running`, `closed`, `in_flight`, `read_count`, `success_count`, `error_count`, `last_attempt_at`, `next_attempt_at` y `last_error_code`. No incluye paths, modelo, identidad, medidas, datos del vehículo ni mensajes de excepción. Los códigos de salida son fijos (`clock_invalid`, `read_failed`, `read_timeout`, `provider_unavailable`, `invalid_snapshot` o `store_failed`); el texto de error del SDK no se devuelve ni persiste.

`close()` deja de esperar como máximo tras un segundo, pero Python no puede terminar a la fuerza el hilo síncrono del proveedor. `ThreadPoolExecutor` usa hilos que pueden mantener vivo el proceso al salir; por eso el transporte real del SDK debe tener un timeout de lectura/conexión finito y probado. El timeout del collector no sustituye ese límite ni demuestra que un proceso con un SDK atascado vaya a terminar limpiamente.

## Normalización del registro

| Campo almacenado | Regla |
|---|---|
| `source` | `MATE-API B10ReadClient` solo si coincide con el identificador fijo actual; en otro caso `configured_provider`. |
| `observed_at` | Timestamp de observación aware del proveedor, convertido a UTC ISO 8601; ausente/inválido → `null`. |
| `provider_received_at` | Recepción declarada por proveedor, guardada separada; ausente/inválida → `null`. No reemplaza `observed_at`. |
| `vehicle.battery_pct` | Número finito de 0 a 100; cualquier valor inválido → `null`. |
| `vehicle.range_km` | Número finito de 0 a 5000; cualquier valor inválido → `null`. |
| `vehicle.odometer_km` | Número finito de 0 a 10 000 000; cualquier valor inválido → `null`. |
| `vehicle.interior_temperature_c` | Temperatura interior en °C; número finito de −100 a 100. |
| `vehicle.battery_min_temperature_c` | Temperatura mínima de batería en °C; número finito de −100 a 150. No es la temperatura media. |
| `vehicle.front_left_pressure_kpa`, `vehicle.front_right_pressure_kpa`, `vehicle.rear_left_pressure_kpa`, `vehicle.rear_right_pressure_kpa` | Presión de cada neumático en kPa; número finito de 0 a 1000. No se infiere una presión recomendada. |
| `vehicle.vehicle_security_code`, `vehicle.sentry_code` | Código de diagnóstico entero de 0 a 255. El adapter/collector acepta números normalizados finitos sin fracción y los convierte a `int`; Store/API/copia exigen entero exacto. Booleanos, texto, fracciones y valores fuera de rango → `null` en la lectura. |
| `vehicle.locked`, `vehicle.climate_on`, `vehicle.charging` | Booleano JSON exacto (`true`/`false`); enteros `0`/`1` y otros tipos → `null`. |
| `field_sources` | Mismas catorce claves que `vehicle`; se conserva solo el nombre de origen permitido para el valor válido; el resto → `null`. |

Las seis magnitudes añadidas corresponden al contrato normalizado de [MATE-API fijado en 5b7d30c](https://github.com/ProtossBlaster/MATE-API/blob/5b7d30c30ae10eb18677214e36a9d42398a769e6/leapmotor_cloud/vehicle_data.py). Sus límites son límites de validación del SDK, no umbrales de alarma ni evidencia de disponibilidad física. El adapter acepta únicamente números ya normalizados: texto, booleanos, NaN e infinito quedan en `null`. Los códigos crudos de carga, cierre y climatización no se convierten en booleanos. No se añaden señales a widgets, MQTT o ABRP por defecto.

Los códigos `vehicle_security_code` (señal 1255) y `sentry_code` (3636) conservan su nombre de origen solo cuando el valor es válido. Son enumeraciones crudas: no significan armado/desarmado, alarma o intrusión hasta cualificar el firmware y la señal. «Diagnóstico de seguridad» los presenta plegados por defecto, separados de los cambios de cierre/carga. Requiere conexión, modo real y edad temporal reciente para mostrar el registro actual; la demo no inventa códigos. No habilitan controles ni avisos ni se agregan a campos seleccionables de MQTT/widgets/ABRP.

El worker omite `model` y elimina `freshness` y `capabilities` del adapter. Ninguna medida inválida se convierte en cero; no se rellenan campos ausentes, no se copia el estado de demo y no se deduce conexión física a partir de una lectura. La normalización solo acepta la estructura `vehicle` del provider; una estructura ausente/incompatible produce `invalid_snapshot` y no se persiste.

## Calidad temporal y frescura física

`quality(snapshot, now, fresh_seconds=300, max_age_seconds=7200)` es una función pura que debe aplicarse sobre un snapshot persistido. Para `snapshot=None`, devuelve `freshness: unknown`, `temporal_status: unknown`, edad y observación nulas, con `reason_code: no_snapshot`. En otros casos devuelve:

```text
{
  freshness: "unknown",
  temporal_status: "fresh" | "stale" | "unknown" | "unavailable",
  age_seconds: número | null,
  observed_at: ISO UTC | null,
  reason_code: código fijo
}
```

`temporal_status` solo describe la edad de `observed_at`: falta/inválida → `unknown`; más de 60 segundos en el futuro → `unknown`; edad hasta 300 s → `fresh`; edad mayor de 300 s → `stale`; edad mayor de 7200 s → `unavailable`. Una marca futura dentro de la tolerancia de 60 s se computa como edad cero. `age_seconds` queda `null` si no se puede calcular con seguridad. `provider_received_at` y `server_received_at` nunca participan en esa edad.

El campo `freshness` se conserva en `unknown` hasta que exista evidencia explícita de que el proveedor y la señal física concreta están cualificados. Por tanto, `temporal_status: fresh` no significa que el B10 esté actualizado ni que el dato refleje el estado actual del coche. La respuesta de API puede exponer ambas etiquetas sin colapsarlas. Ni una lectura recibida con éxito ni un timestamp reciente habilitan comandos; estos siguen bajo los bloqueos F6 independientes.

## Contrato de lectura y estado actual

El snapshot persistido tiene `id`, `source`, `observed_at`, `provider_received_at`, `server_received_at`, los catorce campos nullable de `vehicle` y sus `field_sources`. Los campos de observación, recepción del provider y recepción del servidor tienen origen distinto y no se sustituyen entre sí. La interfaz muestra las seis magnitudes en «Temperaturas y neumáticos» y en «Lecturas recibidas». Una lectura caducada oculta sus valores actuales; el historial conserva los anteriores con su fecha y fuente.

El collector no lee archivos de configuración/secretos, no crea sesión SDK ni invoca comandos. Solo invoca `fetch_snapshot()` en el provider inyectado, que podría ser quien comunique con el fabricante. No inicia a menos que una instancia explícita reciba `start()`/`request_refresh()`. El adapter actual marca frescura y capacidades como desconocidas; el collector lo mantiene así. Las pruebas usan providers/coordinators falsos locales y no verifican conexión del B10, autenticación ni señales físicas.

## Persistencia y recuperación de este incremento

El incremento diagnóstico de fase 9 migra SQLite 16→17 de forma transaccional: añade dos columnas enteras nullable y completa las fuentes históricas con `null`, sin inventar códigos. Los valores, fuentes previas, IDs, tiempos y datos de negocio se conservan. Exporta JSON 15 con catorce campos; restaura JSON 14 con exactamente sus doce campos y formatos 1–13 bajo su forma anterior. La validación de copia ocurre antes de modificar el almacén. La migración histórica de fase 3 (SQLite 15→16, JSON 13→14) sigue documentada en su recibo fechado.

Antes de desplegar este incremento se necesita una copia SQLite 16 coherente y el artefacto anterior. Para volver atrás se restauran juntos el artefacto anterior y esa copia: el código anterior rechaza SQLite 17. La prueba sintética con código genuino congelado verificó migración, restauración JSON 14 y reapertura de copia 16 con el código anterior. No se ha migrado una base de producción ni cualificado el B10 con estas pruebas.
