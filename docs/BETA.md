# Estado de la beta pública

Leapmotor Pulse se publica como software experimental bajo licencia MIT. La beta permite revisar la interfaz y el código y evaluar una instalación local. No es un producto terminado ni está certificada para supervisar o controlar un vehículo.

La primera instalación se limita a Linux con Docker Engine nativo y Compose v2, escucha en `127.0.0.1:8780` y usa autenticación local. No conecta un B10, no configura cloud, login OIDC, push, comandos físicos ni la aplicación móvil. La guía de [instalación](INSTALL.md) describe ese alcance; los despliegues remotos se documentan por separado en [alojamiento](HOSTING.md).

La interfaz web, los puentes móviles y los adaptadores están implementados en el repositorio. Los builds de simulador y las pruebas automatizadas comprueban contratos de software, no una instalación completa en un iPhone, un Android, un coche o un cargador físico. El workflow iOS del repositorio es manual; las ejecuciones de CI del nuevo repositorio público deben revisarse antes de afirmar su estado.

La lectura B10 está preparada para un cliente SDK opcional y callbacks autorizados, pero la beta no tiene wizard de login ni conexión real. Los comandos físicos permanecen deshabilitados. No uses esta versión para tomar decisiones de seguridad o como sustituto de la información del vehículo, cargador o comercializadora.
