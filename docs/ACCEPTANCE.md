# Registro de aceptación y alcance

El estado del objetivo, las nueve fases y las siete puertas reales está en [DELIVERY-STATUS.json](DELIVERY-STATUS.json). La aceptación final permanece pendiente; completar software, builds o verificaciones estructurales no permite inferirla.

## Candidato local, 12 de octubre de 2026

Android 0.1.1/versionCode 2 e iOS 0.1.1/build 2 no se han entregado. La suite Python final pasó localmente en ambos stages: 1.174 pruebas y 87 subcasos. La primera ejecución limpia encontró una dependencia de `test_navigation_http` respecto a un `dist` generado antes de pytest; se corrigió únicamente el fixture para usar un directorio temporal, pasaron las pruebas focales y luego la suite completa. No es una nueva ejecución de CI.

La validación final del código web/móvil compartido pasó 43 pruebas web y 7 móviles; Svelte informó 0 errores y 0 avisos, TypeScript y builds web/móvil pasaron. Las fuentes compartidas son idénticas en ambos stages. Android se ensambló sin firma; iOS solo completó sincronización de Capacitor. No hay build Xcode, firma, distribución o aceptación física para este candidato. Los resultados y límites se resumen en [VALIDATION.md](VALIDATION.md).

El diagnóstico pasivo usa SQLite 17 y exportación JSON 15 para conservar códigos crudos, origen y momento de observación. No asigna significado físico, no activa avisos ni demuestra vigilancia o acceso a cámaras. La selección MQTT se mantiene desactivada por defecto.

## Aceptación humana histórica

El historial conserva confirmaciones humanas acotadas de instalación PWA, uso offline y recepción de avisos en dispositivos iOS y Android concretos. También registra una notificación recibida con el teléfono bloqueado. Son observaciones reportadas de interacciones anteriores, no una inspección automatizada. No prueban latencia, compatibilidad universal, el candidato local actual, app/widgets nativos, autenticación tras reinicio, conexión al vehículo ni aceptación del producto completo.

## Criterios pendientes

Las siete puertas originales siguen abiertas: login negativo con otra cuenta; recuperación integral; aceptación móvil y nativa; vehículo/cargador y aceptación de la entrega; seguridad y dependencias; sesión autenticada y push tras reinicio; rendimiento móvil y uso habitual. Los detalles normativos se conservan en el registro estructurado.
