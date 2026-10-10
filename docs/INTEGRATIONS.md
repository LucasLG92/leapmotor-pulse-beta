# Integraciones

Las integraciones se activan explícitamente. La instalación local deshabilita todas las conexiones externas opcionales.

| Integración | Función | Estado inicial |
| --- | --- | --- |
| evcc | Lecturas de un punto de carga, sesiones cerradas e histórico opcional | Desactivada |
| MQTT | Lecturas seleccionadas con TLS y publicación de discovery tras configurarlo | Desactivada |
| PVPC de REData | Precios públicos para estimaciones de coste | Solo cuando se solicita una cotización |
| Mapas | Búsqueda, cartografía y cargadores mediante servicios configurados | Desactivada |
| ABRP | Envío limitado de SOC reciente con claves y consentimiento explícitos | Desactivada |
| Web Push | Avisos con claves VAPID, clave de almacenamiento y contacto explícitos | Desactivada |
| MATE-API | Lecturas B10 a través de un cliente y callbacks autorizados aportados por el operador | Sin configurar |

Cada integración puede comunicar datos a su endpoint o proveedor. Revisa el destino, las credenciales y los datos transmitidos antes de activarla. No añadas claves al repositorio ni a un `.env` público. La configuración remota y las credenciales deben gestionarse en el entorno privado del operador, fuera del instalador de beta.

Los conectores descritos son adaptadores y contratos de software. Una respuesta válida no demuestra compatibilidad física ni autoriza comandos al vehículo.
