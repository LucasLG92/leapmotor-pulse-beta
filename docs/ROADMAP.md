# Roadmap y criterios vigentes

El estado por fase y el registro de entregas están en [DELIVERY-STATUS.json](DELIVERY-STATUS.json), la fuente única de estado. Esta hoja mantiene el alcance y la aceptación esperada. [ACCEPTANCE.md](ACCEPTANCE.md) conserva debajo de su cabecera los recibos históricos con sus fechas y límites.

El trabajo sigue nueve fases en orden: coherencia/UI, clientes móviles, B10, cargas/energía, trayectos/informes, integraciones/navegación, controles/rutinas, estabilidad/recuperación y, al final, centinela/cámaras. Las fases pueden tener trabajo de software mientras esperan una puerta real, respetando dependencias. Una entrega, build, CI, fixture o checker correcto no declara terminado el objetivo.

## Criterios que se conservan

- Mantener íntegros los criterios originales A0–A12 y las siete puertas reales. Los recibos anteriores conservan su fecha y alcance; este roadmap no los reinterpreta como aprobados.
- Coche, cargador, energía, trayectos, integraciones, controles, teléfonos, accesibilidad, firma, distribución, identidad, seguridad, recuperación y aceptación requieren la evidencia real aplicable. Simuladores y datos sintéticos solo acreditan su cobertura de software.
- No completar cifras o señales que falten con datos de catálogo. No activar lecturas ni usar material privado sin autorización específica para esa operación.
- Las mejoras opcionales de trayectos (notas, unión reversible, informes ampliados y salud estimada) siguen siendo opcionales y no se convierten en prerrequisitos ni evidencia.
- Centinela/cámaras permanece como fase final. Si la capacidad no está disponible o autorizada, se conserva como pendiente; no se reduce el objetivo.

## Puertas reales preservadas

El registro conserva las siete puertas pendientes del estado histórico: (1) login negativo con otro usuario real; (2) recuperación ante desastre de host/dominio y proxy con login humano después; (3) actualización/accesibilidad PWA en ambos teléfonos, login/widgets nativos, firma/distribución iOS y aceptación física; (4) B10, cargador, energía, trayectos, integraciones, comandos, centinela y aceptación de la entrega final; (5) avisos de imagen, dependencias nativas/toolchain/SPM/SDK y seguridad integral; (6) sesión autenticada y push después de reiniciar la imagen; (7) rendimiento/consumo móviles reales y aceptación habitual del alcance completo. Sus estados permanecen pendientes; el detalle conservado está en el registro JSON.

## Criterios por fase

| Fase | Alcance de software | Aceptación real |
|---|---|---|
| 1. Coherencia/UI | Registro/documentación coherentes, preparación, accesos contextuales, estados bloqueados y errores/reintentos accesibles; tipos/build y QA sintética. | Aceptación física y de accesibilidad sigue abierta. |
| 2. Clientes móviles | Fuentes compartidas, identidad, APK y App/WidgetKit cualificados. | Firma/distribución y OAuth, actualización, privacidad, caducidad, widgets, PWA y push en ambos teléfonos. |
| 3. B10 | Configuración autorizada, login/refresh y matriz de señales. | Lectura pasiva contrastada con el vehículo, incluidas unidades, frescura y reposo. |
| 4. Cargas/energía | Sesiones, curva, pausas, cierres, reconciliación y PVPC sin duplicados. | Cargador y energía reales; estimación separada del gasto real, red y autoconsumo. |
| 5. Trayectos/informes | Ingestión, persistencia, paradas, distancia/energía/cobertura, agregados y exportación. | Validación con recorridos y datos reales. Mejoras opcionales continúan opcionales. |
| 6. Integraciones/navegación | HA/MQTT, ABRP, geografía, caducidad, reconexión y privacidad. | Servicios reales y rutas/autonomía respaldadas por datos/modelos cualificados. |
| 7. Controles/rutinas | Capacidades verificadas, permisos, precondiciones y coordinación. | Aceptación y resultado físicos; sin replay automático ante incertidumbre. |
| 8. Estabilidad/recuperación | Seguridad, análisis de artefactos y procedimiento conjunto de recuperación. | Recuperación operativa, proxy/login, otra cuenta, rendimiento/consumo y uso habitual. |
| 9. Centinela/cámaras (final) | Viabilidad específica del B10/firmware, cobertura, visor y retención si hay acceso autorizado. | Vigilancia/cámaras reales; comandos aceptados o diarios de estado no bastan. |

