# Conexión con Leapmotor

Pulse incorpora un adaptador original para lecturas del B10 mediante la API pública de MATE-API. La dependencia es opcional y está fijada a un commit concreto; el código del SDK no se copia dentro de Pulse. El arranque sin proveedor configurado conserva `missing_material`. La disponibilidad de un vehículo o cuenta autorizados no se verifica en este candidato; el estado del software no acredita su ausencia.

## Estado y límites actuales

- `connection_status()` solo consulta el estado guardado en memoria. No abre conexiones, inicia sesión, lee archivos ni inspecciona certificados.
- `inspect_certificate_path_metadata()` es un chequeo explícito y separado para un bootstrap futuro: valida estructura y metadatos del sistema de archivos (`stat`/acceso de lectura), devuelve solo estados y nunca abre ni devuelve los archivos. La aplicación actual no lo llama ni examina rutas existentes.
- La aplicación arranca con `UnconfiguredProvider`. V40 añade `build_b10_provider()` como fábrica explícita de lectura; no se llama en el arranque normal. Un cliente SDK inyectado se considera `not_connected` hasta que se solicita una lectura y esta termina correctamente. Una opción de configuración no basta para declararlo conectado.
- El adaptador acepta un `B10ReadClient` ya configurado y una identidad de vehículo en memoria. No recopila usuario, contraseña, identidad de instalación, certificados, claves ni parámetros privados; tampoco los lee ni registra.
- Solo `fetch_snapshot()` pide una lectura. La ruta actual de `/api/state` no llama a ese método, por lo que mostrar estado no provoca tráfico al servicio.
- Un fallo de lectura conserva un código fijo y no publica el texto de excepción del SDK o de autenticación. No se reenvía esa lectura. El recolector programa futuras lecturas con su espera progresiva; una renovación fallida tiene además su propio enfriamiento monotónico.

## Snapshot y marcas de tiempo

El adaptador consume `B10ReadClient.read(vin)`, que devuelve telemetría y configuración por separado. Comprueba que ambos resultados correspondan a la identidad configurada y solo exporta campos normalizados:

| Campo Pulse | Origen del SDK | Si falta o no es válido |
|---|---|---|
| `battery_pct` | `precise_soc_percent`, con fallback a `soc_percent` | `null` |
| `range_km` | `expected_range_km` | `null` |
| `odometer_km` | `odometer_km` | `null` |
| `interior_temperature_c` | `interior_temperature_c`, −100…100 °C | `null` |
| `battery_min_temperature_c` | `battery_min_temperature_c`, −100…150 °C; mínimo, no media | `null` |
| `front_left_pressure_kpa`, `front_right_pressure_kpa`, `rear_left_pressure_kpa`, `rear_right_pressure_kpa` | Campos homónimos, 0…1000 kPa | `null` |
| `locked`, `climate_on`, `charging`, `model` | Sin mapeo habilitado | `null` |

`observed_at` procede exclusivamente de `telemetry.observed_at`. Si el proveedor no trae una marca válida, queda `null`; `provider_received_at` se conserva por separado para diagnóstico y nunca sustituye la hora de observación. La configuración incluye su propia `configuration_observed_at`. La frescura permanece `unknown` porque todavía no se ha validado una ventana de vigencia para las señales del B10.

El snapshot no incluye VIN, respuesta cruda, credenciales, tokens, rutas de certificados ni parámetros de autenticación. Las capacidades también quedan `unknown`, porque este adaptador no ha cualificado permisos de una cuenta ni una lectura de capacidades en un B10 real.

## Comandos

Los comandos físicos permanecen deshabilitados. `command_status()` responde `enabled: false` y `availability: unknown`; `execute_command()` rechaza la operación, incluso con confirmación, cliente configurado o lectura correcta. No se llama a ningún método de comandos del SDK. El código no ofrece una bandera de configuración para abrirlos.

