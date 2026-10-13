# Dirección visual de Pulse

La dirección combina el **cuadro de instrumentos personal del B10** para la lectura inmediata con el **cuaderno de viaje** para Actividad. Cada pantalla usa la composición que explica mejor su tarea; una cuadrícula de tarjetas no es el lenguaje por defecto.

## Jerarquía por pantalla

- **Coche:** vehículo y magnitudes protagonistas, con origen y momento de lectura visibles; banda de estado y acciones contextuales después.
- **Energía:** registros y comparación lideran. El simulador informativo queda plegado hasta que se solicita.
- **Actividad:** cronología en filas abiertas para trayectos y mantenimientos; el detalle se revela al abrir una entrada.
- **Mapa:** destinos guardados y acción externa explícita. Sin mapa, posición o ruta fingidos.
- **Estados:** línea temporal de fecha, cambio y procedencia en lugar de repetir tarjetas de estado.
- **Ajustes:** grupos nombrados por propósito y detalles técnicos disponibles a demanda.

## Ritmo, color e interacción

La tipografía de sistema, cifras tabulares cuando ayudan a comparar, separadores y espacio entre filas establecen jerarquía. Superficies cerradas se reservan para contenido que necesita contexto propio. Los temas Claro y Charcoal conservan contraste y significado consistente; verde comunica un estado favorable, ámbar atención/demo y rojo error.

El contenido refluye a una columna en móvil, respeta safe areas y permite que etiquetas y texto largos envuelvan. Tablas desplazan su propio contenedor. Foco de teclado visible, controles táctiles de al menos 48 px y `prefers-reduced-motion` evitan que la densidad visual limite el uso. No hay animación permanente ni fuentes, paquetes o assets remotos.

## Evidencia y límites

La entrega anterior se revisó a 1280×720 CSS en los dos temas y seis rutas, sin overflow horizontal ni errores/avisos de consola observados. Se comprobó que el simulador conserva el borrador al plegarse y al cancelar la salida, y un flujo de actualización desde una PWA local anterior. Esto no sustituye reflujo en teléfono, VoiceOver, aceptación física ni pruebas de servicio/vehículo reales. Ningún indicador decorativo afirma sensores, GPS, cámaras, centinela o capacidad física.

## Refinamiento cotidiano del 13 de octubre de 2026

La tesis evoluciona hacia **tu garaje personal, con calma y respuesta**. Las [referencias primarias adicionales](UI-REFERENCES-20261013.md) de Rivian, Tesla, Tibber, Oura y Wallet orientan jerarquía e interacción; los componentes y recursos de Pulse conservan su autoría y licencia.

Autonomía y batería preceden al escenario tonal del coche, los accesos frecuentes tienen un contorno táctil reconocible y la navegación móvil flota sobre el borde seguro. Diario, energía y lugares guardados combinan títulos, separadores, iconos y vacíos orientados al siguiente paso. Ajustes ofrece una vista previa de Claro, Charcoal y Sistema. El estado de carga de una sección dispone de reintento y restaura el foco si vuelve a fallar sin que el usuario haya movido el foco.

Las entradas y respuestas de controles duran 120–280 ms, sin actividad decorativa continua; reduced-motion elimina la animación ornamental. El simulador permanece montado al plegarse para conservar borradores. La actualización mantiene los contratos, cálculos, API y capacidades anteriores.

Esta iteración se inspeccionó en navegador a 709×682 CSS (área de contenido de 700 px) con datos sintéticos: las seis rutas sin desbordamiento horizontal, ambos temas, persistencia de apariencia, foco al navegar y cancelación de salida con borrador conservado. No acredita iPhone físico, VoiceOver, 320 px ni escala de texto; movimiento reducido y fallo repetido de importación se revisaron en código, sin simulación de dispositivo.
