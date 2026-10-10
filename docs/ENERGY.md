# Energía y carga

Pulse puede mostrar registros locales de energía y sesiones de carga importadas desde una instancia de evcc configurada expresamente. La integración consulta un único punto de carga seleccionado y lee estado, sesiones cerradas y, si se activa por separado, curvas históricas de energía. Evcc está desactivado por defecto.

Para estimar el coste de una sesión cerrada, Pulse consulta los precios horarios PVPC publicados por REData y distribuye la energía total declarada entre el intervalo de inicio y fin. Esa distribución temporal es una estimación: no es la curva medida por un contador. Las curvas de energía de evcc pueden alimentar un cálculo temporal cuando se suministran explícitamente; su forma y procedencia aún no prueban que sean una medición certificada de red.

Pulse conserva por separado el dato recibido, su hora de observación y el cálculo derivado. Una carga activa o una desconexión no bastan para inventar el cierre de una sesión. Si una lectura falla o queda antigua, la vista puede conservar los datos anteriores y señalar su estado.

La beta local no conecta evcc, no consulta MQTT y no activa integraciones al arrancar. La configuración de evcc requiere un origen fijo y un punto de carga explícito. No uses precios estimados como factura ni como medición de energía.
