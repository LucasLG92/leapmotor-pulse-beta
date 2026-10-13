# Sincronización y consultas: incremento local, 14 de octubre de 2026

El refresco del estado agrupa consultas simultáneas, registra los eventos inmediatamente y reconcilia cuando cambian los datos durante una lectura. Ignora respuestas de otra sesión, se invalida antes de mutaciones/restauración y tiene un plazo de 30 segundos para liberar lecturas que no terminan. La lógica se separa en `state-refresh.ts`.

Diario y Perfil mantienen sus formularios montados, pero consultan únicamente la pestaña activa. Al cambiar de pestaña cancelan o invalidan las lecturas pendientes y recuperan datos al volver; perder conexión sigue eliminando resultados donde lo exigía el contrato. Los widgets pertenecen a Datos y los avisos a Preferencias.

La ingesta del cargador compara las sesiones recibidas y las evicciones de retención, sin materializar dos veces la tabla completa. El documento de trayectos obtiene resumen y detalle de una lectura limitada a 10.001 filas, conserva el máximo de 10.000, propietario, fechas y agregados. El manejador HTTP conserva Allow y desafíos de autenticación con cuerpo genérico; el middleware mantiene la política de caché de cada ruta.

La cancelación nativa es opcional y se anuncia por status. Los clientes antiguos conservan la espera local. Los clientes nuevos identifican lecturas y cancelan su transporte Android/iOS; las mutaciones y la exportación nativa no se vuelven cancelables ni se reenvían. Cancelaciones anticipadas y solicitudes activas tienen límites y limpieza.

## Validación local

- Backend: 1.373 pruebas y 131 subcasos correctos; 34 omisiones de SDK opcional.
- Web: 74 pruebas correctas; Svelte sin errores ni avisos y build correcto.
- Navegador: cuatro flujos contra un servidor efímero y base sintética: pestañas/borradores, consultas de Perfil, respuesta perdida sin duplicar el trayecto y evento durante una lectura lenta. El service worker se bloquea en este contexto de pruebas; esto no acepta una PWA instalada.
- Móvil compartido: tipado, nueve pruebas y build correctos.
- Android: compilación Debug y 104 pruebas JVM correctas tras sync; sin instalación física.
- iOS: estructura verificada; compilación Swift/Xcode y dispositivos pendientes.
- Dependencias: seis locks, 757 entradas, sin avisos conocidos en el análisis acotado. Playwright 1.62.1 se añade solo como dependencia de desarrollo para flujos de navegador y CI.
- Contrato OpenAPI idéntico. Sin migración de esquema ni cambios de diseño/estilos.
- Revisión independiente: carrera del debounce corregida y comprobada; no quedan P1/P2 confirmados en el alcance revisado.

## Entrega y límites

Este documento describe fuentes y comprobaciones locales. Publicación, CI externa, despliegue y nuevos APK/IPA no se acreditan con este incremento. Los resultados anteriores conservan su fecha y alcance. No cierra fases, puertas reales, coche/cargador, widgets, firma, accesibilidad, rendimiento o batería físicos. Centinela/cámaras permanece al final.

Los conteos SQL muestran una consulta de trayectos por documento frente a tres anteriores y eliminación de las dos lecturas globales de sesiones del cargador. No son una medición de latencia o consumo. Evidencia local y baseline de recuperación en `evidence/optimization-20261014` del workspace, sin datos operativos.
