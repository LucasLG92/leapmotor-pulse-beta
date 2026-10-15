# Build iOS

## Cualificación vigente, 15 de octubre de 2026

App/PulseWidget Debug y Release Simulator y Release iPhoneOS pasan en GitHub run38646413599, revisión876c4515. XCTest comprueba arranque semántico y píxeles; captura revisada. IPAgenérica sin firma/configuración:73archivos y49entradas públicas auditados. El primer run38643875351 conserva su timeout y captura; no se atribuye retrospectivamente una causa. Firma, distribución, OAuth/WidgetKit/Keychain/AppGroups reales siguen pendientes. [Recibo y alcance](PHASE2-DELIVERY-20261014.json). Las secciones inferiores son históricas.

El [configurador local original](IOS-LOCAL-PACKAGE.md) prepara esa IPA desde Linux usando solo valores públicos y sin recompilar o firmar. La conversión real cambia únicamente ambas `Info.plist` y conserva los otros 71 archivos; 32 pruebas y 45 subcasos de configuración, packaging y estructura pasan. IPA configurada SHA256 `17425a9dd2d55fccf79d897cd2edac1ce9b907ba9699314e082e9723566d2656`, privada y sin instalar. Cambiar `PulseAppGroup` no crea los entitlements ni concede permisos del perfil.

La [prueba personal desde Linux](IOS-PERSONAL-INSTALL.md) está preparada con revisión acotada del transporte y almacenamiento de iLoader. Acceso Apple, requisitos USB, firma y capacidades siguen sin ejecutar o verificar en el dispositivo.

## Registro anterior, conservado como histórico

El candidato iOS está identificado como versión **0.1.1**, build **2**. La preparación inicial completó `cap sync ios`. Posteriormente, el run `38313672619` del commit publicado `2acb4da019acb34ea7a53f57fe0310bf44408e2f` compiló App y PulseWidget para simulador en Debug y Release. El primer intento falló en `bootstatus` por timeout; no instaló ni arrancó la app y eliminó su simulador temporal. Se solicitó una repetición del job fallido. Esta evidencia corresponde al commit publicado, anterior a esta nueva opción de IPA; firma, distribución e instalación física siguen pendientes.

Para continuar, usa macOS/Xcode y el proyecto generado por Capacitor. Las claves, certificados, perfiles de aprovisionamiento, identificadores de operador y valores de autenticación deben permanecer fuera del repositorio. Una sincronización de Capacitor no prueba que la app o WidgetKit compilen o se ejecuten.

La aceptación del dispositivo, permisos, Keychain, autenticación, notificaciones y widgets sigue pendiente. Consulta [VALIDATION.md](VALIDATION.md) y [DELIVERY-STATUS.json](DELIVERY-STATUS.json).


## IPA genérica de dispositivo: opción de revisión

El workflow manual admite `device_ipa`, booleano desactivado por defecto. Al elegirlo, después del build/arranque de simulador realiza un build Release de App y PulseWidget para `generic/platform=iOS`, con firma desactivada. La carpeta `ci-device` contiene `unsigned.ipa` y un recibo con el commit exacto y hashes. El exportador propio rechaza configuración de conexión, perfiles/claves/firmas, enlaces, productos de simulador, identidades/versiones incorrectas y salidas previas. Se conservan App.app y su extensión bajo Payload. No contiene configuración del operador.

Este incremento está preparado para revisión: las ocho pruebas nuevas usan bundles/plists inventados y no acreditan Xcode, Mach-O o iPhone. El artefacto necesita una vía de firma antes de instalación; App Groups, WidgetKit, Keychain y OAuth necesitan cualificación física. El pipeline de simulador anterior no valida esta nueva opción. No se instala ni añade ios-builder o SideStore; no se crean cuentas ni materiales de firma. Los artefactos genéricos se conservan siete días.

