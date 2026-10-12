# Leapmotor Pulse

Aplicación independiente y no oficial para consultar información de movilidad, energía y carga. La identidad visual es propia y configurable; el producto no usa logotipos ni afirma afiliación con Leapmotor. Cada dato debe mostrar fuente, hora de observación y frescura.

## Alcance del producto

La primera ejecución presenta la preparación de funciones y permite abrir una demo explícita con datos ficticios. La demo no activa conexiones ni órdenes reales; al desactivarla, solo se eliminan sus datos sintéticos. La aplicación no solicita credenciales del fabricante en el modo local.

El objetivo de energía es importar sesiones cerradas cuando una fuente autorizada lo permita, calcular estimaciones PVPC con sus supuestos visibles y conservar los datos incompletos como pendientes. Una pausa o lectura ausente no debe convertirse en cierre confirmado. El coste estimado no se presenta como factura, gasto de red medido ni medida de autoconsumo.

Las integraciones MQTT, Home Assistant, ABRP y navegación son optativas y no se habilitan por defecto. Los campos compartidos requieren selección explícita. Automatizar lecturas no autoriza órdenes físicas. Toda acción debe conservar su resultado incierto sin repetición automática.

## Estado técnico

El candidato local actual usa esquema SQLite **17** y formato de exportación JSON **15**. Conserva códigos de diagnóstico sin interpretación física, fuente o momento de observación cuando están disponibles. Los significados, la cobertura y la capacidad del vehículo permanecen pendientes de validar con una fuente autorizada y un vehículo real.

Android **0.1.1/versionCode 2** e iOS **0.1.1/build 2** son candidatos locales. La validación de software no acredita firma, distribución, login, widgets en teléfonos, cargador, vehículo ni uso habitual. El alcance, las nueve fases y las siete puertas pendientes están en [VALIDATION.md](docs/VALIDATION.md) y [DELIVERY-STATUS.json](docs/DELIVERY-STATUS.json).

La licencia aplicable se define en el archivo `LICENSE` de cada distribución. Las dependencias conservan sus licencias propias; véase `THIRD-PARTY-NOTICES.md`.
