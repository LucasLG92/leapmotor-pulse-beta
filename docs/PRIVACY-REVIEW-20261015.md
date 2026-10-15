# Revisión de confidencialidad · 15 de octubre de 2026

La revisión busca credenciales y datos privados en lo compartido, con un corte verificable anterior a esta actualización. No es una auditoría integral de la aplicación ni una garantía de ausencia de secretos arbitrarios.

## Cobertura

- Repositorio público: espejo obtenido de GitHub con 228 commits alcanzables, 57 referencias y 1.067 blobs únicos; revisión de patrones de credenciales, documentación, rutas y metadatos de autoría. Baseline de la rama de trabajo: `876c45158d8abcc7efe33207f26a50bb914dbfb4`.
- Repositorio privado: árbol actual de 512 blobs en `d82ec726a4bb58dab35cca3eb057d74839eb6ce0`. De ellos, 474 coinciden con objetos públicos revisados y se inspeccionan los 38 contenidos restantes mediante el conector autorizado. No se cubre toda la historia privada.
- Superficie pública adicional: cuerpos de 24 issues/PR, 10 comentarios generales y 12 comentarios de revisión. Las dos releases publicadas no tienen archivos adjuntos.
- Galería nueva: siete capturas originales JPEG de una instancia local de demostración, revisadas visualmente y sin entradas EXIF. No son capturas de una sesión de la instalación privada.

## Resultado y corrección

No se encontraron valores de credenciales reales en las fuentes inspeccionadas. Las coincidencias de campos de autenticación eran variables, referencias de configuración o ejemplos; los correos de commits corresponden a GitHub noreply y a su servicio de commits. Los patrones revisados incluyen tokens GitHub, claves de proveedores, JWT, Fernet y claves privadas PEM. Una contraseña de formato arbitrario podría escapar a estos patrones.

Se confirmaron dos metadatos publicados: una ruta absoluta del ordenador en `docs/DELIVERY-STATUS.json` y el hostname de la instalación en `docs/MOBILE-REFLOW-RELEASE-20261014.json`. Esta actualización sustituye la ruta por una referencia local relativa y el hostname por un dominio de ejemplo reservado. No se modifica la configuración operativa.

Un commit nuevo no elimina los valores de commits, ramas o referencias PR anteriores, ni de copias, cachés o forks. La reescritura o eliminación de historia no forma parte de esta actualización. Los metadatos encontrados no son claves de acceso y no justifican por sí solos revocar credenciales.

Los runbooks de instalación del directorio `deploy/lucas` permanecen exclusivamente en el repositorio privado; no se copian a la beta. Los paquetes configurados, claves, certificados, sesiones, bases de datos, logs de cuenta y artefactos del operador quedan fuera del lote de publicación.

## Límites

No se inspeccionan almacenes de credenciales, secretos del servidor o datos privados para compararlos con el código publicado. Tampoco se acredita una revisión completa de logs/artefactos históricos de CI, objetos Git no alcanzables, enlaces externos o copias realizadas por terceros. El registro de entregas y la aceptación física del producto siguen siendo comprobaciones separadas.
