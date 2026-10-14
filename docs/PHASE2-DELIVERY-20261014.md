# Fase 2: actualización de clientes móviles

La fase1 está aceptada por Lucas en iPhone y Android: revisión visual/texto/teclado, lectores en las seis pantallas, errores/foco del favorito vacío, borrador tras pérdida/recuperación de red y recepción JSON. Continúa exclusivamente fase2; no se empieza fase3 hasta completar su aceptación.

## Entrega en preparación

El APK genérico R4 anterior no incluye la última corrección de conexión. Se prepara un APK configurado del piloto con la interfaz actual, conservando com.lucas.pulse y su certificado, versión0.1.1/código2. Antes de entregarlo se comprobarán build, pruebas, lint, assets, configuración y firma. La instalación/actualización real, login y widgets requieren prueba separada.

App y WidgetKit iOS se compilan en GitHub macOS con las fuentes actuales. El IPA de CI es genérico y sin firma: acredita compilación/auditoría, no instalación en el iPhone. Lucas sigue sin Mac ni cuenta Apple Developer; las vías de firma/distribución y las pruebas físicas conservan ese pendiente.

No se publican APK configurado, claves, configuración de operador, bases de datos o archivos personales en la beta. La descarga del piloto sigue protegida en el servidor. Photon permanece preparado sin instalar.
