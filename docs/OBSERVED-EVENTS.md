# Historial de estados recibidos

Pulse guarda automáticamente los cambios de cierre y carga entre dos lecturas admisibles del conector. El registro conserva lo que indicaron esas lecturas; no confirma una acción física ni una intrusión. Un cambio de `charging` tampoco establece el fin de una sesión: las sesiones cerradas siguen procediendo del historial del cargador.

El adapter B10 actual solo tiene señales numéricas contrastadas y devuelve cierre/carga ausentes. Este incremento prepara el software y lo verifica con entradas sintéticas; no acredita esos campos en el vehículo. El preview y las integraciones reales permanecen deshabilitados hasta su cualificación autorizada.

## Registro automático y fechas

Solo se comparan booleanos estrictos `locked` y `charging`, de la fuente fija `MATE-API B10ReadClient`. Ambas observaciones deben tener hora con zona, no ser posteriores a su recepción y tener una edad de hasta300segundos respecto a ella. La hora observada debe avanzar como máximo600segundos, sin retroceder la recepción. Cada campo requiere valores conocidos y distintos en las dos lecturas. No se infiere estado a partir de SOC, potencia o datos ausentes.

La primera lectura establece una referencia. Reiniciar Pulse, restaurar una copia o cambiar el modo demo restablece esa referencia y conserva el historial. Una lectura inválida, antigua o futura la limpia; una observación repetida o fuera de orden no la adelanta. Un hueco grande establece otra referencia sin reconstruir lo ocurrido durante el intervalo. La hora observada procede del proveedor; la recepción corresponde al almacenamiento en Pulse y no acredita frescura física.

Lectura y cambios se guardan en una sola transacción. El cursor de comparación solo se mueve después del commit. La identidad SHA256 depende de fuente, campo, tiempos observados y valores, para evitar duplicados por relectura. El collector descarta resultados de una lectura iniciada antes de reemplazar el almacenamiento mediante importación y no los entrega al observador Push. El historial funciona independientemente de la cola y entrega de notificaciones; no acredita una entrega al teléfono.

## Consulta y separación de demo

`GET /api/protection/observations` requiere sesión y devuelve `items,total,limit,offset`, con20elementos por defecto, máximo100 y offset0–1000. Solo lee SQLite y envía `Cache-Control: no-store`. El permiso nativo admite únicamente ese GET y los parámetros acotados. No existe endpoint para crear o ejecutar un cambio real desde la interfaz.

Protección muestra el historial normal primero; recepción, observación anterior y fuente se revelan en detalles. La página confirmada se conserva si falla solicitar otra; reintentar repite la página solicitada. Se invalidan respuestas por montaje, generación, revisión, modo y conexión; una401 vigente elimina la vista privada. Offline se retiran los datos recibidos. La UI de demo muestra exclusivamente sus eventos ficticios y no solicita el diario observado, con controles independientes. El endpoint privado del diario conserva su historial independiente durante la demo. El Resumen obtiene el contador observado en la misma transacción de estado; la demo utiliza su propio contador.

V22 inicia la consulta con el estado cargando y revela las tarjetas inferiores al resolver el primer intento, evitando que se desplacen cuando llega la lista. El plazo inicial de ocho segundos desde montaje no se reinicia al sustituir peticiones; al vencer cancela la lectura vigente, invalida su generación y muestra error/reintento con las tarjetas disponibles. Un resultado vigente o el desmontaje limpia ese temporizador. Los refrescos/reintentos posteriores no vuelven a ocultarlas. Ese plazo corresponde a la primera carga del panel, no a un timeout global de todas las peticiones.

## Persistencia y recuperación

SQLite10 añade `observed_events`, con columnas acotadas, retención de los1000últimos cambios y deduplicación por ID. Los IDs de lecturas son referencias de procedencia, sin FK: eliminar una lectura por retención no borra un cambio histórico. La exportación JSON9 añade un bloque separado `observed_events`; formatos1–8 se restauran con ese bloque vacío. No se reconstruyen cambios al importar lecturas antiguas.

La restauración valida todo antes de backup/escritura: campos exactos, tipos, límite, IDs derivados, duplicados, fechas y relación con las lecturas ancladas que todavía estén presentes. Conserva el orden de consulta y restablece la referencia de comparación. La copia JSON sigue limitada a5MB y puede rechazar conjuntos máximos; una copia SQLite consistente conserva el almacenamiento completo. Las copias no son prueba de autenticidad física del proveedor.

Antes de migrar una instalación, guardar una copia SQLite9 consistente con permisos600. Para volver aV20 hacen falta sus fuentes/build y la copia schema9 anterior juntos; un binario antiguo rechaza schema10. No activar un proveedor o servicio público por haber pasado pruebas sintéticas.

## Referencias de producto

[Tesla Sentry Mode](https://www.tesla.com/ownersmanual/model3/en_gb/GUID-56703182-8191-4DAE-AF07-2FDC0EB64663.html) emplea sensores/cámaras y alarmas del vehículo. Pulse no traslada esas capacidades a un registro de lecturas. [Apple Lists and tables](https://developer.apple.com/design/human-interface-guidelines/lists-and-tables) orienta la jerarquía de una lista y sus controles; los detalles de cada cambio utilizan una etiqueta descriptiva y conservan los tokens existentes. No se copian activos o componentes.
