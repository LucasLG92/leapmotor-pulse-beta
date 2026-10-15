# Energía: etiquetas Manual completas

Lucas informa de otra etiqueta Manual partida en iPhone. Se extiende la regla de palabras completas a los títulos de totales, insignias de las sesiones y leyenda del gráfico, conservando la protección previa de analítica. Solo cambia una regla CSS de Energy.svelte; no se modifica tamaño de letra, datos o lógica. Las etiquetas de varias palabras conservan sus saltos naturales entre palabras.

La web/PWA está entregada el 15 de octubre en una imagen que añade únicamente 63 archivos estáticos sobre la imagen anterior; conserva 334 recursos con hash para las sesiones abiertas. Los 62 archivos del backend, SQLite 21/exportación 17, la configuración y los cinco archivos Compose se conservan, salvo la referencia a la imagen. La copia previa queda protegida en el servidor; la recuperación revierte la imagen, sin rebobinar datos.

Svelte-check termina con 0 errores y 0 avisos; la compilación de producción pasa con Node 24.19.0/pnpm 11.25.0. Permanece el aviso previo de deprecación de inlineDynamicImports del build PWA. Pasan 20 pruebas de guardas del procedimiento de entrega. Chrome con datos sintéticos muestra Manual completo en resumen, analítica, leyenda e historial, también con fuente al 200%; se conservan capturas locales en claro/oscuro. La versión anterior no reprodujo el corte dentro de palabra en Chrome: esta comprobación no acredita Safari físico. Falta aceptación de esta corrección concreta en el iPhone.

HTTPS estricto: 37 recursos normales y 26 gzip coinciden exactamente con el paquete; health/ready devuelven 200 y la API anónima o con token sintético inválido devuelve 401, todos con no-store. Esto no acredita una sesión autenticada o notificación tras el reinicio.

La fuente de este ajuste se incluye en esta revisión junto con la galería y los documentos móviles. Los intentos anteriores de ampliar permisos del plugin fueron rechazados; no se cambió ese permiso ni se eludió por otro canal. La publicación se comprueba mediante los resultados reales de las operaciones autorizadas. No se incluyen configuración operativa, bases de datos, claves o paquetes configurados.

Los APK/IPA previamente documentados no se reconstruyen en este incremento; sus pruebas y hashes siguen describiendo las versiones anteriores a esta regla CSS. La fase 2 permanece abierta y no se avanza a la fase 3.
