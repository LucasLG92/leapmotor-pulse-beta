# Estado de la beta

Leapmotor Pulse es software experimental y no oficial. La licencia aplicable se indica en la raíz de cada distribución. La beta permite revisar la interfaz y evaluar una instalación local; no es un producto terminado ni está certificada para supervisar o controlar un vehículo.

La instalación local genérica se limita a Linux con Docker Engine nativo y Compose v2; escucha en loopback y no conecta un vehículo, configura servicios externos, activa push, órdenes físicas ni autenticación remota. La [guía de instalación](INSTALL.md) describe ese alcance. La [guía comunitaria](../deploy/community/README.md) ofrece ejemplos genéricos; no describe instalaciones individuales.

El candidato local actual se identifica como Android 0.1.1/versionCode 2 e iOS 0.1.1/build 2. La suite Python final pasó localmente en ambos stages con 1.174 pruebas y 87 subcasos; la validación web/móvil compartida pasó 43 pruebas web, 7 móviles, Svelte 0/0, TypeScript y builds. No es CI nueva. El estado nativo y sus límites se detallan en [VALIDATION.md](VALIDATION.md).

La base del candidato usa SQLite 17 y exportación JSON 15. El diagnóstico conserva códigos crudos sin atribuirles significado físico. Las selecciones MQTT son optativas y están desactivadas por defecto. Las pruebas automatizadas y sintéticas no prueban un servicio, teléfono, vehículo o cargador real.

Las nueve fases de trabajo y siete puertas reales permanecen abiertas. La [aceptación](ACCEPTANCE.md) y el [registro estructurado](DELIVERY-STATUS.json) separan resultados históricos, candidatos locales y requisitos físicos pendientes. No uses esta versión para decisiones de seguridad ni como sustituto de la información del fabricante o la comercializadora.
