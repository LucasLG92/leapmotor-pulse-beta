# Notificaciones Push

Pulse admite avisos optativos que requieren consentimiento explícito y una configuración válida del operador. La aplicación no comparte claves ni suscripciones entre usuarios. Los cuerpos deben limitar la información personal; el servicio Push conoce metadatos de entrega aunque el contenido vaya cifrado.

Las pruebas locales usan claves efímeras y transporte simulado. Comprueban políticas de sesión, autorización, caducidad, revocación y manejo de resultados ambiguos; no envían avisos a un proveedor ni prueban la recepción del teléfono.

## Aceptación y límites

Hay confirmaciones humanas históricas de recepción de avisos en dispositivos concretos, incluso en una prueba con el teléfono bloqueado. Son evidencia de esas interacciones reportadas y no se generalizan al candidato actual, a otros modelos, a la latencia ni a la entrega universal. Para el candidato actual siguen pendientes la sesión autenticada y la recepción tras reinicio, así como la aceptación en teléfonos y la comprobación de los flujos reales.

La validación local y su alcance están resumidos en [VALIDATION.md](VALIDATION.md); los criterios restantes están en [DELIVERY-STATUS.json](DELIVERY-STATUS.json). No se publican suscripciones, credenciales, endpoints privados, claves, registros de envío ni recibos de servidor.

## Estado de copias

Los avisos de copias usan un consumidor optativo y separado del monitor. El monitor comunitario solo conserva estado y transiciones metadata-only; nunca envía Push. El contrato, el perfil comunitario `archive` y los límites de UID/transferencia están en [BACKUP-ALERTS.md](BACKUP-ALERTS.md).