## Lista de implementación y comprobaciones pendientes

Las marcas completadas de esta lista describen únicamente trabajo del candidato local. Su entrega y su aceptación se registran por separado en `DELIVERY-STATUS.json`.

### 1. Coherencia y UI/UX

- [x] Registrar entregas históricas y candidatos con sus versiones, límites y resúmenes de recibos verificables.
- [x] Simplificar «Preparar Pulse» a un desplegable, explicar configuración frente a comprobación real y destacar el siguiente paso.
- [x] Abrir preparación desde Coche y desde el diario vacío, con foco y protección de borradores.
- [x] Explicar los controles físicos bloqueados y gestionar caducidad, errores y reintentos del historial.
- [x] Comprobar reflujo sintético con fuente al 200 % en las seis pantallas a 390 px, y corregir la insignia y cabecera. Escala nativa y accesibilidad física pendientes.
- [x] Comprobar navegación con teclado entre las seis pantallas en claro/oscuro, foco del título, navegación móvil y salto al contenido con fixtures locales.
- [x] Corregir y comprobar el guardado fallido de cargas manuales: error persistente, borrador y foco; teclado en alta/edición y reflujo a 390 px. Los formularios restantes siguen pendientes.
- [x] Corregir y comprobar errores persistentes en preferencias, perfiles de widget y mantenimiento; conservar foco externo y borradores durante respuestas tardías. Guardado/edición y persistencia SQLite comprobados con datos sintéticos, sin alterar privacidad o capacidades nativas.
- [x] Mantener el error de exportación y el estado de operación en el panel de copias; comprobar HTTP503, foco, borrador externo y reflujo390claro/oscuro. Restauración devuelve resultado estructurado, conserva confirmación y trata el resultado incierto; revisión estática, sin aceptación de ese flujo en navegador.
- [x] Bloquear acciones Push/nativas durante consultas y conciliar un enlace rechazado con una lectura sin replay. Error de acción separado, código borrado, caché apagada y foco al control o título estable; fixture en memoria sin permisos, grants ni API reales.
- [x] Corregir ABRP, cambio de demo, eliminación de perfil y revocación de dispositivo: error incierto persistente, controles liberados ante rechazo, guardas tras confirmar y foco que respeta borradores. QA en memoria: false/throw, cancelación, cambios de revisión/modo y eliminación con refresco demorado; encabezado estable cuando desaparece la fila. Reflujo390 claro/oscuro; no consentimientos ni dispositivos reales.
- [ ] Completar activación y renovación de avisos, enlace real y conexión intermitente. Recepción del JSON descargado y restauración en navegador pendientes por permisos de carga/descarga denegados; sin elusión. La simulación offline por CDP tampoco se ejecutó por denegación; accesibilidad física pendiente.
- [ ] Entregar el candidato y comprobar accesibilidad en los teléfonos.

### 2. PWA, aplicaciones y widgets

- [x] Incorporar la recuperación compartida del arranque y las correcciones actuales a un APK Android genérico local con Lint activo.
- [ ] Preparar el APK operativo con configuración y firma autorizadas.
- [ ] Cualificar App y WidgetKit iOS de la revisión final, completar firma y distribución.
- [ ] Verificar actualización de Android desde r14 con la misma identidad y firma, sin pérdida de datos; conservar una recuperación adecuada a las restricciones de downgrade.
- [ ] Verificar retorno OAuth, caducidad y revocación en ambos teléfonos.
- [ ] Verificar actualización de la PWA instalada, offline, recuperación, avisos con bloqueo y sesión/push tras reiniciar el servidor.
- [ ] Verificar widgets: campos seleccionados, tamaños, privacidad, caducidad, refresco, bloqueo y accesibilidad.

### 3. B10 y telemetría

- [x] Conservar las dos temperaturas y cuatro presiones numéricas definidas por el SDK fijado: adapter, collector, SQLite, estado actual, historial y exportación/importación. Unidades, fechas y ausencia de datos explícitas; los códigos crudos de estado siguen sin cualificar.
- [x] Verificar software con SDK fijado y fixtures: normalización, caducidad, conservación histórica, migración transaccional 15→16, JSON 14 y restauración de formatos anteriores. Recuperación con código y copia SQLite 15 conjuntamente comprobada localmente.
- [ ] Preparar la configuración autorizada del proveedor y comprobar adquisición, renovación y persistencia de sesión reales.
- [ ] Contrastar batería, autonomía y odómetro con el coche; conservar unidades, fuente y hora de observación.
- [ ] Investigar y mapear únicamente señales demostradas: carga/cable, cierre, climatización, puertas/maletero, temperaturas, neumáticos y ubicación.
- [ ] Comprobar desconexiones, reinicio, reposo y convivencia con la app oficial; ajustar consultas sin prometer cobertura durante sueño profundo.

