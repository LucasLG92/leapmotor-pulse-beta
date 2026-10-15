# Fase 2: entrega actual y aceptación móvil

La fase1 está aceptada por Lucas en iPhone y Android: visual/texto/teclado, lectores en seis pantallas, errores/foco de favoritos, borrador tras recuperación de red y recepción JSON. Continúa únicamente fase2; no se empieza fase3 hasta completarla. [Registro vigente](PHASE2-DELIVERY-20261014.json).

## Android entregado y probado por Lucas

Piloto0.1.1/código2, com.lucas.pulse, con el mismo certificado anterior.47archivos del bundle y2stubs idénticos dentro del APK;9pruebas del puente,104JUnit yLint0errores17avisos. La compilación offline carecía de tres dependencias de lint; se resolvieron desde los repositorios fijados sin actualizar locks. Firma debug piloto; no firma de distribución en tienda.

La descarga protegida del servidor fue sustituida atómicamente después de verificar el APK y la copia anterior. HTTPS estricto conserva401/no-store para anónimos. Configuración, servicios y datos se conservan. La copia previa permite recuperar la descarga del servidor; Android no ofrece un downgrade ordinario de código2a1 y no se desinstala ni borra la aplicación para simular recuperación.

Lucas confirma actualización/preferencias y retorno OAuth; widget enlazado en launcher con tamaños/temas/texto grande/TalkBack y apertura de Pulse. También confirma pérdida/recuperación de red con Reintentar, logout/relogin y retirar copia/desvincular tras reabrir/refrescar. Son comprobaciones humanas, separadas de los tests. No incluyen esperar dos horas de caché, expiración de sesión ni refresco bloqueado/autónomo.

## iOS compilado; distribución pendiente

Run38646413599 sobre876c4515 compila App/PulseWidget Debug y Release Simulator y Release iPhoneOS; XCTest verifica arranque semántico y píxeles y la PNG se ha revisado. IPAarm64 genérica:73archivos y49entradas públicas auditados, hash31cbe14937738616844c4036760717fb677ba83ba42030f4ba6c63d633d7d88c. Sin firma o configuración del operador, no se instala en el iPhone. La consulta de avisos conocidos por revisión SPM no encontró coincidencias; no es una auditoría completa de seguridad.

El primer run38643875351 generó los productos, pero la prueba de arranque agotó su plazo. Se conservan recibo, IPA y PNG de diagnóstico en el laboratorio privado. El éxito del segundo ensayo en un runner nuevo no demuestra retrospectivamente su causa; no se cambió el producto ni se amplió el plazo.

Se mantiene pendiente una vía autorizada de firma/distribución compatible con App Groups y Keychain y la instalación/OAuth/WidgetKit/privacidad/recuperación física. La compilación en GitHub cubre Xcode sin Mac local; no elimina el provisionamiento. Apple permite pruebas personales gratuitas con límites, por lo que no se presupone que pagar sea obligatorio para cualquier prueba. [Requisitos y límites de Apple](https://developer.apple.com/help/account/basics/about-your-developer-account).

## Preparación local del IPA desde Linux

El configurador original [configure-ios-ipa.py](../scripts/configure-ios-ipa.py) admite únicamente la IPA genérica auditada por su hash y seis valores públicos. Deriva el callback, comprueba URLs y ambos `PulseAppGroup`, rechaza material de firma y ZIP inseguros y crea una salida nueva con permisos privados. La revisión independiente corrigió el límite del callback y los caracteres invisibles; pasan 7 pruebas nuevas, además de las 25 de empaquetado/proyecto, con 45 subcasos en conjunto.

La conversión real se contrastó archivo por archivo: de 73 entradas cambian únicamente las dos `Info.plist`; los otros 71 contenidos y los modos, fechas y métodos de compresión se conservan. SHA256 resultante `17425a9dd2d55fccf79d897cd2edac1ce9b907ba9699314e082e9723566d2656`. El IPA y su recibo permanecen en el laboratorio privado, sin firma ni instalación. La configuración no concede entitlements o capacidades: falta comprobar perfiles y el grupo compartido al firmar. [Uso y límites](IOS-LOCAL-PACKAGE.md).

[SideStore documenta la instalación inicial desde Linux](https://docs.sidestore.io/docs/installation/prerequisites) con iLoader, cuenta Apple, USB y VPN local. Es una vía candidata para probar; no se ha instalado ni introducido una cuenta. La revisión de su código no demuestra que remapee automáticamente la clave propia `PulseAppGroup`, ni que el perfil personal conceda todas las capacidades de Pulse. Cuenta, Anisette, firma y prueba física requieren una decisión informada antes de ejecutarse.

La [guía de prueba personal](IOS-PERSONAL-INSTALL.md) incorpora la revisión del transporte de la dependencia exacta de iLoader: Anisette, almacenamiento protegido con fallback a archivos y logs DEBUG. Se prepara una prueba por USB condicionada a cuenta introducida directamente por el usuario, protección del almacenamiento y capacidades verificadas. No se ha iniciado el instalador ni accedido a Apple.

## Pendientes y protección de información

Caducidad real de sesión/copia, refresco autónomo/bloqueado y aceptación restante en Android; firma/configuración/distribución y pruebas nativas iOS; renovación de avisos PWA y sesión/push después de reiniciar la imagen. Rendimiento/consumo habitual mantienen su puerta global. Sin coche, ausencia de lectura o demostración no valida telemetría.

Las fuentes revisadas se publican por separado de los paquetes/configuración del operador. No se publican claves, tokens, datos privados ni el APK configurado en la beta. Photon permanece preparado sin instalar por elección de Lucas. La fase2 sigue abierta.

## Seguimiento web15oct

La [corrección Manual en Energía](PHASE1-ENERGY-MANUAL-20261015.md) está entregada únicamente en web/PWA; su fuente está incluida en esta revisión. El APK y los IPA descritos arriba conservan su evidencia histórica y no contienen esta nueva regla CSS. Su reconstrucción/aceptación deberá registrarse por separado; la corrección concreta en Safari físico aún no está confirmada.

La prueba de firma e instalación con iLoader fue autorizada por Lucas y después aplazada por no disponer del teléfono. El instalador y la IPA se han verificado de nuevo, pero no se han ejecutado ni accedido a Apple. Antes de la cuenta siguen pendientes transporte USB y almacenamiento protegido.
