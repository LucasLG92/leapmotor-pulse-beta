# Incremento local: cuadro de instrumentos y cuaderno de viaje — 13 de octubre de 2026

Este candidato combina un cuadro de instrumentos para la consulta cotidiana del B10 con un cuaderno de viaje en Actividad. Es código original de Pulse y conserva las capas de procedencia, privacidad, caducidad, modo demo y error existentes.

## Cambios funcionales

- **Energía:** los registros manuales se comparan por mes, con energía/coste conocidos, incompletos y unidades comparables. El simulador se abre a petición, explicita tarifa, eficiencia, huecos y energía insuficiente, y no programa cargas ni controla el coche o el cargador. Al plegarlo o cancelar la salida se conserva el borrador.
- **Actividad:** trayectos manuales en cronología, con notas, etiquetas, búsqueda/filtro, detalle desplegable, edición protegida por revisión e informes/exportación web. Los planes de mantenimiento admiten referencia manual de fecha/odómetro, estado próximo/vencido/desconocido, coste opcional y un historial de realizaciones. No se presentan como lecturas del vehículo.
- **Mapa:** favoritos privados con nombre y coordenadas introducidos explícitamente; el enlace a una aplicación de mapas externa se abre solo cuando la persona lo pulsa. No hay GPS, localización del coche ni mapa/recorrido simulado.
- **Estados:** historial cronológico de cambios, fecha y procedencia, manteniendo demo, valores desconocidos, expiración y errores distinguibles.
- **Ajustes:** preferencias agrupadas por propósito, con detalles técnicos accesibles cuando hacen falta.
- **Coche:** el vehículo y las magnitudes observadas o demo ocupan la jerarquía principal; fuente y momento acompañan las lecturas. No se infiere salud de batería, cámaras, centinela ni control físico.

## Interacción y límites

Se conservan acciones, campos, coerción de texto, valores desconocidos, procedencia, demo, privacidad, estados vencidos, conexión desactivada, errores persistentes, borradores, revisiones y respuestas tardías. Las entradas nuevas mantienen sus flujos de error, foco y guardas de salida. La UI no convierte un dato incompleto en una observación ni activa servicios externos.

El detalle de los recibos locales y lo todavía pendiente está en [cualificación](EVOLUTION-QUALIFICATION.json), [dirección visual](VISUAL-DIRECTION.md), [roadmap](ROADMAP.md) y [estado de entrega](DELIVERY-STATUS.json). Este incremento no declara publicación, despliegue, aceptación física ni finalización del producto.
