# Lectura opcional del cargador de casa

Estado V42: DB14/JSON12 admite cotizaciones uniformes y por intervalos, conservando cada revisión. La fuente temporal es una inyección async interna explícita, independiente de los candidatos evcc; no está activada por defecto. El contrato completo y la cualificación física pendiente están en [CHARGER-INTERVALS](CHARGER-INTERVALS.md#motor-por-sesión-v42).

La migración13→14 es una barrera semántica sin tabla nueva. Las copias JSON1–11 siguen admitidas con su semántica anterior y rechazan curvas temporales; JSON12 conserva distribución/vínculo/revisiones. El límite JSON sigue5MB y rechaza exceso sin truncar. Antes de migrar una instalación existente, guardar una copia SQLite13 consistente y protegida. Volver aV41 requiere sus fuentes/build y esa copiaDB13 juntas; V41 no abreDB14. Este incremento no migra preview ni servidor.

Pulse contiene un adaptador original de solo lectura para evcc. Está apagado por defecto; habilitarlo no instala evcc ni configura un wallbox. No incorpora código de evcc ni de Mate y no añade dependencias. La versión y el cargador físicos necesitan una prueba autorizada antes de considerar cualificada la integración.

V29 prioriza `sessionEnergy` del estado actual, en kWh, y mantiene compatibilidad con `chargedEnergy` cuando el campo actual está ausente. Un `null` actual permanece como ausencia y un valor actual inválido se rechaza; no revive energía antigua. El historial cerrado sigue usando su campo `chargedEnergy`. Contrato de intervalos, límites de atribución y aceptación pendiente: [CHARGER-INTERVALS](CHARGER-INTERVALS.md).

La vista muestra potencia y energía directamente. «Cargando», «Conectado, sin carga» y «Desconectado» solo representan la última respuesta que el servidor considera reciente. Una observación antigua o conservada tras error se identifica como «Última lectura», con estado pasado e indicador neutro; no confirma actividad actual ni una pausa definitiva.

## Configuración explícita del servidor

Ejemplo genérico; sustituye el hostname por el de tu instalación:

```dotenv
PULSE_EVCC_ENABLED=true
PULSE_EVCC_ORIGIN=http://evcc-host:7070
PULSE_EVCC_LOADPOINT=Casa
PULSE_EVCC_POLL_SECONDS=60
PULSE_EVCC_HISTORY_SECONDS=600
PULSE_EVCC_HISTORY_MONTHS=3
PULSE_EVCC_ENERGY_HISTORY_ENABLED=false
```

El título debe identificar exactamente un `loadpoints[].title` del estado y coincidir con `loadpoint` en el historial. Un título ambiguo se rechaza. Solo se permite un origen sin ruta, consulta, fragmento o credenciales: HTTPS, o HTTP sobre una IP literal loopback, RFC1918 o ULA. No se siguen redirecciones ni proxies del entorno. No se buscan credenciales del operador. Un proxy protegido produce un error de acceso.

Por defecto, el lector hace exclusivamente GET a `/api/state` y `/api/sessions`. El estado se sondea cada 60s por defecto (30–3600s); el historial cada 600s (300–86400s) para 1–12 meses configurados, con filtros mes/año UTC. Cada petición tiene un límite total de 8s y 1MiB. La retención global es de 1000 sesiones cerradas y 16 ámbitos de configuración. El panel pagina desde SQLite, 20 sesiones por página; abrirlo y comparar PVPC no llama a evcc ni al fabricante.

La opción experimental `PULSE_EVCC_ENERGY_HISTORY_ENABLED=true` requiere que evcc ya esté habilitado. Añade un GET acotado a `/api/history/energy` cuando vence la consulta histórica: entidad del punto observado, grupo loadpoint y resolución15min, sin agrupar otras entidades. Cada consulta abarca como máximo24h de slots cerrados. La opción está apagada por defecto y no mejora ni modifica el coste: conserva candidatos para una validación temporal posterior. Si falla, siguen funcionando las observaciones y sesiones normales. No hay operación manual por carga.

DB12/JSON10 conserva hasta2048slots globales,64épocas y16ámbitos. Una época registra la asociación observada entre nombre de entidad, título y revisión del contrato; no acredita configuración histórica ni pertenencia a una sesión. Solo se reciben slots posteriores al primer vínculo y anteriores a su última corroboración. Ausencias no se convierten en ceros. Un cambio, una identidad ausente, un fallo del estado, un reinicio o una restauración cierran la referencia anterior. Las copiasJSON1–9 se aceptan con ledger vacío; lasJSON10 conservan candidatos históricos y cierran épocas abiertas al restaurar. Resultados iniciados antes de restaurar no escriben en el estado nuevo.

Antes de migrar aDB12, conservar una copia SQLite11 consistente con permisos600. Para volver aV29 se requieren sus fuentes/build y esa copiaDB11 juntos; no ejecutarV29 sobreDB12. La opción experimental permanece apagada y no cambia cotizaciones existentes.

## Qué significan los datos

Solo se conservan título, recepción local, conexión/carga, potencia, energía agregada, duración e identidad técnica mínima de la sesión. Con la opción experimental se añaden entidad/revisión/época observada y límites, kWh y energía devuelta de cada slot candidato; la última corrección recibida reemplaza ese candidato. Se descartan matrícula, VIN, ubicación, RFID, URL y payload bruto. El ámbito es un hash del origen y título; el origen no aparece en la API ni en las copias JSON. Los candidatos no se suman a cargas ni costes y no acreditan energía de red atribuida al coche.

`received_at` es la hora en que Pulse recibió los datos, no la hora de medición física. El estado distingue espera, lectura recibida, datos antiguos y error. Los fallos conservan y califican los últimos datos; `charging=false` no acredita por sí solo una finalización física. La energía se presenta como «comunicada», no como medida certificada.

El estado actual expresa `chargeDuration` en segundos, incluidos decimales. El historial examinado en el commit oficial `53e4bd87cdf0e62e1017c2c9db689bfc211046c6` serializa un `time.Duration` de Go en nanosegundos enteros. Pulse convierte explícitamente el historial a segundos y rechaza tipos o duraciones incompatibles; no adivina unidades por magnitud. El API de estado es interno y no promete estabilidad: se debe cualificar una versión de evcc y la semántica de sus filtros.

La clave local incorpora ámbito, ID externo, inicio UTC y título. Se comprueba también en las copias restauradas; una sesión corregida conserva su identidad y actualiza sus valores solo si la recepción es posterior. La restauración valida todo antes de modificar datos.

## Registro y coste automáticos V19

Una vez configurado evcc y seleccionado PVPC en Ajustes, Pulse guarda automáticamente las sesiones que el origen confirma como cerradas y calcula su coste en segundo plano. No es necesario rellenar fechas o pulsar «Comparar». Los estados públicos son `ready`, `pending`, `ineligible` y `disabled`, acompañados de `pvpc` cuando hay un cálculo guardado. Abrir el historial solo consulta SQLite: no inicia peticiones de evcc o precios.

Una pausa, desconexión o `charging=false` provoca una consulta anticipada del historial, acotada al intervalo de sondeo y al enfriamiento de errores. No fabrica una sesión ni su fin. Solo se acepta el inicio, fin y energía del registro cerrado que publique la fuente. Este comportamiento se prueba con datos sintéticos; la versión y el modelo de cargador reales siguen pendientes de cualificación.

El cálculo admite energía positiva hasta500kWh y un intervalo positivo de hasta72horas. Reparte uniformemente los kWh entre inicio y fin, suponiendo que toda la energía procede de la red. No incluye impuestos ni potencia fija y no descuenta autoconsumo. Una pausa puede cambiar el coste real: no hay curva de energía por hora.

Un fallo de precios deja el coste pendiente y programa un reintento después de diez minutos. Se consultan como máximo cuatro cotizaciones por vuelta y1000 candidatos; los cálculos terminados persisten y no se repiten al abrir la página, reiniciar o cambiar la tarifa. Una corrección posterior del fin o los kWh invalida el coste y permite recalcular; cambiar solo recepción o tiempo activo conserva el desglose. Un resultado anterior a una restauración no puede escribir sobre los datos restaurados.

Las sesiones y sus costes tienen un registro separado: no crean una carga manual ni se suman a sus totales o a la demo. Al desactivar PVPC se conservan los costes históricos; las sesiones elegibles aún sin coste permiten una comparación puntual desde la interfaz. V42 incorpora estimación por intervalos bajo el contrato interno explícito; su fuente física, las pausas reales, energía de red, viajes y analítica completa siguen pendientes de A7.

## Datos y recuperación

V19 migra SQLite de schema8 a schema9 añadiendo `charger_pricing`, con referencia a una sesión y eliminación en cascada. El JSON pasa a formato8 y añade `charger_pricing: [{session_id, quote}]`. Acepta formatos1–7 sin ese bloque y los restaura con costes del cargador vacíos. Cada cotización se valida matemáticamente y debe corresponder a una sesión existente, sin IDs repetidos. Fechas, identidad, cuotas y snapshots se validan antes de backup/importación. Los permisos nativos y suscripciones privadas siguen fuera del JSON.

El límite de exportación/importación es5MB: no se garantiza exportar cualquier combinación máxima de1000 sesiones con72horas de desglose. Si se supera, se rechaza explícitamente; no se recorta una copia en silencio. La recuperación SQLite consistente conserva el conjunto completo.

Antes de migrar una base existente, conservar una copia SQLite consistente con permisos600. Volver a V18 requiere sus fuentes/build y la base schema8 anterior juntos; V18 rechaza schema9. No restaurar ni activar una instalación real por haber superado pruebas sintéticas.

## Referencias primarias

- [API evcc y límites de estabilidad](https://docs.evcc.io/integrations/rest-api/).
- [Estado](https://docs.evcc.io/integrations/rest-api/operations/getstate/) e [historial](https://docs.evcc.io/integrations/rest-api/operations/getsessions/).
- [Contrato de sesión revisado](https://github.com/evcc-io/evcc/blob/53e4bd87cdf0e62e1017c2c9db689bfc211046c6/core/session/session.go), [handler de sesiones](https://github.com/evcc-io/evcc/blob/53e4bd87cdf0e62e1017c2c9db689bfc211046c6/server/http_session_handler.go) y [serialización JSON](https://github.com/evcc-io/evcc/blob/53e4bd87cdf0e62e1017c2c9db689bfc211046c6/server/http_site_handler.go).
- [Unidad de Go Duration](https://pkg.go.dev/time#Duration).
- Jerarquía de información y feedback: [Tesla Charge Stats](https://www.tesla.com/support/tesla-app/charge-stats) y [Apple Loading](https://developer.apple.com/design/human-interface-guidelines/loading). No se incorporan sus activos.

## Historial de cálculos V32

SQLite13 y JSON11 añaden un historial privado de las cotizaciones guardadas. Un precio nuevo y su evento se escriben en la misma transacción. Si evcc corrige los kWh o el fin con una recepción posterior, se conserva la sesión y el desglose anterior antes de invalidar el precio; el siguiente cálculo queda registrado aparte. Una recepción antigua, un cambio solo de recepción/tiempo activo o un precio duplicado no crean una corrección ficticia. Un resultado iniciado antes de restaurar sigue descartándose.

«Historial del cálculo» consulta solo SQLite al abrir el detalle de una sesión. El GET autenticado `/api/charger/pricing-history` requiere `session_id` hexadecimal local de32caracteres; `limit`1–20 y `offset`0–2048. Usa el ámbito actual del cargador y no devuelve ámbito, ID externo ni credenciales. La paginación y los reintentos mantienen la página anterior si fallan; cerrar, cambiar de sesión, revisar datos, perder conexión o desmontar la vista descarta respuestas abandonadas.

Se conservan como máximo2048 eventos entre todas las sesiones; al retirar una sesión por su límite de1000 también se retira su historial. No es un archivo ilimitado ni prueba de integridad frente a alguien que pueda escribir la base de datos. Las copias JSON1–10 siguen admitidas: los costes actuales anteriores se conservan como `baseline`, con la fecha de incorporación explícita; los costes borrados antes de V32 no se reconstruyen. JSON11 conserva IDs, orden, sesiones originales y precios; se validan referencias estables, fechas y cálculo contra su sesión original antes de restaurar. Mantiene el límite de5MB de exportación/importación.

Antes de migrar DB12→13 se necesita una copia SQLite12 coherente y protegida. Volver aV31 requiere sus fuentes/build y esa copiaDB12 juntos; V31 no abre DB13. Este registro conserva el método `estimated_uniform`: no cualifica los tramos candidatos de evcc, las pausas, la energía solar ni el coste físico exacto.
