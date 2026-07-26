#!/usr/bin/env python3
"""Capture and live-validate the exact executable capability behind a stage.

The manifest binds the skill contract, declared entrypoints, their local Python
import closure, schemas/profiles/configuration, sanitized invocation, Python
runtime, and third-party distribution versions.  It deliberately does not hash
an entire skill directory: only declared or statically reachable execution
inputs belong to the capability identity.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import json
import platform
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


VERSION = "execution-capability/1.0.0"
SCHEMA_VERSION = "execution-capability-manifest/1.0"
SENSITIVE_FLAG = re.compile(r"(?:password|passwd|secret|token|api[-_]?key|access[-_]?key|secret[-_]?key|credential)", re.I)
URI_CREDENTIAL = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)(?P<userinfo>[^/@\s]+@)", re.I)


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite execution capability manifest: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def artifact(path: Path, role: str, skill_root: Path) -> dict[str, Any]:
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"capability input is not a file: {resolved}")
    try:
        logical = resolved.relative_to(skill_root).as_posix()
        basis = "skill_root"
    except ValueError:
        logical = str(resolved)
        basis = "absolute"
    return {
        "role": role,
        "path": str(resolved),
        "logical_path": logical,
        "path_basis": basis,
        "size_bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def sanitize_argv(argv: Iterable[str]) -> tuple[list[str], int]:
    values = [str(item) for item in argv]
    sanitized: list[str] = []
    redact_next = False
    redactions = 0
    for value in values:
        if redact_next:
            sanitized.append("<redacted>")
            redactions += 1
            redact_next = False
            continue
        if value.startswith("--") and "=" in value:
            flag, payload = value.split("=", 1)
            if SENSITIVE_FLAG.search(flag):
                sanitized.append(f"{flag}=<redacted>")
                redactions += 1
                continue
            value = f"{flag}={URI_CREDENTIAL.sub(lambda m: m.group('scheme') + '<redacted>@', payload)}"
        elif value.startswith("--") and SENSITIVE_FLAG.search(value):
            sanitized.append(value)
            redact_next = True
            continue
        elif "=" in value and SENSITIVE_FLAG.search(value.split("=", 1)[0]):
            sanitized.append(value.split("=", 1)[0] + "=<redacted>")
            redactions += 1
            continue
        replaced = URI_CREDENTIAL.sub(lambda m: m.group("scheme") + "<redacted>@", value)
        if replaced != value:
            redactions += 1
        sanitized.append(replaced)
    if redact_next:
        raise ValueError("sensitive invocation flag has no value")
    return sanitized, redactions


def _candidate_local_modules(import_name: str, current: Path, skill_root: Path) -> list[Path]:
    relative = Path(*import_name.split("."))
    roots = [current.parent, skill_root, skill_root / "scripts"]
    candidates: list[Path] = []
    for root in roots:
        candidates.extend((root / relative.with_suffix(".py"), root / relative / "__init__.py"))
    return candidates


def _imports(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise ValueError(f"cannot statically inspect Python capability input {path}: {exc}") from exc
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
    return names


def local_code_closure(entrypoints: list[Path], skill_root: Path) -> tuple[list[Path], set[str]]:
    pending = [path.resolve() for path in entrypoints]
    seen: set[Path] = set()
    external_imports: set[str] = set()
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        if not path.is_file():
            raise FileNotFoundError(f"execution entrypoint or local module is missing: {path}")
        seen.add(path)
        for name in _imports(path):
            local = next((candidate.resolve() for candidate in _candidate_local_modules(name, path, skill_root) if candidate.is_file()), None)
            if local:
                if local not in seen:
                    pending.append(local)
            else:
                external_imports.add(name.split(".", 1)[0])
    return sorted(seen, key=str), external_imports


def dependency_inventory(import_names: set[str]) -> list[dict[str, Any]]:
    standard = getattr(sys, "stdlib_module_names", set())
    distributions = importlib.metadata.packages_distributions()
    inventory: list[dict[str, Any]] = []
    for name in sorted(import_names):
        if name in standard or name == "__future__":
            inventory.append({"import_name": name, "kind": "standard_library", "distribution": None, "version": None})
            continue
        owners = sorted(distributions.get(name, []))
        if not owners:
            inventory.append({"import_name": name, "kind": "unresolved_external", "distribution": None, "version": None})
            continue
        for owner in owners:
            try:
                version = importlib.metadata.version(owner)
            except importlib.metadata.PackageNotFoundError:
                version = None
            inventory.append({"import_name": name, "kind": "third_party", "distribution": owner, "version": version})
    return inventory


def runtime_inventory() -> dict[str, Any]:
    executable = Path(sys.executable).resolve()
    return {
        "python": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
            "cache_tag": sys.implementation.cache_tag,
            "executable_path": str(executable),
            "executable_sha256": sha256_file(executable),
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
    }


def manifest_payload(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key not in {"generated_at", "payload_hash"}}


def build_manifest(
    *, manifest_id: str, skill_root: Path, entrypoints: list[tuple[str, Path]],
    resources: list[tuple[str, Path]], invocation: list[str], producer: str,
) -> dict[str, Any]:
    skill_root = skill_root.resolve()
    if not (skill_root / "SKILL.md").is_file():
        raise FileNotFoundError(f"skill root lacks SKILL.md: {skill_root}")
    if not entrypoints:
        raise ValueError("at least one execution entrypoint is required")
    entry_paths = [path.resolve() for _, path in entrypoints]
    closure, external_imports = local_code_closure(entry_paths, skill_root)
    contracts = [artifact(skill_root / "SKILL.md", "skill_contract", skill_root)]
    if (skill_root / "agents/openai.yaml").is_file():
        contracts.append(artifact(skill_root / "agents/openai.yaml", "skill_interface", skill_root))
    entry_records = [artifact(path, role, skill_root) for role, path in entrypoints]
    entry_set = {item["path"] for item in entry_records}
    closure_records = [artifact(path, "local_python_module", skill_root) for path in closure if str(path) not in entry_set]
    resource_records = [artifact(path, role, skill_root) for role, path in resources]
    sanitized, redactions = sanitize_argv(invocation)
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "manifest_id": manifest_id,
        "generated_at": now(),
        "status": "passed",
        "producer": producer,
        "skill": {"name": skill_root.name, "root": str(skill_root), "contracts": contracts},
        "entrypoints": entry_records,
        "local_code_closure": closure_records,
        "resources": resource_records,
        "runtime": runtime_inventory(),
        "dependencies": dependency_inventory(external_imports),
        "invocation": {
            "argv": sanitized,
            "argv_sha256": canonical_hash(sanitized),
            "redaction_count": redactions,
            "environment_values_recorded": False,
        },
        "closure_policy": {
            "identity_scope": "skill contracts plus declared entrypoints plus statically reachable local Python modules plus declared schemas/profiles/configuration plus runtime and dependencies",
            "whole_skill_directory_hashed": False,
            "undeclared_local_imports_allowed": False,
            "secrets_recorded": False,
        },
        "summary": {
            "skill_contracts": len(contracts),
            "entrypoints": len(entry_records),
            "local_modules": len(closure_records),
            "resources": len(resource_records),
            "dependencies": len(dependency_inventory(external_imports)),
            "redactions": redactions,
        },
        "payload_hash": "",
    }
    manifest["payload_hash"] = canonical_hash(manifest_payload(manifest))
    return manifest


def validate_manifest(path: Path) -> dict[str, Any]:
    stored = read_json(path.resolve())
    if stored.get("schema_version") != SCHEMA_VERSION or stored.get("status") != "passed":
        raise ValueError("unsupported or non-passed execution capability manifest")
    if stored.get("payload_hash") != canonical_hash(manifest_payload(stored)):
        raise ValueError("execution capability manifest payload hash is invalid")
    skill = stored.get("skill", {})
    entrypoints = [(item["role"], Path(item["path"])) for item in stored.get("entrypoints", [])]
    resources = [(item["role"], Path(item["path"])) for item in stored.get("resources", [])]
    rebuilt = build_manifest(
        manifest_id=stored["manifest_id"], skill_root=Path(skill["root"]), entrypoints=entrypoints,
        resources=resources, invocation=stored.get("invocation", {}).get("argv", []), producer=stored["producer"],
    )
    expected = manifest_payload(stored)
    actual = manifest_payload(rebuilt)
    if actual != expected:
        categories = [key for key in sorted(set(actual) | set(expected)) if actual.get(key) != expected.get(key)]
        raise ValueError(f"execution capability drift detected in: {categories}")
    return {
        "status": "passed",
        "manifest_id": stored["manifest_id"],
        "payload_hash": stored["payload_hash"],
        "entrypoints": len(stored.get("entrypoints", [])),
        "local_modules": len(stored.get("local_code_closure", [])),
        "resources": len(stored.get("resources", [])),
        "dependencies": len(stored.get("dependencies", [])),
        "live_rehash": True,
    }


def parse_role_paths(values: list[str]) -> list[tuple[str, Path]]:
    result: list[tuple[str, Path]] = []
    for value in values:
        if "=" not in value:
            raise ValueError(f"expected ROLE=PATH: {value}")
        role, raw = value.split("=", 1)
        if not role:
            raise ValueError(f"empty role in: {value}")
        result.append((role, Path(raw)))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    capture = sub.add_parser("capture")
    capture.add_argument("--manifest-id", required=True)
    capture.add_argument("--skill-root", type=Path, required=True)
    capture.add_argument("--entrypoint", action="append", default=[], required=True)
    capture.add_argument("--resource", action="append", default=[])
    capture.add_argument("--invocation-arg", action="append", default=[])
    capture.add_argument("--producer", default=VERSION)
    capture.add_argument("--output", type=Path, required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "capture":
            result = build_manifest(
                manifest_id=args.manifest_id, skill_root=args.skill_root,
                entrypoints=parse_role_paths(args.entrypoint), resources=parse_role_paths(args.resource),
                invocation=args.invocation_arg, producer=args.producer,
            )
            write_json(args.output.resolve(), result)
        else:
            result = validate_manifest(args.manifest)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "failed", "tool": VERSION, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
