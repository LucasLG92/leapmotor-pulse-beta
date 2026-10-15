# Prueba personal de Pulse en iPhone desde Linux

Este procedimiento está preparado, sin ejecutar. El 15 de octubre Lucas autorizó la prueba local de firma e instalación, pero después pidió aplazarla porque no tenía el iPhone disponible. Se conserva esa autorización; falta disponer del teléfono, cualificar el transporte USB y comprobar el almacenamiento protegido antes de introducir la cuenta. La fase 2 sigue abierta. App y PulseWidget de la interfaz actual compilan en GitHub y la IPA está configurada localmente, pero aún falta comprobar firma, perfiles, instalación y funcionamiento físico. El alcance y los hashes están en [PHASE2-DELIVERY-20261014.json](PHASE2-DELIVERY-20261014.json).

## Vía candidata y requisitos

La primera prueba propuesta usa [iLoader 2.3.6](https://github.com/nab138/iloader/releases/tag/v2.3.6) por USB para firmar e instalar la IPA privada de Pulse. La revisión se fija al commit `dc396af059fd46982062cd8fc90252d7e27085e9`; no equivale a una auditoría del binario distribuido. El instalador es una herramienta externa y su código no se incorpora a Pulse.

Se necesitan iPhone con iOS 17.4 o posterior, cable USB de datos, servicio usbmuxd operativo en Linux y una cuenta Apple que el usuario introduzca directamente en el instalador. La firma personal gratuita documentada por [Apple](https://developer.apple.com/help/account/basics/about-your-developer-account) caduca a los siete días y limita dispositivos y aplicaciones. No garantiza todas las capacidades de Pulse. Para App Store/TestFlight siguen aplicándose sus requisitos de distribución.

[SideStore documenta Linux](https://docs.sidestore.io/docs/installation/prerequisites) para instalación inicial y renovación desde el teléfono, con una VPN local adicional. Esa instalación es una decisión separada; no se ha realizado. La prueba inicial debe conservar la app y su widget.

## Cuenta y datos de firma

El usuario introduce cuenta, contraseña y 2FA directamente en la herramienta local. No se envían por el chat ni se convierten en argumentos de terminal, configuración de Pulse o secretos de GitHub. [El flujo de acceso de iLoader](https://github.com/nab138/iloader/blob/dc396af059fd46982062cd8fc90252d7e27085e9/src-tauri/src/account.rs#L28-L66) permite desactivar el guardado de la contraseña. Eso no elimina el almacenamiento de sesiones, certificados o estado necesarios para firmar.

La revisión de `isideload 0.4.3`, dependencia exacta del lock de iLoader, contrastó la descarga del registro con SHA256 `46de455a50785bb772f4a1e586420a12861a07f4607fe5b0a5b2507da6a5c4bd`. En el [proveedor Anisette revisado](https://github.com/nab138/isideload/blob/c23db688c7f6079413bf82dad4e4f19e5ddf4ce7/isideload/src/anisette/remote_v3/mod.rs#L109-L159), las consultas envían identificador y datos de provisión; no reciben la contraseña ni el correo de la cuenta. El acceso Apple sigue una ruta separada. El servicio externo recibe además tráfico y metadatos de conexión. Esta observación de código no garantiza todo el comportamiento del instalador o sus dependencias en ejecución.

Hay un límite material: [iLoader cambia a almacenamiento de archivos si el keyring falla](https://github.com/nab138/iloader/blob/dc396af059fd46982062cd8fc90252d7e27085e9/src-tauri/src/secure_storage.rs#L40-L56). Por tanto, antes de introducir la cuenta debe comprobarse su almacenamiento protegido. La existencia del servicio Secret Service no demuestra que funcione esa ruta. Si ofrece desactivar el keyring o usar el fallback sin protección, se detiene la prueba.

También [genera logs de nivel DEBUG](https://github.com/nab138/iloader/blob/dc396af059fd46982062cd8fc90252d7e27085e9/src-tauri/src/lib.rs#L42-L65). Pueden incluir datos de provisión, números o detalles de cuenta: no se publican ni se comparten los logs completos, archivos de emparejamiento, sesiones, certificados o perfiles. La evidencia de Pulse conservará únicamente resultados sanitizados de las comprobaciones.

## Decisiones antes de ejecutar

1. Confirmar modelo/versión iOS y disponibilidad USB, sin pedir identificadores del teléfono.
2. Autorizar instalación local de la versión revisada y sus requisitos, acceso a Apple y el proveedor Anisette escogido. No se han contratado servicios ni creado cuentas.
3. El usuario realiza confianza USB, Developer Mode y cualquier acuerdo de Apple directamente. No revocar certificados o identificadores existentes para resolver cuotas ni aceptar pagos sin una decisión específica.
4. Comprobar el almacenamiento protegido del instalador y la integridad del paquete descargado antes del acceso. Una firma o un checksum por sí solos no prueban que el programa sea seguro.
5. Verificar perfiles y entitlements de app y widget antes de aceptar la instalación: identidades, grupo compartido, Keychain y retorno OAuth. Si falta una capacidad, conservarla como pendiente.

## Grupo compartido y aceptación

El [configurador local](IOS-LOCAL-PACKAGE.md) cambia `PulseAppGroup` en ambas Info.plist. No concede `com.apple.security.application-groups`, no crea perfiles y no adapta automáticamente una identidad de firma. Debe coincidir con el grupo autorizado para ambos bundles después de firmar. SideStore no remapea por sí mismo esa clave propia de Pulse. No se elimina el widget para declarar terminada la fase.

La prueba física debe acreditar instalación/actualización, preferencias, acceso y regreso OAuth, enlace y ambos tamaños del widget, texto grande y VoiceOver, privacidad con bloqueo, retirada de consentimiento, caducidad de la copia local, desconexión/reintento y renovación de firma. Debe registrar versiones y resultados sin datos de cuenta. Una pantalla sin lecturas o con datos de demostración no valida la telemetría del B10. No avanzar a fase 3 mientras falte la aceptación aplicable de la fase 2.
