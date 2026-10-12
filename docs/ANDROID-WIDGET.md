# Widget de Android

El widget Glance muestra una copia local y optativa de los datos que el usuario ha elegido compartir. El renderizador no necesita red, servicio propio ni alarmas. La app anfitriona solicita la actualización mediante WorkManager y evita mostrar campos no seleccionados. Las acciones se limitan a abrir la pantalla correspondiente; el widget no emite órdenes al vehículo.

La política de actualización aplica consentimiento explícito, TTL, rangos y reloj monotónico, y evita tratar un contador de arranque desconocido como fresco. La copia caducada o incompleta no se presenta como lectura actual. La apariencia puede enmascarar valores y debe mantener contraste y reflujo legibles.

Las pruebas JVM previas cubrieron `WidgetPolicy` y `WidgetConsentPolicy`, decisiones puras, selección, masking, ventanas temporales, caché y transiciones de consentimiento. Esas pruebas no simulan fallos físicos de Android Keystore o SharedPreferences. La revisión visual sintética al 200 % detectó recortes en cuatro de seis composiciones y se registró una corrección de layout; el resultado no acredita launcher, bloqueo real, actualización programada ni instalación.

La aceptación en emulador y teléfono, la autenticación nativa, el grant, el estado tras reinicio, la firma y la distribución permanecen pendientes para el candidato actual. El resumen de pruebas y los límites se conservan en [VALIDATION.md](VALIDATION.md); no se distribuyen capturas, APKs ni recibos locales.
