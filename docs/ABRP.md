# Batería compartida con ABRP

Pulse añade un puente original opcional para el piloto privado de ABRP. Aprovecha las observaciones que el Collector ya recibe; no añade consultas al fabricante. Permanece apagado por defecto y requiere consentimiento en Ajustes. Planificación de rutas, GPS y autorización OAuth de ABRP son capacidades separadas aún pendientes.

## Datos y consentimiento

El puente considera únicamente porcentaje de batería y hora UTC de observación, con origen y unidades admitidos. Se exige una observación nueva recibida durante la ejecución actual, guardada como la más reciente y con antigüedad máxima de cinco minutos. La hora de recepción no sustituye la hora observada. No se envían lecturas de demo, importadas o antiguas, ni campos de ubicación, identidad, historial, comandos o widgets. El token configurado vincula el destinatario a la cuenta de ABRP; no se promete anonimato.

El consentimiento se conserva en SQLite entre reinicios. La vinculación de credenciales queda representada por un hash, sin almacenar sus valores en esa preferencia. Una vinculación diferente requiere consentimiento nuevo. Arrancar con permiso conservado sigue esperando una observación nueva. Desconectar bloquea nuevos envíos, también en demo. Una transmisión que ya salió puede llegar al destinatario. Detener este puente no revoca el token en ABRP ni borra sus datos remotos.

Restaurar un JSON válido apaga el consentimiento dentro de la misma transacción. Una importación inválida conserva la preferencia almacenada, aunque se descarta el candidato que estaba en memoria antes del intento. Entrar o salir de demo descarta el candidato; al volver a datos reales se espera otra observación. La demo nunca concede permisos externos.

## Configuración privada del servidor

Parámetros: `PULSE_ABRP_ENABLED` (`true`/`false`, predeterminado `false`), `PULSE_ABRP_API_KEY`, `PULSE_ABRP_USER_TOKEN`; opcionalmente `PULSE_ABRP_POLL_SECONDS` (1–60, predeterminado60), `PULSE_ABRP_SEND_INTERVAL_SECONDS` (60–3600, predeterminado300) y `PULSE_ABRP_MAX_AGE_SECONDS` (30–300, predeterminado300). Las credenciales se suministran fuera del código mediante el mecanismo privado del despliegue. No pegarlas en el navegador, chat, comandos visibles o repositorio.

La página oficial recomienda OAuth y describe el token manual para desarrollo/pruebas. Esta implementación no provisiona cuentas, API keys, OAuth ni asociaciones de vehículo. La configuración normal de Pulse y la vista local mantienen el puente desactivado.

## Transporte y resultados

Se deriva el contrato v1 de la referencia primaria de Iternio fijada a una revisión: POST HTTPS a `/1/tlm/bulk`, API key en query y token de usuario en el cuerpo. El cuerpo contiene un único punto con `utc` en segundos Unix y `soc` en porcentaje. Se acepta sólo HTTP200 más JSON inequívoco con `status` igual a `ok`. La referencia no acredita la compatibilidad actual de una cuenta Pulse ni publica un presupuesto de frecuencia; las pausas y límites anteriores son decisiones de Pulse.

TLS se verifica y el destino es fijo. No se siguen redirecciones ni proxies de entorno, ni se registran URL, cuerpos, credenciales o excepciones de transporte. El cuerpo de respuesta se limita a64KiB y se rechazan claves JSON duplicadas. Hay un solo envío activo y un plazo de red de8s. Un hilo tardío impide solapar el siguiente envío. Antes de transmitir se comprueban nuevamente permiso y antigüedad, y se reserva el intento en SQLite.

El transporte rechaza la presencia de `SSLKEYLOGFILE` antes de crear el contexto TLS o una conexión; no lee ni muestra su valor. Al vencer el plazo o cerrar el puente, se interrumpe el socket de un envío activo, también cuando la respuesta HTTP ya conserva el socket por su cuenta. El trabajador mantiene la exclusión hasta terminar y una respuesta tardía no produce una confirmación. La biblioteca estándar no garantiza interrumpir inmediatamente DNS o una conexión que aún no ha retornado: si devuelve una sesión después del cierre, Pulse la cierra sin enviar el POST.

Un fallo o resultado ambiguo no repite la misma observación. La reserva y el intervalo entre intentos sobreviven a reinicios. Una observación distinta y reciente puede intentarse más tarde. Sólo la confirmación del servidor produce el estado de recepción confirmada; no implica que el coche se haya comprobado físicamente ni que ABRP haya calculado una ruta.

`/api/state` informa estado y consentimiento sin credenciales; utiliza las actualizaciones existentes. `PUT /api/integrations/abrp` recibe exclusivamente `{"enabled":true|false}` y conserva autenticación, CSRF y respuestas `no-store`. La UI comparte este flujo con Android/iOS mediante las allowlists nativas. La PWA no guarda los datos privados en su caché.

## Persistencia, recuperación y aceptación

DB11 añade la preferencia privada al esquema10; el JSON exportado sigue en versión9 y excluye la autorización externa. La copia SQLite privada contiene esa preferencia y debe mantenerse protegida. Para volver al softwareV27 se restaura conjuntamente su código y una copiaDB10 previa; ese software rechazaDB11. Restaurar una copia SQLite autorizada exige revisar los permisos externos conservados antes de configurar el puente.

La aceptación técnica usa fixtures originales y transporte simulado. Sigue pendiente probar credenciales/cuenta, autorización OAuth, recepción real en ABRP y observaciones del B10. Los datos sintéticos y una respuesta simulada no cualifican la integración real.

## Fuentes primarias

- [API de ABRP](https://web.abetterrouteplanner.com/resources/api): capacidades, acceso y diferencia entre OAuth y token manual. Consultada el9oct2026.
- [Referencia oficial OVMS de Iternio](https://github.com/iternio/ovms-link/blob/d3eadc79223760772b34ba50fa54add962ec3d60/lib/abrp.js): revisión `d3eadc79223760772b34ba50fa54add962ec3d60`; se examinó únicamente el contrato, sin incorporar código/tests/algoritmos.
- [HTTP.Request de OVMS](https://docs.openvehicles.com/en/3.3.004/userguide/scripting.html): semántica POST de esa referencia.
- [Privacidad en interfaces Apple](https://developer.apple.com/design/human-interface-guidelines/privacy/): consentimiento contextual y explicación del propósito. Se mantiene el sistema visual propio de Pulse.
