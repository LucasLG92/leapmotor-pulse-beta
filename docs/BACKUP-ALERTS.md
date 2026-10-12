# Avisos de estado de copias

El monitor comunitario produce informes de metadatos para los perfiles `sqlite` y `archive`. Mantiene el último estado y, con el formato 2, un journal ordenado de hasta 64 transiciones por perfil. La actualización acepta el estado previo de formato 1 y lo convierte al formato actual al persistir una nueva observación. El productor no envía avisos.

## Consumidor optativo

El consumidor Push es un componente separado y permanece desactivado por defecto. Solo prepara avisos genéricos de fallo o recuperación con consentimiento explícito por dispositivo y una suscripción válida. El cuerpo no contiene nombres de archivos, rutas, manifiestos, contenido de copias ni detalles del vehículo. Las pruebas usan claves y transporte sintéticos; no acreditan recepción física.

El lector asocia el perfil real elegido para el host a la etiqueta lógica `host`. En esta proyección comunitaria, `archive` es el valor predeterminado de `PULSE_BACKUP_ALERTS_HOST_PROFILE`, acorde al productor comunitario; la fuente canónica conserva `host`. Esta diferencia solo afecta a la proyección comunitaria de una función nueva y optativa.

## Límite de confianza

El estado del productor debe permanecer en un directorio privado. El consumidor separado necesita una transferencia protegida de esos metadatos y propiedad UID/permisos compatibles con su lector; los ejemplos requieren directorio `0700` y archivo `0600`. No se aceptan rutas relativas, enlaces ni informes que excedan los límites del lector. La integración de despliegue no se crea automáticamente desde scripts de instalación ni archivos Compose.

El estado indica únicamente la observación del monitor. No demuestra que se haya comprobado el hash del contenido, que una copia se pueda restaurar, que un host se haya recuperado ni que un servicio real esté instalado. La cualificación local y sus pruebas están resumidas en [VALIDATION.md](VALIDATION.md) y [BACKUP-ALERTS-QUALIFICATION.json](BACKUP-ALERTS-QUALIFICATION.json). La fase permanece abierta hasta la transferencia protegida y la aceptación operativa/real correspondiente.


## Rollback y recuperación

La cualificación de software parte del esquema 17 y genera el candidato local con esquema 18; eso no demuestra qué esquema está desplegado. Antes de cualquier cambio real se debe conservar una copia íntegra del estado que exista y el artefacto/código compatible con ese estado. Si el operador parte de 17, la recuperación corresponde a la copia 17 junto con el artefacto anterior; si parte de otro esquema, debe preservarse y recuperarse ese esquema con su artefacto compatible. Esta preparación local no migra ni restaura una instancia real.
