# Home Assistant mediante MQTT

El puente comparte exclusivamente los campos de telemetría guardada elegidos por el operador: batería, autonomía, estado de carga, temperatura interior, temperatura mínima de batería y cuatro presiones de neumáticos. Está **apagado por defecto**, excluye la demo, no hace consultas al fabricante ni recibe comandos. Configurar y activar el puente es un consentimiento explícito para compartir esos campos con el broker configurado y sus suscriptores. La integración opera sobre los datos comunes de esta instancia personal; no crea un espacio MQTT por usuario. Ampliar los campos admitidos no amplía la selección existente ni activa nuevas transmisiones.

No se envían identidad, VIN, nombres personalizados, ubicación, perfiles de widget, credenciales ni respuestas crudas del fabricante. La antigüedad se calcula desde `observed_at`; una lectura reciente en el tiempo no acredita que el coche haya actualizado físicamente ese dato. Los mensajes indican `physical_freshness: unknown`. Las observaciones futuras, sin fecha o expiradas y los valores fuera de rango no se publican como lecturas válidas.

## Configuración del servidor

Las variables se leen al arrancar. Un cambio exige reiniciar Pulse. La UI muestra disponibilidad y selección, sin exponer broker, topics, usuario, contraseña o CA. No admite cambiar estas opciones mediante `/api/settings`.

| Variable | Valor inicial | Uso |
|---|---|---|
| `PULSE_MQTT_ENABLED` | `false` | `true` habilita el puente con selección explícita |
| `PULSE_MQTT_HOST` | vacío | Host o IP del broker, sin URL/ruta/credenciales |
| `PULSE_MQTT_PORT` | `8883` | Puerto 1–65535 |
| `PULSE_MQTT_TLS` | `true` | Certificado y hostname verificados; `false` solo loopback de QA |
| `PULSE_MQTT_CA_FILE` | vacío | CA PEM pública explícita; vacío usa las CA del sistema |
| `PULSE_MQTT_USERNAME` / `PULSE_MQTT_PASSWORD` | vacío | Credenciales del broker fuera del repositorio, copias y UI |
| `PULSE_MQTT_INSTANCE` | vacío | Identificador estable y genérico, sin identidad o VIN, p. ej. `casa` |
| `PULSE_MQTT_DISCOVERY_PREFIX` | `homeassistant` | Un segmento igual al prefijo Discovery de HA; no `pulse` |
| `PULSE_MQTT_FIELDS` | vacío | Selección explícita separada por comas; nombres admitidos en la tabla siguiente |
| `PULSE_MQTT_MAX_AGE_SECONDS` | `900` | Antigüedad máxima de la observación, 30–7200 segundos |
| `PULSE_MQTT_POLL_SECONDS` | `15` | Revisión local de SQLite, 1–60 segundos; no consulta al coche |

| Campo seleccionable | Unidad y límites de software | Entidad Home Assistant |
|---|---|---|
| `battery_pct` | 0…100 % | `battery`, `measurement` |
| `range_km` | 0…5000 km | `distance`, `measurement` |
| `charging` | Booleano estricto | `enum`: `charging` / `not_charging` |
| `interior_temperature_c` | −100…100 °C | `temperature`, `measurement` |
| `battery_min_temperature_c` | −100…150 °C; mínimo, no media | `temperature`, `measurement` |
| `front_left_pressure_kpa`, `front_right_pressure_kpa`, `rear_left_pressure_kpa`, `rear_right_pressure_kpa` | 0…1000 kPa | `pressure`, `measurement` |

Los límites rechazan datos incompatibles con el contrato; no son umbrales de seguridad o recomendaciones para el vehículo. Los campos numéricos requieren números finitos, sin convertir texto ni booleanos. Un campo ausente o inválido queda no disponible; no impide compartir otros campos seleccionados válidos. La selección anterior `battery_pct,range_km,charging` conserva su comportamiento.

`enabled=true` requiere host, instancia y campos válidos. Los booleanos son exactamente `true`/`false`; no se aceptan comodines de topics ni campos adicionales. TLS no se desactiva para hosts LAN o públicos. Con una CA propia se debe montar el archivo público de CA en el contenedor como solo lectura y proporcionar su ruta interna. No se instalan certificados globales ni se ignoran errores TLS. `compose.yaml` transmite estas variables; no incorpora credenciales o certificados.

Con TLS habilitado, el transporte rechaza la presencia de `SSLKEYLOGFILE` antes de crear el cliente Paho, el contexto TLS o una conexión. No lee ni muestra el valor de esa variable. El rechazo conserva el destino y la comprobación de certificados; no activa una conexión alternativa sin TLS.

La instancia debe ser única por broker: dos procesos con el mismo identificador se expulsarían mutuamente y compartirían entidades. Al cambiar su nombre hay que retirar las antiguas entidades propias de forma explícita; Pulse no borra namespaces de otras instancias. Asigna al usuario de Pulse permisos de escritura únicamente en `pulse/<instance>/#` y en `<discovery_prefix>/sensor/pulse_<instance>/+/config`, y lectura exclusivamente en `<discovery_prefix>/status`. Home Assistant necesita sus propios permisos de lectura. No uses la cuenta administradora del broker para este puente. Esto es una recomendación de configuración; Pulse no cambia las ACL del broker.

