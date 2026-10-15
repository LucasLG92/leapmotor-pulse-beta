# Leapmotor Pulse · beta

Aplicación independiente y experimental para consultar y organizar información de movilidad y carga desde un servidor propio. Pulse no es una aplicación oficial de Leapmotor. La ilustración del vehículo es orientativa; la conexión a un coche y sus controles físicos requieren validación específica.

| Escritorio | Móvil |
| --- | --- |
| <img src="docs/screenshots/01-coche-oscuro.jpg" alt="Pulse: Coche en escritorio, tema oscuro y datos de demostración" width="640"> | <img src="docs/screenshots/mobile/01-coche-oscuro.jpg" alt="Pulse: Coche con ancho móvil, tema oscuro y datos de demostración" width="220"> |

[Ver las seis secciones en escritorio y móvil, con los temas claro y oscuro de Coche](docs/SCREENSHOTS.md).

## Estado actual

La web y la PWA están entregadas. Lucas confirmó la primera fase de aceptación en iPhone y Android el 14 de octubre: interfaz, texto grande, teclado, lectores de pantalla, formularios, recuperación de red y recepción de exportaciones JSON. Esa aceptación no acredita distribución nativa ni compatibilidad con un coche.

La corrección de Energía entregada el 15 de octubre mantiene completa la etiqueta «Manual». Se comprobó en Chrome con datos sintéticos y texto al 200 %; falta aceptar este ajuste concreto en Safari físico. Esta revisión incorpora sus fuentes y la documentación de la fase 2; la publicación y la entrega operativa se registran por separado.

El piloto Android 0.1.1 (código 2) tiene comprobaciones humanas de retorno OAuth, preferencias, widget, recuperación de red, cierre de sesión y desvinculación. Faltan caducidad de caché y refresco autónomo. App y widget de iOS compilan; firma, instalación, OAuth y pruebas físicas de WidgetKit siguen pendientes. La telemetría B10 no está validada y las órdenes físicas permanecen bloqueadas.

## Capacidades y límites

| Área | Estado |
| --- | --- |
| Web y PWA | Interfaz entregada; ajuste de Energía comprobado en navegador sintético. Falta su aceptación en Safari físico. |
| Energía y cargas | Registro manual, historial y herramientas informativas. No programa cargas ni controla el coche. |
| Diario y mantenimiento | Trayectos y planes introducidos por la persona; no implica captura automática del vehículo. |
| Mapa | Favoritos manuales y apertura explícita de mapas externos. Sin GPS ni cálculo propio de rutas. El buscador privado está preparado, sin instalar. |
| Aplicaciones y widgets | Requieren firma, distribución y comprobaciones específicas en los teléfonos. |
| Vehículo, cámaras y centinela | Conexión B10 y controles físicos pendientes de validación. Cámaras y centinela permanecen como última fase. |

La [galería de capturas](docs/SCREENSHOTS.md) procede de una instancia local con datos de prueba, sin telemetría privada. La ilustración del coche es orientativa y las capturas no demuestran pruebas físicas en teléfonos o vehículos.

## Instalación local

En Linux con Docker Engine y Docker Compose v2 ya instalados:

```sh
git clone https://github.com/LucasLG92/leapmotor-pulse-beta.git
cd leapmotor-pulse-beta
bash install.sh --check
bash install.sh --install
```

La instalación local escucha en `127.0.0.1:8780`. No configura un vehículo ni publica un puerto en Internet. Consulta la [guía de instalación](docs/INSTALL.md), el [alojamiento remoto](docs/HOSTING.md) y el [despliegue comunitario](deploy/community/README.md) antes de adaptar el acceso a tu entorno.

## Documentación

- [Arquitectura](docs/ARCHITECTURE.md), [desarrollo](docs/DEVELOPMENT.md) y [estado beta](docs/BETA.md).
- [Energía y cargas](docs/ENERGY.md), [lecturas](docs/VEHICLE.md) e [integraciones](docs/INTEGRATIONS.md).
- [PWA, aplicaciones y widgets](docs/MOBILE.md), [privacidad](docs/PRIVACY.md) y [seguridad](docs/SECURITY-QUALIFICATION.json).
- [Validación](docs/VALIDATION.md), [aceptación](docs/ACCEPTANCE.md) y [registro de entregas](docs/DELIVERY-STATUS.json).
- [Revisión de confidencialidad](docs/PRIVACY-REVIEW-20261015.md).
- [Licencia](LICENSE) y [avisos de terceros](THIRD-PARTY-NOTICES.md).

No publiques credenciales, tokens, VIN, bases personales, certificados ni paquetes configurados para una instalación concreta. Las marcas pertenecen a sus propietarios; Pulse es una aplicación independiente y no oficial.
