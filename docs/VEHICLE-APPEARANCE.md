# Apariencia del B10

En **Ajustes → Tu B10 y tu tarifa → Color del B10**, elige un color y pulsa **Guardar ajustes**. La elección se guarda en el servidor y se aplica a Coche y a la vista previa de Ajustes. No necesitas conectar el coche. El nombre y el acabado también se pueden editar; Pulse no los deduce ni cambia ajustes del vehículo.

La [gama española del Leapmotor B10](https://www.leapmotor.net/es-es/modelos/b10) consultada el 10 de octubre de 2026 ofrece:

| Color | Valor del contrato |
| --- | --- |
| Azul Noche Estrellada | `starry_night_blue` |
| Púrpura Medianoche | `midnight_purple` |
| Gris Tundra | `tundra_grey` |
| Negro Metálico | `metallic_black` |
| Plata Estelar | `stellar_silver` |
| Blanco Lunar | `lunar_white` |

**Sin elegir** conserva el campo en `null` y muestra una representación plateada orientativa. Las ilustraciones de Pulse son originales, generadas para este proyecto; no son fotografías oficiales ni una reproducción exacta del acabado. Las seis imágenes WebP suman aproximadamente 354 KiB. No contienen fotos personales ni información del coche.

La interfaz ofrece Claro, Charcoal y el tema del dispositivo. Solo la preferencia de tema se guarda en el navegador; el color del vehículo queda en SQLite. La caché de la PWA contiene activos genéricos, nunca respuestas privadas de la API.

## Actualización y copias

Esta versión migra SQLite de esquema 14 a 15 añadiendo `vehicle_color` opcional. Conserva las 24 tablas existentes. Las exportaciones pasan a formato JSON 13; se pueden restaurar formatos 1–12, asignando el color a `null`. Una actualización parcial que omite el color conserva la elección previa; enviar `null` la borra.

Antes de actualizar, conserva una copia SQLite coherente y la imagen anterior. Para volver a una versión con esquema 14, restaura **esa imagen y su copia SQLite 14 conjuntamente**; el binario anterior no acepta una base migrada a 15. El instalador y los secretos mantienen sus ubicaciones habituales.

## Alcance comprobado

Las pruebas de persistencia, migración, importación/exportación y API pasan. La QA de Chromium comprobó los seis colores, teclado, guardado y recarga, aviso de cambios sin guardar, temas y tamaños móviles de 393 y 320 px. El funcionamiento en teléfonos, lectores de pantalla y el consumo real requieren comprobación física. El rediseño no cualifica comandos ni lecturas del vehículo.

## Experiencia del vehículo

La interfaz organiza Coche, Energía, Mapa, Actividad, Estados y Ajustes. La navegación móvil mantiene cuatro destinos estables; el perfil abre Ajustes y el escudo abre el historial de estados. El B10 se presenta con CGI original de tres cuartos, claro/Charcoal y los seis colores. El nombre y modelo describen el perfil configurado, no una identidad acreditada por el fabricante.

La portada distingue autonomía, batería, odómetro y estados disponibles, con fecha y fuente. Los detalles conservan recepción/procedencia por campo y vigencia física desconocida. Los valores ausentes no se convierten en cero. El estado de carga recibido del vehículo y la observación del cargador tienen fuentes distintas; no se inventa una potencia, coste o final de sesión del coche.

Energía conserva sesiones, desglose y cálculo PVPC, registros manuales, filtros, curvas e historial. Actividad conserva trayectos, tareas y acciones; Estados conserva cambios entre lecturas y su procedencia. Ajustes conserva perfil, punto de carga habitual, precio, integraciones, avisos, widgets y copias. El rediseño no habilita órdenes físicas ni un modo centinela.

Esta revisión supera la validación local y la revisión independiente de sus correcciones materiales. Las pruebas de versiones anteriores siguen siendo evidencia histórica, no una comprobación de esta UI en un teléfono o vehículo físico.
