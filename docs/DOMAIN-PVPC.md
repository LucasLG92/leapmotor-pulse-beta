# Persistencia PVPC

El esquema SQLite V4 añade `settings.pricing_method` (`manual` por defecto o
`pvpc`), el fin opcional de cada carga, el método aplicado y el snapshot PVPC
validado. La migración conserva tarifas, costes e índices previos; las cargas
históricas se marcan como manuales sin volver a calcular su coste. La tarifa
global se usa al crear una carga manual. Al editarla se conserva su tarifa
histórica o se aplica la nueva tarifa indicada expresamente; ajustar el precio
global nunca modifica cargas ya guardadas.

Una carga manual admite `ended_at` opcional. Cuando se informa, debe ser
posterior al inicio y el periodo no puede superar 72 horas. Una carga PVPC
requiere `ended_at` y un snapshot producido por el servicio del servidor. El
Store valida el desglose por intervalos, la energía, el coste redondeado y la
tarifa efectiva ponderada. El coste puede ser negativo; la tarifa efectiva se
calcula como `sum(kWh_intervalo × precio_intervalo) / kWh_total`, sin derivarla
del coste redondeado.

Cambiar inicio, fin, energía o método de una carga PVPC exige un nuevo snapshot
completo. Convertir una carga PVPC a manual exige una tarifa manual explícita y
elimina el desglose PVPC. Los valores de tarifa o coste no se aceptan como
campos libres de la carga.

`Store.cache_pvpc_day` valida y guarda días horarios normalizados de la serie
peninsular REE REData PVPC 1001. `Store.read_pvpc_day` consulta solo SQLite y
devuelve la última ingestión local del día. La caché mantiene las 400 últimas
ingestiones y se excluye deliberadamente del backup: los días pueden volver a
obtenerse del servicio público. Los backups sí conservan íntegro el snapshot de
precios asociado a cada carga PVPC.
