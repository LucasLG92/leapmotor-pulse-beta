# Hosting Pulse

Esta guía describe requisitos generales de despliegue; cada operador adapta su infraestructura y no debe copiar configuración privada al repositorio.

Pulse sirve la interfaz y su API desde el mismo origen. Coloca la aplicación detrás de un proxy inverso con TLS y una capa de autenticación apropiada. Mantén credenciales y valores de entorno fuera del paquete y del control de versiones.

El contenedor puede ejecutarse sin privilegios, con el sistema de archivos de solo lectura y un volumen persistente dedicado a los datos. Limita recursos, conserva una copia coherente antes de migraciones y guarda la imagen compatible con esa copia. Si necesitas retroceder tras una migración, restaura juntos el binario y la base de datos compatibles.

No expongas descargas privadas ni interfaces de administración sin autenticación. Verifica salud, autenticación, datos persistentes y TLS en el entorno que administres; una build local no demuestra por sí sola una instalación operativa.
