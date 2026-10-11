# Instalar y actualizar la PWA

Pulse se utiliza desde un navegador y puede instalarse desde su menú. Fuera de localhost requiere HTTPS; la interfaz y la API pertenecen al mismo origen. En iOS, abre la web en Safari y usa Compartir → Añadir a pantalla de inicio. En Android, usa Instalar aplicación o Añadir a pantalla de inicio, según tu navegador.

## Privacidad sin conexión

El worker conserva únicamente el shell genérico y los activos públicos. Nunca guarda respuestas API, sesión, identidad, perfil, ubicación, exportaciones ni telemetría. Una apertura completa sin servidor muestra un aviso genérico y permite reintentar al recuperar la conexión. No ofrece el perfil privado offline.

Las seis ilustraciones orientativas del B10 se descargan bajo demanda. Solo se conservan al usarlas: URLs exactas de esta build, GET de mismo origen y respuestas 200 WebP sin redirección. Los requests omiten credenciales; al activar otra versión se retiran URLs obsoletas. Si la imagen cargó antes de activar el worker, el componente solicita conservar ese activo público al terminar la instalación. Esto no persiste el acabado elegido del perfil. Capacitor empaqueta las imágenes y omite este worker.

## Actualización explícita

La versión nueva espera hasta pulsar «Actualizar ahora». Las operaciones pendientes deben terminar primero; los cambios sin guardar requieren confirmación. Cancelar conserva el formulario. La otra pestaña abierta mantiene su documento y borrador hasta aplicar su propia actualización o recargar. Las conexiones SSE se cierran antes del relevo del worker.

## Comprobación del candidato

Chromium en dos orígenes nuevos, backend real y datos ficticios: primera instalación de 284246 bytes de cuerpos públicos frente a 574152, una ilustración frente a seis; carga de imagen antes del control del worker sin duplicar su descarga. Se verifican seis colores, caché sin API, recurso WebP disponible con servidor detenido, aviso genérico de reapertura, recuperación, limpieza de activo obsoleto, migración de la PWA anterior y actualización con dos pestañas y borrador conservado. Son pruebas locales; instalación, actualización, consumo y accesibilidad física en iOS/Android requieren comprobación específica de esta versión. Consulta [Rendimiento](PERFORMANCE.md) y [Móvil](MOBILE.md).
