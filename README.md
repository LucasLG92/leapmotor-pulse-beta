# Leapmotor Pulse · beta

Aplicación independiente para explorar datos de movilidad y carga desde un servidor propio. Es una beta experimental y no oficial; la conexión a un vehículo, el control físico y la compatibilidad con cada dispositivo requieren comprobaciones separadas.

## Estado del candidato local

El incremento de interfaz y herramientas manuales del 13 de octubre es un corte **prepublicación** de software local, todavía no entregado ni desplegado. El objetivo global sigue bloqueado: permanecen nueve fases y siete puertas reales pendientes. Un recibo posterior debe registrar cualquier publicación; este corte no predice ese estado. La aceptación física y el cierre del producto no se infieren de una compilación o de datos sintéticos.

La interfaz presenta el coche y sus lecturas con procedencia visible; Energía muestra registros comparables y abre el simulador solo a petición; Actividad organiza trayectos y mantenimiento en un diario; Mapa guarda destinos introducidos manualmente; Estados prioriza la cronología; Ajustes agrupa preferencias por propósito. El simulador es informativo y no programa cargas ni controla el vehículo.

En la cualificación local registrada para este incremento: 1.370 pruebas Python y 131 subcasos pasaron, se omitieron 34 pruebas por un SDK opcional no instalado, pasaron 65 pruebas web y 8 de puente móvil; Svelte informó 0 errores y 0 avisos, y los contratos/builds compartidos finalizaron correctamente. La revisión visual cubrió las seis pantallas a 1280×720 CSS en Claro y Charcoal, sin overflow horizontal ni errores de consola observados. También se comprobó la conservación del borrador del simulador al plegarlo y cancelar la salida, y la actualización desde una PWA local anterior. Estos recibos no acreditan dispositivos físicos, accesibilidad física, firma, distribución o servicios reales.

Android e iOS siguen como candidatos locales sin entregar. Las órdenes físicas permanecen bloqueadas; no se atribuyen sensores, GPS, cámaras o recepción de señales que no estén verificados. Consulta [dirección visual](docs/VISUAL-DIRECTION.md), [incremento](docs/EVOLUTION-20261013.md), [cualificación](docs/EVOLUTION-QUALIFICATION.json), [roadmap](docs/ROADMAP.md) y [estado estructurado](docs/DELIVERY-STATUS.json).

## Qué ofrece

| Capacidad | Estado |
| --- | --- |
| Interfaz web y PWA | Candidato de software con Claro/Charcoal, reflujo y estados de borrador/error; compatibilidad y aceptación física siguen pendientes. |
| Energía y carga | Registros manuales comparables por mes; simulador informativo bajo demanda, con huecos y supuestos explícitos. |
| Actividad y mantenimiento | Diario de trayectos manuales con notas/etiquetas e historial de planes por fecha/odómetro; no implica ingestión del coche. |
| Mapa | Favoritos de coordenadas aportadas por la persona y apertura explícita de aplicaciones externas; sin GPS ni ruta automática. |
| Estados y Ajustes | Historial cronológico y preferencias agrupadas por propósito. |
| Integraciones y datos del vehículo | Requieren configuración y cualificación específica; sus estados de demo, caducidad, procedencia y errores se mantienen diferenciados. |
| Apps y widgets | El código compartido no acredita firma, distribución, autenticación ni aceptación nativa. |
| Órdenes físicas, cámaras y centinela | Permanecen bloqueados o pendientes de demostrar capacidad y acceso autorizados. |

## Instalación local

En Linux con Docker Engine y Docker Compose v2 ya instalados:

```sh
git clone https://github.com/LucasLG92/leapmotor-pulse-beta.git
cd leapmotor-pulse-beta
bash install.sh --check
bash install.sh --install
```

La instalación local escucha en `127.0.0.1:8780` y no configura el vehículo ni publica un puerto en Internet. Consulta [instalación](docs/INSTALL.md), [alojamiento remoto](docs/HOSTING.md) y [despliegue comunitario](deploy/community/README.md) antes de adaptar el acceso a tu entorno.

## Documentación

- [Arquitectura](docs/ARCHITECTURE.md), [desarrollo](docs/DEVELOPMENT.md) y [estado de la beta](docs/BETA.md).
- [Energía y carga](docs/ENERGY.md), [lecturas del vehículo](docs/VEHICLE.md) e [integraciones](docs/INTEGRATIONS.md).
- [PWA, apps y widgets](docs/MOBILE.md), [privacidad](docs/PRIVACY.md) y [seguridad](docs/SECURITY-QUALIFICATION.json).
- [Validación y límites del candidato](docs/VALIDATION.md), [aceptación](docs/ACCEPTANCE.md) y [registro de entrega](docs/DELIVERY-STATUS.json).
- [Licencia](LICENSE) y [avisos de terceros](THIRD-PARTY-NOTICES.md).

No publiques credenciales, tokens, VIN, bases de datos personales, certificados ni paquetes configurados para una instalación concreta. Leapmotor y las demás marcas pertenecen a sus titulares; Pulse no es una aplicación oficial.
