"""Original, bounded read-only adapter for the documented evcc HTTP interface.

Only normalized charger fields leave this module. Neither aggregate session
energy nor a successful HTTP response qualifies the physical meter or vehicle.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import math
import os
import re
from urllib.parse import urlsplit

import httpx2
from pulse.charger_store import session_id


ENERGY_CONTRACT_REVISION = "evcc-0.316.2-15m-v1"
_ENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9:_.-]{0,79}\Z", re.ASCII)


class EvccError(ValueError):
    def __init__(self, code: str = "contract"):
        self.code = code if code in {"contract", "network", "access", "large", "timeout"} else "contract"
        super().__init__("No se pudo leer un contrato válido del cargador.")


@dataclass(frozen=True)
class EvccConfig:
    enabled: bool = False
    origin: str = ""
    loadpoint: str = ""
    poll_seconds: int = 60
    history_seconds: int = 600
    history_months: int = 3
    energy_history_enabled: bool = False

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise ValueError("La activación evcc debe ser booleana.")
        if type(self.energy_history_enabled) is not bool:
            raise ValueError("La activación del histórico energético debe ser booleana.")
        if (type(self.poll_seconds) is not int or not 30 <= self.poll_seconds <= 3600
                or type(self.history_seconds) is not int or not 300 <= self.history_seconds <= 86400
                or type(self.history_months) is not int or not 1 <= self.history_months <= 12):
            raise ValueError("Los límites de sondeo evcc no son válidos.")
        if not isinstance(self.origin, str) or not isinstance(self.loadpoint, str):
            raise ValueError("La configuración evcc debe ser explícita.")
        if self.origin:
            try:
                url = urlsplit(self.origin)
                port = url.port
            except ValueError:
                raise ValueError("El origen evcc no es válido.") from None
            if (url.scheme not in {"http", "https"} or not url.hostname
                    or url.username is not None or url.password is not None
                    or url.path not in {"", "/"} or url.query or url.fragment
                    or any(char.isspace() or ord(char) < 32 for char in self.origin)
                    or (port is not None and not 1 <= port <= 65535)):
                raise ValueError("evcc necesita un origen fijo sin ruta ni credenciales.")
            if url.scheme == "http":
                try:
                    address = ipaddress.ip_address(url.hostname)
                except ValueError:
                    raise ValueError("HTTP evcc requiere una IP privada o loopback literal.") from None
                local = address.is_loopback or (
                    address.version == 4 and any(address in ipaddress.ip_network(network)
                        for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
                ) or (address.version == 6 and address in ipaddress.ip_network("fc00::/7"))
                if not local:
                    raise ValueError("HTTP evcc requiere una IP privada o loopback literal.")
        if self.loadpoint and (not 1 <= len(self.loadpoint) <= 80
                or self.loadpoint != self.loadpoint.strip()
                or any(ord(char) < 32 for char in self.loadpoint)):
            raise ValueError("El título del punto de carga evcc no es válido.")
        if self.enabled and (not self.origin or not self.loadpoint):
            raise ValueError("evcc requiere origen y título de un único punto de carga.")
        if self.energy_history_enabled and (not self.enabled or not self.origin or not self.loadpoint):
            raise ValueError("El histórico energético requiere evcc y un punto de carga configurados.")

    @property
    def scope(self) -> str:
        return hashlib.sha256((self.origin.rstrip("/") + "\n" + self.loadpoint).encode()).hexdigest()

    @classmethod
    def from_env(cls):
        enabled = os.environ.get("PULSE_EVCC_ENABLED", "false")
        energy_history_enabled = os.environ.get("PULSE_EVCC_ENERGY_HISTORY_ENABLED", "false")
        if enabled not in {"true", "false"}:
            raise ValueError("PULSE_EVCC_ENABLED debe ser true o false.")
        if energy_history_enabled not in {"true", "false"}:
            raise ValueError("PULSE_EVCC_ENERGY_HISTORY_ENABLED debe ser true o false.")
        try:
            return cls(enabled=enabled == "true", origin=os.environ.get("PULSE_EVCC_ORIGIN", ""),
                       loadpoint=os.environ.get("PULSE_EVCC_LOADPOINT", ""),
                       poll_seconds=int(os.environ.get("PULSE_EVCC_POLL_SECONDS", "60")),
                       history_seconds=int(os.environ.get("PULSE_EVCC_HISTORY_SECONDS", "600")),
                       history_months=int(os.environ.get("PULSE_EVCC_HISTORY_MONTHS", "3")),
                       energy_history_enabled=energy_history_enabled == "true")
        except (TypeError, ValueError):
            raise ValueError("La configuración evcc no es válida; revisa los campos y límites.") from None


def _number(value, maximum, *, integer=False):
    if type(value) not in (int, float) or not 0 <= value <= maximum or not math.isfinite(value):
        raise EvccError()
    if integer and int(value) != value:
        raise EvccError()
    return int(value) if integer else float(value)


def _optional(row, key, maximum, *, integer=False):
    value = row.get(key)
    return None if value is None else _number(value, maximum, integer=integer)


def _instant(raw, now):
    if not isinstance(raw, str) or len(raw) > 64:
        raise EvccError()
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if value.tzinfo is None or value.year < 2014:
            raise ValueError
        value = value.astimezone(timezone.utc)
        if (value - now).total_seconds() > 60:
            raise ValueError
        return value
    except (ValueError, OverflowError):
        raise EvccError() from None


def iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_state(payload, config: EvccConfig, *, received_at: datetime):
    if not isinstance(payload, dict) or not isinstance(payload.get("loadpoints"), list):
        raise EvccError()
    points = payload["loadpoints"]
    if len(points) > 128:
        raise EvccError("large")
    # History uses the public loadpoint title, rather than a release-dependent index/name.
    selected = [point for point in points if isinstance(point, dict) and point.get("title") == config.loadpoint]
    if len(selected) != 1:
        raise EvccError()
    point = selected[0]
    if type(point.get("connected")) is not bool or type(point.get("charging")) is not bool:
        raise EvccError()
    if point["charging"] and not point["connected"]:
        raise EvccError()
    return {"received_at": iso(received_at), "loadpoint": config.loadpoint,
            "connected": point["connected"], "charging": point["charging"],
            "power_w": _optional(point, "chargePower", 1_000_000),
            # The current session field supersedes the deprecated alias. A
            # present null means unavailable; invalid current data must fail
            # closed instead of silently reviving a legacy value.
            "charged_kwh": _optional(point,
                "sessionEnergy" if "sessionEnergy" in point else "chargedEnergy", 500),
            "active_seconds": _optional(point, "chargeDuration", 31 * 86400)}


def parse_sessions(payload, config: EvccConfig, *, received_at: datetime):
    if not isinstance(payload, list) or len(payload) > 1000:
        raise EvccError("large" if isinstance(payload, list) else "contract")
    result = {}
    for row in payload:
        if not isinstance(row, dict) or not isinstance(row.get("loadpoint"), str):
            raise EvccError()
        if row["loadpoint"] != config.loadpoint:
            continue
        upstream_id = _number(row.get("id"), 2**53 - 1, integer=True)
        if upstream_id < 1:
            raise EvccError()
        start, end = _instant(row.get("created"), received_at), _instant(row.get("finished"), received_at)
        duration = (end - start).total_seconds()
        if not 0 < duration <= 31 * 86400:
            raise EvccError()
        energy = _number(row.get("chargedEnergy"), 500)
        # Historical JSON exposes Go time.Duration (integer nanoseconds), unlike
        # the live /state field documented in seconds. No magnitude guessing.
        raw_active = row.get("chargeDuration")
        if raw_active is None:
            active = None
        else:
            if type(raw_active) is not int or not 0 <= raw_active <= int(duration * 1_000_000_000):
                raise EvccError()
            active = raw_active / 1_000_000_000
        key = session_id(config.scope, upstream_id, start, config.loadpoint)
        item = {"id": key, "upstream_id": upstream_id, "loadpoint": config.loadpoint,
                "started_at": iso(start), "ended_at": iso(end), "kwh": energy,
                "active_seconds": active, "received_at": iso(received_at)}
        if key in result and result[key] != item:
            raise EvccError()
        result[key] = item
    return list(result.values())


def _aware_utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise EvccError()
    try:
        return value.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise EvccError() from None


def _energy_window(started_at, ended_at, received_at):
    start, end, received = (_aware_utc(value) for value in (started_at, ended_at, received_at))
    if (start.second or start.microsecond or end.second or end.microsecond
            or start.minute % 15 or end.minute % 15
            or end < start or (end - start).total_seconds() > 24 * 3600
            or end > received):
        raise EvccError()
    return start, end, received


def parse_energy_history(payload, config: EvccConfig, *, entity: str, started_at: datetime,
                         ended_at: datetime, received_at: datetime):
    """Normalize original 15-minute loadpoint candidates; these are not qualified evidence."""
    if not config.enabled or not config.energy_history_enabled:
        raise EvccError("access")
    if not isinstance(entity, str) or _ENTITY_RE.fullmatch(entity) is None:
        raise EvccError()
    start_bound, end_bound, received = _energy_window(started_at, ended_at, received_at)
    if not isinstance(payload, list) or len(payload) > 128:
        raise EvccError("large" if isinstance(payload, list) else "contract")
    if not payload:
        return []
    if len(payload) != 1:
        raise EvccError()
    series = payload[0]
    if not isinstance(series, dict) or series.get("group") != "loadpoint":
        raise EvccError()
    # A missing title cannot establish that the returned series is this loadpoint.
    if series.get("title") != config.loadpoint:
        raise EvccError()
    if "isTemp" in series and type(series["isTemp"]) is not bool:
        raise EvccError()
    if series.get("isTemp", False):
        raise EvccError()
    rows = series.get("data")
    if not isinstance(rows, list):
        raise EvccError()
    if len(rows) > 100:
        raise EvccError("large")
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise EvccError()
        slot_start = _instant(row.get("start"), received)
        slot_end = _instant(row.get("end"), received)
        if ((slot_start.minute % 15) or slot_start.second or slot_start.microsecond
                or (slot_end.minute % 15) or slot_end.second or slot_end.microsecond
                or (slot_end - slot_start).total_seconds() != 900
                or slot_start < start_bound or slot_end > end_bound):
            raise EvccError()
        energy = _number(row.get("energy"), 500)
        return_energy = _number(row.get("returnEnergy"), 500)
        if "socTemp" in row:
            soc_temp = row["socTemp"]
            if soc_temp is not None and (type(soc_temp) not in (int, float) or not math.isfinite(soc_temp)):
                raise EvccError()
        item = {"started_at": iso(slot_start), "ended_at": iso(slot_end), "kwh": energy,
                "return_kwh": return_energy, "received_at": iso(received)}
        key = (slot_start, slot_end)
        if key in result and result[key] != item:
            raise EvccError()
        result[key] = item
    return [result[key] for key in sorted(result)]


class EvccClient:
    """Fixed GET destinations, no credentials, redirects, env proxies or raw logging."""
    def __init__(self, config: EvccConfig, *, transport=None):
        self.config = config
        self.transport = transport

    async def _read(self, path: str, *, params=None):
        allowed = {"/api/state", "/api/sessions"}
        if self.config.energy_history_enabled:
            allowed.add("/api/history/energy")
        if not self.config.enabled or path not in allowed:
            raise EvccError("access")
        try:
            async with asyncio.timeout(8):
                async with httpx2.AsyncClient(transport=self.transport, trust_env=False,
                        follow_redirects=False, timeout=httpx2.Timeout(8, connect=3)) as client:
                    async with client.stream("GET", self.config.origin.rstrip("/") + path,
                                             params=params, headers={"Accept": "application/json"}) as response:
                        if response.status_code in {401, 403}:
                            raise EvccError("access")
                        if response.status_code != 200:
                            raise EvccError("network")
                        if "application/json" not in response.headers.get("Content-Type", "").lower():
                            raise EvccError()
                        parts, size = [], 0
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > 1024 * 1024:
                                raise EvccError("large")
                            parts.append(chunk)
            def unique_object(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise EvccError()
                    result[key] = value
                return result
            return json.loads(b"".join(parts), object_pairs_hook=unique_object,
                              parse_constant=lambda _value: (_ for _ in ()).throw(EvccError()))
        except EvccError:
            raise
        except (asyncio.TimeoutError, httpx2.TimeoutException):
            raise EvccError("timeout") from None
        except httpx2.HTTPError:
            raise EvccError("network") from None
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise EvccError() from None

    async def state(self, *, received_at: datetime):
        return parse_state(await self._read("/api/state"), self.config, received_at=received_at)

    async def state_with_identity(self, *, received_at: datetime):
        payload = await self._read("/api/state")
        observation = parse_state(payload, self.config, received_at=received_at)
        points = [point for point in payload["loadpoints"]
                  if isinstance(point, dict) and point.get("title") == self.config.loadpoint]
        name = points[0].get("name") if len(points) == 1 else None
        identity = ({"entity": name, "loadpoint": self.config.loadpoint,
                     "contract_revision": ENERGY_CONTRACT_REVISION}
                    if isinstance(name, str) and _ENTITY_RE.fullmatch(name) else None)
        return {"observation": observation, "identity": identity}

    async def energy_history(self, *, entity: str, started_at: datetime, ended_at: datetime,
                             received_at: datetime):
        if not self.config.enabled or not self.config.energy_history_enabled:
            raise EvccError("access")
        start, end, received = _energy_window(started_at, ended_at, received_at)
        if not isinstance(entity, str) or _ENTITY_RE.fullmatch(entity) is None:
            raise EvccError()
        params = {"from": iso(start), "to": iso(end), "aggregate": "15m", "grouped": "false",
                  "group": "loadpoint", "name": entity, "format": "json"}
        payload = await self._read("/api/history/energy", params=params)
        return parse_energy_history(payload, self.config, entity=entity, started_at=start,
                                    ended_at=end, received_at=received)

    async def sessions(self, *, year: int, month: int, received_at: datetime):
        return parse_sessions(await self._read("/api/sessions", params={"year": year, "month": month}),
                              self.config, received_at=received_at)
