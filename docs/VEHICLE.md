# Lecturas del vehículo

El proveedor de vehículo incluido tiene un adaptador de lectura B10 para el SDK opcional MATE-API. El adaptador recibe un cliente ya configurado y una identidad de vehículo; normaliza solo un conjunto pequeño de valores, como carga de batería, autonomía y odómetro cuando la fuente los ofrece.

El arranque normal no importa el SDK, no busca credenciales o certificados y no inicia una sesión. Para una futura conexión, un operador tendría que aportar de forma explícita callbacks autorizados de adquisición y renovación de sesión y un transporte TLS, junto con el material legítimo requerido por el proveedor. El repositorio no ofrece wizard de login ni configuración B10 en la beta pública. Consulta la [licencia MIT del SDK](licenses/mate-api-MIT.txt) antes de instalarlo.

Una lectura aceptada por el adaptador solo confirma que el SDK devolvió datos con una forma válida y una identidad coincidente. La frescura y capacidad física permanecen sin validar. No se habilitan comandos de bloqueo, climatización o carga: las operaciones físicas siguen bloqueadas mientras falte validación de capacidad y seguridad.

La beta local no inicia sesión en servicios del vehículo ni conecta un B10. Las pruebas de contrato y datos de ejemplo no equivalen a una prueba con vehículo real.
