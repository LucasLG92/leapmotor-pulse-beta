# Energía temporal del cargador: contrato y aceptación

## Motor por sesión V42

Pulse admite ahora una distribución temporal vinculada explícitamente a una sesión cerrada mediante un lector interno async optativo. El cálculo `estimated_intervals`, sus revisiones auditables y el desglose están implementados; todavía hace falta un adaptador concreto y la cualificación del origen físico. El ledger candidato evcc de V30 conserva su función y no alimenta este motor. La documentación experimental de evcc no acredita por sí sola pertenencia, exclusividad ni energía de red.

El contrato exige `version=1`, `scope` SHA-256, `session_id` hexadecimal local de32 caracteres, `source_id`, `source_version`, `basis=charger_total` e `intervals`. Cada fila conserva inicio/fin con zona y kWh finitos; se normalizan las fechas a UTC. Acepta1–1024 intervalos contiguos de hasta una hora, cobertura completa y suma coherente con el cierre (tolerancia1e-9kWh). Los ceros deben ser explícitos. No se rellenan huecos, se normaliza energía ni se recortan buckets externos para hacer coincidir el total.

El motor conserva los intervalos originales, los divide únicamente en sus límites y fronteras PVPC, calcula con fracciones exactas y redondea una sola vez el total. Supone potencia uniforme dentro de cada intervalo recibido. Valora energía total del cargador aPVPC; no acredita gasto de red, descuento solar o factura real. Valida/reproduce los snapshots al guardar o restaurar.

La fuente se inyecta como `create_app(energy_curve_reader=...)`, junto al cargador y PVPC configurados; recibe `(scope, session)` y devuelve la distribución o `None`. No hay formulario, variable de entorno nueva o activación automática. Si no se configura, permanece el cálculo uniforme. Una revisión idéntica no añade eventos ni descarga precios. El trabajo obtiene hasta cuatro sesiones por pasada con cursor, timeout8s cooperativo y cooldown10min. Fuente inválida/fallo conserva el precio previo; `None` permite la estimación uniforme cuando falta. Restore, corrección o cambio de método descartan resultados obsoletos.

El almacenamiento conserva curva, vínculo privado y revisiones en DB14/JSON12; la API pública omite `scope` y `session_id` de la distribución. La interfaz distingue ambos métodos, indica fuente/resolución/supuestos y crea hasta veinte filas por página al abrir los detalles. El contrato interno no prueba la autenticidad de lo que declare su fuente: esa aceptación permanece pendiente.

Pulse conserva el objetivo de calcular el coste automático según la energía de cada tramo. El cálculo por sesión sin fuente temporal sigue siendo `estimated_uniform`, con inicio, fin y kWh del cierre confirmado por evcc. La recepción de una observación no equivale al instante de medición. No generar un fin de sesión cuando solo se observa una pausa.

## Contrato contrastado el 9 de octubre de 2026

Referencia evcc0.316.2, revisión `7db12597018f97076633cdd8262d434a89893fd2`. `sessionEnergy` es la energía acumulada de la sesión en kWh; `chargedEnergy` sigue presente como alias obsoleto. Pulse prioriza el campo actual, permite el antiguo cuando el actual falta y mantiene `null` como ausencia. Un valor actual inválido no se sustituye por el alias. Las sesiones históricas siguen comunicando `chargedEnergy`; enlazan `loadpoint` con el título visible, mientras las métricas usan `state.name`.

El endpoint experimental `/api/history/energy` ofrece buckets de15minutos con límites temporales y energía en kWh. En esta revisión, la agrupación horaria se llama `hour`, aunque la documentación actual muestra un ejemplo `1h`. Los filtros seleccionan slots completos por su comienzo; no recortan energía cuando una sesión ocupa una parte del bucket. Una fila ausente significa cobertura desconocida, no consumo cero.

No existe vínculo de las métricas con el ID de sesión. Dos cargas pueden compartir bucket y un bucket puede cruzar el inicio o fin de una carga. El origen no distingue por slot contador, integración de potencia o recuperación; tampoco ofrece energía de red por loadpoint. La curva de red corresponde a toda la vivienda. Por ello, sumar o recortar buckets indiscriminadamente podría atribuir energía y coste ajenos.

## Cualificación pendiente del origen temporal

