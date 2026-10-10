# Leapmotor Pulse · beta

Tu coche y tus cargas, en una interfaz sencilla que alojas en tu propio servidor.

**Beta temprana, independiente y no oficial.** Puedes instalar Pulse para probar su interfaz, usar registros personales y preparar tus integraciones. La conexión real al B10, los comandos del vehículo y el funcionamiento con distintos cargadores aún necesitan validación física. No se promete compatibilidad con todos los modelos ni una conexión al coche con un solo clic.

## Apariencia del B10

El Resumen incorpora una ilustración original del B10 y temas Claro/Charcoal. En **Ajustes → Color del B10** puedes elegir entre los seis colores de la gama española y guardar la selección, incluso sin conectar el coche. [Guía de apariencia y actualización](docs/VEHICLE-APPEARANCE.md).

La versión actual usa SQLite esquema 15 y exportación JSON 13. Restaura copias JSON 1–12 asignando el color a `null`. Para volver al binario con esquema 14 hay que restaurar su imagen **junto con** la copia SQLite anterior. Sigue siendo beta; QA sintética y builds no sustituyen las comprobaciones con teléfonos, coche o cargador.

## Qué ofrece

| Capacidad | Estado de la beta |
| --- | --- |
| Interfaz adaptable, temas y PWA instalable | Implementada; instalación, recuperación offline y avisos con teléfonos bloqueados probados en iPhone y Android. No equivale a compatibilidad con todos los dispositivos. |
| Preparación de funciones y próximos pasos | Ajustes detecta las habilitaciones del servidor y señala qué pruebas reales faltan. [Guía de preparación](docs/SETUP.md). |
| Registro personal de cargas y análisis | Disponible sin coche conectado. Los datos de demostración están identificados y separados. |
| Precios PVPC oficiales para la Península | Integrados. El coste energético se calcula cuando hay precios completos; no incluye potencia fija ni impuestos. |
| Sesiones automáticas con evcc | Integración opcional: importa inicio, fin y energía. Pendiente de validación con cargadores reales. |
| Datos del B10 y renovación de sesión | Adaptador de lectura preparado. Requiere material y callbacks legítimos del operador; no hay asistente público de conexión al coche. |
| Home Assistant/MQTT, ABRP y navegación | Opcionales, apagados por defecto. Requieren configuración y pruebas con los servicios del usuario. |
| App Android, iOS y widgets | Código incluido. Cada instalación necesita su configuración OAuth, firma/asociación y pruebas; los widgets no se crean directamente desde la web. |
| Controles del vehículo y modo centinela | Bloqueados o en estudio. No hay grabación de cámaras ni protección centinela operativa. |

## Instalar

En un servidor **Linux con Docker Engine y Docker Compose v2** ya instalados:

```sh
git clone https://github.com/LucasLG92/leapmotor-pulse-beta.git
cd leapmotor-pulse-beta
bash install.sh --check
bash install.sh --install
```

La instalación inicial queda en `http://127.0.0.1:8780`, accesible solo en el equipo donde se ejecuta. Construye la imagen desde las fuentes fijadas y conserva los datos en un volumen propio. No configura tu coche ni publica un puerto en Internet.

[Instalación paso a paso y acceso desde un móvil](docs/INSTALL.md) · [HTTPS y autenticación](docs/HOSTING.md) · [Estado de la beta](docs/BETA.md)

## Puntos fuertes

Pulse prioriza estas decisiones de producto, para que puedas compararlas con las aplicaciones que utilizas:

- **Servidor propio y control sobre tus datos.** SQLite y copias exportables, sin cuentas compartidas ni credenciales incluidas en la distribución.
- **Menos pasos al registrar una carga.** Con evcc configurado, las sesiones cerradas aportan inicio, fin y kWh; PVPC se calcula en segundo plano cuando dispone de precios completos.
- **Costes transparentes.** Distingue energía medida, reparto estimado, precios pendientes y registros manuales. Un coste estimado no se presenta como factura exacta.
- **Lecturas reutilizadas.** La interfaz consulta el estado guardado; abrir pantallas no dispara otra consulta al fabricante. Las integraciones aprovechan esas observaciones con caducidad.
- **Móvil y privacidad.** PWA, avisos genéricos, perfiles de widget con campos seleccionados, privacidad ocultada por defecto y caché opcional con vencimiento.
- **Base técnica contenida.** Svelte 5, TypeScript, FastAPI y SQLite; integraciones apagadas hasta configurarlas y dependencias fijadas para builds reproducibles.

Son características del proyecto, no una clasificación de superioridad: no se han realizado benchmarks comparativos con otras aplicaciones.

## Documentación

- [Arquitectura](docs/ARCHITECTURE.md) y [desarrollo](docs/DEVELOPMENT.md).
- [Energía, PVPC y evcc](docs/ENERGY.md).
- [Conexión al coche y límites](docs/VEHICLE.md).
- [PWA, avisos, apps y widgets](docs/MOBILE.md).
- [Integraciones opcionales](docs/INTEGRATIONS.md).
- [Privacidad](docs/PRIVACY.md) y [seguridad](SECURITY.md).
- [Preparación y comprobaciones pendientes](docs/SETUP.md).
- [Contrato OpenAPI](docs/openapi.json).

## Contribuir y licencia

Para informar de un fallo, indica versión, sistema, pasos y resultado esperado. Evita publicar VIN, ubicaciones, credenciales, certificados, tokens, logs completos o exportaciones personales. [Guía para contribuir](CONTRIBUTING.md).

El código original se publica bajo [MIT](LICENSE), con autoría de LucasLG92. Las dependencias mantienen sus [licencias y avisos](THIRD-PARTY-NOTICES.md). Leapmotor y las demás marcas pertenecen a sus titulares; este proyecto no está afiliado al fabricante ni ofrece garantía de compatibilidad.
