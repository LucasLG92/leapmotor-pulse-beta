# Preparación de Pulse

En Ajustes, abre **Preparar Pulse** para consultar las funciones habilitadas en el servidor y sus siguientes pasos. La información se actualiza junto con el estado habitual de Pulse; abrir esta sección no consulta al coche ni a servicios externos.

**Habilitado** significa que el servidor tiene activada esa opción. **Pendiente de comprobación** recuerda que su funcionamiento debe contrastarse con el coche, cargador, servicio o dispositivo correspondiente. La sección no registra aprobaciones ni convierte una configuración en una prueba superada.

La vista agrupa lectura del vehículo y cargas, aplicación/widgets/notificaciones e integraciones Home Assistant, ABRP y navegación. También muestra el modo de acceso y recuerda seleccionar PVPC cuando se quiera calcular nuevas cargas con precios horarios. Las sesiones, precios y permisos conservan sus propios estados en las pantallas correspondientes.

Sin conexión se retira la lista y se indica cómo recuperarla. Con un servidor anterior que no incluya esta información, se muestra su ausencia en lugar de presentar funciones desactivadas. Los comandos físicos permanecen bloqueados; las cámaras y el centinela requieren estudio y validación independientes.

Si el panel del widget nativo falla al consultar el estado de la app, **Reintentar consulta** vuelve a consultar ese estado. No repite el inicio de sesión, el enlace ni cambios de preferencias. El puente nativo conserva su gestión habitual de caducidad, que puede retirar credenciales o caché inválidas.

## Comprobaciones pendientes

La configuración y los errores recuperables pueden comprobarse con datos ficticios. La instalación, retorno OAuth, widgets, accesibilidad y consumo deben probarse en teléfonos físicos. La lectura B10, las cargas y sus costes necesitan el coche y el cargador; las integraciones necesitan cuentas y servicios reales. Mantener estos requisitos pendientes hasta guardar evidencia de esas pruebas.
