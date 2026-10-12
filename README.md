# Leapmotor Pulse · beta

Aplicación independiente para explorar datos de movilidad y carga desde un servidor propio. Es una beta experimental y no oficial; la conexión a un vehículo, el control físico y la compatibilidad con cada dispositivo requieren comprobaciones separadas.

## Estado del candidato local

El candidato identifica Android como versión **0.1.1 (versionCode 2)** e iOS como **0.1.1 (build 2)**. Ambos siguen siendo candidatos locales sin entregar. En el checkout limpio de publicación, la suite Python pasó en ambos stages: **1.174 pruebas y 87 subcasos**. La validación web pasó **43 pruebas**, la móvil compartida **7**, Svelte registró **0 errores y 0 avisos**, y TypeScript y los builds web/móvil terminaron correctamente. Son resultados locales, no una ejecución nueva de CI.

La primera ejecución limpia mostró que `test_navigation_http` suponía que `dist` existía antes de pytest. Se corrigió solo el fixture de prueba para crear ese directorio temporal; pasaron las pruebas focales y la suite completa posterior pasó en ambos stages. No se alteró el runtime para resolver esa dependencia del test.

Android se ensambló sin firma. Para iOS solo se completó la sincronización de Capacitor; no hay build Xcode ni verificación de firma o distribución para este candidato. No se incluyen APKs, archivos de configuración de una instalación concreta, logs externos ni recibos de servidor.

El diagnóstico pasivo conserva esquema SQLite **17** y formato de exportación JSON **15**. Guarda códigos recibidos junto con su origen y momento de observación cuando están disponibles, sin asignarles significado físico. Las selecciones MQTT son optativas y comienzan desactivadas. Las pruebas de interfaz y de integraciones usan datos sintéticos.

El proyecto mantiene **nueve fases** de trabajo y **siete puertas reales pendientes**. Builds, pruebas sintéticas y datos de simulador no acreditan por sí solos aceptación física ni el objetivo completo. Consulta [validación del candidato](docs/VALIDATION.md), [registro de aceptación](docs/ACCEPTANCE.md) y [estado estructurado](docs/DELIVERY-STATUS.json).

## Qué ofrece

| Capacidad | Estado |
| --- | --- |
| Interfaz web y PWA | La interfaz y el modo local están implementados; compatibilidad y aceptación se documentan por candidato y dispositivo. |
| Registros de energía | Admite registros personales y estimaciones PVPC; los importes estimados no son facturas ni medidas certificadas. |
| Sesiones de evcc | Integración optativa; necesita configuración y validación con el cargador correspondiente. |
| Datos del vehículo | Adaptador de lectura preparado; el login, las señales del B10 y su frescura siguen pendientes de cualificación. |
| Home Assistant/MQTT, ABRP y mapas | Integraciones optativas; no se activan sin elección y configuración explícitas. |
| Apps y widgets | Hay código Android/iOS; la firma, distribución, autenticación y aceptación nativa siguen pendientes para este candidato. |
| Órdenes físicas, cámaras y centinela | Las órdenes permanecen bloqueadas; cámaras y centinela requieren demostrar primero capacidad y acceso autorizados. |

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
