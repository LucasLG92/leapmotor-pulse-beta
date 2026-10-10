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

## Imagen de ejecución

La comprobación de locks no cubre todos los componentes de la imagen. Una
auditoría adicional con OSV-Scanner 2.6.0 sobre un archivo de `docker image save`
revisa el filesystem final, sin ejecutar la imagen ni exportar contenedores,
volúmenes o configuración del operador. Se incluyen todos los avisos, también
los que Debian clasifica como `unimportant` o `ignored`; los IDs y las entradas
de paquetes no equivalen a escenarios de explotación independientes.

El runtime conserva Python 3.12.14 y Bookworm. El Dockerfile actualiza únicamente
`libssl3` y `openssl` a `3.0.22-1~deb12u1` desde el snapshot Debian firmado
`20261010T000000Z`; no cambia a otra ABI ni desactiva la validación TLS. Esta
versión corrige seis IDs presentes en la base anterior: CVE-2026-42767, 54874,
63072, 63074, 63076 y 75803. El
[tracker de Debian](https://security-tracker.debian.org/tracker/source-package/openssl)
mantiene otros seis IDs abiertos para Bookworm, incluido CVE-2026-35189 sobre
certificados manipulados. Este parche no permite declarar la imagen libre de
vulnerabilidades; cualificar una base con correcciones adicionales sigue pendiente.

Después de comprobar las dependencias con `pip check`, la etapa final retira
`pip` y `ensurepip`. Los instaladores siguen disponibles en las etapas de build;
los extras opcionales deben incorporarse allí, antes del runtime. Esto reduce la
superficie disponible en ejecución, pero no elimina sus bytes de las capas base
ni garantiza una descarga menor. No se eliminan bibliotecas del sistema para
ocultar resultados del scanner.

`scripts/build-container.sh` comprueba UID 10001, ausencia de Node, setuptools,
pip y ensurepip, imports y los assets gzip. También comprueba por separado
OpenSSL 3.0.22 cargado por el módulo `ssl` de Python y OpenSSL 4.0.3 embebido en
cryptography 50.0.2: son bibliotecas distintas.

SQLite activo se compila desde la fuente oficial 3.53.4 y se carga desde
`/usr/local/lib`. Su configuración actual no incluye Session ni FTS5; la ausencia
de sus símbolos se comprobó en la imagen anterior. El inventario Debian aún
contiene SQLite 3.40.1, por lo que sus avisos no se descartan automáticamente.
En particular, observar la versión 3.53.4 por sí solo no demuestra que incorpore
el parche adicional de
[CVE-2026-50813](https://security-tracker.debian.org/tracker/CVE-2026-50813).

Las correcciones y bibliotecas realmente cargadas deben comprobarse en cada
imagen construida. El resultado de locks, un build correcto o una sonda de
versión no sustituyen una auditoría de rutas alcanzables, bibliotecas embebidas,
compatibilidad ni seguridad integral. Pulse continúa en beta.
