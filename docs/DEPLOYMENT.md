# Despliegue

Esta guía cubre un despliegue genérico. Adapta red, proxy, autenticación, almacenamiento y supervisión a tu entorno; no copies configuración operativa o credenciales de otra instalación.

## Modo local

La instalación comunitaria usa Docker Compose en Linux y queda limitada a loopback (`127.0.0.1:8780`). Ejecuta primero `bash install.sh --check` y después `bash install.sh --install`. El instalador conserva su ciclo de comprobación e instalación documentado en [INSTALL.md](INSTALL.md). El modo local no ofrece acceso remoto ni integra un vehículo.

## Acceso remoto

Para acceso desde otros dispositivos, configura HTTPS y autenticación en un proxy o proveedor compatible. Asegúrate de que Pulse confíe únicamente en la identidad validada por el proxy y de que no se pueda alcanzar la API por una ruta anónima alternativa. No publiques puertos ni desactives TLS o autenticación para simplificar la instalación.

Guarda secretos y valores específicos del operador fuera del repositorio, del archivo de configuración de ejemplo y de las imágenes. Define respaldos del volumen y prueba su restauración en un entorno aislado antes de depender de ellos. La guía [community](../deploy/community/README.md) contiene ejemplos genéricos; no describe una instalación concreta.

## Aceptación del despliegue

Antes de usar datos reales, valida por separado arranque, readiness, autenticación, renovación de sesión, actualización y recuperación. Un health check o un monitor que solo revisa metadatos no demuestra integridad de copia, restauración, disponibilidad del proveedor ni aceptación física.
