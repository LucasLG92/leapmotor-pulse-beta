# Límites del origen para cargas y trayectos

Revisión local del 11 de octubre de 2026. El contrato público y el software existente permiten concretar el siguiente trabajo; no acreditan un cargador ni un B10 real. Los estados de las fases y las siete puertas reales permanecen en [DELIVERY-STATUS.json](DELIVERY-STATUS.json).

## Fase 4: intervalos y sesión de carga

El motor por sesión ya admite intervalos, conserva revisiones de precio y rechaza huecos o energía incompatible con el cierre. El adaptador físico sigue pendiente. Véase [CHARGER-INTERVALS.md](CHARGER-INTERVALS.md).

La [API de historial de evcc](https://docs.evcc.io/integrations/rest-api/operations/getenergyhistory/) publica bloques agregados con inicio, fin y energía en kWh; filtra por entidad, grupo o título. La [API de sesiones](https://docs.evcc.io/integrations/rest-api/operations/getsessions/) publica sesiones cerradas, sus fechas, punto de carga y energía. En los contratos revisados no hay un identificador común que vincule cada bloque con una sesión.

En el commit de evcc utilizado por el contrato experimental actual, `7db12597018f97076633cdd8262d434a89893fd2`, [Slot y Series](https://github.com/evcc-io/evcc/blob/7db12597018f97076633cdd8262d434a89893fd2/core/metrics/db_history.go) no serializan ID de sesión; [Session](https://github.com/evcc-io/evcc/blob/7db12597018f97076633cdd8262d434a89893fd2/core/session/session.go) sí tiene su propio ID. La documentación REST y el código difieren en la descripción de `id`; no se resuelve esa diferencia suponiendo equivalencia.

Un nombre o título coincidente y una suma igual no prueban exclusividad, identidad histórica ni cobertura. Los bloques pueden cruzar bordes de sesión o incluir más de una carga. Los candidatos experimentales de Pulse conservan `not_qualified` y no alimentan automáticamente el precio.

Para implementar el adaptador concreto hacen falta:

- Modelo y versión del cargador/controlador, y un origen autorizado de lecturas.
- Intervalos vinculados por el origen a una sesión, o una cualificación suficiente del contador exclusivo y su identidad histórica.
- Cobertura y reglas de borde demostradas, incluyendo pausas, reanudaciones, huecos, correcciones y reinicios.
- Reconciliación con los kWh del cierre sin recortar, escalar o rellenar bloques arbitrariamente.
- Mediciones adicionales si se pretende distinguir energía de red y autoconsumo. El total del cargador valorado con PVPC sigue siendo una estimación, no una factura.

## Fase 5: registros de trayectos

El SDK opcional de Pulse sigue fijado al commit `5b7d30c30ae10eb18677214e36a9d42398a769e6`; no se ha cambiado su versión ni instalado en producción. Su [CloudReadClient.list_trips](https://github.com/ProtossBlaster/MATE-API/blob/5b7d30c30ae10eb18677214e36a9d42398a769e6/leapmotor_cloud/cloud.py) consulta el historial `mileage/daily/detail/page`. El [lector de historial](https://github.com/ProtossBlaster/MATE-API/blob/5b7d30c30ae10eb18677214e36a9d42398a769e6/leapmotor_cloud/history.py) comprueba páginas y totales, pero conserva las filas crudas sin definir sus campos como viajes.

`HistoryResult.complete` acredita la coherencia de esa paginación según los totales recibidos. No acredita que la nube incluya todos los viajes del periodo. La [prueba de historial](https://github.com/ProtossBlaster/MATE-API/blob/5b7d30c30ae10eb18677214e36a9d42398a769e6/tests/test_cloud_reads.py) comprueba conservación de filas sintéticas; no define unidades ni un esquema B10.

Antes de normalizar y persistir viajes hacen falta identidad estable de registro y vehículo, inicio/fin con unidad y huso inequívocos, distancia en km, energía en kWh cuando exista y una interpretación documentada de la cobertura. También falta una muestra B10 autorizada que permita verificar ese contrato. Origen/destino y paradas requerirán ubicación disponible y autorizada.

La diferencia de odómetro permanece como observación con sus límites en [ANALYTICS.md](ANALYTICS.md). No se convierte en viajes ni consumo por suposición. Las funciones de ingestión, agregados y exportación de viajes reales conservan su estado pendiente.

### Referencia LPB10: muestras y trayectos reconstruidos

Revisión primaria del 12 de octubre de 2026, fijada al commit `38cd101f8790e9c71085557e9d0ad3b03221b6fe`, sin copiar ni ejecutar el proyecto. LPB10 selecciona la [ruta `c10` para B10/B11](https://github.com/txurtxil/LPB10/blob/38cd101f8790e9c71085557e9d0ad3b03221b6fe/lib/leapmotor_engine.dart#L3071-L3086), guarda [muestras locales de odómetro y SoC](https://github.com/txurtxil/LPB10/blob/38cd101f8790e9c71085557e9d0ad3b03221b6fe/lib/history_archive.dart#L1168-L1216) y [reconstruye segmentos](https://github.com/txurtxil/LPB10/blob/38cd101f8790e9c71085557e9d0ad3b03221b6fe/lib/trip_rebuild.dart#L1015-L1046). Su [estimación de eficiencia](https://github.com/txurtxil/LPB10/blob/38cd101f8790e9c71085557e9d0ad3b03221b6fe/lib/trip_rebuild.dart#L1182-L1237) usa la caída de SoC y una capacidad configurada; no es una medición de energía.

`TripPointStore.addPoint` asigna [la hora del cliente al guardar la muestra](https://github.com/txurtxil/LPB10/blob/38cd101f8790e9c71085557e9d0ad3b03221b6fe/lib/main.dart#L2598-L2605). Ese flujo no aporta hora de observación o antigüedad del dato del vehículo. Por tanto, una respuesta obsoleta podría quedar fechada como reciente. Es una inferencia sobre el límite del método, no una comprobación con un B10 real.

Esta referencia no resuelve identidad remota del trayecto, frescura ni cobertura. Pulse conserva `observed_at` separado de la recepción y no adopta esos segmentos como viajes reales ni SoC convertido a kWh como energía medida. La ingestión real de fase 5 sigue pendiente del contrato y la muestra autorizada descritos arriba.

## Incremento histórico del 11 de octubre: formularios

La QA sintética detectó que un error HTTP al guardar una carga dejaba solamente un aviso temporal y perdía el foco al deshabilitar el botón. El formulario de Energía ahora conserva un error visible con `role="alert"` tanto si la mutación devuelve `false` como si lanza una excepción. El borrador permanece intacto; el foco se recupera únicamente si lo perdió el control del formulario y no pasó a otro elemento.

El mensaje indica que no se pudo confirmar el guardado y pide revisar el historial antes de reintentar. No declara que una respuesta incierta equivalga a una operación no ejecutada. Esta corrección no añade replay automático ni acredita idempotencia ante una respuesta perdida después de guardar.

Se comprobaron teclado, fallo sintético 503 en claro/oscuro, persistencia del error tras desaparecer el aviso temporal y reflujo a 390 px. Al retirar el fallo se guardó una carga; su edición actualizó ese mismo registro, con comprobación SQLite de una única fila de 12 kWh/1,80 €. Los formularios restantes, pérdida real de red, lectores y teléfonos siguen pendientes. El resumen autocontenido está en [VALIDATION.md](VALIDATION.md); no se hicieron lecturas externas ni cambios en producción.


## Incremento del 13 de octubre: reintentos de cargas manuales

La pérdida sintética de respuesta después de un commit SQLite reprodujo el riesgo de duplicar energía y coste. El contrato nuevo reserva solicitudes en servidor para el propietario autenticado y enlaza la intención normalizada con la carga de forma atómica. La UI conserva la solicitud en memoria y el mismo intento devuelve la fila existente; conflictos, caducidad, eliminación, restauración y propietarios ajenos no crean una fila. El endpoint antiguo sin solicitud conserva su comportamiento por compatibilidad.

Pruebas del dominio/HTTP y QA del formulario real cubren commit seguido de503, conflicto por cambio de campos, campos vaciados, cancelación de navegación/edición/descarte, reintento sin duplicar y fallo de reserva antes del alta. El aviso explica que el guardado podría haberse completado. SQLite19 añade el ledger operativo, JSON15 lo excluye; una importación válida invalida intentos anteriores, incluso al terminar una cotización en curso. La cobertura es local y sintética: no cualifica red móvil real, cargador, PVPC real de esta versión, iPhone ni B10. El adaptador de intervalos y los trayectos siguen pendientes del origen descrito arriba. [Recibo](CHARGE-SAVE-QUALIFICATION.json).
