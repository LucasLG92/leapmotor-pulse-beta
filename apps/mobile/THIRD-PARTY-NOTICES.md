# Dependencias de la app nativa

El código original de Pulse se publica bajo MIT. Los assets, fuentes y componentes compartidos se atribuyen en `../web/THIRD-PARTY-NOTICES.md`.

- Capacitor core, CLI, Android e iOS 8.5.3: MIT, Ionic Team; https://github.com/ionic-team/capacitor. Los targets iniciales Android y Xcode proceden de sus plantillas, después adaptadas para Pulse. Texto completo en `LICENSES/Capacitor-MIT.txt`; `CapApp-SPM` fija exactamente 8.5.3. Bridge/cache, PulseCore y renderer WidgetKit nuevos son originales.
- AppAuth Android 0.11.1: Apache-2.0, OpenID Foundation; https://github.com/openid/AppAuth-Android. Dependencia de autenticación en navegador del sistema, sin código vendorizado.
- AndroidX Glance 1.2.0 y bibliotecas AndroidX transitivas: Apache-2.0, Android Open Source Project; https://developer.android.com/jetpack/androidx/releases/glance.
- AndroidX WorkManager 2.12.0: Apache-2.0, Android Open Source Project; https://developer.android.com/jetpack/androidx/releases/work. Actualización de caché bajo los límites de ejecución de Android, sin analítica ni servicios permanentes.
- Kotlin/Compose Compiler 2.2.20: Apache-2.0, JetBrains; https://github.com/JetBrains/kotlin.
- Gradle Wrapper 8.14.3: Apache-2.0, Gradle; https://github.com/gradle/gradle. Scripts y jar de bootstrap necesarios para reproducir el build; distribución descargada contrastada con SHA-256 oficial.
- Desugar JDK libraries 2.1.5: Apache-2.0 con componentes OpenJDK bajo GPLv2 y Classpath Exception; Android Open Source Project. Permite `java.time` en el mínimo Android24; https://github.com/google/desugar_jdk_libs.

Temurin JDK21 y el Android SDK usados para compilar son herramientas separadas, no se incluyen en el código ni el APK como runtimes. El JDK se contrastó con el checksum de la publicación oficial; las herramientas SDK se instalaron en un directorio aislado usando hashes de licencias ya aceptadas por el usuario. No se aceptaron condiciones nuevas ni se modificó su SDK instalado.

La distribución debe conservar los avisos/licencias de las dependencias empaquetadas y las condiciones del SDK; el debug APK no es una publicación ni una firma de distribución.

SwiftUI, WidgetKit, AuthenticationServices, CryptoKit, Security y BackgroundTasks son frameworks del SDK Apple, no fuentes vendorizadas. Xcode/SDK, firma y distribución conservan las condiciones de Apple y no están incluidos. El toolchain Swift Linux solo verifica el paquete portable; no produce una app iOS.

- Leaflet 1.9.4: BSD-2-Clause, Vladimir Agafonkin y CloudMade; https://leafletjs.com/. Código de interacción del mapa compartido con la web, bajo demanda. Texto íntegro en `LICENSES/Leaflet-BSD-2-Clause.txt` y en avisos web empaquetados. Datos OSM con atribución visible separada.
