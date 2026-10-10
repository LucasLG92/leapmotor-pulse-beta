"""Opt-in, metadata-only checks for externally supplied SDK material paths."""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import re
import stat
from typing import Any


_MATERIAL_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")


def inspect_certificate_path_metadata(paths: Mapping[str, Any]) -> dict[str, Any]:
    """Check configured paths with OS metadata only; never open or return them.

    This helper is opt-in for a future trusted SDK bootstrap. The default
    connection status and provider construction do not call it.
    """
    if not isinstance(paths, Mapping) or not paths:
        return {"ready": False, "paths": {}, "reason": "invalid_or_missing_structure"}

    results: dict[str, str] = {}
    for name, configured_path in paths.items():
        if not isinstance(name, str) or not _MATERIAL_NAME.fullmatch(name):
            return {"ready": False, "paths": {}, "reason": "invalid_or_missing_structure"}
        try:
            path_value = os.fspath(configured_path)
        except TypeError:
            results[name] = "invalid_path"
            continue
        if not isinstance(path_value, str) or not path_value or "\x00" in path_value:
            results[name] = "invalid_path"
            continue

        try:
            metadata = Path(path_value).stat()
        except (OSError, ValueError):
            results[name] = "unavailable"
            continue
        if not stat.S_ISREG(metadata.st_mode):
            results[name] = "not_regular_file"
        elif not os.access(path_value, os.R_OK):
            results[name] = "unreadable"
        else:
            results[name] = "ready"

    ready = all(result == "ready" for result in results.values())
    return {
        "ready": ready,
        "paths": results,
        "reason": "metadata_ready" if ready else "material_path_unavailable",
    }
