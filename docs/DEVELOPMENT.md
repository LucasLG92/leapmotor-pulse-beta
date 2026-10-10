# Desarrollo

El repositorio separa la interfaz Svelte, la API FastAPI y el cliente Capacitor. Para reproducir el flujo automatizado se usan Python 3.12, Node.js 24 y pnpm 11.25; las versiones de las acciones de CI están declaradas en `.github/workflows/`.

`python scripts/check.py` comprueba el contrato OpenAPI, ejecuta las comprobaciones Python, revisa los proyectos móviles, valida tipos y ejecuta las comprobaciones, pruebas y builds de web y móvil. `bash scripts/check-container.sh` prepara dependencias fijadas, ejecuta esas comprobaciones y valida la imagen. El workflow `verify` llama a ese script en Ubuntu.

El workflow `verify-ios` es manual y construye la aplicación y el widget para el simulador iOS. Una build de simulador no es una prueba en un iPhone y no valida permisos, credenciales ni una conexión física. Los workflows describen comprobaciones definidas en el repositorio; revisa la ejecución de CI correspondiente antes de afirmar que una revisión concreta las ha superado.

No añadas tokens, certificados, bases de datos de usuario, APK de una instalación personal ni artefactos generados. Las pruebas sintéticas de adaptadores no acreditan compatibilidad con un servicio, vehículo, cargador o dispositivo físico.
