from __future__ import annotations

import hashlib
import json
from typing import Any


_SHA256_CHARS = frozenset("0123456789abcdef")


def _require_sha256(value: Any, field_name: str) -> str:
    normalized = str(value or "").lower()
    if len(normalized) != 64 or any(
        char not in _SHA256_CHARS for char in normalized
    ):
        raise ValueError(f"{field_name} must be a lowercase SHA-256")
    return normalized


def runtime_identity_for_manifest(manifest: dict[str, Any]) -> str:
    """Return the sole runtime identity defined by a release manifest."""

    runtime = manifest.get("runtime")
    if not isinstance(runtime, dict):
        raise ValueError("release manifest runtime is missing")
    system_tools = runtime.get("system_tools")
    identity_path = (
        system_tools.get("identity")
        if isinstance(system_tools, dict)
        else None
    )
    if isinstance(identity_path, str) and identity_path:
        files = manifest.get("files")
        if not isinstance(files, list):
            raise ValueError("release manifest files are missing")
        matches = [
            row
            for row in files
            if isinstance(row, dict) and row.get("path") == identity_path
        ]
        if len(matches) != 1:
            raise ValueError(
                "release runtime identity file is missing or duplicated"
            )
        return _require_sha256(
            matches[0].get("sha256"),
            "runtime identity file sha256",
        )
    canonical = json.dumps(
        runtime,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
