# Búsqueda privada de favoritos

El formulario puede obtener coordenadas por nombre o dirección, elegir un resultado y editar el alias. Buscar y seleccionar no crean un favorito: Guardar realiza la persistencia por propietario. Las coordenadas manuales permanecen como alternativa.

Para direcciones privadas, el formulario exige un estado coherente de `photon_private`; un proveedor público o desconocido deja su búsqueda deshabilitada. Photon privado está preparado, pero todavía no está instalado ni activado. No interpretar los tests con transporte simulado como una conexión TLS real.

## Configuración externa

`PULSE_NAVIGATION_PLACES_PROVIDER=photon_private` selecciona el adaptador; `PULSE_NAVIGATION_PLACES_ORIGIN` requiere origen HTTPS con IP privada literal, sin ruta, credenciales ni redirecciones. Al habilitar navegación, `PULSE_NAVIGATION_GEOCODER_CA_FILE`, `PULSE_NAVIGATION_GEOCODER_CERT_FILE` y `PULSE_NAVIGATION_GEOCODER_KEY_FILE` requieren rutas absolutas a archivos externos protegidos y montados solo lectura. Mantener el contacto de navegación configurado. Nunca incluir sus contenidos en el repositorio.

La búsqueda utiliza un cliente mTLS independiente con verificación de CA e identidad del servidor. Los errores no consultan Nominatim como alternativa. Se mantienen consentimiento, autenticación/CSRF, límite de tasa, cinco resultados, tamaño de respuesta y timeout. Solo hay caché RAM acotada; el borrador no se persiste automáticamente ni en el shell PWA.

El backend reconoce GeoJSON Point con orden longitud/latitud, valida coordenadas/textos e identifica resultados con procedencia `OpenStreetMap Photon`. La UI rechaza respuestas cuyo proveedor no corresponde al estado privado.

## Servicio y límites

El candidato de servicio es [Photon 1.3.0](https://github.com/komoot/photon/blob/1.3.0/README.md), software Apache-2.0, con datos OSM sujetos a ODbL. El [índice oficial de España](https://download1.graphhopper.com/public/europe/spain/index.html) incluye España. La cobertura inicial propuesta se limita a esa región; un resultado puede carecer de número de portal o no existir en OSM.

Antes de activar: huésped aislado, recursos cualificados, TLS con certificado cliente obligatorio, acceso solo desde Pulse, ausencia de logs de consulta y tests reales de identidad/rechazo. Actualmente esos gates siguen pendientes. Los certificados se generan y permanecen únicamente en el servidor.

La privacidad se refiere a buscar lugares. Mostrar mapas, buscar cargadores y abrir Apple/Google Maps son acciones separadas que conservan sus servicios públicos y avisos. Descargar el dataset público no transmite consultas.
