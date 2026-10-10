# Arquitectura

Leapmotor Pulse combina una interfaz web, una API y una base SQLite local. La interfaz se construye con Svelte 5, TypeScript y Vite; FastAPI sirve los archivos resultantes y expone la API. El código de servidor vive en `pulse/`, la web en `apps/web/` y el cliente nativo en `apps/mobile/`.

El contenedor ejecuta un proceso Pulse y guarda la base en `/data/pulse.sqlite3`. La instalación local usa un volumen Docker propio del proyecto. El Compose beta emplea red de host y fija el servidor a `127.0.0.1:8780`, para que el modo de autenticación local solo acepte el mismo equipo.

Las lecturas externas pasan por adaptadores acotados. Sus resultados se normalizan antes de entrar en el estado persistente; una lectura reciente indica qué devolvió una fuente y cuándo, no demuestra por sí sola el estado físico del vehículo o el cargador. Los conectores opcionales permanecen desactivados en la beta local.

Las peticiones de la interfaz usan la misma ruta de origen. La API valida sesión, origen y protección CSRF para las operaciones que modifican datos. El modo remoto es una configuración diferente: requiere HTTPS y OIDC y debe seguir [la guía de alojamiento](HOSTING.md).

La aplicación móvil reutiliza la interfaz web mediante Capacitor y añade un puente nativo para autenticación, lecturas y widgets. El cliente móvil no hace que la instalación local sea accesible desde una red: sus funciones remotas dependen de un servidor configurado con HTTPS y autenticación OIDC.
