# Mediciones del lector JSON nativo

## Lector JSON nativo: comparación acotada del 10 de octubre de 2026

El lector comprueba ahora el tamaño acumulado antes de copiar cada bloque al
buffer. Mantiene el máximo de 64 KiB inclusive, los mismos errores, parseo,
destinos, permisos y cierre del stream. No añade dependencias, cambia SQLite,
modifica la UI ni requiere un APK nuevo.

Se compararon 48 muestras en las imágenes inmutables anterior y candidata:
24 por imagen, con procesos frescos, respuestas identity/gzip/gzip+deflate,
aceptación exactamente en el límite y rechazo, bloques de transporte de
127/65536 bytes y dos repeticiones. Los casos comprimidos rechazados representan
128 MiB lógicos generados incrementalmente. El decoder y el lector son reales;
el transporte es sintético, sin sockets, DNS, TLS, sesión o datos del operador.
Las 24 parejas conservan aceptación/rechazo, errores, bytes entregados y cierre.

Tracemalloc comienza después de imports y preparación, y mide asignaciones de
Python durante todo el flujo instrumentado, incluidos asyncio, cliente y
descompresor. El máximo observado pasa de 2490635 a 2485454 bytes. En las seis
parejas comprimidas que entregan un bloque decodificado de 1 MiB, la diferencia
es de 4961–5181 bytes. Los demás casos fluctúan decenas o centenares de bytes;
estas dos repeticiones no prueban una mejora relevante del pico total. El cambio
evita una copia innecesaria, pero el trabajo del decoder también contribuye al
pico. No se atribuyen diferencias de CPU/tiempo a rendimiento de producción:
ambas medidas incluyen el coste de tracemalloc.

Los contenedores de ensayo usan red desactivada, UID 10001, raíz de sólo lectura,
256 MiB, 0,5 CPU y tmpfs sin volúmenes persistentes ni puertos. Se retiraron los
recursos propios y se conservaron identidad, arranque, salud e inventarios de
Pulse. No acredita toda la memoria nativa, concurrencia, OAuth real, consumo
móvil ni rendimiento del servidor con integraciones activas. A11 permanece
pendiente de sus pruebas de campo.

