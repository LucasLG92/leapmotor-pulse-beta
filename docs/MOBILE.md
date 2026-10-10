# Aplicación móvil

El proyecto incluye clientes para Android e iOS construidos con Capacitor. Comparten la interfaz web y usan un puente nativo para sesión, peticiones autenticadas, eventos y widgets. El código Android integra un widget de inicio; iOS tiene una extensión WidgetKit. El widget presenta una selección limitada de campos y requiere consentimiento y emparejamiento con un servidor compatible.

El cliente móvil está implementado en el repositorio, pero la instalación sencilla de esta beta no configura sus credenciales ni expone la API a la red. El modo local solo escucha en loopback. El acceso desde un teléfono requiere un despliegue remoto con HTTPS y OIDC y la configuración manual de los clientes nativos.

El workflow `verify-ios` construye la app y el widget para el simulador; eso no prueba un iPhone físico. Las builds Android tampoco demuestran que un modelo de teléfono, su widget, permisos y servidor real funcionen juntos. Revisa las ejecuciones de CI del repositorio público y prueba cada dispositivo por separado antes de considerar esa compatibilidad validada. No publiques ni uses APKs configurados para una instalación particular.

El workflow también instala y arranca una build genérica sin configuración en un iPhone 17 Pro Max con iOS 26.5 Simulator. Comprueba el proceso durante cinco segundos, retira únicamente el simulador que ha creado y conserva una captura inicial y un recibo durante siete días. La captura necesita revisión visual. Ese ensayo no autentica un usuario, no ejecuta el widget y no acredita firma, distribución, consumo o comportamiento en teléfonos físicos. No acepta configuración particular del operador ni publica apps compiladas.
## Comprobación de la interfaz actual

La [ejecución pública38050807578](https://github.com/LucasLG92/leapmotor-pulse-beta/actions/runs/38050807578), sobre `bedfa9b8304a486f137b1d26264972cc004d4fc0`, pasó el10 de octubre de2026 en9m9s. Construye la app y PulseWidget en Debug/Release para iOS Simulator con la interfaz de navegación actual y ejecuta el ensayo automático de arranque genérico. El PNG y el recibo se descargaron y su SHA256 coincidió con el digest publicado. La captura inicial revisada muestra la pantalla de acceso sin configuración y el botón deshabilitado; el recibo confirma instalación/lanzamiento, proceso vivo durante cinco segundos y retirada del simulador propio. Esa pantalla no acredita OAuth ni renderizado del widget. El artefacto de CI conserva el plazo de siete días del workflow.

El nuevo empaquetado Android de la misma interfaz pasó tipado, siete contratos móviles y compilación offline con Node24.19.0, pnpm11.25.0, JDK21 y SDK36. Se contrastaron los35archivos del bundle dentro del APK y la conservación de las fuentes nativas y los locks. Es evidencia de compilación de un piloto privado, sin publicación de su configuración o APK. Descargar, actualizar, iniciar sesión y utilizar widgets en Android16 o iPhone requieren comprobaciones físicas independientes; ambas plataformas continúan en beta.

## Widget Android: renderizado y cambio de tema comprobados

El renderer conserva colores día/noche para seguir el tema del host desde Android 12 (API 31), mantiene los modos explícitos claro/oscuro y elimina una etiqueta que repetía la marca. La fila de cabecera utiliza el ancho disponible. Caché, consentimiento, permisos, acciones y OAuth no cambian; no hay dependencia nueva.

Comprobación realizada en Android 14 (API 34), AVD nueva sin cuentas y sin iniciar sesión: AppWidgetHost real, 180×100 y 280×170 dp, claro/oscuro y transición sobre el mismo widget. Se midieron dimensiones/colores, texto visible completo sin elipsis y contraste de paleta (mínimo 5,70:1 en claro y 9,42:1 en oscuro); capturas revisadas y emulador propio detenido. Glance 1.2 compila offline. En Android 7–11 (API 24–30), Glance selecciona un color al regenerar el widget: la transición inmediata no queda acreditada.

**Pendiente de comprobación:** Android 16/LineageOS, launcher real, caché activa y otros estados, Keystore, opt-in/TTL/revocación/bloqueo, actualización en background, OAuth, TalkBack y consumo. Continúa beta y esta evidencia no acredita uso físico ni coche real. Las APK/configuraciones particulares, credenciales, copias y datos del operador quedan fuera de este repositorio público.