Antes de habilitarla se necesita una asociación temporal persistente entre ámbito/título/nombre de entidad y la versión cualificada del origen, con invalidación al cambiar esa relación. La coincidencia actual de nombres no acredita el historial antiguo. El cálculo debe conservar los intervalos originales, comprobar atribución exclusiva, límites, huecos y reconciliación con la energía del cierre; no normalizar una curva para hacer coincidir el total ni convertir huecos en ceros. Los bordes parciales requieren una política de estimación expresamente calificada o un origen con medición vinculada a la sesión.

Toda mejora posterior de un coste uniforme debe ser transaccional, reproducible y trazable, conservando el desglose previo y evitando resultados calculados antes de restauraciones o correcciones. El producto distinguirá «Estimación por intervalos» de «Estimación uniforme», con resolución y cobertura en detalles. Sin energía de red atribuida al vehículo, el valor PVPC de la energía total no acredita gasto real con autoconsumo. No afirmar coste medido o exacto por disponer de buckets.

V29 resolvió el contrato de investigación. V30 implementó adquisición y persistencia de candidatos, apagadas por defecto. V42 añade cotización temporal y revisiones bajo el contrato independiente anterior; no convierte esos candidatos en energía atribuida. A7 sigue abierto por el adaptador/cualificación, energía de red, trayectos y wallbox/B10 físicos. La consulta pública de demostración de V29 devolvió HTTPError; no se obtuvo un payload que cualifique el servicio. No se hicieron consultas autenticadas, lecturas privadas ni transmisiones de telemetría.

## Adquisición candidata V30

El lector consulta slots completos de15min para una entidad observada, con límites de24h/100slots por lote/1MiB/8s. No recupera energía anterior a esa asociación ni conserva slots posteriores a su última corroboración. Cada slot conserva comienzo/fin, kWh, energía devuelta y recepción local; su resumen siempre indica `not_qualified`. Los detalles de la UI explican que aún no modifica el coste ni se suma a sesiones. No se sustituye `estimated_uniform` por datos candidatos.

La recepción se fecha después de que termine el GET histórico. Ese recibo no amplía la ventana elegible: el fin de cada slot sigue limitado por la última corroboración del estado anterior a la consulta. Sondeos posteriores pueden avanzar esa corroboración sin modificar la recepción histórica de slots ya guardados.

DB12/JSON10 conserva hasta2048slots/64épocas/16scopes globales. Cambio o ausencia de identidad, fallo del estado, reinicio y restauración terminan referencias observadas. La importación cierra épocas históricas abiertas y el siguiente sondeo empieza una nueva; `expected_import_epoch` impide publicar escrituras/resultados anteriores a restore. El ledger admite correcciones posteriores y preserva huecos; todavía no es un historial auditable de revisiones de coste. Los archivos1–9 son compatibles y no inventan candidatos.

Una época observada no prueba que no hubiera reconfiguración entre sondeos. La admisión futura necesita evidencia adicional de identidad histórica, historial exhaustivo y exclusividad, con reconciliación sin normalizar la curva. Una carga no alineada puede producir toda su energía en un bucket parcial: recortarla proporcionalmente a su duración puede infravalorarla. Si se cualifica exclusividad, los bordes requieren conservar la energía íntegra y expresar la hipótesis de distribución dentro de su soporte real. Cuando falte esa prueba, conservar la estimación uniforme y el motivo de rechazo.

## Fuentes primarias, sin incorporar implementaciones

- [Estado versionado](https://github.com/evcc-io/evcc/blob/7db12597018f97076633cdd8262d434a89893fd2/server/openapi.state.yaml) y [publicación de campos](https://github.com/evcc-io/evcc/blob/7db12597018f97076633cdd8262d434a89893fd2/core/loadpoint.go).
- [Contrato HTTP versionado](https://github.com/evcc-io/evcc/blob/7db12597018f97076633cdd8262d434a89893fd2/server/openapi.yaml) y [slots históricos](https://github.com/evcc-io/evcc/blob/7db12597018f97076633cdd8262d434a89893fd2/core/metrics/db_history.go).
- [Recolector y cobertura](https://github.com/evcc-io/evcc/blob/7db12597018f97076633cdd8262d434a89893fd2/core/metrics/collector.go) y [sesiones separadas](https://github.com/evcc-io/evcc/blob/7db12597018f97076633cdd8262d434a89893fd2/core/session/session.go).
- [Documentación actual del historial energético](https://docs.evcc.io/integrations/rest-api/operations/getenergyhistory/).
