# Contrato HTTP Pulse 0.1

Actualización de apariencia: SQLite15 y JSON13 añaden `settings.vehicle_color`, nullable y limitado a seis colores del B10. Importar JSON1–12 asigna el color a `null`; actualizar ajustes sin ese campo conserva la elección. [Guía](VEHICLE-APPEARANCE.md). Las referencias V42 siguientes documentan el incremento previo.

V42: `ChargeQuote.pricing` discrimina por `accuracy` entre `estimated_uniform` y `estimated_intervals`. El segundo incluye `energy_distribution` con fuente/versión/base/intervalos; omite vínculo privado `scope`/`session_id`. Aparece en las cotizaciones guardadas de sesiones y su historial. Ningún payload HTTP acepta curvas ni habilita una fuente; las cargas manuales siguen uniformes. Export/import usan JSON12 y DB14, con compatibilidad1–11 bajo su semántica anterior.

La fuente ejecutable es `docs/openapi.json`, generada con `python scripts/export_openapi.py`. Los tipos del navegador derivan de sus schemas mediante `pnpm types:api`. Se regenera y comprueba el contrato antes de cada build; no contiene configuración, credenciales ni datos de usuario.

Toda API personal exige acceso local de loopback en modo `local` o una sesión OIDC verificada en modo `oidc`. `Remote-User` y las cabeceras forwarded no autentican. No se permite CORS. La interfaz pública contiene únicamente el shell genérico. El backend nunca consulta Leapmotor al navegar por una pantalla.

`GET /api/session` devuelve `{csrf_token, authenticated, user:{id,name}|null, login_url}` con una cookie opaca HttpOnly. OIDC usa cookie Secure con prefijo `__Host-`, SameSite=Lax y path `/`; PKCE, state y nonce se conservan únicamente en servidor. Las sesiones autenticadas caducan a las ocho horas; las anónimas a los diez minutos. Se invalidan al reiniciar Pulse. La lista de propietarios es explícita. El ID token se valida antes de consultar UserInfo; sus subjects deben coincidir.

`GET /api/auth/login` inicia Authorization Code + PKCE contra el issuer configurado, con callback fijo. `GET /api/auth/callback` verifica y rota la sesión. `POST /api/auth/logout {}` cierra Pulse; la sesión independiente del proveedor de identidad no se modifica.

Toda mutación requiere cookie, `X-Pulse-CSRF` y JSON del mismo origen. Campos desconocidos, coerciones de booleanos, números no finitos y datos inválidos se rechazan; las respuestas de validación no reflejan los valores enviados. Tamaño máximo HTTP: 5 MiB, incluido transfer encoding sin Content-Length; el documento importado tiene además el límite estricto del dominio. Todas las respuestas API llevan `Cache-Control: no-store`.

| Método/ruta | Datos/resultado |
|---|---|
| GET `/api/state` | `StateResponse`: preparación o demo, vehículo nullable, cargas y totales separados manual/demo, mantenimiento, eventos, comandos e historial. Ver schemas OpenAPI. |
| PUT `/api/settings` | Actualización parcial de vehicle_name, model, tariff_eur_kwh, home_label, pricing_method y vehicle_color. Color nullable con seis valores admitidos; omitirlo conserva la elección. Tarifa entre 0 y 5 €/kWh. |
| POST `/api/demo` | `{enabled:bool}`; salir elimina solo registros ficticios. |
| POST `/api/commands` | `{command,confirmed:bool}`. Solo simulación; cambia estado persistido y añade journal. Fuera de demo: 409. |
| GET `/api/commands/history?limit=50` | `{items:[{id,command,status,created_at,message}]}`; entre 1 y 100 elementos. |
| PUT `/api/protection` | `{enabled:bool}`; solo demo. |
| POST `/api/protection/event` | `{}`; evento ficticio solo en demo. |
| GET `/api/charges?limit=100&offset=0` | `{items,limit,offset,total}`, máximos 100 por página. |
| POST `/api/charges` | `{started_at:ISO_con_zona,kwh}`; coste con tarifa actual, conservada por sesión. |
| PUT `/api/charges/{id}` | Actualización parcial started_at, kwh, tariff_eur_kwh; solo registros manuales. |
| DELETE `/api/charges/{id}` | `{}`; solo registros manuales. |
| POST `/api/maintenance` | `{title,due_date:YYYY-MM-DD}`. |
| PUT `/api/maintenance/{id}` | Actualización parcial title, due_date, completed. |
| DELETE `/api/maintenance/{id}` | `{}`. |
| GET `/api/widget-profiles` | `{items}` de perfiles del subject de sesión. Sin IDs de propietario en respuestas de editor. |
| POST `/api/widget-profiles` | `{name,fields,appearance,privacy_mode,max_age_minutes}`; hasta cinco perfiles por propietario. Campos battery_pct/range_km/charging sin duplicados, nombre1–60, privacidad masked por defecto, antigüedad15–120min. |
| PUT `/api/widget-profiles/{id}` | Actualización parcial del perfil propio y aumento de revision. ID ajeno devuelve404 igual que uno inexistente. |
| DELETE `/api/widget-profiles/{id}` | `{}`; elimina solo el perfil propio. No concede acceso nativo, pairing ni comandos. |
| GET `/api/telemetry` | Último snapshot normalizado, calidad temporal y counters del collector. No consulta al fabricante; frescura física sigue unknown. |
| GET `/api/telemetry/history?limit=100&offset=0` | Snapshots observados separados de demo, máximo100 por página; retención actual1000 por orden local de ingestión. No constituye todavía historial de viajes/energía completo. |
| GET `/api/charger` | Estado del lector evcc y última observación mínima guardada. Apagado por defecto; nunca inicia una lectura externa. Recepción local y cualificación física explícitas. |
| GET `/api/charger/sessions?limit=20&offset=0` | `{items,total,limit,offset}` del ámbito seleccionado; solo historial local. Límite1–100, offset0–1000, retención global1000. |
| GET `/api/export` | JSON formato13, con el color opcional del B10, con historial del cargador y revisiones de cotización y sin permisos/códigos/hashes nativos; descarga explícita. Perfiles limitados al propietario de sesión, conservando su subject en el archivo para integridad al restaurar. |
| POST `/api/import` | `{confirmed:true,data:documento}`; acepta formatos1–13, validación íntegra y transacción atómica. Valida documento/propietario/fechas/identidad del cargador antes del backup. Backup e importación son un solo trabajo; poda a cinco copias privadas tras restauración correcta. Rechazos no alteran recuperación ni perfiles ajenos; restaurar perfiles revoca sus dispositivos. Una copia anterior a6 reemplaza el historial del cargador por vacío. |
| GET `/api/events` | SSE `event: state`, `data: {revision:int}`; sin datos personales, heartbeat 15s, máximo 32 suscriptores. Se cierra al caducar/invalidad la sesión. |