Para habilitar comandos (fase 7 del roadmap vigente) harán falta capacidades y permisos observados para la cuenta y el vehículo, validación de precondiciones y respuesta ambigua, y evidencia física individual por comando. Una respuesta aceptada por la nube no acredita que el vehículo haya ejecutado la orden.

## Puerta de integración real

V40 incorpora la construcción del `B10ReadClient`, su `SessionStore` privado y el proveedor, con adquisición y renovación de sesión en memoria. Se inyecta el resultado en `create_app(provider=...)`, que ya dispone de recolector fuera del ejecutor SQLite. `configure_provider()` es una fachada independiente y no configura la instancia ASGI.

La autenticación necesita callbacks y transporte preparados por un operador con material legítimo. El paquete no consigue certificados, credenciales ni identidad de instalación. V41 permite conservar la sesión en un almacén cifrado explícito, separado de las copias JSON/SQLite de Pulse. Los archivos privados y la clave deben permanecer fuera del repositorio. Login real, gestión operativa de la clave y cualificación del B10 siguen pendientes.

Los tests del proveedor usan lectores y snapshots sintéticos etiquetados como fixtures de test. Con el extra, cubren los modelos reales del SDK y su `B10ReadClient`; un `SessionStore` vacío y un transporte que falla si se llama garantizan que no haya tráfico. No prueban login, compatibilidad de señales, frescura, historial ni actuación física. La aceptación real requiere comparar lecturas pasivas con la aplicación oficial y el vehículo, registrando modelo, versión, campo, marca temporal y evidencia.

## Bootstrap de lectura V40 y persistencia opcional V41

La fábrica `pulse.providers.build_b10_provider` requiere transporte, identidad del vehículo, orígenes regionales y `acquire_session` explícitos. `renew_session` es opcional. Construcción y consulta de estado no autentican, inspeccionan archivos ni consultan al coche. El SDK se importa únicamente al invocar la fábrica; el arranque normal sigue funcionando sin el extra.

Ejemplo de ensamblaje para un futuro operador autorizado; los objetos y callbacks se preparan fuera de este ejemplo, sin secretos en el código de Pulse:

```python
from pulse.app import create_app
from pulse.providers import build_b10_provider

provider = build_b10_provider(
    transport=authorized_tls_transport,
    acquire_session=acquire_authorized_session,
    renew_session=renew_authorized_session,
    vehicle_id=authorized_vehicle_id,
    center=authorized_center_origin,
    region=authorized_region_origin,
)
app = create_app(provider=provider)
```

El callback de adquisición devuelve un `CloudSession` del SDK fijado y se ejecuta una vez, en la primera lectura. Puede invocar `LoginClient.login(...)` o recuperar una sesión desde un almacén autorizado del operador. Si falla, Pulse no repite el login: se requiere reconfigurar el proveedor o reiniciar explícitamente. No existe un endpoint web que recopile ese material.

El callback `renew_authorized_session(session)` puede invocar `LoginClient.refresh(session, device_id=authorized_installation_id)`. Durante una lectura, Pulse intenta renovar si faltan como máximo120s para caducar. Comprueba tipo, validez y continuidad de cuenta, vínculo de instalación y pareja cert/key antes de reemplazar la sesión. Si el material devuelto es inválido, bloquea nuevas renovaciones hasta reconfigurar. Sin almacén persistente, un fallo del callback permite otro intento después de600s medidos por reloj monotónico; el recolector puede esperar aún más. Con el almacén V41, una excepción del callback bloquea nuevos intentos porque el resultado de la operación puede ser incierto. Una sesión renovada muy corta puede usarse mientras siga válida, pero no dispara renovaciones continuas dentro de esa espera.

