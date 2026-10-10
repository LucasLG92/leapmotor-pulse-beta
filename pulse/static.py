"""Negotiated gzip sidecars for generic public build files, never API data."""

from __future__ import annotations

from pathlib import Path
import mimetypes
import re
import stat

from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, Response
from starlette.staticfiles import NotModifiedResponse, StaticFiles

_QUALITY = re.compile(r"(?:0(?:\.\d{0,3})?|1(?:\.0{0,3})?)\Z")


def _encoding_weights(headers: Headers) -> tuple[float, float]:
    """Return gzip and identity preferences; malformed weights are refused."""
    value = ", ".join(headers.getlist("accept-encoding"))
    if not value.strip():
        return 0, 1
    weights: dict[str, float] = {}
    for item in value.split(","):
        parts = [part.strip().lower() for part in item.split(";")]
        if parts[0] not in {"gzip", "identity", "*"}:
            continue
        weight = 1.0
        if len(parts) > 1:
            if len(parts) != 2 or not parts[1].startswith("q=") or not _QUALITY.fullmatch(parts[1][2:]):
                weight = 0
            else:
                weight = float(parts[1][2:])
        # Conflicting repeated entries cannot override an explicit refusal.
        weights[parts[0]] = min(weights.get(parts[0], weight), weight)
    gzip_weight = weights.get("gzip", weights.get("*", 0))
    identity_weight = weights.get("identity", 0 if weights.get("*") == 0 else 1)
    return gzip_weight, identity_weight


class PublicStaticFiles(StaticFiles):
    """Reuse Starlette's containment, methods, conditionals and file ranges."""

    async def get_response(self, path: str, scope) -> Response:
        if path.endswith(".gz"):
            raise HTTPException(status_code=404)
        return await super().get_response(path, scope)

    def file_response(self, full_path, stat_result, scope, status_code=200) -> Response:
        request_headers = Headers(scope=scope)
        gzip_weight, identity_weight = _encoding_weights(request_headers)
        selected_path, selected_stat = full_path, stat_result
        encoding = None
        if gzip_weight > 0 and gzip_weight >= identity_weight and "range" not in request_headers:
            relative = Path(full_path).relative_to(Path(self.directory).resolve()).as_posix()
            compressed_path, compressed_stat = self.lookup_path(relative + ".gz")
            if (compressed_stat and stat.S_ISREG(compressed_stat.st_mode)
                    and compressed_stat.st_size < stat_result.st_size
                    and compressed_stat.st_mtime_ns >= stat_result.st_mtime_ns):
                selected_path, selected_stat, encoding = compressed_path, compressed_stat, "gzip"
        if encoding is None and identity_weight == 0:
            return Response(status_code=406, headers={"Vary": "Accept-Encoding"})
        headers = {"Vary": "Accept-Encoding"}
        if encoding:
            headers["Content-Encoding"] = encoding
        media_type = "application/javascript" if str(full_path).endswith(".js") else None
        # MIME describes the original resource, not the .gz container.
        if media_type is None:
            media_type = mimetypes.guess_type(str(full_path))[0] or "application/octet-stream"
        response = FileResponse(selected_path, status_code=status_code, stat_result=selected_stat,
                                media_type=media_type, headers=headers)
        if self.is_not_modified(response.headers, request_headers):
            return NotModifiedResponse(response.headers)
        return response
