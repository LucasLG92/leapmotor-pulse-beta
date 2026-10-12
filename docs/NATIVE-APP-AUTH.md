# Sesión de la app nativa

**Estado:** bootstrap BFF, bundle Capacitor compartido y bridges HTTPS Android/Swift implementados. Android compila; la CI Apple compila App/PulseWidget Debug/Release para Simulator y pasa27XCTest. Firma, distribución y ejecución iOS física siguen pendientes. Login real y teléfonos pendientes. Tests backend con issuer/tokens sintéticos; consultar [Android](ANDROID-BUILD.md) e [iOS](IOS-BUILD.md) para distinguir pruebas portables, build y aceptación física.

## Registro OAuth y scopes

La app completa aloja un bundle local de la interfaz web y accede al origen HTTPS de Pulse mediante un puente nativo. Usa el mismo cliente público nativo y la misma audiencia Pulse descritos en el [contrato nativo](NATIVE-CONTRACT.md), con autorización separada por propósito:

- La app solicita exactamente `openid profile pulse.app.access`.
- El enlace de widgets mantiene exactamente `openid pulse.widget.read`.

Pulse rechaza scopes adicionales. Un permiso `pulse.widget.read` no inicia sesión ni autoriza `/api/state` o escrituras. La configuración `PULSE_NATIVE_APP_ENABLED` vale `false` por defecto. Para activarla requiere `PULSE_NATIVE_ENABLED=true`, OIDC HTTPS completo y todos los controles existentes de cliente, audience, introspección y UserInfo.

El cliente nativo es público y no incluye un `client_secret`. Android usa AppAuth y el navegador del sistema para Authorization Code con PKCE S256, `state` de un solo uso y retorno HTTPS registrado, conforme a [RFC 8252](https://www.rfc-editor.org/rfc/rfc8252.html) y [RFC 7636](https://www.rfc-editor.org/rfc/rfc7636.html). Su implementación no acredita el issuer operativo ni la asociación real de App Links.

iOS usa el flujo original ASWebAuthenticationSession (callback HTTPS desde iOS17.4), Security/SecRandom y CryptoKit/SHA256. `state`: un uso, máximo cinco minutos, ligado a generación de sesión. Callback: origen/ruta/puerto configurados y exactamente un code/state, sin error/fragment/credenciales. La codificación form preserva `+` y caracteres reservados. AASA, SDK Apple y retorno real pendientes; el scheme del widget solo abre Pulse.

## Canje por una sesión BFF

`POST /api/native/app-sessions` recibe JSON vacío `{}` y `Authorization: Bearer <access token>`. El cliente nativo omite `Origin`; no falsifica un origen de navegador. El gateway verifica el access token mediante la misma introspección HTTPS y UserInfo protegida que el enlace nativo: issuer, audience, cliente, expiración y scopes exactos, mismo `sub`, timeout total de ocho segundos y respuestas limitadas a 64 KiB. `preferred_username` debe ser una cadena exacta incluida en `PULSE_ALLOWED_USERS`. Un usuario válido del issuer que no figure en esa lista se rechaza.

Al terminar UserInfo, el gateway vuelve a validar las claims y exige que `exp` siga siendo posterior al reloj del servidor. Si el token vence durante esa consulta, devuelve `401` antes de iniciar la sesión; no realiza otra petición al proveedor. Una sesión ya concedida conserva su duración independiente, indicada a continuación.

Una respuesta válida contiene `{ "ok": true, "expires_in": min(1800, session_ttl) }` y una cookie nueva `HttpOnly`, `Secure`, `SameSite=Lax` y `__Host-` en modo OIDC. La sesión dura como máximo 30 minutos y nunca más que el `session_ttl` configurado (ocho horas por defecto, entre 60 y 86.400 segundos). La cookie contiene el identificador opaco; el JSON no expone el identificador ni el token OAuth. Pulse guarda la identidad y el estado de sesión solo en memoria. El token OAuth no se copia a la sesión.

El bootstrap crea una sesión nueva sin adoptar ni revocar la cookie que acompañe la petición. El puente consulta `GET /api/session`, valida y guarda `csrf_token` en memoria nativa; entrega a la UI el marcador no secreto `native-session` y construye internamente `X-Pulse-CSRF` y la cookie en las mutaciones. JavaScript solo aporta método, ruta local y JSON; no puede suministrar origen, headers, cookies o destinos arbitrarios. El middleware conserva sesión, CSRF, `Origin`, tamaño de cuerpo y `Cache-Control: no-store`.

La sesión BFF no se renueva silenciosamente. Al caducar, el cliente debe repetir un login interactivo; el backend no guarda ni renueva access, refresh o ID tokens. El endpoint no realiza consultas al vehículo ni habilita comandos fuera de la autorización normal de Pulse.

El bridge limita rutas/métodos, tiempos y cuerpos y no sigue redirecciones. Descarta lecturas de una sesión sustituida; una escritura cuya respuesta no pueda entregarse por caducidad/cambio de sesión, o cuyo transporte falle, informa que puede haberse guardado y pide comprobar antes de repetir. No cancela ni reintenta escrituras automáticamente. SSE usa leases y genera error cuando su sesión vence; la UI recupera el sondeo de 60 segundos.

## Evidencia y pendientes

`tests/test_native_app.py` cubre scopes separadas, rechazo de refresh, widget, scopes extra, usuario no autorizado y mismatch de subject; cookie segura, sesión independiente, lectura de CSRF y mutación; caducidad, límite de sesiones, configuración cerrada y payload estricto. Los tests JVM ejercitan políticas del cliente; los tests Node ejercitan leases y errores del bridge. La QA de UI usa un bridge sintético. Ninguna de esas pruebas verifica Authelia real, App Links, Android Keystore ni el login en un teléfono.
