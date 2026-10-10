# Destinos, mapa y cargadores públicos

Navegación permite buscar un lugar público al pulsar Buscar, elegir un destino, abrir su mapa bajo demanda y consultar cargadores a cinco kilómetros del centro elegido. Las listas funcionan sin abrir el mapa. Apple Maps y Google Maps reciben únicamente las coordenadas del destino al abrir su enlace; no se les envía un origen, identidad ni nombre privado. El mapa no recibe la posición del coche o del dispositivo. Los puntos proceden de datos cartográficos: no acreditan ocupación, disponibilidad, potencia efectiva ni una ruta por carretera.

## Configuración explícita

Por defecto los tres servicios están apagados y no generan tráfico ni caché de imágenes. Las coordenadas públicas introducidas manualmente siguen permitiendo preparar enlaces externos. Activar en el entorno del servidor, después de elegir un contacto público real:

```text
PULSE_NAVIGATION_ENABLED=true
PULSE_NAVIGATION_CONTACT=<URL HTTPS de contacto o email público del operador>
```

El contacto identifica al servicio en su User-Agent y debe ser ASCII, de hasta 512 caracteres. No usar datos inventados ni credenciales. Los destinos HTTPS compatibles pueden cambiarse mediante `PULSE_NAVIGATION_TILES_ORIGIN`, `PULSE_NAVIGATION_PLACES_ORIGIN` y `PULSE_NAVIGATION_CHARGERS_ORIGIN`. Son orígenes sin credenciales, rutas, consultas o fragmentos; el usuario no puede introducir una URL arbitraria en la API. No hay claves de proveedor, redirecciones ni proxies tomados del entorno. Cambiar estos servicios requiere verificar su contrato y condiciones. El proveedor público no ofrece un SLA; para una instalación con varios usuarios debe elegirse capacidad propia o un servicio apropiado.

## Consultas y límites

La UI lee el estado local al entrar. Buscar, Mostrar mapa y Buscar cargadores son acciones explícitas. No hay autocompletado, seguimiento de ubicación, consultas de cargadores al arrastrar el mapa, descarga de mapas offline o precarga de zonas. Leaflet 1.9.4 y su CSS se importan al abrir el mapa. El service worker conserva el código genérico, nunca consultas, coordenadas o imágenes cartográficas.

La API usa sesión y CSRF también en sus POST de lectura. Sus cuerpos están limitados a 4096 bytes y las respuestas son `no-store`, sin compresión ni errores que reflejen consultas. Las búsquedas públicas y estaciones tienen caché de RAM acotada: 64 búsquedas/30 minutos y 32 áreas/una hora. Se borran al cerrar el servicio, no se exportan ni guardan como historial de usuario. La vista descarta respuestas tardías y borra sus datos al salir, desconectar o caducar la sesión.

- Nominatim: máximo cinco resultados, una petición por segundo para toda la instancia, consulta explícita y cuerpo de respuesta máximo de 256 KiB. No enviar información personal o confidencial.
- Overpass: consulta fija de estaciones a 5 km, máximo 60 resultados, una petición cada diez segundos para toda la instancia y respuesta máxima de 512 KiB. Distancia en línea recta. No interpretar la ausencia de estaciones como prueba de ausencia real.
- Imágenes OSM: zoom 0–18, PNG de 256 × 256 y máximo de 128 KiB. Dos solicitudes activas como máximo. User-Agent contactable y Referer web del mismo origen, sin ruta o consulta privada. Caché regenerable en `map-cache/navigation-tiles` junto a la base: directorio 0700, archivos 0600, nombres hash y escritura atómica sin seguir enlaces. Cuota 64 MiB/1024 archivos; no elimina imágenes frescas para esquivar su plazo de caché. Respeta política HTTP y revalida condicionalmente; siete días si no se dispone de política. No entra en las copias de SQLite/JSON.

El código original mantiene su licencia propia. Leaflet utiliza BSD-2-Clause, con aviso completo en la distribución. Los datos OSM conservan su atribución y condiciones independientes: [OpenStreetMap copyright](https://www.openstreetmap.org/copyright).

La cola del servidor admite dos solicitudes de imagen activas y dos pendientes; el plazo de timeout abarca la espera y la descarga. Una respuesta 304 que solo trae un validador conserva la política de caché anterior. La cola de la vista comprueba que la imagen todavía pertenece al área visible antes de iniciarla. Un ajuste del centro de hasta diez metros debido a redondeo de píxeles o resize conserva los cargadores; un desplazamiento mayor invalida la consulta, que solo se repite por acción del usuario.

## Verificación V16

Suite completa de servidor: 589 pruebas, dos omisiones por SDK opcional; contrato HTTP/tipos, Svelte sin errores ni avisos y builds web/móvil pasan. Los últimos cambios de UI se comprobaron además con diez pruebas Node. Android: 72 pruebas JVM sin fallos y APK debug compilado y firmado; 35 recursos del build móvil coinciden con APK e iOS. iOS: 24 XCTest del núcleo portable en Linux y análisis de sintaxis del puente; no compilación con SDK Apple.

QA con transporte sintético original, sin peticiones a los proveedores: selección de destino/cargador, enlace por coordenadas inspeccionado sin abrirlo, teclado Enter, fallo/reintento de búsqueda, salida con petición pendiente, borrado offline y actualización explícita. Se comprueba reflujo a 320/440/1280 px y texto de 32 px a 320/440; los tres marcadores tienen objetivos de 44 × 44 px. Las capturas cartográficas de QA son una cuadrícula inventada, no datos geográficos. Abrir el mapa mantiene la lista de cargadores tras ajuste del centro. La revisión independiente de código cerró los riesgos encontrados dentro de su alcance; no sustituye la QA física.

## Fuentes y límites de verificación

[Política de imágenes OSM](https://operations.osmfoundation.org/policies/tiles/), [política de Nominatim](https://operations.osmfoundation.org/policies/nominatim/), [servicio público Overpass](https://dev.overpass-api.de/overpass-doc/en/preface/commons.html), [Leaflet](https://leafletjs.com/reference.html), [Google Maps URLs](https://developers.google.com/maps/documentation/urls/get-started) y [Apple Map Links](https://developer.apple.com/library/archive/featuredarticles/iPhoneURLScheme_Reference/MapLinks/MapLinks.html).

Los enlaces de navegación externa y el mapa público no constituyen navegación del vehículo ni integración ABRP. La telemetría ABRP sigue pendiente de consentimiento, contrato y cualificación independiente. Tampoco hay API de rutas, autonomía estimada en un trayecto ni autorización de controles físicos. Las pruebas sintéticas no acreditan servicios públicos, OAuth, Safari, instalación PWA o dispositivos reales.

## Coordenadas y accesibilidad

Los campos de latitud y longitud admiten punto o coma decimal: `40.4168` y `40,4168` representan el mismo valor. Cada campo contiene una sola coordenada; no admite separadores mezclados, exponentes ni texto. Los límites siguen siendo −90 a 90 para latitud y −180 a 180 para longitud.

Al enviar valores inválidos, Pulse identifica el error bajo cada campo, lo vincula a su etiqueta accesible y enfoca el primer campo que necesita corrección. Al editarlo desaparece su error; los errores del otro campo se conservan hasta corregirlo. En pantallas estrechas los campos se apilan para mantener legibles las ayudas y los avisos.

El parser, el formulario con teclado y los enlaces de mapa se han comprobado con datos públicos sintéticos en navegador. La comprobación del anuncio con VoiceOver y TalkBack en teléfonos reales sigue pendiente.
