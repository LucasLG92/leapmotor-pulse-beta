# Lecturas del vehículo

El proveedor de vehículo incluido tiene un adaptador de lectura B10 para el SDK opcional MATE-API. El adaptador recibe un cliente ya configurado y una identidad de vehículo; normaliza solo un conjunto pequeño de valores, como carga de batería, autonomía y odómetro cuando la fuente los ofrece.

El arranque normal no importa el SDK, no busca credenciales o certificados y no inicia una sesión. Para una futura conexión, un operador tendría que aportar de forma explícita callbacks autorizados de adquisición y renovación de sesión y un transporte TLS, junto con el material legítimo requerido por el proveedor. El repositorio no ofrece wizard de login ni configuración B10 en la beta pública. Consulta la [licencia MIT del SDK](licenses/mate-api-MIT.txt) antes de instalarlo.

Una lectura aceptada por el adaptador solo confirma que el SDK devolvió datos con una forma válida y una identidad coincidente. La frescura y capacidad física permanecen sin validar. No se habilitan comandos de bloqueo, climatización o carga: las operaciones físicas siguen bloqueadas mientras falte validación de capacidad y seguridad.

La beta local no inicia sesión en servicios del vehículo ni conecta un B10. Las pruebas de contrato y datos de ejemplo no equivalen a una prueba con vehículo real.

## Transporte opcional preparado por Pulse

`pulse.providers.build_b10_transport(ca_file=..., center=..., region=...)` compone el `UrllibTransport` del SDK fijado. Reduce la preparación manual; no instala el extra, no lee archivos durante construcción, no inicia sesión ni configura automáticamente la aplicación.

```python
from pathlib import Path
from pulse.providers import build_b10_transport

transport = build_b10_transport(
    ca_file=Path("/run/pulse-cloud/authorized-ca.pem"),
    center="https://appgateway.leapmotor-international.de",
    region="https://appgateway.leapmotor-international.de",
)
```

Pasa este mismo `transport` a los callbacks autorizados de `LoginClient` y a `build_b10_provider`. La CA y los certificados deben proceder del operador legítimo y permanecer fuera del repositorio; el ejemplo es una ruta genérica, no material suministrado. Los orígenes se validan con la allowlist del SHA fijado. No permite hosts añadidos por el usuario ni consultas de rutas regionales: cuando la cuenta requiera otra región, habrá que validar su soporte antes de ampliar la integración.

Pulse limita el envío a cuatro parejas método/host/ruta: login y renovación POST en el origen global del SDK; telemetría POST y configuración GET en el gateway B10 admitido. Rechaza comandos, destinos externos, método/host incorrectos, rutas codificadas o desconocidas y query en POST antes de TLS/archivos/red. El SDK exige CA explícita, cadena y hostname verificados y certificado de cliente; desactiva proxies ambientales, cookies, redirecciones y reintentos. Pulse fija respuesta máxima1MiB y timeout15s. **Es un timeout de operación de socket, no un plazo total de la lectura ni un límite demostrado para DNS.** El lector realiza dos solicitudes independientes; el timeout del recolector tampoco cancela una operación de socket ya iniciada.

La política TLS sigue la del SDK: `CERT_REQUIRED`, hostname y CA explícita, cadena parcial admitida y `VERIFY_X509_STRICT` desactivado. Esto no equivale a verificación X.509 estricta y necesita comprobación con los certificados/gateway autorizados antes de conectar. Además, Pulse rechaza cada envío si existe `SSLKEYLOGFILE`, sin leer su valor ni modificar el entorno: `ssl.create_default_context` puede usar esa variable para escribir claves TLS. No hay opción de Pulse para habilitar ese registro.

Las comprobaciones sintéticas usan el SDK real con contexto TLS y opener sustituidos: no leen certificados ni hacen tráfico. Comprueban las cuatro rutas, lector B10, rechazo antes de TLS, límites, cierre, errores saneados y ausencia de reintento. No acreditan login/refresh, TLS real, material, frescura o señales del vehículo. El transporte genérico inyectado continúa admitido para integraciones explícitas; sus controles siguen siendo responsabilidad de su autor. No se abren comandos físicos.


Referencias: [contextos SSL de Python](https://docs.python.org/3.12/library/ssl.html#ssl.create_default_context), [timeout urllib](https://docs.python.org/3.12/library/urllib.request.html#urllib.request.urlopen) y [licencia MIT del SDK](licenses/mate-api-MIT.txt).
