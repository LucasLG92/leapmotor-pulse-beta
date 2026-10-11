# Instalación y actualización de la PWA

La web usa el mismo origen que la API. Fuera de localhost necesita HTTPS para instalarse. El service worker conserva únicamente el shell y los assets genéricos; no guarda respuestas API, exportaciones, login, identidad ni telemetría privada. Una apertura offline no proporciona los datos privados del usuario. La app Capacitor empaqueta su UI y no registra este service worker.

Las seis ilustraciones públicas orientativas del B10 se sirven bajo demanda. Sólo se conserva una imagen al utilizarla; el worker acepta las URLs exactas de esta build, GET de mismo origen y respuestas 200 WebP sin redirección. Omite credenciales y elimina URLs de ilustraciones obsoletas al activar otra versión. No guarda el color del perfil ni sus ajustes en Cache Storage. Si la ilustración cargó antes de que el worker controlase la página, el componente pide conservar ese activo público al terminar la instalación. La apertura completa sin conexión sigue mostrando el aviso genérico; la disponibilidad del archivo de imagen no implica acceso offline al perfil privado. Capacitor omite este mecanismo porque empaqueta las imágenes.

## Prueba física del iPhone, 10 de octubre de 2026

Lucas confirma desde su iPhone 17 Pro Max, sobre el dominio de producción: acceso e inicio de sesión por datos móviles con el Wi-Fi apagado; PWA añadida a la pantalla de inicio que abre a pantalla completa y permite entrar; aviso sin conexión tras activar modo avión, cerrar completamente y reabrir desde el icono, y recuperación de la pantalla al restablecer datos; tras habilitar Web Push, permiso concedido y recepción real de una notificación de prueba.

Son observaciones realizadas por Lucas, no una inspección automatizada del teléfono. Acreditan esos flujos y recepción Web Push; no una auditoría de caché privada, VoiceOver, widgets ni Android. Después de la actualización autorizada de Pulse a prioridad high y de recibir instrucciones para reactivar los avisos y enviar desde otro dispositivo con el iPhone ya bloqueado, Lucas confirma recepción sin desbloquear. La comprobación pública verifica los assets nuevos y Ajustes autenticados; no demuestra por sí sola el relevo del service worker instalado en el teléfono. La actualización física de la PWA conserva esa comprobación pendiente. Los límites de las pruebas históricas siguientes conservan su fecha y alcance.

## Prueba física de Android, 10 de octubre de 2026

Lucas confirma en su Android16 con LineageOS: instalación y entrada desde el icono; aviso offline al activar modo avión con Wi-Fi apagado, cerrar completamente y reabrir Pulse, y recuperación al volver a conectar; recepción de una prueba Web Push con el Android ya bloqueado, enviada desde otro dispositivo y con la instrucción de esperar al menos60s entre pruebas.

Evidencia humana conservada en `../pulse-native-activation/android-pwa-acceptance.json`. El navegador, launcher y modelo no se han identificado. Actualización del worker, TalkBack y consumo bajo restricciones de batería siguen pendientes. Los widgets Glance requieren instalar la app Android y validar su autenticación y asociación HTTPS; estas comprobaciones de la PWA no acreditan el widget ni el APK.

## Navegación y transferencia V16

El shell reconoce las seis rutas, incluida `/navigation`. El precache conserva el código genérico de Leaflet y sus estilos; las consultas y las imágenes del mapa no se cachean en el navegador. El servidor puede conservar imágenes públicas en su caché independiente y acotada, descrita en [Navegación](NAVIGATION.md). Los 25 recursos completos incluido el worker transfieren 184904 bytes con gzip en loopback frente a 568784 identity; [medición](PERFORMANCE.md).

La QA aislada en Chromium confirmó relevo explícito del documento con «Actualizar ahora», búsqueda y mapa, y borrado de destinos/cargadores al perder conexión. La herramienta tardó 2598,36 segundos en la emulación offline pese al límite solicitado; no es una medida de Pulse. La conexión se restauró y se retiró la pestaña de pruebas. Esta comprobación no acredita Safari, instalación física, ubicación real, proveedor público o una nueva prueba multitab.

## Transferencia V14

Las URL y el worker de V13 se conservan. El servidor entrega representaciones gzip de los activos genéricos, negociadas por el navegador; el precache no añade URL `.gz`. El conjunto único de 21 recursos incluido el worker transfiere 121860 bytes de cuerpo en el preview con gzip, frente a 359221 identity. [Política de activos](STATIC-ASSETS.md) y [medición](PERFORMANCE.md).