Este incremento sigue siendo un candidato local, documentado en [PHASE3-QUALIFICATION.json](PHASE3-QUALIFICATION.json) y [TELEMETRY.md](TELEMETRY.md). No demuestra que estas señales estén disponibles ni cualificadas físicamente en el B10 objetivo; la fase 3 sigue abierta.

### 4. Cargas y energía

- [ ] Conectar el cargador real y contrastar inicio, fin, pausas, reanudaciones y kWh.
- [ ] Implementar el adaptador concreto de intervalos vinculado inequívocamente a la sesión; evitar duplicados y conservar huecos.
- [ ] Reconciliar sesiones y precios pendientes, PVPC histórico y revisiones de coste.
- [ ] Separar registros manuales, observados y estimados; distinguir total del cargador, red y autoconsumo solo con mediciones suficientes.

Identificación de casa/carga pública y comparaciones por lugar son mejoras propuestas.

La revisión del origen confirma que los buckets de evcc no comparten ID de sesión con el historial cerrado. El motor existente no los promociona a costes. Contrato y evidencia necesaria para el adaptador concreto en [SOURCE-QUALIFICATION.md](SOURCE-QUALIFICATION.md).

### 5. Trayectos, historial e informes

- [ ] Implementar recepción y persistencia de trayectos reales.
- [ ] Detectar inicio/fin/paradas con señales verificadas; conservar distancia y duración, origen/destino y recorrido si existe ubicación.
- [ ] Calcular consumo y eficiencia solo con muestras suficientes; explicar cobertura y ausencia de datos.
- [ ] Resolver recorridos cortos, huecos, reinicios y cambios de día; completar agregados y exportación sin mezclar demo.

Notas/etiquetas, unión reversible de trayectos, informes mensuales ampliados, recordatorios por odómetro y salud/capacidad estimada de batería son mejoras propuestas. Una estimación de salud requiere método y límites propios.

El SDK fijado conserva registros crudos de kilometraje sin un esquema tipado de viaje B10 ni unidades definidas. La paginación completa no acredita cobertura de todos los viajes. Los campos y la muestra autorizada necesarios para avanzar se detallan en [SOURCE-QUALIFICATION.md](SOURCE-QUALIFICATION.md).

La referencia LPB10 revisada en un commit fijado reconstruye segmentos por odómetro/SoC y fecha las muestras con el reloj del cliente. No acredita frescura física, identidad remota del viaje o energía medida; no se adopta como cierre de la ingestión real.

### 6. Home Assistant, ABRP y navegación

- [x] Admitir la selección explícita de dos temperaturas y cuatro presiones ya normalizadas, con clases/unidades Home Assistant, rangos estrictos, disponibilidad/caducidad y limpieza limitada a entidades propias. Defaults y selecciones previas conservados; software comprobado con transporte sintético, HTTP y UI a 390 px en ambos temas.
- [ ] Comprobar recepción real y descubrimiento MQTT en Home Assistant, permisos, caducidad y reconexión del broker.
- [ ] Comprobar recepción ABRP y ampliar señales únicamente cuando estén disponibles y autorizadas.
- [ ] Comprobar mapas/cargadores y búsquedas/navegación externa en ambos teléfonos.
- [ ] Completar rutas por carretera y autonomía prevista si se mantiene esa función, con datos y modelo cualificados.
- [ ] Comprobar consentimiento, privacidad y desconexión de cada integración.

Favoritos, destinos frecuentes y dirección legible del coche son mejoras propuestas. Enviar un destino al vehículo pertenece a la fase 7.

Este incremento de fase 6 no activa ninguna integración, no añade consultas al coche y no comparte campos que el operador no haya seleccionado. Detalle en [MQTT.md](MQTT.md) y [MQTT-QUALIFICATION.json](MQTT-QUALIFICATION.json). En aquel incremento SQLite16/JSON14 no cambiaron; la evolución posterior a SQLite17/JSON15 corresponde al diagnóstico de fase 9. Se conserva la planificación externa documentada mientras se espera la preferencia solicitada sobre rutas internas; el pendiente condicional de rutas/autonomía permanece visible.

