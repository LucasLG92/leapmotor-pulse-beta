# Privacidad

En la instalación local, Pulse guarda los datos de la aplicación en SQLite dentro del volumen Docker del proyecto. El volumen persiste cuando se detiene o reconstruye el contenedor. Usa la exportación de Ajustes para crear y restaurar copias JSON; conserva esas copias en un lugar privado.

El modo local está pensado para el mismo equipo y rechaza las peticiones API remotas. No cambies la dirección de escucha para compartir la instalación. El acceso remoto requiere HTTPS, OIDC y una revisión de despliegue; consulta [la guía de alojamiento](HOSTING.md).

Las integraciones opcionales pueden transmitir al destino elegido lecturas del vehículo, estado y sesiones del cargador, solicitudes a proveedores de mapas, datos de carga a ABRP o suscripciones a servicios push. Activa solo las que comprendas y consulta las políticas de cada proveedor. La beta no activa esas integraciones por defecto.

Las pantallas de demostración son sintéticas y no reflejan una vigilancia real. El historial de protección compara estados recibidos; no confirma que una orden se haya ejecutado. Pulse no ofrece cámaras, centinela ni control físico del vehículo.

No compartas copias de seguridad, tokens, claves, sesiones, VIN ni capturas que contengan datos personales. La beta no promete retención, disponibilidad ni recuperación de datos.
