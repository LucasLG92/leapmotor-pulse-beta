# Almacenamiento de permisos para widgets

El esquema SQLite V5 añade `widget_pairings` y `widget_grants`. Ambas tablas
referencian `widget_profiles(id)` con `ON DELETE CASCADE`; borrar un perfil o
sustituirlo al importar invalida su código pendiente y todos sus permisos. Editar
un perfil conserva los permisos ya enlazados: cada lectura usa los campos,
privacidad y revisión actuales. Un código pendiente se invalida al editar. La
migración V4→V5 es aditiva y conserva ajustes, perfiles, telemetría y snapshots
PVPC existentes.

`create_widget_pairing(owner_id, profile_id)` devuelve una sola vez
`{pairing_code, expires_at}`. El código contiene 256 bits aleatorios, vence a
los cinco minutos y se guarda únicamente como SHA-256. Cada perfil tiene un
código vigente y no admite otra emisión durante 30 segundos. El rechazo por
enfriamiento deja intacto el código vigente.

`register_widget_grant(identity, pairing_code, installation_id, device_name)`
consume el código y crea el permiso en la misma transacción. `identity` debe
contener exactamente `issuer`, `subject` y `client_id`; la identidad ya debe
haber sido autenticada por la capa de aplicación. El `subject` tiene que ser el
propietario del perfil. Los fallos de código inexistente, caducado, consumido o
de otro propietario responden con el mismo error 401. Una instalación puede
reenlazarse con el mismo perfil, issuer, subject y cliente; el permiso previo se
revoca durante ese reemplazo. Un perfil admite hasta cinco permisos activos.

La respuesta de registro es
`{grant: {id, profile_id, device_name, created_at, expires_at, last_seen_at}, widget_token}`.
El token opaco tiene 256 bits aleatorios, prefijo `pw_` y caduca a los 30 días.
Solo se devuelve al emitirlo: SQLite guarda hashes SHA-256 del token y del
identificador aleatorio de instalación. Los listados solo incluyen los seis
campos públicos de `grant`; no incluyen issuer, subject, cliente, hash, token o
instalación. `last_seen_at` se conserva como `null`: una lectura no escribe en
SQLite ni renueva el vencimiento.

`widget_grant_context(token, grant_id)` verifica cada lectura y devuelve el
perfil público, los metadatos públicos del permiso, `owner_id` y los valores
internos `issuer` y `client_id` para que la aplicación compare su configuración
OIDC actual. No devuelve subject, instalación ni hashes. Errores de token,
identificador, permiso ausente o vencido son uniformes. La revocación desde web
comprueba propietario y perfil; la revocación con token valida primero la
credencial. La eliminación del perfil y las importaciones compatibles con formatosJSON1–7
invalidan los grants afectados mediante claves foráneas; una edición mantiene
el enlace y hace que el widget reciba la configuración actualizada.

`widget_snapshot_context(token, grant_id)` resuelve autorización y perfil dentro de una transacción de lectura. Para vehículo consulta la última telemetría y demo mínimo de campos seleccionados —incluido cierre si se eligió— más hora observada. Para energía agrega únicamente manual_charges en la ventana inclusiva de30días UTC terminada en el reloj del cálculo; no consulta demo/telemetría, estado completo, trayectos ni red externa. No actualiza last_seen_at ni rejuvenece permisos. La aplicación decide selección/privacidad/TTL y mantiene procedencia demo o manual_energy separada. No acredita vehículo o medición física.

DB8 agrega una barrera semántica7→8 sin tablas nuevas: los campos V12 no pueden leerse con código V11. ExportJSON7 incluye campos nuevos; formatos1–6 solo aceptan sus campos anteriores. Preserva datos de cargador/telemetría/ajustes y consentimiento según versiones previas. No serializa códigos, grants, hashes, instalaciones ni permisos. Exportar con owner_id limita perfiles al propietario; restaurar ese owner sustituye solo sus perfiles y conserva los demás. owner_id=None se reserva para copia/restauración integral interna, nunca una ruta HTTP.

Rollback requiere código/build V11 junto con copiaDB7 anterior, privada y consistente; no cambiar PRAGMA user_version para simular una degradación. Las pruebas de migración/restauración son locales y no acreditan recuperación física del servidor.

Una copia o restauración SQLite externa al Store puede recuperar hashes de
grants revocados desde una copia anterior. Tras una recuperación de ese tipo,
un operador debe invocar explícitamente `revoke_all_widget_grants()` antes de
volver a aceptar lecturas; elimina grants y códigos pendientes. No se ejecuta
al arrancar para no invalidar permisos en reinicios normales. El archivo SQLite
y sus copias contienen material privado de autorización y deben tener permisos
de almacenamiento restrictivos.
