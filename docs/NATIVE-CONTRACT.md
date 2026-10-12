# Contrato de la aplicación nativa

## Estado

Pulse conserva una app compartida con Capacitor y código de integración nativa para Android/iOS y widgets. El candidato actual Android 0.1.1/versionCode 2 se ensambló sin firma. En iOS 0.1.1/build 2 solo se sincronizó Capacitor; no se ejecutó Xcode. No hay una nueva ejecución de CI para el candidato, distribución firmada ni aceptación física. Las pruebas anteriores o la presencia de código no cambian esos estados.

## API y autenticación

El contrato de rutas y modelos está en [openapi.json](openapi.json). El modo nativo permanece cerrado por defecto. La configuración OAuth es explícita y separa cliente público de cualquier material confidencial. El cliente no incluye credenciales, tokens, grants, certificados ni configuración de una instancia. Ninguna plantilla de configuración genérica convierte una sesión sintética en autenticación real.

La capa de aplicación debe validar issuer, audience, uso del token y sesión según el proveedor configurado; no basta aceptar la forma de un token. Las renovaciones usan callbacks y almacenamiento explícitos. No se persiste material de autenticación en preferencias compartidas, logs, exportaciones ni widgets. La app no interpreta la ausencia de una respuesta como autorización ni repite órdenes físicas automáticamente.

## Privacidad y capacidades

La app nativa solo puede mostrar campos elegidos por el usuario. Los widgets usan una copia con consentimiento, caducidad y masking; no consultan directamente el vehículo ni se autentican por su cuenta. El bloqueo de órdenes físicas, la incertidumbre de una acción y la reconciliación con el estado observado son parte del contrato.

## Validación pendiente

La sincronización Capacitor y el ensamblado Android sin firma no verifican build Xcode, firma, distribución, login/refresh con proveedor real, seguridad del almacenamiento, actualización o push tras reinicio, widgets instalados, consumo, vehículo ni cargador. Consulta [VALIDATION.md](VALIDATION.md), [ANDROID-BUILD.md](ANDROID-BUILD.md), [IOS-BUILD.md](IOS-BUILD.md) y [ACCEPTANCE.md](ACCEPTANCE.md).
