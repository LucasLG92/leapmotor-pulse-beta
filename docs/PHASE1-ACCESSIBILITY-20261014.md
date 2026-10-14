# Fase 1: accesibilidad y aceptación de la interfaz

Incremento del 14 de octubre de 2026. La fase continúa abierta hasta validar los teléfonos reales; no se empieza la fase 2 mientras la fase 1 no esté totalmente terminada.

La interfaz conserva el coche protagonista y los temas actuales. Favoritos, trayectos y planes de mantenimiento recuperan el foco al cancelar; guardar vuelve a un control estable aunque la lista se actualice después. La restauración respeta el foco que el usuario haya movido fuera del editor. Veinte mensajes de error quedan asociados a sus campos, conservando las indicaciones de las fechas UTC. El foco dentro del contenido se mantiene visible ante la barra inferior y los cambios de altura del teclado; su comportamiento físico aún debe comprobarse. Las fechas de trayectos manuales vuelven a ocupar una fila legible en móvil.

Validación local: Svelte sin errores ni avisos, build, 74 pruebas web, 11 flujos completos E2E y una prueba adicional de guardado tardío con foco externo. Incluyen las seis rutas, reflujo y fuente raíz al 200 %, teclado con altura reducida, conservación de borradores, respuestas fuera de orden, persistencia sin duplicar y fechas tras crear registros. Los errores de favoritos, mantenimiento y simulador se comprueban en el árbol de accesibilidad calculado por Chromium. La sonda final inspecciona 36 estados de foco a 390×844 y 320×450 con datos sintéticos: controles visibles, sin foco en body ni obstrucción detectada. Hay revisión independiente de código.

Estos resultados acreditan el software probado. Solo se dispone aquí de Chromium para la automatización: no acreditan Safari/WebKit, VoiceOver, TalkBack ni aceptación física.

La entrega web inicial cambia recursos de interfaz, conserva los 62 archivos de backend, SQLite 21/JSON 17, los cinco Compose y el montaje de avisos de copias en solo lectura. Se conserva copia protegida y rollback de imagen sin rebobinar datos. Las fuentes y la web se verifican antes de marcar su entrega; nunca se publica configuración privada, claves o bases de datos.

## Pruebas físicas pendientes

Lucas confirma disponibilidad de iPhone y Android. En la entrega inicial informó de palabras partidas en Energía y respondió que el flujo propuesto con lectores funciona; la aceptación completa y las superficies modificadas siguen pendientes. Registrar modelo, versión de sistema, navegador/PWA y resultados, sin información privada.

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

## Correcciones tras la revisión del iPhone

El candidato posterior corrige el corte de «Manual» y «Demostración» en los resúmenes y meses de Energía, también con texto ampliado; Climatización utiliza un copo de nieve y Ajustes un engranaje convencional. Los SVG son originales del proyecto.

Añadir lugar ofrece búsqueda por nombre/dirección, selección y alias editable. La selección rellena el borrador y Guardar persiste. Coordenadas manuales quedan plegadas como alternativa; siguen siendo válidas al volver a plegarlas. Enter durante composición no busca; consultas viejas/canceladas no reponen resultados y los borradores se conservan al cancelar el cierre.

Lucas solicita también direcciones privadas. El formulario solo permite buscar con estado coherente de Photon privado y rechaza respuestas públicas. El adaptador mTLS original está preparado y desactivado; no hay fallback público. Lucas ha elegido mantener el geocodificador preparado sin instalar; no se crea un LXC ni se activa su búsqueda. Si en el futuro se instala, siguen siendo necesarias sus pruebas de TLS/recursos reales. Mapas/cargadores y Apple/Google mantienen servicios públicos con avisos visibles antes de sus acciones. [Contrato y límites](PRIVATE-PLACE-SEARCH.md).

Validación local del candidato: 74 pruebas web, 19 E2E Chromium, 1426 pruebas Python y 131 subcasos; 34 pruebas Python omitidas por sus gates de entorno. Tipado Svelte 0 errores/0 avisos y build. La suite acotada de navegación aporta 68 pruebas con transporte falso; no acredita handshake TLS real. La fuente actual conserva SQLite21/JSON17 y cambia exactamente tres módulos de navegación/contrato frente a la imagen inicial. El operador exige manifiestos base/candidato, cinco Compose preservados y rollback sin rebobinar datos.

La versión corregida ya está entregada en la web: 65 recursos HTTPS exactos, cinco Compose y esquemas conservados. Los monitores de copias mantienen sus tres timers activos y dos informes sanos. También se corrigió el foco inicial diferido para respetar un control que el usuario haya enfocado, y los errores con buscador inactivo abren la alternativa manual accesible. Esto no acredita el geocodificador instalado ni nueva aceptación física. La fase 1 permanece abierta y no habilita avanzar a la fase 2.

## Confirmación de Lucas después de la entrega corregida

Lucas confirma que «Se ve bien en ambos» al revisar iPhone y Android: palabras de Energía, iconos y pantallas con texto grande y teclado. No facilitó modelos/versiones. Esta confirma la revisión visual propuesta; no sustituye la prueba específica con lectores: a la pregunta sobre VoiceOver/TalkBack en el formulario actualizado respondió «no he podido comprobarlo». La aceptación del lector y los demás flujos físicos detallados permanecen pendientes.

A la propuesta del LXC privado respondió «Mantenerlo preparado sin instalar». No hay aprobación pendiente ni se repetirá esa solicitud sin nuevo encargo. La búsqueda privada queda desactivada por esta elección; no se consultan domicilios en servicios públicos. La fase 1 permanece abierta para sus comprobaciones pendientes; no avanzar a fase 2.

## Recuperación de copias en la interfaz

Se corrigió el foco/contexto tras restauración válida y tras recuperar una lectura fallida, conservando foco externo y evitando replay.26E2E/74web pasan; archivosJSON recibidos/restaurados en laboratorio con datos sintéticos.63recursosHTTPS exactos y62backend preservados. [Evidencia y límites](PHASE1-RECOVERY-20261014.md). Las pruebas físicas de lectores y archivos siguen pendientes; esto no valida restauración operativa de producción ni cierra fase1.
