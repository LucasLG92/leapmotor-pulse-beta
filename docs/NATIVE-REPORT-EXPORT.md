# Guardar el informe manual en Android e iOS

La acción «Guardar informe JSON» obtiene una copia actual de los agregados manuales de 30, 90 o 365 días y abre el selector del sistema. No incluye etiquetas, filas del diario, identidad, coordenadas ni credenciales. Conserva `source: manual` y `coverage: unknown`; no confirma viajes o energía del B10.

El puente opcional `exportJourneyReport({days})` recibe únicamente un entero permitido. La sesión permanece en el cliente nativo y se usa para GET fijo `/api/journeys/report?days=…`. Se rechazan argumentos adicionales, respuesta distinta de 200/JSON, UTF-8 inválido, más de 64 KiB, campos desconocidos, claves repetidas, estructura profunda y valores fuera del contrato. Se reconstruye el objeto permitido antes de entregarlo. No hay reautenticación ni repetición automáticas.

Una sola operación mantiene el informe en memoria y comprueba que la sesión siga vigente. Cierre de sesión o destrucción retiran la operación; callbacks antiguos no la completan bajo otra sesión. Android usa `ACTION_CREATE_DOCUMENT` con `application/json` y escribe una vez en el URI `content:` elegido, sin permisos de directorio, permisos persistentes o FileProvider. Las llamadas al proveedor ocurren fuera del monitor de ciclo de vida: una llamada ya iniciada puede terminar tras retirar la operación y su resultado queda incierto.

iOS usa `UIDocumentPickerViewController(forExporting:asCopy:true)`. La fuente temporal reside en un directorio UUID propio, con protección completa y excluido de copias de seguridad. Se limpia al terminar, cancelar, fallar, cerrar sesión o destruir el plugin; el siguiente inicio retira restos propios. Nunca se borra el destino seleccionado.

`{status: 'cancelled'}` conserva el informe y distingue cancelación de error. `{status: 'completed'}` indica que el sistema terminó el selector o la entrega; comprueba el archivo en la ubicación elegida. Un error de resultado incierto puede dejar una copia vacía o parcial: comprueba antes de reintentar. Ninguna ruta o URI regresa a JavaScript. Los mensajes usan códigos fijos y no reflejan detalles privados de excepciones. Las apps anteriores pueden seguir consultando el informe y piden actualizar para guardarlo.

La cualificación de software y las pruebas físicas pendientes se registran en [NATIVE-REPORT-EXPORT-QUALIFICATION.json](NATIVE-REPORT-EXPORT-QUALIFICATION.json). Los documentos de fuente son una instantánea anterior a la publicación; los resultados posteriores de CI y artefactos se anotan en las PR y en el recibo local. Una compilación o un selector simulado no acredita recepción en un teléfono, proveedores cloud, firma, OAuth, WidgetKit o acceso al coche. Se mantienen nueve fases, siete puertas reales y A0–A12; centinela/cámaras van al final.

No cambian API, SQLite20, JSON16, permisos, dependencias ni configuración operativa. El rollback de este incremento consiste en restaurar su snapshot de fuente; no se migra ni modifica una base operativa.
