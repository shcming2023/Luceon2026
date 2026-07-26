#!/usr/bin/env python3
"""Emit and verify the immutable ordinary Worker V3 runtime identity."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
from typing import Any
from urllib.parse import quote
import uuid


APP_ROOT = Path(os.getenv("WORKER_V3_APP_ROOT", "/app"))
PYTHON_LOCK = APP_ROOT / "requirements-worker-v3.lock"
SYSTEM_LOCK = APP_ROOT / "worker-v3-system-packages.lock"
IDENTITY_SCHEMA = "luceon.worker-v3-runtime-identity/v1"
CONTROL_PLANE_SCHEMA = "luceon.worker-v3-control-plane-baseline/v1"
CONTROL_PLANE_MANIFEST = Path(
    os.getenv(
        "WORKER_V3_CONTROL_PLANE_MANIFEST",
        "/opt/worker-v3/control-plane-baseline.json",
    )
)
SBOM_COMPONENT_NAME = "luceonweb2026-worker-v3-runtime"
_IMAGE_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")

REQUIRED_TOOLS: dict[str, tuple[str, ...]] = {
    "xelatex": ("xelatex", "--version"),
    "latexmk": ("latexmk", "-v"),
    "pdftoppm": ("pdftoppm", "-v"),
    "pdfinfo": ("pdfinfo", "-v"),
    "qpdf": ("qpdf", "--version"),
    "ghostscript": ("gs", "--version"),
    "biber": ("biber", "--version"),
    "fontconfig": ("fc-list", "--version"),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _control_plane_records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for root_name in ("app", "scripts"):
        root = APP_ROOT / root_name
        if not root.is_dir() or root.is_symlink():
            raise ValueError(f"control-plane root is unavailable: {root}")
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(APP_ROOT).as_posix()
            if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
                continue
            if path.is_symlink():
                raise ValueError(f"control-plane symlink is forbidden: {relative}")
            if not path.is_file():
                continue
            records.append(
                {
                    "path": relative,
                    "bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                }
            )
    if not records:
        raise ValueError("control-plane baseline contains no files")
    return records


def _control_plane_tree_sha256(records: list[dict[str, Any]]) -> str:
    payload = json.dumps(
        records,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def write_control_plane_baseline(path: Path | None = None) -> dict[str, Any]:
    path = path or CONTROL_PLANE_MANIFEST
    records = _control_plane_records()
    payload = {
        "schema": CONTROL_PLANE_SCHEMA,
        "tree_sha256": _control_plane_tree_sha256(records),
        "files": records,
    }
    _write_json(path, payload)
    return payload


def measure_control_plane_baseline(
    path: Path | None = None,
) -> dict[str, Any]:
    path = path or CONTROL_PLANE_MANIFEST
    try:
        expected = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"control-plane manifest is unavailable: {exc}") from exc
    if (
        not isinstance(expected, dict)
        or expected.get("schema") != CONTROL_PLANE_SCHEMA
        or not isinstance(expected.get("files"), list)
        or not _IMAGE_DIGEST_RE.fullmatch(
            "sha256:" + str(expected.get("tree_sha256") or "")
        )
    ):
        raise ValueError("control-plane manifest is invalid")
    expected_records = expected["files"]
    if _control_plane_tree_sha256(expected_records) != expected["tree_sha256"]:
        raise ValueError("control-plane manifest tree hash is invalid")
    actual_records = _control_plane_records()
    actual_tree = _control_plane_tree_sha256(actual_records)
    return {
        "manifest_path": str(path),
        "manifest_sha256": _sha256(path),
        "expected_tree_sha256": expected["tree_sha256"],
        "actual_tree_sha256": actual_tree,
        "matches": actual_records == expected_records,
    }


def _runtime_image_identity() -> tuple[str | None, bool]:
    reference = os.getenv("WORKER_V3_IMAGE_REFERENCE", "").strip()
    if _IMAGE_DIGEST_RE.fullmatch(reference):
        return reference, True
    if "@" not in reference:
        return None, False
    repository, digest = reference.rsplit("@", 1)
    repository_leaf = repository.rsplit("/", 1)[-1]
    pinned = bool(
        repository
        and repository_leaf
        and ":" not in repository_leaf
        and _IMAGE_DIGEST_RE.fullmatch(digest)
        and reference == f"{repository}@{digest}"
    )
    return (digest if pinned else None), pinned


def _locked_system_packages(path: Path) -> dict[str, str]:
    packages: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, version = line.partition("=")
        if not separator or not name or not version:
            raise ValueError(f"invalid system package lock line: {raw_line!r}")
        packages[name] = version
    return packages


def _installed_system_package(name: str) -> str | None:
    result = subprocess.run(
        ["dpkg-query", "-W", "-f=${Version}", name],
        text=True,
        capture_output=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _all_installed_system_packages() -> dict[str, str]:
    result = subprocess.run(
        ["dpkg-query", "-W", "-f=${binary:Package}\\t${Version}\\n"],
        text=True,
        capture_output=True,
        check=True,
    )
    packages: dict[str, str] = {}
    for line in result.stdout.splitlines():
        name, separator, version = line.partition("\t")
        if separator and name and version:
            packages[name] = version
    return packages


def _tool_version(command: tuple[str, ...]) -> dict[str, Any]:
    binary = shutil.which(command[0])
    if binary is None:
        return {"available": False, "path": None, "version": None}
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    combined = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
    first_line = combined.splitlines()[0] if combined else ""
    return {
        "available": result.returncode == 0,
        "path": binary,
        "version": first_line,
    }


def _python_components() -> list[dict[str, str]]:
    components = []
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name")
        if name:
            components.append(
                {
                    "type": "library",
                    "name": name,
                    "version": distribution.version,
                    "purl": f"pkg:pypi/{name.lower().replace('_', '-')}@{distribution.version}",
                    "properties": [{"name": "luceon.component.ecosystem", "value": "python"}],
                }
            )
    return sorted(components, key=lambda item: (item["name"].lower(), item["version"]))


def build_identity() -> dict[str, Any]:
    locked_packages = _locked_system_packages(SYSTEM_LOCK)
    image_digest, image_reference_pinned = _runtime_image_identity()
    installed_packages = {
        name: _installed_system_package(name)
        for name in sorted(locked_packages)
    }
    return {
        "schema": IDENTITY_SCHEMA,
        "runtime_id": os.getenv("WORKER_V3_RUNTIME_ID", "unbound"),
        "base_image": os.getenv("WORKER_V3_BASE_IMAGE", "unknown"),
        "image_digest": image_digest,
        "image_reference_pinned": image_reference_pinned,
        "architecture": platform.machine(),
        "python": platform.python_version(),
        "effective_uid": os.geteuid(),
        "non_root": os.geteuid() != 0,
        "locks": {
            "python": {
                "path": str(PYTHON_LOCK),
                "sha256": _sha256(PYTHON_LOCK),
                "expected_sha256": os.getenv("WORKER_V3_PYTHON_LOCK_SHA256"),
            },
            "system": {
                "path": str(SYSTEM_LOCK),
                "sha256": _sha256(SYSTEM_LOCK),
                "expected_sha256": os.getenv("WORKER_V3_SYSTEM_LOCK_SHA256"),
            },
        },
        "system_packages": {
            name: {
                "locked": locked_packages[name],
                "installed": installed_packages[name],
                "matches": installed_packages[name] == locked_packages[name],
            }
            for name in sorted(locked_packages)
        },
        "tools": {
            name: _tool_version(command)
            for name, command in REQUIRED_TOOLS.items()
        },
        "control_plane": measure_control_plane_baseline(),
    }


def build_sbom(identity: dict[str, Any]) -> dict[str, Any]:
    os_components = [
        {
            "type": "library",
            "name": name,
            "version": version,
            "purl": f"pkg:deb/debian/{quote(name, safe='')}@{quote(version, safe='')}?distro=trixie",
            "properties": [{"name": "luceon.component.ecosystem", "value": "debian"}],
        }
        for name, version in _all_installed_system_packages().items()
    ]
    serial = os.getenv("WORKER_V3_SBOM_UUID") or str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"urn:luceon:{identity['runtime_id']}:{identity['locks']}")
    )
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{serial}",
        "version": 1,
        "metadata": {
            "component": {
                "type": "container",
                "name": SBOM_COMPONENT_NAME,
                "version": identity["runtime_id"],
                "properties": [
                    {"name": "luceon.runtime.schema", "value": IDENTITY_SCHEMA},
                    {"name": "luceon.base.image", "value": identity["base_image"]},
                ],
            }
        },
        "components": sorted(
            os_components + _python_components(),
            key=lambda item: (item["name"].lower(), item["version"]),
        ),
    }


def validate(identity: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if str(identity.get("runtime_id") or "") in {"", "unbound"}:
        errors.append("runtime_id_unbound")
    if str(identity.get("base_image") or "") in {"", "unknown"}:
        errors.append("base_image_unknown")
    if not _IMAGE_DIGEST_RE.fullmatch(str(identity.get("image_digest") or "")):
        errors.append("container_image_digest_missing_or_invalid")
    if identity.get("image_reference_pinned") is not True:
        errors.append("container_image_reference_not_digest_pinned")
    control_plane = identity.get("control_plane")
    if (
        not isinstance(control_plane, dict)
        or not _IMAGE_DIGEST_RE.fullmatch(
            "sha256:" + str(control_plane.get("actual_tree_sha256") or "")
        )
    ):
        errors.append("control_plane_baseline_missing_or_invalid")
    elif control_plane.get("matches") is not True:
        errors.append("control_plane_baseline_mismatch")
    if not identity.get("non_root"):
        errors.append("runtime_must_not_run_as_root")
    for lock_name, row in identity.get("locks", {}).items():
        expected = row["expected_sha256"]
        if not expected:
            errors.append(f"{lock_name}_lock_expected_sha256_missing")
        elif expected != row["sha256"]:
            errors.append(f"{lock_name}_lock_sha256_mismatch")
    for name, row in identity.get("system_packages", {}).items():
        if not row["matches"]:
            errors.append(f"system_package_version_mismatch:{name}")
    for name, row in identity.get("tools", {}).items():
        if not row["available"]:
            errors.append(f"required_tool_unavailable:{name}")
    return errors


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--sbom", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--emit-control-plane-baseline", type=Path)
    args = parser.parse_args()

    if args.emit_control_plane_baseline:
        write_control_plane_baseline(args.emit_control_plane_baseline)
        return 0
    identity = build_identity()
    errors = validate(identity) if args.check else []
    identity["validation"] = {"status": "passed" if not errors else "failed", "errors": errors}
    if args.output:
        _write_json(args.output, identity)
    if args.sbom:
        _write_json(args.sbom, build_sbom(identity))
    if not args.output:
        print(json.dumps(identity, ensure_ascii=False, sort_keys=True))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