### 7. Controles y automatizaciones

- [ ] Implementar y cualificar por separado los comandos que admita el B10: cierre, climatización, preparación y localización mediante luces/sonido.
- [ ] Evaluar capacidades adicionales antes de implementarlas: ventanillas, maletero, asientos, límite de carga, cable y destinos al navegador del coche.
- [ ] Comprobar permisos, precondiciones, aceptación de nube y ejecución física; conservar historial y resultado incierto sin repetir órdenes automáticamente.
- [ ] Si se incorporan rutinas/horarios, resolver cancelación, zona horaria y coordinación con el cargador para evitar controladores competidores.

Las rutinas y los horarios son ampliaciones propuestas, sujetas a capacidades reales.

### 8. Estabilidad y recuperación

- [x] Comprobar los seis locks del candidato con OSV fijado, conservar informe/procedencia/resumen y fallar ante errores, avisos o inventario incompleto. Pruebas focales y ejecución real local; la retención en CI está preparada, sin ejecución externa de esta revisión. [Cualificación acotada](SECURITY-QUALIFICATION.json).
- [ ] Cerrar revisión integral de dependencias, imagen, componentes nativos y toolchains sobre el artefacto final.
- [x] Preparar un monitor genérico de metadatos de copias: ausencia, antigüedad, tamaño, permisos, espacio y transiciones de estado. La validación focal registró 29 pruebas y 45 subcasos; la guía comunitaria está en [deploy/community/README.md](../deploy/community/README.md). No valida contenido, restauración, una instancia externa ni entrega de avisos.
- [x] Instalar y observar el monitor con autorización; hashes, permisos y visibilidad del montaje real verificados; Pulse y scripts de copia conservados, sin ejecutar copias.
- [ ] Elegir consumidor/canal y comprobar aviso de fallo/recuperación y fallos/ausencia/caducidad del propio monitor; observar ciclos naturales y cualificar recuperación operativa.
- [ ] Actualizar recuperación y rollback; completar copia fuera del servidor y restauración integral.
- [ ] Comprobar rechazo de otra cuenta real y recuperación conjunta de host, Pulse, proxy y autenticación con acceso humano posterior.
- [ ] Medir rendimiento, tráfico y batería con teléfonos e integraciones reales.
- [ ] Acordar y completar un periodo de uso habitual que acepte el conjunto anterior.

### 9. Centinela y cámaras — última fase

- [x] Investigar las interfaces públicas de Mate/LPB10/SDK y la documentación oficial: estudio de viabilidad [SENTINEL-CAMERAS.md](SENTINEL-CAMERAS.md), con revisiones fijadas. Contrato220 con actuación no verificada,400 deshabilitado por la ruta examinada y3636 como código sin semántica B10 cualificada. Cámaras360/dashcamUSB a bordo separadas del vídeo remoto desconocido; ninguna función física activada. [Cualificación](PHASE9-QUALIFICATION.json).
- [x] Recoger y conservar los códigos crudos 1255/3636, fuentes y tiempos: normalización sin coerción de texto/booleanos, SQLite16→17, JSON14→15 e importación1–14. Panel de diagnóstico e historial sin interpretación, separado de demo y datos actuales caducados; pruebas, builds y QA sintética. No habilita comandos, intrusión o cámaras.
- [ ] Comprobar modelo, firmware e interfaces del B10: alarma/apertura, función centinela y acceso a imágenes, directo o grabaciones.
- [ ] Implementar reglas sobre señales verificadas, activación/desactivación, historial, deduplicación, avisos y cobertura visible durante reposo/desconexión.
- [ ] Si hay acceso autorizado, implementar visor autenticado, selección de cámara y gestión de sesiones/consumo.
- [ ] Evaluar grabación, clips, retención, descarga y borrado únicamente con acceso demostrado.
- [ ] Comprobar físicamente vigilancia, cobertura, recepción de avisos e imágenes del coche aparcado.

## Referencias funcionales y opcionales

[Leapmotor Mate](https://github.com/ProtossBlaster/leapmotor-mate) y [LPB10](https://github.com/txurtxil/LPB10) orientan las posibilidades y los flujos; no acreditan capacidades del B10 objetivo ni autorizan copiar su implementación. Android Auto/CarPlay, otros modelos, varios vehículos e idiomas adicionales quedan fuera del cierre actual salvo decisión expresa.
