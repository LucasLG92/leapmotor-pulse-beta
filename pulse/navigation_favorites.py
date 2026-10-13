"""Private, explicitly saved destinations. No geolocation or external requests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import sqlite3
import uuid
from collections.abc import Mapping

from pulse.widget_profiles import validate_owner_id, WidgetProfileValidationError


def _error(message, status=400):
    from pulse.domain import DomainError
    raise DomainError(message, status)


def _owner(value):
    try:
        return validate_owner_id(value)
    except WidgetProfileValidationError as exc:
        _error(str(exc))


def _fields(value):
    if not isinstance(value, Mapping) or set(value) != {'name', 'latitude', 'longitude'}:
        _error('El destino debe incluir nombre, latitud y longitud.')
    name = value['name']
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 160:
        _error('Escribe un nombre de hasta 160 caracteres.')
    try:
        name.encode('utf-8')
    except UnicodeEncodeError:
        _error('El nombre contiene texto no válido.')
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        _error('El nombre contiene caracteres no permitidos.')
    result = {'name': name.strip()}
    for key, limit in [('latitude', 90), ('longitude', 180)]:
        x = value[key]
        if isinstance(x, bool) or not isinstance(x, (int, float)):
            _error('Las coordenadas deben ser números finitos.')
        try:
            x = float(x)
        except (ValueError, OverflowError):
            _error('Las coordenadas no son válidas.')
        if not math.isfinite(x) or abs(x) > limit:
            _error('Las coordenadas están fuera de rango.')
        result[key] = 0.0 if x == 0 else x
    return result


def create_navigation_favorite_schema(connection):
    connection.execute('''CREATE TABLE navigation_favorites (
        owner_id TEXT NOT NULL, id TEXT NOT NULL, name TEXT NOT NULL,
        latitude REAL NOT NULL, longitude REAL NOT NULL,
        created_at TEXT NOT NULL, PRIMARY KEY(owner_id,id),
        UNIQUE(owner_id,name,latitude,longitude))''')


def validate_navigation_favorite_archive(raw, *, now):
    if not isinstance(raw, list) or len(raw) > 1000:
        _error('La copia admite como máximo 1000 destinos.')
    items, ids, identities, counts = [], set(), set(), {}
    for item in raw:
        if not isinstance(item, Mapping) or set(item) != {'owner_id','id','name','latitude','longitude','created_at'}:
            _error('El destino no cumple el formato de copia.')
        owner = _owner(item['owner_id'])
        ident = item['id']
        if not isinstance(ident, str) or len(ident) != 32 or any(c not in '0123456789abcdef' for c in ident):
            _error('El identificador de destino no es válido.')
        data = _fields({k:item[k] for k in ['name','latitude','longitude']})
        key = (owner, ident)
        identity = (owner, data['name'], data['latitude'], data['longitude'])
        if key in ids or identity in identities:
            _error('La copia contiene destinos duplicados.')
        ids.add(key); identities.add(identity)
        counts[owner] = counts.get(owner, 0) + 1
        if counts[owner] > 100:
            _error('Un usuario admite como máximo 100 destinos.')
        try:
            if not isinstance(item['created_at'], str) or len(item['created_at']) > 64:
                raise ValueError
            created = datetime.fromisoformat(item['created_at'].replace('Z','+00:00'))
            if created.tzinfo is None or created.utcoffset() is None:
                raise ValueError
            created = created.astimezone(timezone.utc)
            if created > now + timedelta(minutes=5):
                raise ValueError
        except (ValueError, OverflowError, TypeError):
            _error('La fecha del destino no es válida.')
        items.append({'owner_id':owner,'id':ident,**data,'created_at':created.isoformat(timespec='microseconds').replace('+00:00','Z')})
    return items


class NavigationFavoriteStoreMixin:
    def list_navigation_favorites(self, owner_id):
        owner = _owner(owner_id)
        with self._read_transaction() as connection:
            rows = connection.execute('SELECT id,name,latitude,longitude,created_at FROM navigation_favorites WHERE owner_id=? ORDER BY name COLLATE NOCASE,id', (owner,)).fetchall()
            return {'items':[dict(row) for row in rows]}

    def create_navigation_favorite(self, owner_id, values):
        owner, data = _owner(owner_id), _fields(values)
        with self._transaction() as connection:
            existing = connection.execute('SELECT id,name,latitude,longitude,created_at FROM navigation_favorites WHERE owner_id=? AND name=? AND latitude=? AND longitude=?', (owner,data['name'],data['latitude'],data['longitude'])).fetchone()
            if existing is not None:
                return dict(existing)
            total = connection.execute('SELECT COUNT(*) FROM navigation_favorites').fetchone()[0]
            count = connection.execute('SELECT COUNT(*) FROM navigation_favorites WHERE owner_id=?',(owner,)).fetchone()[0]
            if total >= 1000 or count >= 100:
                _error('Has alcanzado el límite de destinos guardados.',409)
            item = {'id':uuid.uuid4().hex,**data,'created_at':self._now()}
            connection.execute('INSERT INTO navigation_favorites VALUES (?,?,?,?,?,?)', (owner,item['id'],item['name'],item['latitude'],item['longitude'],item['created_at']))
            self._touch(connection)
            return item

    def delete_navigation_favorite(self, owner_id, favorite_id):
        owner = _owner(owner_id)
        with self._transaction() as connection:
            deleted = connection.execute('DELETE FROM navigation_favorites WHERE owner_id=? AND id=?',(owner,favorite_id)).rowcount
            if not deleted:
                _error('No se encontró el destino guardado.',404)
            self._touch(connection)
            return {'ok':True}

    def export_navigation_favorites(self, connection, owner):
        if owner is None:
            if connection.execute('SELECT 1 FROM navigation_favorites LIMIT 1').fetchone():
                _error('La exportación de destinos requiere un propietario.',403)
            return []
        return [dict(row) for row in connection.execute('SELECT * FROM navigation_favorites WHERE owner_id=? ORDER BY id',(_owner(owner),))]

    def validate_navigation_favorite_replacement(self, connection, owner, items):
        """Reject predictable restore conflicts before a recovery backup is made."""
        if owner is not None:
            owner = _owner(owner)
            if any(row['owner_id'] != owner for row in items):
                _error('La copia contiene destinos de otro usuario.')
            remaining = connection.execute(
                'SELECT COUNT(*) FROM navigation_favorites WHERE owner_id != ?', (owner,)
            ).fetchone()[0]
        else:
            remaining = 0
        if remaining + len(items) > 1000:
            _error('La restauración supera el límite global de destinos.',409)

    def replace_navigation_favorites(self, connection, owner, items):
        self.validate_navigation_favorite_replacement(connection, owner, items)
        if owner is None:
            connection.execute('DELETE FROM navigation_favorites')
        else:
            connection.execute('DELETE FROM navigation_favorites WHERE owner_id=?',(owner,))
        for row in items:
            connection.execute('INSERT INTO navigation_favorites VALUES (?,?,?,?,?,?)',tuple(row[k] for k in ['owner_id','id','name','latitude','longitude','created_at']))
