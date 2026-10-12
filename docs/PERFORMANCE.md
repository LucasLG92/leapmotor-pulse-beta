# Rendimiento y límites de campo

## Medición histórica de activos web

Una medición local de una versión anterior de la PWA contó 21 recursos únicos: **359.221 bytes** en modo identity frente a **121.860 bytes** con gzip negociado, una reducción del **66,08 %** en el cuerpo transferido. Dieciocho respuestas se comprimieron y tres imágenes PNG permanecieron en identity. Los bytes se midieron en respuestas HTTP locales y se contrastaron con sus originales.

Ese resultado describe aquella versión y entorno de prueba. No es una medida de latencia, renderizado, TLS exterior, cacheo físico, Web Vitals, consumo móvil, datos del vehículo ni estado actual de producción. La implementación y negociación de activos se describen en [STATIC-ASSETS.md](STATIC-ASSETS.md).

## UX y límites medidos

Las revisiones sintéticas previas cubrieron formularios, cambios de ruta y actualización explícita de PWA con viewports móviles. Los resultados actuales de software se resumen en [VALIDATION.md](VALIDATION.md). Los fixtures no representan latencia de red real ni uso prolongado en un teléfono.

## Criterios pendientes

La puerta de rendimiento y uso habitual sigue abierta. Falta cualificar con datos de campo el tiempo de respuesta, sesiones SSE e integraciones activas, Web Vitals, memoria/batería, accesibilidad, teléfonos reales y efectos de conectividad. Las pruebas de reposo con API sintética o integraciones apagadas no demuestran esos criterios; los datos del vehículo y del cargador requieren cualificación independiente.
