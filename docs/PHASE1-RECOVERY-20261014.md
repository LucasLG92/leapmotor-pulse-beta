# Fase 1: copias y recuperación de la interfaz

> Estado vigente: fase1 aceptada por Lucas el14oct en iPhone y Android. Las referencias a pendientes de las secciones inferiores describen la evidencia histórica previa a esta confirmación. Firma, widgets y restauración productiva conservan sus requisitos propios.


Continuación del 14oct2026. Se conserva la aceptación visual confirmada por Lucas en ambos teléfonos. VoiceOver/TalkBack y recepción de archivos en los teléfonos siguen sin acreditación; la fase 1 permanece abierta y no habilita empezar la fase 2. Photon queda preparado sin instalar por elección de Lucas.

## Problemas reproducidos y correcciones

Una restauración válida conciliaba el estado, pero reconstruía Perfil en Vehículo y dejaba el foco en body. Ahora mantiene Datos y enfoca el encabezado «Exportar o restaurar». Esto también funciona al haber entrado por `#preparation`; una navegación posterior a preparación recupera su prioridad habitual. El remount sigue descartando los borradores expresamente confirmados.

La comprobación de GitHub detectó además un aviso de éxito que tapaba el encabezado con texto al 200% y poca altura. Se reprodujo en un navegador ralentizado: el aviso flotante cubría el foco. La confirmación de restauración ahora permanece dentro de Datos, como estado accesible, sin tapar el encabezado. La prueba mantiene su comprobación de oclusión y añade diagnóstico de geometría; no se omite el fallo.

Si el servidor aplica el archivo pero falla la lectura posterior, el mensaje explica que se restauró y ofrece Reintentar conexión. El control recibe el foco; recuperar la lectura devuelve el foco al encabezado sin volver a enviar el archivo. Un foco que el usuario movió a la navegación se conserva. Si se pierde la respuesta de la restauración, el error permanece, el borrador se conserva y no hay replay automático.

El cambio afecta solo a UI y tipos internos. API, autenticación, esquema SQLite21 y exportaciónJSON17 permanecen iguales. El helper de foco existente se reutiliza; no hay dependencia de producción nueva.

## Evidencia de software

Svelte0errores/0avisos,74pruebasweb y26E2EChromium aprobadas. Siete escenarios de copias verifican: archivo descargado y parseado realmente, cancelación sin POST ni pérdida del borrador, restauración/descartes, foco externo, lectura fallida y recuperación, respuesta perdida sin replay, JSON inválido y rechazo del servidor sin sustituir datos. La restauración se comprueba a390×844 y320×450 con texto al200%, sin desbordamiento ni foco tapado por navegación fija. El entorno del servidor sintético excluye las variables PULSE operacionales.

El laboratorio usa servidor loopback y SQLite temporal con datos originales de prueba. No exporta ni restaura datos de producción y no accede a claves. Las denegaciones históricas de archivos reales no se eluden. Los tests de Chromium no acreditan Safari/iPhone ni lectores físicos: WebKit se descargó, pero faltan bibliotecas del host y no se ejecutó.

## Pruebas físicas pendientes

En ambos teléfonos: comprobar recepción de una exportación JSON desde Perfil → Datos, sin compartir el archivo ni su contenido; practicar VoiceOver/TalkBack en el formulario de favoritos actualizado. No restaurar datos reales para una prueba de interfaz sin una decisión específica y recuperación preparada. La prueba de restauración de producción pertenece además al cierre operativo de fase8 y conserva sus gates.

## Entrega web

Web entregada y comprobada mediante HTTPS estricto:37recursos sin comprimir y26gzip exactos. Los62archivos backend,5Compose,SQLite21/JSON17 y configuración se conservan. Tres timers de copias activos y dos informes sanos. Se guarda rollback de imagen sin rebobinar datos. No se ejecutó ninguna restauración de producción.

## Aceptación final de fase1

Lucas respondió «Funciona todo en ambos» a las comprobaciones de VoiceOver/TalkBack en seis pantallas y errores/foco de Mapa → Añadir lugar → Guardar vacío, conservación del borrador tras cortar/recuperar red y recepción JSON desde Perfil → Datos. Junto con la revisión visual, texto grande y teclado confirmada antes, cierra la aceptación de fase1 en ambos teléfonos. Es evidencia humana; no se presenta como una ejecución automatizada en dispositivos. No se proporcionaron modelos/versiones.

La confirmación permite continuar fase2. No acredita instalación/firma/OAuth/widgets nativos, consumo prolongado, restauración productiva o Photon instalado; esas comprobaciones mantienen su alcance y pendientes.