QA en Chromium y origen aislado: al detener el servidor, las cinco rutas abren el aviso genérico sin datos del vehículo. Al reiniciarlo, «Reintentar conexión» recupera la pantalla. Dos variantes del mismo build con marcadores genéricos A/B comprueban «Actualizar ahora»: el documento pasa de A a B con SSE abierta antes de la acción. La observación de red confirma activos gzip, API sin compresión ni disco y SSE fuera del worker. No se enumeró de nuevo toda CacheStorage porque esa capacidad no está disponible en el navegador de pruebas; la evidencia V8 de caché conserva su alcance. No acredita instalación ni ciclo de vida en Safari o móviles físicos.

## Actualizar ahora

El aviso aparece también antes de iniciar sesión y cuando falla la conexión. Actualizar el shell público no requiere autenticarse. Durante la conexión inicial el aviso puede mostrarse, pero espera a que termine esa consulta; tampoco permite iniciar acceso o reintento mientras aplica la actualización. La sesión, API y datos personales siguen protegidos y fuera de la caché.

El arranque tiene un plazo común de15s para consultar sesión y estado mientras JavaScript está activo. Si la conexión no termina, muestra un aviso con «Reintentar»; no inicia otro arranque concurrente ni acepta respuestas tardías del intento cancelado. En web cancela las lecturas mediante AbortSignal, incluido un cuerpo JSON pendiente. En Capacitor limita la espera de la interfaz y descarta resultados tardíos; el puente actual no permite asegurar la cancelación física del transporte. La suspensión del navegador puede retrasar el temporizador. Este plazo no reintenta ni cancela automáticamente escrituras o controles del vehículo.

Una versión nueva permanece en espera hasta que el usuario pulsa «Actualizar ahora». Durante una escritura, cotización PVPC, lectura/importación de una copia u operación nativa pendiente, el botón indica que esa acción debe terminar primero. La lectura del archivo y su confirmación forman parte de la operación; el selector no permite iniciar otra lectura concurrente.

Al aplicar una actualización, Pulse cierra la suscripción SSE, bloquea su reapertura por visibilidad y solicita `skipWaiting`. Al cambiar el controlador, recarga la página. La otra pestaña abierta conserva su documento hasta que su usuario aplica la actualización o recarga; su botón puede recargar hacia la versión que ya está activa.

Si el controlador no cambia en ocho segundos o falla la solicitud, Pulse vuelve a abrir la suscripción, recupera las lecturas pendientes y permite reintentar. Un formulario de carga ya tocado por el usuario conserva método, fechas, kWh y cotización durante esa recuperación y ante nuevos eventos. Cancelar la edición adopta de nuevo los ajustes actuales.

## Causa corregida y primer relevo

En la app V7 compilada, una respuesta EventSource interceptada por el worker activo permanecía abierta. La versión en espera recibía la solicitud de actualización, pero solo avanzaba tras cerrar esa conexión. V8 cierra SSE antes de actualizar y excluye exactamente `/api/events` de la ruta NetworkOnly. Los demás GET de API siguen siendo NetworkOnly, sin caché privada.

