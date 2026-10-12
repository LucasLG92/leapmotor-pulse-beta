# Validación del candidato local

**Fecha del resumen:** 12 de octubre de 2026. Este documento resume resultados locales comprobados para los stages de publicación. No es un informe de CI y no enlaza logs, paquetes ni recibos externos.

## Candidato y suite Python

Android se identifica como **0.1.1/versionCode 2** e iOS como **0.1.1/build 2**. Ambos son candidatos locales no entregados.

La primera ejecución limpia reveló que `test_navigation_http` esperaba un directorio estático `dist` previo al test runner. El fixture del test se ajustó en ambos stages para crear un directorio temporal cuando falta. Tras la corrección, la suite Python completa terminó con **1.174 pruebas y 87 subcasos en cada stage**. La corrección solo afecta al fixture de prueba; no cambia el runtime. Las 20 pruebas focales de ese arreglo también pasaron. Estos resultados son locales; no hay una nueva ejecución de CI asociada al candidato.

La validación final del código web y móvil compartido registró **43 pruebas web**, **7 móviles**, **0 errores y 0 avisos de Svelte**, TypeScript correcto y builds web/móvil correctos. Las fuentes compartidas son idénticas en ambos stages; estos resultados se aplican a su contenido actual. Todos los resultados anteriores son locales; no se atribuyen a CI.

Android se ensambló sin firma; su APK no forma parte de los stages ni de la publicación. Para iOS solo se completó la sincronización de Capacitor: no se ejecutó build Xcode, firma, distribución ni prueba física para este candidato.

## Datos, interfaz e integraciones

El candidato conserva esquema SQLite **17** y formato JSON **15**, con importación de formatos anteriores. Los códigos de diagnóstico se almacenan como valores crudos, junto con el origen y el momento de observación disponible. Semántica, disponibilidad en un vehículo, cobertura y relación con vigilancia siguen sin cualificar.

Las selecciones nuevas MQTT son optativas y empiezan desactivadas. Las pruebas de software no demuestran recepción por un broker o Home Assistant reales. Las correcciones de UX probadas con datos sintéticos cubren errores persistentes de formularios, conservación de borradores y foco, y reflujo móvil. Accesibilidad con tecnologías de asistencia, pérdida de red real y aceptación en teléfonos siguen pendientes.

### Monitor de metadatos

El monitor genérico se limita a metadatos de copias: estado, antigüedad, tamaño, permisos y espacio. Su prueba focal registró **29 pruebas y 45 subcasos**; esto no valida el contenido de una copia, una restauración ni la entrega de avisos. La guía autocontenida está en [deploy/community/README.md](../deploy/community/README.md). Esta publicación no documenta el estado de una instalación particular.

## Fases, puertas y aceptación

El registro estructurado conserva **nueve fases** y **siete puertas reales pendientes**. La aceptación física y la finalización del objetivo permanecen abiertas. El estado por fase, las versiones y los criterios están en [DELIVERY-STATUS.json](DELIVERY-STATUS.json).

El historial de aceptación conserva observaciones humanas acotadas de instalación PWA, funcionamiento offline y recepción de avisos en dispositivos concretos, incluida una notificación recibida con un teléfono bloqueado. No demuestra el candidato actual, compatibilidad universal, entrega general de avisos, autenticación tras reinicio, app/widget nativos, conexión del vehículo ni aceptación final.