## Descubrimiento, disponibilidad y caducidad

Las entidades usan `unique_id` con prefijo `pulse_<instance>_`. Discovery se publica con QoS1 y retención. Los estados y la disponibilidad de cada campo se publican **sin retención**: un suscriptor nuevo no recupera un valor antiguo como si acabara de observarse. Solo el estado del puente tiene disponibilidad retenida y Last Will `offline`. La configuración no contiene un `command_topic`.

Al iniciar, reconectar o recibir el mensaje de inicio de Home Assistant se anuncian la configuración y los valores todavía vigentes. La publicación se confirma únicamente con PUBACK; esto acredita entrega al broker, no recepción por HA ni validación del vehículo. Si un campo deja de ser válido se comunica su indisponibilidad. Se limpian con payload vacío únicamente los topics propios de los nueve campos soportados que no estén seleccionados.

`expire_after` usa el tiempo restante hasta `observed_at + max_age_seconds`, redondeado hacia abajo y con diez segundos de margen conservador. No se publica un valor con diez segundos o menos de vigencia restante. Reenviar una observación después de una reconexión no le concede de nuevo toda la duración. Las revisiones periódicas no reenvían el mismo estado como un heartbeat. El reloj del servidor debe estar sincronizado; cambios de reloj, tiempos de entrega, reinicio y procesamiento de Home Assistant pueden afectar la presentación visual, que requiere QA real. No se promete desaparición visual instantánea al retirar un dato.

Se limita la cola y las publicaciones pendientes. Una conexión perdida o un timeout descarta la generación de cliente antes de reconectar; cada conexión utiliza un cliente nuevo, sin restaurar la cola QoS anterior. TCP y el handshake TLS tienen un límite de cinco segundos. El supervisor espera el cierre confirmado del cliente antes de crear otro y el cierre del puente está acotado a ocho segundos. Una resolución DNS bloqueada por el sistema operativo puede conservar temporalmente un único hilo de red y su stopper daemon; no se crean clientes adicionales mientras siga pendiente. Errores externos se traducen a códigos constantes y no se exponen sus cuerpos o excepciones.

Al activar demo o importar datos, una publicación real iniciada previamente puede terminar antes del siguiente ciclo/sondeo; nunca se envían valores de demostración. Si se desactiva el puente mediante configuración, no se vuelve a conectar para borrar Discovery: las entidades anteriores permanecen no disponibles; su eliminación en HA/broker es una acción operativa explícita.

## Relación con la carga y PVPC

Este puente exporta lecturas de Pulse y no mide energía del wallbox. PVPC ya calcula el componente variable de energía usando los precios horarios oficiales y los kWh aportados; su distribución uniforme sigue siendo una estimación. Automatizar el registro y calcularlo con energía por tramo requiere validar el cargador y su contador/integración. Una sesión conectado–desconectado puede incluir pausas; no equivale necesariamente a tiempo efectivo cargando. La existencia de un endpoint de sesiones de evcc tampoco demuestra que sus kWh procedan de un contador físico. Véase [PVPC](PVPC.md).

## Fuentes y límites de verificación

Contrato de Discovery y mensajes de inicio: [MQTT de Home Assistant](https://www.home-assistant.io/integrations/mqtt.html). Semántica de caducidad y retención: [Sensor MQTT](https://www.home-assistant.io/integrations/sensor.mqtt/). Clases `temperature`/`pressure` y unidades °C/kPa comprobadas en la [documentación primaria de sensores](https://www.home-assistant.io/integrations/sensor/), revisada el 12 de octubre de 2026. Protocolo, ACK y cola de reconexión: [Paho Client](https://eclipse.dev/paho/files/paho.mqtt.python/html/client.html). Paho 2.1.0 se fija con hashes y se usa bajo su opción permisiva EDL-1.0/BSD-3-Clause; el aviso completo está en `THIRD-PARTY-NOTICES.md`.

El registro histórico de `ACCEPTANCE.md` conserva 17 comprobaciones del puente anterior de tres campos contra Mosquitto 2.1.2 aislado y Paho 2.1.0: TLS/CA/hostname, rechazo de CA incorrecta, QoS1, retención mediante suscriptor tardío, birth, exclusión de demo, caducidad, reinicio del broker y cierre offline. Sus observaciones y reloj son sintéticos; no se probó un proxy que retenga PUBACK. Esas comprobaciones no se presentan como una ejecución nueva de los nueve campos.

La ampliación local del 12 de octubre de 2026 se registra en [MQTT-QUALIFICATION.json](MQTT-QUALIFICATION.json): 55 pruebas MQTT con transporte sintético, contrato HTTP, suite completa del backend, tipos/build y nueve nombres en UI a 390 px en claro/oscuro. No se ha ejecutado un broker ni Home Assistant para esta ampliación. El transporte conserva envío secuencial, cola20/in-flight5 y sus reglas de retención/cierre. Ambas evidencias conservan sus límites: no cualifican Home Assistant del usuario, ACL, hardware, wallbox o B10 ni activan integraciones reales.