El [borrador de la especificación Service Workers](https://w3c.github.io/ServiceWorker/#activation-algorithm) condiciona la activación a que el worker anterior no tenga eventos pendientes; `skipWaiting` no elimina ese requisito. La reproducción observada no atribuye el fallo al navegador.

Para el **primer relevo desde V7**, puede ser necesario cerrar todas las pestañas y ventanas instaladas de Pulse y abrirlo de nuevo: la UI antigua aún no cierra su SSE. No se ha validado una migración automática con varias pestañas V7 antiguas activas. Las siguientes actualizaciones usan la reparación de V8.

## Evidencia V8 y límites

QA en Chromium del navegador de Codex, con orígenes loopback y SQLite sintética aislada:

- App completa sin observadores: A→B mediante el botón, con SSE abierta antes de actualizar.
- Dos pestañas visibles: B→C en la primera; la segunda conserva B y pasa a C mediante su propia actualización explícita.
- Cotización deliberadamente lenta: actualizar y editar permanecen bloqueados; al finalizar vuelven a estar disponibles.
- Lectura deliberadamente lenta de copia: actualizar y seleccionar otro archivo permanecen bloqueados; cancelar la confirmación libera los controles. No se restauraron datos.
- Timeout inducido: SSE se recupera y un evento nuevo llega; método PVPC, fechas, kWh y cotización permanecen idénticos.
- Auditoría de la caché observada: ningún recurso privado `/api/`, `/auth/` o exportación.

Los precios y nombres de esa QA son sintéticos; no acreditan un coste PVPC oficial ni una lectura de coche. La prueba A→B sin observadores usa el mismo App/SW/Settings final; el ajuste posterior de conservación del formulario se comprobó con timeout y evento real del servidor aislado. Svelte/tipado/build web y móvil finales pasan; el backend no cambió.

Quedan pendientes la PWA instalada en iPhone y Android, Safari, ciclo de vida real, permisos y entrega push. Estas pruebas no equivalen a validación física ni a publicación del servicio.

## Avisos V9

El editor de Ajustes permite activar, probar, reactivar y revocar avisos por navegador. El permiso se pide exclusivamente al pulsar activar; leer Ajustes o reiniciar no autoriza nuevos envíos. La gestión de avisos mantiene el contador central ocupado durante permiso, confirmación y operación; las acciones concurrentes conservan sus guardas. El servidor está apagado por defecto. Contratos, sesiones, proveedores admitidos, privacidad y limitaciones: [PUSH.md](PUSH.md).

QA V9 en Chromium con permiso/PushManager simulados y API real aislada: servicio apagado, falta de soporte, permiso denegado, activación lenta con Guardar/Importar bloqueados, recarga sin otro permiso, caducidad y reactivación humana, resultado incierto por timeout de ocho segundos, aceptación por transporte falso y revocación. 320px sin overflow y botones de44px;440px sin overflow. No se solicitó permiso del sistema ni se contactó un servicio push. El runner de `sw.js` compilado en Node VM comprueba contenido fijo y foco sin navegación; no acredita service worker ni móvil reales. El informe V9 distingue estas evidencias de las pruebas V8 de actualización.

## Cambios sin guardar V17

Actualizar con un formulario pendiente muestra una confirmación antes de cerrar SSE o iniciar el relevo. Cancelar conserva campos y cotización; aceptar descarta el borrador al recargar. Si el relevo falla, la recuperación mantiene el formulario y vuelve a proteger las salidas. Navegar entre secciones y reemplazar una edición también pide confirmación. Los borradores no se persisten.

La recarga o salida del documento usa `beforeunload` cuando el navegador lo permite y después de interacción humana. El sistema puede cerrar o suspender una PWA móvil sin mostrar ese aviso; no se promete recuperación de un borrador tras cerrar la app. Las entradas del historial creadas por esta sesión conservan Atrás/Adelante al cancelar. Para entradas antiguas sin metadatos de dirección, cancelar restaura la URL activa reemplazando esa entrada, sin recargar ni borrar el formulario.

QA V17 en sesiones locales sintéticas: cancelar navegación, Atrás, sustitución de edición o actualización mantiene los campos; guardar correctamente limpia la guarda. Se comprobó el relevo real entre dos builds de prueba y la continuidad de SSE tras cancelarlo. Caducar la sesión retira los formularios. En el navegador integrado la recarga terminó sin exponer una confirmación nativa; ese intento no acredita un aviso de cierre en Safari, Chrome móvil o una PWA instalada. Las preferencias protegen también una elección posterior al envío cuando la escritura se confirma pero falla la lectura del estado.

## Disponibilidad de actualización V31

El aviso observa también el worker que ya está instalándose cuando termina el registro, además del que está en espera y de futuras instalaciones. La primera instalación sin controlador previo no ofrece una actualización. Los observadores se retiran al desmontar la app; una resolución tardía del registro no modifica su estado.

La reproducción determinista ejecuta el `setupServiceWorker` exacto de V30 y V31: con una instalación ya iniciada y sin otro `updatefound`, V30 no ofrece el aviso y V31 sí, con cero recargas en ambos casos. Esto demuestra esa ventana del código; no establece que fuera la única causa del documento antiguo observado en el preview V30. [MDN describe el worker actual](https://developer.mozilla.org/en-US/docs/Web/API/ServiceWorkerRegistration/installing) y [el evento de adquisición](https://developer.mozilla.org/en-US/docs/Web/API/ServiceWorkerRegistration/updatefound_event). La disponibilidad y la aceptación del relevo son pasos distintos, como explica la [guía oficial de Chrome](https://developer.chrome.com/docs/workbox/handling-service-worker-updates).

QA Chromium con API/SQLite y precios ficticios propios: dos actualizaciones sucesivas A→B→C, aceptación individual en dos pestañas, ausencia de recarga automática, bloqueo durante cotización pendiente y conservación de método/fechas/kWh/coste al cancelar la confirmación. A 320 y 375 px de viewport se midieron cero desbordamientos horizontales y un botón de 44 px. Una primera medida de escritorio fue descartada y se conservó su corrección en la evidencia. No se inspeccionaron almacenes privados ni se repitió la auditoría offline de CacheStorage.

Pasan Svelte (0 errores y 0 avisos), 20 pruebas web, 7 móviles, ambos builds, sincronización Android/iOS y APK debug. Backend, API, base de datos, núcleo nativo y seis lockfiles permanecen idénticos a V30; sus pruebas anteriores conservan su alcance y no se presentan como ejecutadas en V31. Safari, PWA instalada, teléfonos físicos, push recibido y compilación Xcode continúan pendientes.
