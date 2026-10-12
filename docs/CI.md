# CI y comprobaciones

Los workflows versionados definen comprobaciones para cambios futuros. Para el candidato local descrito aquí no se atribuye una ejecución nueva de CI.

La suite Python final se ejecutó localmente en los stages público y privado y pasó con **1.174 pruebas y 87 subcasos en cada uno**. Una ejecución limpia inicial mostró que `test_navigation_http` dependía de un `dist` previo; el fixture se ajustó para generar un directorio temporal, pasaron las comprobaciones focales y la suite completa posterior. El cambio es solo de prueba.

La validación final del código web/móvil compartido pasó **43 pruebas web**, **7 móviles**, Svelte con **0 errores y 0 avisos**, TypeScript y builds web/móvil. Las fuentes de ambos stages son idénticas. Todos esos resultados son locales; no se presentan como CI. Android fue ensamblado sin firma; iOS solo completó la sincronización de Capacitor, sin Xcode ni firma.

Las ejecuciones históricas corresponden a otras revisiones y conservan su alcance. Consulta el workflow y el commit exacto antes de atribuir un resultado a una versión. Pruebas sintéticas o de simulador no demuestran funcionamiento de un servicio, teléfono, vehículo o cargador real.
