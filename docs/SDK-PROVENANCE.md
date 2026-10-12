# Procedencia del SDK de conexión

## Dependencia opcional

Pulse declara MATE-API como dependencia opcional en `requirements-cloud.txt`, fijada al commit completo [`5b7d30c30ae10eb18677214e36a9d42398a769e6`](https://github.com/ProtossBlaster/MATE-API/commit/5b7d30c30ae10eb18677214e36a9d42398a769e6). En ese commit, `pyproject.toml` declara `mate-api` versión `0.1.0a16`, importable como `leapmotor_cloud` y compatible con Python `>=3.11`. El README de la misma revisión muestra `0.1.0a9`; por esa discrepancia, la dependencia se fija por SHA y no por tag. El paquete base de Pulse no lo requiere ni importa.

El commit upstream contiene licencia MIT; el texto correspondiente se conserva en [`licenses/mate-api-MIT.txt`](licenses/mate-api-MIT.txt). Pulse no incorpora código fuente del SDK. La licencia y límites de reutilización están resumidos en [THIRD-PARTY-NOTICES.md](../THIRD-PARTY-NOTICES.md).

## Superficie utilizada

La API pública `leapmotor_cloud.b10_reads.B10ReadClient` expone `read(vin)` y devuelve `VehicleReadBundle` con `TelemetrySnapshot` y `ConfigurationSnapshot`. El adaptador original de Pulse valida que ambas respuestas correspondan al vehículo configurado y traduce únicamente los valores enumerados en [CONNECTION.md](CONNECTION.md).

El SDK separa `observed_at` de `received_at`. Pulse conserva la marca de telemetría como hora de observación, deja campos ausentes en `null` y no usa la hora de recepción como sustituto. El endpoint de estado no consume login, almacenamiento privado ni comandos.

## Límites de validación

Las pruebas focales anteriores comprobaron importación e interfaz del SDK con una instalación opcional, y el contrato sin esa extra omitió las pruebas que importan sus clases reales. Este historial no es la suite final del candidato ni CI y no acredita login, certificados, frescura o señales reales, historial de trayectos ni actuación física. El candidato actual mantiene el proveedor optativo y no incorpora material de autenticación. El estado comprobado y sus límites están en [VALIDATION.md](VALIDATION.md).

El SDK no concede derechos sobre certificados, claves, cuentas o activos del fabricante. No se descargan, extraen, guardan ni inspeccionan materiales de autenticación como parte de esta integración.
