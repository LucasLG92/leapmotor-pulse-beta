# Aplicación móvil

El proyecto incluye clientes Android e iOS construidos con Capacitor. Comparten la interfaz web y usan puentes nativos para sesión, peticiones autenticadas y widgets. Android tiene un widget de inicio e iOS una extensión WidgetKit. Cada widget requiere consentimiento y una conexión configurada.

El candidato local se identifica como Android 0.1.1/versionCode 2 e iOS 0.1.1/build 2. Android se ensambló sin firma; iOS solo completó `cap sync`. No se registró una nueva CI para este candidato. Esto no acredita una instalación en teléfono, OAuth real, actualización, permisos ni funcionamiento del widget.

La instalación comunitaria local no configura credenciales ni expone la API a la red. El acceso desde un teléfono requiere un despliegue remoto con HTTPS y autenticación, además de configuración nativa válida. No publiques APKs o apps configuradas para una instalación particular.

Los workflows y tests del repositorio cubren código y contratos delimitados; un build de simulador no prueba un dispositivo físico. Revisa [la validación actual](VALIDATION.md), [build Android](ANDROID-BUILD.md), [build iOS](IOS-BUILD.md) y [estado de aceptación](DELIVERY-STATUS.json).

El almacén iOS distingue un registro inexistente de una lectura bloqueada, fallida o inválida. Esos errores deben impedir confirmar cambios de consentimiento o sustituir permisos. Las pruebas con almacenes falsos no sustituyen una prueba en un dispositivo con bloqueo, widgets, firma y distribución reales.
