# Comprobación de dependencias

Pulse sigue en beta. El workflow `dependency-security` consulta avisos conocidos
con [OSV-Scanner](https://google.github.io/osv-scanner/) 2.6.0, fijado mediante
SHA-256. Se ejecuta en pull requests, cambios de locks/checker y de forma manual.
Es independiente de los builds; no instala paquetes ni despliega Pulse.

Comprueba seis archivos con las versiones realmente fijadas:

- `requirements-lock.txt`: backend y transitivas.
- `requirements-dev-lock.txt`: entorno de desarrollo Python.
- `requirements-build-lock.txt`: herramienta de compilación Python.
- `apps/web/pnpm-lock.yaml`: herramientas y dependencias web.
- `apps/mobile/pnpm-lock.yaml`: herramientas y dependencias móviles.
- `apps/mobile/android/app/gradle.lockfile`: dependencias Android resueltas.

Las entradas repetidas entre entornos se comprueban en sus respectivos archivos;
el conteo de entradas no equivale a un conteo de paquetes únicos. El checker exige
un inventario no vacío para cada archivo. Falla ante un aviso conocido o un error
de descarga, integridad, consulta o parsing. No filtra por severidad ni incorpora
excepciones. Una consulta sin avisos no garantiza ausencia de vulnerabilidades.

Para reproducirlo en Linux x86_64 con Bash, curl, sha256sum y Python 3:

```sh
bash scripts/check-dependencies.sh
```

Solo usa los locks indicados y una configuración vacía de OSV. No analiza el
directorio del servidor, almacenes de secretos o configuraciones personales.
La consulta remite identificadores/versiones de dependencias públicas a OSV.
No ejecuta código de los paquetes, resolución de rangos o call analysis.

## Corrección de la herramienta iOS

`xcode@3.0.1`, dependencia de desarrollo de Capacitor CLI, incorporaba `uuid@7.0.3`,
incluido en [GHSA-w5hq-g745-h8pq](https://github.com/uuidjs/uuid/security/advisories/GHSA-w5hq-g745-h8pq).
El override se limita a `xcode@3.0.1>uuid` y fija `11.1.1`, versión corregida que
mantiene CommonJS. El consumidor usa `uuid.v4()` sin buffers; no se ha demostrado
explotación en Pulse. La corrección elimina la versión afectada del lock, sin
añadir una excepción al scanner ni cambiar las dependencias de la aplicación.

## Pendiente de comprobación

Este check no cubre sistema operativo, imagen y bibliotecas nativas embebidas,
toolchains/SDK, Swift Package Manager, el SDK B10 opcional fijado a Git ni errores
propios de Pulse. Tampoco prueba permisos, aislamiento, autenticación, dispositivo
físico o servicios reales. Estas revisiones y las pruebas completas de seguridad
y recuperación conservan sus criterios de aceptación separados.
