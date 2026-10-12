# Build iOS

El candidato iOS está identificado como versión **0.1.1**, build **2**. La preparación inicial completó `cap sync ios`. Posteriormente, el run `38313672619` del commit publicado `2acb4da019acb34ea7a53f57fe0310bf44408e2f` compiló App y PulseWidget para simulador en Debug y Release. El primer intento falló en `bootstatus` por timeout; no instaló ni arrancó la app y eliminó su simulador temporal. Se solicitó una repetición del job fallido. Esta evidencia corresponde al commit publicado, anterior a esta nueva opción de IPA; firma, distribución e instalación física siguen pendientes.

Para continuar, usa macOS/Xcode y el proyecto generado por Capacitor. Las claves, certificados, perfiles de aprovisionamiento, identificadores de operador y valores de autenticación deben permanecer fuera del repositorio. Una sincronización de Capacitor no prueba que la app o WidgetKit compilen o se ejecuten.

La aceptación del dispositivo, permisos, Keychain, autenticación, notificaciones y widgets sigue pendiente. Consulta [VALIDATION.md](VALIDATION.md) y [DELIVERY-STATUS.json](DELIVERY-STATUS.json).


## IPA genérica de dispositivo: opción de revisión

El workflow manual admite `device_ipa`, booleano desactivado por defecto. Al elegirlo, después del build/arranque de simulador realiza un build Release de App y PulseWidget para `generic/platform=iOS`, con firma desactivada. La carpeta `ci-device` contiene `unsigned.ipa` y un recibo con el commit exacto y hashes. El exportador propio rechaza configuración de conexión, perfiles/claves/firmas, enlaces, productos de simulador, identidades/versiones incorrectas y salidas previas. Se conservan App.app y su extensión bajo Payload. No contiene configuración del operador.

Este incremento está preparado para revisión: las ocho pruebas nuevas usan bundles/plists inventados y no acreditan Xcode, Mach-O o iPhone. El artefacto necesita una vía de firma antes de instalación; App Groups, WidgetKit, Keychain y OAuth necesitan cualificación física. El pipeline de simulador anterior no valida esta nueva opción. No se instala ni añade ios-builder o SideStore; no se crean cuentas ni materiales de firma. Los artefactos genéricos se conservan siete días.