La adquisición, renovación y lectura se serializan en el mismo proveedor. No se reenvía una lectura rechazada, no hay login de respaldo tras un refresh fallido y nunca se accede a la superficie de comandos. Si no hay refresco utilizable, un token válido se usa hasta su vencimiento; después se pide autorización. La sesión adquirida y la renovada deben tener vencimiento conocido y zona horaria; si falta, se rechazan antes de usarse. No se inventa un TTL para completar material ausente.

Sin `session_vault`, el proceso conserva la sesión solo en memoria y la primera lectura tras reiniciar ejecuta de nuevo el callback de adquisición. La sesión/store deben pertenecer exclusivamente a esa instancia: esta coordinación no protege otro cliente externo que comparta la cuenta o los tokens. El transporte del operador debe aplicar TLS verificado, destinos permitidos y límites de tiempo/respuesta. No se ha ejecutado login/refresh real ni inspeccionado material de autenticación.

## Almacén privado V41

El operador puede construir `EncryptedSessionVault` desde `pulse.providers.session_vault` e inyectarlo mediante `build_b10_provider(session_vault=...)`. Requiere un directorio absoluto privado, una clave de 32 bytes proporcionada explícitamente y un `binding` estable de cuenta/instalación (ASCII visible, 1–128 caracteres). No genera, busca ni guarda la clave. Construcción y consulta de estado no hacen I/O. Cada objeto pertenece a un único proveedor; la primera carga/escritura conserva un bloqueo exclusivo de proceso hasta el cierre.

En la primera lectura se recupera la sesión válida sin ejecutar otro login. AES-256-GCM protege el documento completo; nonce nuevo por escritura y asociación al ámbito explícito, vehículo y orígenes regionales. El documento contiene material de sesión y referencias de cert/key, nunca el contenido de esos archivos ni la contraseña. Solo se escribe texto cifrado, con reemplazo atómico y sincronización de archivo/directorios. Directorio700 y archivos600, propietario actual, sin symlinks/hardlinks/FIFO; los ancestros deben pertenecer a root o al usuario actual y no admitir escritura de terceros salvo directorios sticky. No se reparan permisos de archivos existentes.

Antes de autenticar o renovar se guarda y sincroniza un estado cifrado `acquisition_pending` o `renewal_pending`; la sesión resultante se persiste antes de activarse. Un fallo de persistencia bloquea la instancia. Si falla el checkpoint previo, no se inicia el callback. Una vez iniciada la operación, el archivo anterior ya ha sido sustituido: tras reiniciar se recupera el resultado nuevo válido si quedó escrito, o se rechaza el estado pendiente. Un documento pendiente/inválido, contexto distinto o clave incorrecta tampoco inicia login de respaldo. No se borra ni se restaura automáticamente material antiguo para repetir una operación cuyo resultado es incierto. Su recuperación exige revisar y reconfigurar explícitamente el material autorizado.

El cierre ASGI detiene el recolector y cierra el proveedor gestionado. Una operación ya activa conserva el bloqueo hasta terminar; el cierre impide empezar otra autenticación, renovación o lectura. Para restaurar o rotar la clave operativa se necesita un procedimiento autorizado separado: la copia de datos de Pulse no incluye el almacén ni la clave. No restaurar un documento de sesión anterior tras una renovación incierta. El bloqueo no coordina otras aplicaciones que compartan cuenta, ni protege frente al mismo UID/root o a restauraciones deliberadas de texto cifrado antiguo.

Validación local V41:857 pruebas Python/6 omitidas,65 pruebas focalizadas/4 omitidas y55 del contrato SDK/1 deseleccionada; esta última comprobación de cierre ASGI sí se ejecuta en la batería base. Datos y transporte son sintéticos. La fixture modela propietario root exclusivamente para `/` y `/tmp`, que el sandbox presenta como UID65534. No acredita permisos, durabilidad ante caída ni recuperación en el futuro despliegue Linux. Clave real, rotación, TLS/login/refresh físicos y B10 siguen pendientes; el almacén está apagado por defecto y no se ha desplegado.

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
