# Apariencia del B10

En **Ajustes → Vehículo y energía → Color del B10**, elige un color y pulsa **Guardar ajustes**. La elección se guarda en el servidor y se aplica al Resumen y a la vista previa de Ajustes. No necesitas conectar el coche. El nombre y el acabado también se pueden editar; Pulse no los deduce ni cambia ajustes del vehículo.

La [gama española del Leapmotor B10](https://www.leapmotor.net/es-es/modelos/b10) consultada el 10 de octubre de 2026 ofrece:

| Color | Valor del contrato |
| --- | --- |
| Azul Noche Estrellada | `starry_night_blue` |
| Púrpura Medianoche | `midnight_purple` |
| Gris Tundra | `tundra_grey` |
| Negro Metálico | `metallic_black` |
| Plata Estelar | `stellar_silver` |
| Blanco Lunar | `lunar_white` |

**Sin elegir** conserva el campo en `null` y muestra una representación plateada orientativa. Las ilustraciones de Pulse son originales, generadas para este proyecto; no son fotografías oficiales ni una reproducción exacta del acabado. Las seis imágenes WebP suman aproximadamente 212 KiB. No contienen fotos personales ni información del coche.

La interfaz ofrece Claro, Charcoal y el tema del dispositivo. Solo la preferencia de tema se guarda en el navegador; el color del vehículo queda en SQLite. La caché de la PWA contiene activos genéricos, nunca respuestas privadas de la API.

## Actualización y copias

Esta versión migra SQLite de esquema 14 a 15 añadiendo `vehicle_color` opcional. Conserva las 24 tablas existentes. Las exportaciones pasan a formato JSON 13; se pueden restaurar formatos 1–12, asignando el color a `null`. Una actualización parcial que omite el color conserva la elección previa; enviar `null` la borra.

Antes de actualizar, conserva una copia SQLite coherente y la imagen anterior. Para volver a una versión con esquema 14, restaura **esa imagen y su copia SQLite 14 conjuntamente**; el binario anterior no acepta una base migrada a 15. El instalador y los secretos mantienen sus ubicaciones habituales.

## Alcance comprobado

Las pruebas de persistencia, migración, importación/exportación y API pasan. La QA de Chromium comprobó los seis colores, teclado, guardado y recarga, aviso de cambios sin guardar, temas y tamaños móviles de 393 y 320 px. El funcionamiento en teléfonos, lectores de pantalla y el consumo real requieren comprobación física. El rediseño no cualifica comandos ni lecturas del vehículo.
