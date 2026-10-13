# Diario manual de trayectos

Actividad conserva recorridos terminados que introduce el usuario. Cada registro indica `source: manual` y `coverage: unknown`; no se recibe del coche ni confirma cobertura de todos los viajes. La recepción automática B10, detección de inicio/fin/paradas y asociación a un vehículo siguen pendientes del contrato y muestra autorizados en [SOURCE-QUALIFICATION.md](SOURCE-QUALIFICATION.md). No se deduce energía del SoC ni se convierten diferencias de odómetro en viajes.

## Datos y cálculos

Inicio y fin incluyen zona horaria y se normalizan a UTC. El fin debe ser posterior al inicio y no superar el reloj del servidor más de cinco minutos. Origen/destino son etiquetas opcionales de hasta120 caracteres, sin coordenadas o identidad del coche. Distancia es nullable en km (0–50000); energía es nullable en kWh (0–5000). Vacío permanece `null`; cero es un dato distinto. Se rechazan booleanos, números no finitos y resultados derivados no representables.

Hasta32 paradas declaradas, ordenadas, sin solapamientos y dentro del recorrido. Duración total procede de inicio/fin; `paused_min` suma únicamente esas paradas y `active_min` es el resto del tiempo. Ese resto no confirma conducción. El consumo de un registro sólo existe si tiene distancia positiva y energía declarada; no es una medición del vehículo.

La interfaz ofrece horas del dispositivo o UTC. Rechaza horas locales inexistentes o ambiguas por cambios de hora y permite introducirlas inequívocamente en UTC. Al editar se conservan los instantes originales si no se modificaron. Una conversión de zona inválida mantiene el texto y pide revisarlo. Las cantidades pequeñas siguen siendo editables sin redondearlas a cero.

## API y privacidad

GET `/api/journeys?limit=20&offset=0`: registros del propietario autenticado, máximo100 por página y orden inicio/id descendente. GET `/api/journeys/report?days=30`: ventanas30/90/365 días, intervalo UTC semiabierto por fecha de inicio y meses Europe/Madrid. Incluye recorridos completos por esa fecha, sin prorratearlos al cruzar un día/mes. Conteos y sumas de valores conocidos se separan; eficiencia sólo usa pares con distancia positiva y energía presente. Se informa el número de pares y sus dos sumas; energía sin distancia queda fuera de ese denominador. No se mezcla demo, carga ni telemetría. El informe valida una fila cada vez y acumula sumas con precisión explícita, sin retener todas las paradas.

POST `/api/journeys/requests {}` reserva `{id,expires_at}` sin crear viaje ni revisión SSE. POST `/api/journeys` requiere `request_id` y todos los campos anteriores, incluidos `null` y `stops: []` cuando faltan. PUT `/api/journeys/{id}` requiere campos completos y `revision` esperada. Un reintento idéntico de edición devuelve la fila actual sin otra revisión; datos distintos con revisión antigua producen409. DELETE exige `revision`; un ID ajeno equivale a uno inexistente. POST/PUT devuelven `ManualJourney` y DELETE `{ok:true}`. IDs32hex.

Propietario siempre procede de sesión verificada, nunca del JSON. CSRF y no-store normales; políticas nativas sólo permiten rutas/métodos/queries exactos. Reserva válida7d,128por propietario/2048global; no conserva payload crudo. Huella del intento y fila del diario se enlazan atómicamente. Repetir el alta con misma reserva/intención devuelve la fila existente, incluida una edición posterior; conflicto, caducidad, otro propietario o eliminación rechazan sin recrear. Al alcanzar cupos se falla sin retirar reservas vivas ni viajes. No hay replay automático ni persistencia de formulario en navegador; la PWA sólo conserva su shell.

## Copia y recuperación

SQLite20 añade `manual_journeys` y su ledger `manual_journey_requests`; JSON16 añade `manual_journeys` separado de los `journeys` ficticios anteriores. StateResponse y totales históricos conservan contrato. JSON1–15 restaura un diario manual vacío para el propietario que restaura. Una copia JSON16 privada incluye propietario/revisión/fechas, valida campos derivados recalculados y conflictos antes de escribir; otros propietarios se conservan. Restore válido invalida solicitudes del propietario en la misma transacción; inválido conserva datos y ledger. La exportación verifica su presupuesto de bytes antes de materializar toda la colección y devuelve413, sin truncarla, si no cabe en el límite JSON16.

El botón de informe prepara un JSON de agregados en navegador. La copia completa de registros se obtiene en Ajustes. La recepción del archivo debe comprobarse en el navegador; preparar el enlace no acredita descarga. El informe se consulta en app nativa, pero este incremento no añade un mecanismo nativo de entrega de archivos. Firma, compilación del nuevo cliente y aceptación en teléfono se cualifican por separado.

Reversión futura: código previo junto a copia coherenteSQLite19, sin abrirSQLite20 con código antiguo. En este incremento no se migra una base operativa. La cualificación de software y sus límites se registran en [MANUAL-JOURNEY-QUALIFICATION.json](MANUAL-JOURNEY-QUALIFICATION.json); no cierra la fase5 completa ni A0–A12.
