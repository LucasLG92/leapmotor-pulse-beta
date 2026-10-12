# Arquitectura

Leapmotor Pulse combina una interfaz web, una API y una base SQLite local. La interfaz se construye con Svelte 5, TypeScript y Vite; FastAPI sirve los archivos y expone la API. El cliente móvil comparte la interfaz mediante Capacitor y añade extensiones nativas.

El contenedor guarda la base en un volumen de datos propio. Las lecturas externas pasan por adaptadores acotados y se normalizan antes de entrar al estado persistente. Una lectura reciente describe lo que devolvió una fuente y cuándo; no demuestra por sí sola el estado físico del vehículo o cargador.

El candidato local documentado conserva esquema SQLite 17 y exportación JSON 15. El diagnóstico de fase final mantiene códigos crudos, fuente y tiempo de observación cuando están disponibles, sin interpretarlos como intrusión, vigilancia o activación física. La guía de migración y aceptación conserva los criterios previos.

Las peticiones de interfaz usan el mismo origen. La API valida sesión, origen y protección CSRF en las operaciones que modifican datos. El modo local se limita a loopback; el modo remoto necesita HTTPS y autenticación apropiada. Las integraciones opcionales requieren elección y configuración explícitas.

Las órdenes físicas permanecen bloqueadas mientras sus permisos, precondiciones y efectos no estén cualificados. El modo demo usa datos sintéticos separados de los datos reales.
