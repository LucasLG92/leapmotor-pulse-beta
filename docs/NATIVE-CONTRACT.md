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

## Compatibilidad de API para la UI nativa

El puente Android/iOS admite además estas rutas de la aplicación compartida. Todas siguen usando el mismo origen HTTPS y la sesión nativa del BFF; cada mutación conserva la comprobación CSRF. Las rutas no amplían OAuth, grants de widgets ni permisos del servidor.

| Método y ruta | Consulta permitida |
|---|---|
| `GET /api/journeys` | `limit` entero 1–100, `offset` entero 0–1.000.000, `search` hasta 100 puntos de código Unicode y `tag` hasta 32; los dos textos rechazan controles. |
| `GET /api/maintenance-plans` | `limit` 1–100, `offset` 0–1.000.000 y `odometer_km` entero 0–2.000.000. |
| `GET /api/navigation/favorites` | Sin parámetros. |
| `GET /api/energy/comparison` | `days=30`, `90` o `365`; también admite la consulta vacía y el valor predeterminado del servidor. |
| `POST /api/maintenance-plans` | Crear un plan. |
| `PUT /api/maintenance-plans/{id}` / `DELETE /api/maintenance-plans/{id}` | Editar o borrar un plan cuyo ID sea exactamente 32 dígitos hexadecimales en minúscula. |
| `POST /api/maintenance-plans/{id}/complete` | Registrar la realización del plan con ese mismo formato de ID. |
| `POST /api/navigation/favorites` / `DELETE /api/navigation/favorites/{id}` | Crear o borrar favoritos; el ID de borrado usa el formato anterior. |
| `POST /api/energy/plan` | Calcular una planificación informativa con el contrato JSON del API. |

Las mutaciones de esta tabla no aceptan query string. Se rechazan claves desconocidas y claves duplicadas incluso si una llega codificada. Los componentes de query se decodifican una sola vez como UTF-8 estricto; los escapes y separadores de ruta se validan antes de consultar el API. El path sigue sin admitir escapes `%`, fragmentos `#`, barras inversas, esquemas u orígenes absolutos ni segmentos con `//`. `/api/journeys/document` permanece fuera de la allowlist nativa: los formatos CSV e imprimible siguen siendo una capacidad de la PWA.

El límite general del cuerpo JSON en los puentes cliente sigue siendo 5 MiB en Android e iOS. El servidor conserva topes menores de 4 KiB para `/api/navigation/*` y `/api/native/*`, además de las validaciones de esquema y campo; `energy/plan` permanece bajo el límite general existente.
