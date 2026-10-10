# Aplicación móvil

El proyecto incluye clientes para Android e iOS construidos con Capacitor. Comparten la interfaz web y usan un puente nativo para sesión, peticiones autenticadas, eventos y widgets. El código Android integra un widget de inicio; iOS tiene una extensión WidgetKit. El widget presenta una selección limitada de campos y requiere consentimiento y emparejamiento con un servidor compatible.

El cliente móvil está implementado en el repositorio, pero la instalación sencilla de esta beta no configura sus credenciales ni expone la API a la red. El modo local solo escucha en loopback. El acceso desde un teléfono requiere un despliegue remoto con HTTPS y OIDC y la configuración manual de los clientes nativos.

El workflow `verify-ios` construye la app y el widget para el simulador; eso no prueba un iPhone físico. Las builds Android tampoco demuestran que un modelo de teléfono, su widget, permisos y servidor real funcionen juntos. Revisa las ejecuciones de CI del repositorio público y prueba cada dispositivo por separado antes de considerar esa compatibilidad validada. No publiques ni uses APKs configurados para una instalación particular.
