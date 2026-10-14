# Fase 1: accesibilidad y aceptación de la interfaz

Incremento del 14 de octubre de 2026. La fase continúa abierta hasta validar los teléfonos reales; no se empieza la fase 2 mientras la fase 1 no esté totalmente terminada.

La interfaz conserva el coche protagonista y los temas actuales. Favoritos, trayectos y planes de mantenimiento recuperan el foco al cancelar; guardar vuelve a un control estable aunque la lista se actualice después. La restauración respeta el foco que el usuario haya movido fuera del editor. Veinte mensajes de error quedan asociados a sus campos, conservando las indicaciones de las fechas UTC. El foco dentro del contenido se mantiene visible ante la barra inferior y los cambios de altura del teclado; su comportamiento físico aún debe comprobarse. Las fechas de trayectos manuales vuelven a ocupar una fila legible en móvil.

Validación local: Svelte sin errores ni avisos, build, 74 pruebas web, 11 flujos completos E2E y una prueba adicional de guardado tardío con foco externo. Incluyen las seis rutas, reflujo y fuente raíz al 200 %, teclado con altura reducida, conservación de borradores, respuestas fuera de orden, persistencia sin duplicar y fechas tras crear registros. Los errores de favoritos, mantenimiento y simulador se comprueban en el árbol de accesibilidad calculado por Chromium. La sonda final inspecciona 36 estados de foco a 390×844 y 320×450 con datos sintéticos: controles visibles, sin foco en body ni obstrucción detectada. Hay revisión independiente de código.

Estos resultados acreditan el software probado. Solo se dispone aquí de Chromium para la automatización: no acreditan Safari/WebKit, VoiceOver, TalkBack ni aceptación física.

La entrega web cambia recursos de interfaz, conserva los 62 archivos de backend, SQLite 21/JSON 17, los cinco Compose y el montaje de avisos de copias en solo lectura. Se conserva copia protegida y rollback de imagen sin rebobinar datos. Las fuentes y la web se verifican antes de marcar su entrega; nunca se publica configuración privada, claves o bases de datos.

## Pruebas físicas pendientes

Lucas confirma disponibilidad de iPhone y Android; todavía no ha realizado esta aceptación de la versión nueva. Registrar modelo, versión de sistema, navegador/PWA y resultados, sin información privada.

| Comprobación | Resultado esperado | iPhone | Android |
| --- | --- | --- | --- |
| Versión y navegación | Recibir la actualización, iniciar sesión, abrir Coche, Energía, Diario, Estados, Mapa y Perfil; títulos y controles comprensibles. | Pendiente | Pendiente |
| Texto grande y temas | Revisar todas las pantallas en claro/oscuro y vertical/horizontal con texto ampliado; nada esencial cortado, solapado o inaccesible. | Pendiente | Pendiente |
| Teclado y borradores | Abrir alta manual, escribir texto de prueba, cerrar teclado, cancelar el descarte y cambiar pestaña; conservar el borrador y acceder a los botones. Descartarlo al terminar. | Pendiente | Pendiente |
| Lector de pantalla | Leer etiquetas y controles; en Mapa enviar favorito vacío, escuchar y revisar los errores sin crear un lugar; cerrar y recuperar un punto útil. Dialogar y navegar sin perder contexto. | VoiceOver pendiente | TalkBack pendiente |
| Recuperación y comodidad | Interrumpir y recuperar la conexión mientras se edita; conservar borrador, explicar errores y permitir recuperación sin duplicados. Revisar zonas cómodas de toque. | Pendiente | Pendiente |
| Copia/exportación e interfaces nativas | Conservar las comprobaciones pendientes del roadmap y las denegaciones de carga/descarga; no inferir aceptación desde pruebas sintéticas ni eludir permisos. | Pendiente | Pendiente |

Para activar y practicar los lectores: [guía de Apple](https://support.apple.com/es-us/guide/iphone/iph3e2e415f/ios) y [guía de Google](https://support.google.com/accessibility/answer/6007100?hl=ES). Las pruebas de distribución, widgets y conexión nativa de fase 2 conservan sus requisitos propios.

El login y una notificación de prueba en iPhone ya fueron confirmados por Lucas en el incremento anterior de fase 8. La renovación de avisos, la conexión intermitente y la accesibilidad completa no se consideran aprobadas por esa recepción.