Errores esperados: `{detail:mensaje}`, 400/401/403/404/409/410/413/415/422/429/500/503 según el caso. Las vistas no interpretan HTML de login como una respuesta API.

V9 incorporó `/api/push/capabilities` y `/api/push/subscriptions`, con activación, revocación y prueba confirmada del propietario; contratos generados en OpenAPI y detalle en [PUSH.md](PUSH.md). Estas mutaciones tienen un límite de4096bytes y exigen la sesión original vigente para enviar. En V9, SQLite pasó a schema6 y export/import seguíaV5. V11 usa SQLite schema7 y JSON formato6; sigue excluyendo las suscripciones cifradas, que una importación no modifica. El resultado `accepted` solo confirma aceptación del servicio externo.

V11 incorpora el lector evcc independiente y opcional, de solo lectura y sin credenciales. Historial y observaciones mínimos tienen su propio ámbito, retención y deduplicación. Estado actual: duración en segundos; historial upstream examinado: nanosegundos Go convertidos explícitamente a segundos. No se integra potencia sobre huecos como energía medida. La comparación PVPC usa el endpoint existente de cotización, sin añadir cargas ni costes a registros manuales. Configuración, límites y versión examinada: [EVCC](EVCC.md).

SQLite dispone de un propietario de proceso y un executor de un hilo. Todas las operaciones de dominio, incluido backup/import, se ejecutan fuera del event loop. Las lecturas no reservan la transacción de escritura. Schema V5 migra V1–V4 conservando datos e incorpora permisos nativos; la versión SQLite debe incluir la corrección WAL requerida por el dominio. PVPC V4 conserva su desglose por carga y la analítica separa orígenes; sus rutas y payloads actuales están en OpenAPI y [PVPC](PVPC.md).

El arranque normal no tiene collector ni SDK. Un bootstrap autorizado puede inyectar un proveedor read-only en `create_app(provider=...)`; existe un único collector con executor separado y consultas compartidas. GET nunca inicia una lectura. `state.mode=telemetry_read_only` solo aparece con proveedor explícito y snapshot; los comandos siguen cerrados. `observed_at`, `provider_received_at` y recepción local se conservan por separado. La calidad temporal no acredita la semántica física de la fuente; tras dos horas los valores desaparecen del estado actual y se conservan como historial. Recepciones locales importadas más de60s en el futuro se rechazan; el orden de ingestión usa rowid local, no fechas externas.

Los perfiles del editor son configuración, no widgets instalados. Pairings, lista/revocación de dispositivos, canje OAuth y snapshot exclusivo están implementados y descritos en [NATIVE-CONTRACT](NATIVE-CONTRACT.md). Las rutas `/api/native/` usan permisos explícitos, sin heredar cookie BFF/CSRF, cuerpo máximo4KiB y origin verificado si se envía. Registro requiere access token introspeccionado; snapshot/desvinculación requieren permiso opaco limitado al perfil. Ninguno autoriza comandos. La habilitación está cerrada por defecto. La UI conserva el código solo temporalmente y lo oculta por defecto; no lo pone en URLs, localStorage o SW. La app/caché protegida nativas y dispositivos físicos siguen pendientes.

`GET /healthz` público solo acredita proceso vivo; `GET /readyz` requiere almacenamiento inicializado y build estático presente. El frontend se sirve desde `apps/web/dist`, incluyendo `/index.html`, `/sw.js`, iconos, manifest y assets con hash. El SW precachea únicamente esos archivos genéricos, nunca API, exportación ni login. Las rutas privadas no están disponibles al reiniciar offline.

Los GET finitos de `/api/` usan NetworkOnly; `/api/events` pasa directamente a la red sin `respondWith`. Antes de solicitar una actualización explícita, la UI cierra SSE y bloquea escrituras, cotizaciones e importaciones concurrentes. Si el controlador no cambia en ocho segundos, recupera la suscripción y permite reintentar. Véase [PWA](PWA.md).
