# Instalación local de Leapmotor Pulse beta

Esta es una beta pública bajo licencia MIT. Está pensada para evaluación local en un equipo con Linux, Docker Engine nativo y Docker Compose v2 ya instalados. El instalador no instala dependencias del sistema ni requiere `sudo`.

El instalador aplica al contenedor un límite de 256 MiB de memoria, 0,5 CPU y 64 procesos.

## Instalar

Desde la raíz del repositorio:

```sh
bash install.sh --check
bash install.sh --install
```

Abre [http://localhost:8780](http://localhost:8780) en el navegador del mismo equipo. El servicio escucha solo en loopback. Esta instalación usa autenticación local para ese equipo y un volumen Docker propio del proyecto para guardar los datos. `--install` espera hasta 90 segundos a que la comprobación de salud responda antes de informar que está disponible.

Para detener el servicio sin borrar los datos:

```sh
bash install.sh --stop
```

`--check` solo hace comprobaciones de plataforma, daemon y configuración Compose. `--install` construye la imagen desde este repositorio y arranca únicamente el servicio Pulse. No descarga configuración de operador, crea un `.env`, ni detiene otros servicios Docker. La imagen y el volumen usan nombres propios de la beta pública. El instalador no elimina volúmenes ni limpia imágenes.

## Antes de actualizar

Antes de actualizar el código o reconstruir la imagen, haz una copia de tus propios datos desde la aplicación y guárdala fuera del volumen Docker. La beta no ofrece recuperación de datos ni garantiza compatibilidad entre versiones. No borres el volumen `leapmotor-pulse-beta-public_pulse-data` si quieres conservar la base local. No introduzcas datos sensibles en una instalación experimental.

## Límites de esta beta

- Esta instalación no configura cloud, OIDC, la aplicación móvil, push ni comandos del vehículo. La lectura B10 que aparece en el proyecto está preparada para desarrollo: no inicia sesión real ni incluye un asistente de configuración de cuenta.
- No expongas el puerto 8780 a la red local o a Internet. `localhost` solo sirve en el equipo donde corre Docker. Acceso desde móvil requiere una configuración separada con HTTPS y OIDC; no basta con cambiar la dirección de escucha.
- Para una configuración remota con HTTPS y OIDC, consulta [la guía de alojamiento](HOSTING.md). Requiere configurar manualmente dominio, TLS e identidad; el instalador local no la activa.
- Este instalador solo admite Linux con Docker Engine local. Docker Desktop sobre macOS o Windows no ofrece aquí las mismas garantías de red de host, por lo que el instalador lo rechaza en lugar de afirmar compatibilidad.
- La beta se ofrece para evaluación. No es un producto terminado ni se garantiza que controle, supervise o se comunique con un vehículo o cargador físico.

Para opciones y comprobaciones:

```sh
bash install.sh --help
```
