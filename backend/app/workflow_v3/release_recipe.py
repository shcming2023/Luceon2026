from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import os
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

from .release import (
    ENTRYPOINT_CLASSES,
    REQUIRED_FORMAL_STAGES,
    REQUIRED_DIRECTORIES,
    SCHEMA_VERSION as RELEASE_SCHEMA_VERSION,
    STRICT_LIMITS,
    TREE_HASH_ALGORITHM,
)
from .pricing import PricingError, validate_release_pricing


RECIPE_SCHEMA_VERSION = "luceon.worker-v3-release-recipe/v1"
SOURCE_TREE_HASH_ALGORITHM = "sha256-source-file-records-v1"
EXECUTABLE_BASELINE_HASH_ALGORITHM = "sha256-executable-baseline-file-records-v1"
ENTRYPOINT_PROTOCOL = "luceon.worker-v3-stage-entrypoint/v1"
REQUIRED_STAGES = REQUIRED_FORMAL_STAGES
REQUIRED_SKILLS = (
    "luceon-popo-to-refined-elegantbook",
    "pdf-clean-markdown-rebuild",
    "material-semantic-annotator",
    "cleanlatex-to-elegantbook",
    "refine-elegantbook-latex",
    "finished-textbook-final-review",
)
SOURCE_ROLES = (
    "executable_baseline",
    "normative_contract",
    "runtime_evidence",
    "template",
    "provenance_only",
    "supporting_evidence",
)
_SHA256_LENGTH = 64
_NOISE_NAMES = {
    ".DS_Store",
    ".pytest_cache",
    "__pycache__",
    "Thumbs.db",
}
_NOISE_SUFFIXES = (".pyc", ".pyo", ".swp", ".swo", "~")
_MAX_SELECTED_FILES = 20_000
_MAX_SELECTED_FILE_BYTES = 100_000_000
QUALIFICATION_EVIDENCE_TYPES = (
    "visual_full_page_provider",
    "spec05_final_image_real_material",
)
QUALIFICATION_EVIDENCE_SCHEMA_VERSIONS = {
    "visual_full_page_provider": (
        "luceon.worker-v3.qualification.visual-full-page-provider/v1"
    ),
    "spec05_final_image_real_material": (
        "luceon.worker-v3.qualification.spec05-final-image-real-material/v1"
    ),
}
_QUALIFICATION_MANIFEST_CATEGORIES = {
    "visual_full_page_provider": "eval",
    "spec05_final_image_real_material": "uat",
}
_QUALIFICATION_GAPS = {
    "visual_full_page_provider": (
        "full_page_review_evidence_provider_unqualified",
        "independent_full_page_review",
    ),
    "spec05_final_image_real_material": (
        "spec05_worker_v3_runtime_qualification_pending",
        "deterministic_elegantbook",
    ),
}


class ReleaseRecipeError(ValueError):
    """The release recipe or one of its immutable sources is unsafe or invalid."""


@dataclass(frozen=True)
class PlannedFile:
    source_id: str
    destination: str
    payload: bytes
    executable: bool
    source_role: str = "generated"

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.payload).hexdigest()


@dataclass(frozen=True)
class RecipeAudit:
    recipe: dict[str, Any]
    source_roots: tuple[Path, ...]
    planned_files: tuple[PlannedFile, ...]
    source_evidence: tuple[dict[str, Any], ...]
    entrypoint_evidence: tuple[dict[str, Any], ...]
    known_gaps: tuple[dict[str, str], ...]

    @property
    def status(self) -> str:
        return "incomplete" if self.known_gaps else self.recipe["release"]["requested_status"]


def _fail(message: str) -> None:
    raise ReleaseRecipeError(message)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256(value: Any, *, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        _fail(f"{field} must be a lowercase SHA-256")
    return value


def _mapping(value: Any, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{field} must be an object")
    return value


def _list(value: Any, *, field: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(f"{field} must be an array")
    return value


def _relative(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or value.startswith("/"):
        _fail(f"{field} must be a non-empty relative POSIX path")
    path = PurePosixPath(value)
    if str(path) != value or any(part in {"", ".", ".."} for part in path.parts):
        _fail(f"{field} is not normalized: {value!r}")
    return value


def _source_relative(value: Any, *, field: str) -> str:
    relative = _relative(value, field=field)
    if _is_noise_path(PurePosixPath(relative)):
        _fail(f"{field} points to excluded source noise: {relative!r}")
    return relative


def _is_noise_path(path: PurePosixPath) -> bool:
    for part in path.parts:
        if part.startswith(".") or part in _NOISE_NAMES or part.endswith(_NOISE_SUFFIXES):
            return True
    return False


def _selected(path: str, include: list[str], exclude: list[str]) -> bool:
    return (not include or any(fnmatch.fnmatchcase(path, pattern) for pattern in include)) and not any(
        fnmatch.fnmatchcase(path, pattern) for pattern in exclude
    )


def _canonical_source_tree(records: Iterable[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    for record in sorted(records, key=lambda item: str(item["path"])):
        canonical = {
            "bytes": record["bytes"],
            "path": record["path"],
            "sha256": record["sha256"],
        }
        digest.update(
            json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        digest.update(b"\n")
    return digest.hexdigest()


def _patterns(raw: Mapping[str, Any], field: str) -> list[str]:
    values = raw.get(field, [])
    if not isinstance(values, list) or any(not isinstance(item, str) or not item for item in values):
        _fail(f"{field} must contain strings")
    return values


def _walk_tree(root: Path, *, include: list[str], exclude: list[str]) -> list[tuple[str, bytes]]:
    if root.is_symlink() or not root.is_dir():
        _fail(f"tree source is not a real directory: {root}")
    selected: list[tuple[str, bytes]] = []
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in [*dirnames, *filenames]:
            candidate = current_path / name
            if candidate.is_symlink():
                _fail(f"symlinks are forbidden in source trees: {candidate}")
        dirnames[:] = sorted(
            name
            for name in dirnames
            if not _is_noise_path(PurePosixPath((current_path / name).relative_to(root).as_posix()))
        )
        for name in sorted(filenames):
            candidate = current_path / name
            relative = candidate.relative_to(root).as_posix()
            relative_path = PurePosixPath(relative)
            if _is_noise_path(relative_path) or not _selected(relative, include, exclude):
                continue
            if not stat.S_ISREG(os.stat(candidate, follow_symlinks=False).st_mode):
                _fail(f"non-regular source file is forbidden: {candidate}")
            size = candidate.stat().st_size
            if size > _MAX_SELECTED_FILE_BYTES:
                _fail(f"selected source file is too large: {candidate}")
            selected.append((relative, candidate.read_bytes()))
            if len(selected) > _MAX_SELECTED_FILES:
                _fail("too many selected source files")
    if not selected:
        _fail(f"tree source selected no files: {root}")
    return selected


def _safe_zip_member(name: str) -> str:
    if name.endswith("/"):
        name = name[:-1]
    if not name or "\\" in name or name.startswith("/"):
        _fail(f"unsafe ZIP member: {name!r}")
    parsed = PurePosixPath(name)
    if str(parsed) != name or any(part in {"", ".", ".."} for part in parsed.parts):
        _fail(f"unsafe ZIP member: {name!r}")
    return name


def _walk_zip(
    archive_path: Path,
    *,
    member_prefix: str,
    include: list[str],
    exclude: list[str],
) -> list[tuple[str, bytes]]:
    prefix = _relative(member_prefix.rstrip("/"), field="member_prefix") + "/"
    selected: list[tuple[str, bytes]] = []
    names: set[str] = set()
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for info in archive.infolist():
                name = _safe_zip_member(info.filename)
                if name in names:
                    _fail(f"duplicate ZIP member: {name!r}")
                names.add(name)
                unix_type = (info.external_attr >> 16) & 0o170000
                if unix_type == stat.S_IFLNK:
                    _fail(f"ZIP symlinks are forbidden: {name!r}")
                if info.is_dir() or not name.startswith(prefix):
                    continue
                relative = name[len(prefix) :]
                if not relative or _is_noise_path(PurePosixPath(relative)):
                    continue
                if not _selected(relative, include, exclude):
                    continue
                if info.file_size > _MAX_SELECTED_FILE_BYTES:
                    _fail(f"selected ZIP member is too large: {name!r}")
                payload = archive.read(info)
                if len(payload) != info.file_size:
                    _fail(f"ZIP member size mismatch: {name!r}")
                selected.append((relative, payload))
                if len(selected) > _MAX_SELECTED_FILES:
                    _fail("too many selected ZIP members")
    except (OSError, zipfile.BadZipFile) as exc:
        _fail(f"cannot read ZIP source {archive_path}: {exc}")
    if not selected:
        _fail(f"ZIP source selected no files: {archive_path}")
    return selected


def _tree_records(files: Iterable[tuple[str, bytes]]) -> list[dict[str, Any]]:
    return [
        {"path": path, "bytes": len(payload), "sha256": _sha256_bytes(payload)}
        for path, payload in sorted(files)
    ]


def _resolve_source(
    raw: Mapping[str, Any],
    *,
    roots: Mapping[str, Path],
) -> tuple[list[PlannedFile], dict[str, Any]]:
    source_id = raw.get("id")
    if not isinstance(source_id, str) or not source_id:
        _fail("every source must have a non-empty id")
    kind = raw.get("kind")
    if kind not in {"file", "tree", "zip_tree"}:
        _fail(f"source {source_id!r} has unsupported kind {kind!r}")
    root_id = raw.get("root")
    if not isinstance(root_id, str) or root_id not in roots:
        _fail(f"source {source_id!r} references unknown root {root_id!r}")
    relative_source = _source_relative(raw.get("path"), field=f"sources.{source_id}.path")
    source_path = roots[root_id]
    for part in PurePosixPath(relative_source).parts:
        source_path /= part
        if source_path.is_symlink():
            _fail(f"source links are forbidden: {source_path}")
    destination = _relative(raw.get("destination"), field=f"sources.{source_id}.destination")
    if destination.split("/", 1)[0] not in REQUIRED_DIRECTORIES:
        _fail(f"source {source_id!r} destination is outside the standard release layout")
    source_role = raw.get("source_role", "supporting_evidence")
    if source_role not in SOURCE_ROLES:
        _fail(
            f"source {source_id!r} has unsupported source_role {source_role!r}"
        )
    include = _patterns(raw, "include")
    exclude = _patterns(raw, "exclude")
    executable = bool(raw.get("executable", False))
    if source_role == "provenance_only":
        if kind != "file":
            _fail(
                f"provenance-only source {source_id!r} must preserve one immutable "
                "file rather than extract a runnable tree"
            )
        if not destination.startswith("references/provenance/"):
            _fail(
                f"provenance-only source {source_id!r} must be confined under "
                "references/provenance/"
            )
        if executable:
            _fail(f"provenance-only source {source_id!r} cannot be executable")

    if kind == "file":
        if include or exclude:
            _fail(f"file source {source_id!r} cannot use include/exclude")
        expected = _sha256(raw.get("expected_sha256"), field=f"sources.{source_id}.expected_sha256")
        if not source_path.is_file():
            _fail(f"file source is missing: {source_path}")
        if source_path.stat().st_size > _MAX_SELECTED_FILE_BYTES:
            _fail(f"selected source file is too large: {source_path}")
        actual = _sha256_file(source_path)
        if actual != expected:
            _fail(f"source {source_id!r} SHA-256 mismatch: expected {expected}, got {actual}")
        payload = source_path.read_bytes()
        planned = [
            PlannedFile(
                source_id,
                destination,
                payload,
                executable,
                str(source_role),
            )
        ]
        return planned, {
            "id": source_id,
            "kind": kind,
            "source_role": source_role,
            "source_sha256": actual,
            "selection_tree_sha256": None,
            "destination": destination,
            "file_count": 1,
            "bytes": len(payload),
        }

    expected_source = None
    if kind == "tree":
        files = _walk_tree(source_path, include=include, exclude=exclude)
    else:
        expected_source = _sha256(raw.get("expected_sha256"), field=f"sources.{source_id}.expected_sha256")
        if not source_path.is_file():
            _fail(f"ZIP source is missing: {source_path}")
        actual_source = _sha256_file(source_path)
        if actual_source != expected_source:
            _fail(
                f"source {source_id!r} SHA-256 mismatch: expected {expected_source}, got {actual_source}"
            )
        files = _walk_zip(
            source_path,
            member_prefix=raw.get("member_prefix", ""),
            include=include,
            exclude=exclude,
        )
    records = _tree_records(files)
    actual_tree = _canonical_source_tree(records)
    expected_tree = _sha256(
        raw.get("expected_tree_sha256"),
        field=f"sources.{source_id}.expected_tree_sha256",
    )
    if actual_tree != expected_tree:
        _fail(
            f"source {source_id!r} tree SHA-256 mismatch: expected {expected_tree}, got {actual_tree}"
        )
    planned = [
        PlannedFile(
            source_id,
            str(PurePosixPath(destination) / relative),
            payload,
            executable or str(PurePosixPath(destination)).split("/", 1)[0] == "scripts",
            str(source_role),
        )
        for relative, payload in files
    ]
    return planned, {
        "id": source_id,
        "kind": kind,
        "source_role": source_role,
        "source_sha256": expected_source,
        "selection_tree_sha256": actual_tree,
        "destination": destination,
        "file_count": len(planned),
        "bytes": sum(len(item.payload) for item in planned),
    }


def _json_payload(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _stage_request_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://luceon.local/schemas/worker-v3-stage-request-v1.json",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "run_id",
            "stage",
            "attempt",
            "release_id",
            "input_artifacts",
            "candidate_output_dir",
        ],
        "properties": {
            "run_id": {"type": "string", "minLength": 1},
            "stage": {"enum": list(REQUIRED_STAGES)},
            "attempt": {"type": "integer", "minimum": 1},
            "release_id": {"type": "string", "minLength": 1},
            "input_artifacts": {"type": "array", "items": {"type": "object"}},
            "candidate_output_dir": {"type": "string", "minLength": 1},
        },
    }


def _stage_result_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://luceon.local/schemas/worker-v3-stage-result-v1.json",
        "type": "object",
        "additionalProperties": False,
        "required": ["stage", "status", "candidate_artifacts", "findings"],
        "properties": {
            "stage": {"enum": list(REQUIRED_STAGES)},
            "status": {"enum": ["candidate_ready", "needs_review", "failed"]},
            "candidate_artifacts": {"type": "array", "items": {"type": "object"}},
            "findings": {"type": "array", "items": {"type": "object"}},
        },
    }


def _stage_evaluation_request_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://luceon.local/schemas/worker-v3-stage-evaluation-request-v1.json",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "mode",
            "job_id",
            "stage_key",
            "stage_version",
            "attempt",
            "candidate",
            "release_manifest_sha256",
            "policy_sha256",
            "required_gates",
            "output_manifest",
        ],
        "properties": {
            "schema_version": {
                "const": "luceon.worker-v3-evaluation-request/v1",
            },
            "mode": {"const": "evaluate"},
            "job_id": {"type": "string", "minLength": 1},
            "stage_key": {"enum": list(REQUIRED_STAGES)},
            "stage_version": {"type": "string", "minLength": 1},
            "attempt": {"type": "integer", "minimum": 1},
            "candidate": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "path", "sha256", "size_bytes"],
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "path": {"type": "string", "minLength": 1},
                    "sha256": {
                        "type": "string",
                        "pattern": "^[0-9a-f]{64}$",
                    },
                    "size_bytes": {"type": "integer", "minimum": 0},
                },
            },
            "release_manifest_sha256": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$",
            },
            "policy_sha256": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$",
            },
            "required_gates": {
                "type": "array",
                "minItems": 1,
                "uniqueItems": True,
                "items": {"type": "string", "minLength": 1},
            },
            "output_manifest": {"const": "evaluation-manifest.json"},
        },
    }


def _stage_evaluation_result_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://luceon.local/schemas/worker-v3-stage-evaluation-result-v1.json",
        "type": "object",
        "additionalProperties": False,
        "required": [
            "schema_version",
            "job_id",
            "stage_key",
            "attempt",
            "candidate_sha256",
            "release_manifest_sha256",
            "policy_sha256",
            "decision",
            "gate_results",
            "findings",
        ],
        "properties": {
            "schema_version": {
                "const": "luceon.worker-v3-stage-evaluation/v1",
            },
            "job_id": {"type": "string", "minLength": 1},
            "stage_key": {"enum": list(REQUIRED_STAGES)},
            "attempt": {"type": "integer", "minimum": 1},
            "candidate_sha256": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$",
            },
            "release_manifest_sha256": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$",
            },
            "policy_sha256": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$",
            },
            "decision": {"enum": ["passed", "failed"]},
            "gate_results": {
                "type": "object",
                "additionalProperties": {"type": "boolean"},
            },
            "findings": {
                "type": "array",
                "items": {"type": "object"},
            },
        },
    }


def _unqualified_runtime_evidence(field: str, reason: str) -> bytes:
    return _json_payload(
        {
            "schema_version": "luceon.worker-v3-runtime-identity-evidence/v1",
            "field": field,
            "qualified": False,
            "reason": reason,
        }
    )


def _entrypoint_static_audit(
    stage: str,
    execution_role: str,
    tool: PlannedFile | None,
) -> tuple[bool, list[str]]:
    if tool is None:
        return False, ["tool_missing"]
    findings: list[str] = []
    try:
        text = tool.payload.decode("utf-8")
    except UnicodeDecodeError:
        return False, ["tool_not_utf8"]
    try:
        syntax = ast.parse(text)
    except SyntaxError:
        return False, ["python_syntax_invalid"]
    assignments: dict[str, Any] = {}
    string_literals: set[str] = set()
    for node in ast.walk(syntax):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if isinstance(node.value, ast.Constant):
                assignments[node.targets[0].id] = node.value.value
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            string_literals.add(node.value)
    if assignments.get("WORKER_V3_ENTRYPOINT_PROTOCOL") != ENTRYPOINT_PROTOCOL:
        findings.append("worker_v3_protocol_marker_missing")
    if assignments.get("WORKER_V3_STAGE") != stage:
        findings.append("worker_v3_stage_marker_missing")
    if assignments.get("WORKER_V3_ENTRYPOINT_ROLE") != execution_role:
        findings.append("worker_v3_execution_role_marker_missing")
    if "--request" not in string_literals or "--result" not in string_literals:
        findings.append("request_result_cli_missing")
    return not findings, findings


def _is_execution_surface(path: str) -> bool:
    parts = PurePosixPath(path).parts
    if not parts:
        return False
    if parts[0] == "scripts":
        return True
    return len(parts) >= 4 and parts[0] == "skills" and parts[2] == "scripts"


def _audit_executable_baseline(
    recipe: Mapping[str, Any],
    planned_by_path: Mapping[str, PlannedFile],
    source_evidence: Iterable[Mapping[str, Any]],
) -> tuple[set[str], set[str], dict[str, Any]]:
    raw = _mapping(
        recipe.get("executable_baseline"),
        field="executable_baseline",
    )
    allowed_keys = {"source_ids", "policy"}
    if set(raw) != allowed_keys:
        _fail(
            "executable_baseline must contain exactly policy and source_ids"
        )
    if raw.get("policy") != "sole-authority":
        _fail("executable_baseline.policy must be 'sole-authority'")
    source_ids = _list(
        raw.get("source_ids"),
        field="executable_baseline.source_ids",
    )
    if (
        not source_ids
        or any(not isinstance(item, str) or not item for item in source_ids)
        or len(source_ids) != len(set(source_ids))
    ):
        _fail(
            "executable_baseline.source_ids must be a non-empty unique string array"
        )
    evidence_by_id = {str(row["id"]): row for row in source_evidence}
    unknown = sorted(set(source_ids) - set(evidence_by_id))
    if unknown:
        _fail(f"executable_baseline references unknown sources: {unknown}")
    role_ids = {
        source_id
        for source_id, row in evidence_by_id.items()
        if row["source_role"] == "executable_baseline"
    }
    if role_ids != set(source_ids):
        _fail(
            "executable_baseline.source_ids must exactly match sources classified "
            f"as executable_baseline; missing={sorted(role_ids - set(source_ids))}, "
            f"unexpected={sorted(set(source_ids) - role_ids)}"
        )

    execution_files = [
        item
        for item in planned_by_path.values()
        if item.executable or _is_execution_surface(item.destination)
    ]
    unauthorized = sorted(
        item.destination
        for item in execution_files
        if item.source_id not in role_ids
    )
    if unauthorized:
        _fail(
            "execution-surface files are outside the sole executable baseline: "
            f"{unauthorized[:20]}"
        )
    invalid_authorities = sorted(
        source_id
        for source_id in role_ids
        if not str(evidence_by_id[source_id]["destination"]).startswith(
            ("scripts/", "skills/")
        )
    )
    if invalid_authorities:
        _fail(
            "sources classified as executable_baseline must be confined to skills/ "
            f"or scripts/: {invalid_authorities}"
        )

    provenance_ids = {
        source_id
        for source_id, row in evidence_by_id.items()
        if row["source_role"] == "provenance_only"
    }
    provenance_paths = {
        item.destination
        for item in planned_by_path.values()
        if item.source_id in provenance_ids
    }
    provenance_code_references: list[str] = []
    for item in execution_files:
        try:
            text = item.payload.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if "references/provenance/" in text:
            provenance_code_references.append(item.destination)
    if provenance_code_references:
        _fail(
            "execution baseline files cannot reference provenance-only payloads: "
            f"{sorted(provenance_code_references)}"
        )
    dynamic_resources = set(
        _list(
            recipe.get("dynamic_closure", {}).get("resources", []),
            field="dynamic_closure.resources",
        )
    )
    leaked_resources = sorted(provenance_paths & dynamic_resources)
    if leaked_resources:
        _fail(
            "provenance-only files cannot be runtime dynamic resources: "
            f"{leaked_resources}"
        )
    runtime = _mapping(recipe.get("runtime"), field="runtime")
    runtime_references = {
        str(runtime.get("application_dependencies_path") or ""),
        str(runtime.get("fonts_identity_path") or ""),
        str(runtime.get("tex_identity_path") or ""),
        str(runtime.get("poppler_identity_path") or ""),
        str(runtime.get("sbom_path") or ""),
        *(
            str(item)
            for item in _list(
                runtime.get("attestations", []),
                field="runtime.attestations",
            )
        ),
    }
    leaked_runtime = sorted(provenance_paths & runtime_references)
    if leaked_runtime:
        _fail(
            "provenance-only files cannot satisfy runtime identity or dependency "
            f"fields: {leaked_runtime}"
        )
    normative_references: set[str] = set()
    identities = _mapping(recipe.get("identities"), field="identities")
    for identity_name in ("skills", "specs", "schemas"):
        for raw_identity in _list(
            identities.get(identity_name),
            field=f"identities.{identity_name}",
        ):
            identity = _mapping(
                raw_identity,
                field=f"identities.{identity_name}[]",
            )
            if isinstance(identity.get("path"), str):
                normative_references.add(str(identity["path"]))
    for raw_prompt in _list(recipe.get("prompts"), field="prompts"):
        prompt = _mapping(raw_prompt, field="prompts[]")
        normative_references.update(
            str(prompt.get(field) or "")
            for field in ("path", "output_schema")
        )
    template = _mapping(recipe.get("template"), field="template")
    normative_references.update(
        str(template.get(field) or "")
        for field in ("archive_path", "capabilities_path")
    )
    leaked_normative = sorted(provenance_paths & normative_references)
    if leaked_normative:
        _fail(
            "provenance-only files cannot satisfy normative skill, spec, schema, "
            f"prompt, or template fields: {leaked_normative}"
        )
    baseline_records = _tree_records(
        (
            item.destination,
            item.payload,
        )
        for item in planned_by_path.values()
        if item.source_id in role_ids
    )
    baseline_sha256 = _canonical_source_tree(baseline_records)

    return (
        role_ids,
        provenance_ids,
        {
            "policy": "sole-authority",
            "source_ids": sorted(role_ids),
            "hash_algorithm": EXECUTABLE_BASELINE_HASH_ALGORITHM,
            "sha256": baseline_sha256,
            "execution_surface_file_count": len(execution_files),
            "provenance_only_source_ids": sorted(provenance_ids),
            "provenance_runtime_references": [],
        },
    )


def _audit_entrypoints(
    recipe: Mapping[str, Any],
    planned_by_path: Mapping[str, PlannedFile],
    executable_source_ids: set[str],
) -> tuple[list[dict[str, Any]], dict[str, list[str]], dict[str, Any], list[dict[str, str]]]:
    rows = _list(recipe.get("stage_entrypoints"), field="stage_entrypoints")
    by_stage: dict[str, Mapping[str, Any]] = {}
    for index, raw in enumerate(rows):
        row = _mapping(raw, field=f"stage_entrypoints[{index}]")
        stage = row.get("stage")
        if stage not in REQUIRED_STAGES or stage in by_stage:
            _fail(f"stage_entrypoints contains an invalid or duplicate stage: {stage!r}")
        by_stage[str(stage)] = row
    if set(by_stage) != set(REQUIRED_STAGES):
        _fail(f"stage_entrypoints must cover exactly the 12 Worker V3 stages")

    allowlists = {classification: [] for classification in ENTRYPOINT_CLASSES}
    definitions: dict[str, Any] = {}
    evidence: list[dict[str, Any]] = []
    gaps: list[dict[str, str]] = []
    for stage in REQUIRED_STAGES:
        row = by_stage[stage]
        legacy_flat = "id" in row or "tool_path" in row or "classification" in row
        if legacy_flat:
            allowed = {"stage", "id", "classification", "tool_path", "timeout_seconds"}
            if set(row) - allowed:
                _fail(
                    f"legacy stage_entrypoints.{stage} contains unknown fields: "
                    f"{sorted(set(row) - allowed)}"
                )
            role_rows: dict[str, Any] = {"producer": row, "evaluator": None}
            gaps.append(
                {
                    "code": "dual_entrypoint_recipe_required",
                    "stage": stage,
                    "detail": "legacy_flat_entrypoint_has_no_independent_evaluator",
                }
            )
        else:
            allowed = {"stage", "producer", "evaluator"}
            if set(row) - allowed:
                _fail(
                    f"stage_entrypoints.{stage} contains unknown fields: "
                    f"{sorted(set(row) - allowed)}"
                )
            role_rows = {
                "producer": row.get("producer"),
                "evaluator": row.get("evaluator"),
            }
        for execution_role in ("producer", "evaluator"):
            raw_role = role_rows[execution_role]
            if raw_role is None:
                gaps.append(
                    {
                        "code": f"formal_{execution_role}_entrypoint_missing",
                        "stage": stage,
                        "detail": "entrypoint_not_declared",
                    }
                )
                continue
            role_row = _mapping(
                raw_role,
                field=f"stage_entrypoints.{stage}.{execution_role}",
            )
            allowed = {"id", "classification", "tool_path", "timeout_seconds"}
            if set(role_row) - allowed:
                _fail(
                    f"stage_entrypoints.{stage}.{execution_role} contains unknown fields: "
                    f"{sorted(set(role_row) - allowed)}"
                )
            identifier = role_row.get("id")
            if not isinstance(identifier, str) or not identifier or identifier in definitions:
                _fail(
                    f"{execution_role} entrypoint for {stage!r} has an invalid or duplicate id"
                )
            requested_class = role_row.get("classification")
            if requested_class not in ENTRYPOINT_CLASSES:
                _fail(f"entrypoint {identifier!r} has invalid classification")
            tool_path = _relative(
                role_row.get("tool_path"),
                field=f"entrypoint.{identifier}.tool_path",
            )
            tool = planned_by_path.get(tool_path)
            if (
                tool is None
                or not tool.executable
                or not tool_path.startswith("scripts/")
                or tool.source_id not in executable_source_ids
            ):
                static_ok, findings = False, ["declared_executable_tool_missing"]
            else:
                static_ok, findings = _entrypoint_static_audit(
                    stage,
                    execution_role,
                    tool,
                )
            effective_class = str(requested_class)
            if requested_class == "formal" and not static_ok:
                effective_class = "prohibited"
                gaps.append(
                    {
                        "code": f"formal_{execution_role}_entrypoint_contract_unverified",
                        "stage": stage,
                        "detail": ",".join(findings),
                    }
                )
            elif requested_class != "formal":
                gaps.append(
                    {
                        "code": f"formal_{execution_role}_entrypoint_missing",
                        "stage": stage,
                        "detail": f"classified_{requested_class}",
                    }
                )
            allowlists[effective_class].append(identifier)
            producer = execution_role == "producer"
            definitions[identifier] = {
                "classification": effective_class,
                "execution_role": execution_role,
                "stage": stage,
                "argv": [tool_path, "--request", "request.json", "--result", "result.json"],
                "input_schema": (
                    "schemas/stage-request-v1.schema.json"
                    if producer
                    else "schemas/stage-evaluation-request-v1.schema.json"
                ),
                "output_schema": (
                    "schemas/stage-result-v1.schema.json"
                    if producer
                    else "schemas/stage-evaluation-result-v1.schema.json"
                ),
                "permission_envelope": (
                    "candidate-only" if producer else "read-only-evaluator"
                ),
                "timeout_seconds": int(role_row.get("timeout_seconds", 86_400)),
                "exit_semantics": {
                    "0": "candidate_ready" if producer else "evaluation_ready",
                    "2": "invalid_request",
                    "other": "failed",
                },
            }
            evidence.append(
                {
                    "id": identifier,
                    "stage": stage,
                    "execution_role": execution_role,
                    "requested_classification": requested_class,
                    "effective_classification": effective_class,
                    "tool_path": tool_path,
                    "static_contract_verified": static_ok,
                    "findings": findings,
                }
            )
    return evidence, allowlists, definitions, gaps


def _identity_rows(
    recipe: Mapping[str, Any],
    name: str,
    planned: Mapping[str, PlannedFile],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(_list(recipe.get("identities", {}).get(name), field=f"identities.{name}")):
        row = _mapping(raw, field=f"identities.{name}[{index}]")
        identity = row.get("id")
        version = row.get("version")
        path = _relative(row.get("path"), field=f"identities.{name}[{index}].path")
        if not isinstance(identity, str) or not identity or identity in seen:
            _fail(f"identities.{name}[{index}].id is invalid or duplicate")
        if not isinstance(version, str) or not version:
            _fail(f"identities.{name}[{index}].version is missing")
        if path not in planned:
            _fail(f"identities.{name}[{index}] references an unplanned file: {path}")
        seen.add(identity)
        result.append({"id": identity, "version": version, "path": path, "sha256": planned[path].sha256})
    return result


def _prompt_rows(recipe: Mapping[str, Any], planned: Mapping[str, PlannedFile]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(_list(recipe.get("prompts"), field="prompts")):
        row = _mapping(raw, field=f"prompts[{index}]")
        path = _relative(row.get("path"), field=f"prompts[{index}].path")
        output_schema = _relative(
            row.get("output_schema"),
            field=f"prompts[{index}].output_schema",
        )
        if path not in planned or output_schema not in planned:
            _fail(f"prompts[{index}] references an unplanned prompt or schema")
        result.append(
            {
                "id": row["id"],
                "version": row["version"],
                "path": path,
                "sha256": planned[path].sha256,
                "output_schema": output_schema,
            }
        )
    return result


def _template_identity(
    recipe: Mapping[str, Any],
    planned: Mapping[str, PlannedFile],
) -> tuple[dict[str, Any], list[dict[str, str]], PlannedFile]:
    raw = _mapping(recipe.get("template"), field="template")
    archive_path = _relative(raw.get("archive_path"), field="template.archive_path")
    archive = planned.get(archive_path)
    if archive is None:
        _fail("template.archive_path is not planned")
    main_member = _relative(raw.get("main_member"), field="template.main_member")
    class_member = _relative(raw.get("class_member"), field="template.class_member")
    fixed_members = raw.get("fixed_asset_members")
    if not isinstance(fixed_members, list) or any(not isinstance(item, str) for item in fixed_members):
        _fail("template.fixed_asset_members must be an array of paths")
    try:
        import io

        with zipfile.ZipFile(io.BytesIO(archive.payload)) as source:
            rows: list[dict[str, Any]] = []
            member_payloads: dict[str, bytes] = {}
            for info in source.infolist():
                name = _safe_zip_member(info.filename)
                if name in member_payloads:
                    _fail(f"template contains a duplicate member: {name!r}")
                if info.is_dir():
                    continue
                unix_type = (info.external_attr >> 16) & 0o170000
                if unix_type == stat.S_IFLNK:
                    _fail(f"template contains a symlink: {name!r}")
                payload = source.read(info)
                member_payloads[name] = payload
                rows.append({"path": name, "bytes": len(payload), "sha256": _sha256_bytes(payload)})
    except zipfile.BadZipFile as exc:
        _fail(f"template archive is invalid: {exc}")
    if main_member not in member_payloads or class_member not in member_payloads:
        _fail("template main or class member is missing")
    missing_fixed = sorted(set(fixed_members) - set(member_payloads))
    if missing_fixed:
        _fail(f"template fixed assets are missing: {missing_fixed}")
    fixed_rows = [row for row in rows if row["path"] in fixed_members]
    capabilities_path_value = raw.get("capabilities_path")
    capabilities_path: str | None = None
    capabilities_hash: str | None = None
    if isinstance(capabilities_path_value, str):
        capabilities_path = _relative(
            capabilities_path_value,
            field="template.capabilities_path",
        )
        if capabilities_path not in planned:
            _fail("template.capabilities_path is not planned")
        capabilities_hash = planned[capabilities_path].sha256
    qualification = {
        "schema_version": "luceon.worker-v3-template-identity/v1",
        "archive_sha256": archive.sha256,
        "tree_hash_algorithm": SOURCE_TREE_HASH_ALGORITHM,
        "tree_sha256": _canonical_source_tree(rows),
        "main_member": main_member,
        "main_sha256": _sha256_bytes(member_payloads[main_member]),
        "class_member": class_member,
        "class_sha256": _sha256_bytes(member_payloads[class_member]),
        "fixed_asset_members": sorted(fixed_members),
        "fixed_assets_sha256": _canonical_source_tree(fixed_rows),
        "capabilities_qualified": capabilities_hash is not None,
        "capabilities_path": capabilities_path,
        "capabilities_sha256": capabilities_hash,
    }
    qualification_file = PlannedFile(
        "generated:template-identity",
        "references/template-identity.json",
        _json_payload(qualification),
        False,
    )
    gaps: list[dict[str, str]] = []
    if not qualification["capabilities_qualified"]:
        gaps.append(
            {
                "code": "template_capabilities_unqualified",
                "stage": "template_construct_binding",
                "detail": "no immutable release-scoped capability manifest is bound",
            }
        )
    return (
        {
            "id": raw["id"],
            "version": raw["version"],
            "archive_path": archive_path,
            "archive_sha256": archive.sha256,
            "tree_sha256": qualification["tree_sha256"],
            "main_sha256": qualification["main_sha256"],
            "class_sha256": qualification["class_sha256"],
            "fixed_assets_sha256": qualification["fixed_assets_sha256"],
            "capabilities_sha256": capabilities_hash or qualification_file.sha256,
        },
        gaps,
        qualification_file,
    )


def _runtime_identity(
    recipe: Mapping[str, Any],
    planned: dict[str, PlannedFile],
) -> tuple[dict[str, Any], list[dict[str, str]], list[PlannedFile]]:
    raw = _mapping(recipe.get("runtime"), field="runtime")
    gaps: list[dict[str, str]] = []
    generated: list[PlannedFile] = []

    def identity(field: str) -> str:
        path = raw.get(f"{field}_identity_path")
        if isinstance(path, str):
            relative = _relative(path, field=f"runtime.{field}_identity_path")
            if relative not in planned:
                _fail(f"runtime.{field}_identity_path is not planned")
            return planned[relative].sha256
        reason = str(raw.get(f"{field}_gap") or f"{field} identity has not been captured")
        target = f"runtime/unqualified-{field}.json"
        item = PlannedFile(
            f"generated:runtime:{field}",
            target,
            _unqualified_runtime_evidence(field, reason),
            False,
        )
        planned[target] = item
        generated.append(item)
        gaps.append({"code": f"runtime_{field}_unqualified", "stage": "delivery_recompile", "detail": reason})
        return item.sha256

    dependency_path = _relative(
        raw.get("application_dependencies_path"),
        field="runtime.application_dependencies_path",
    )
    if dependency_path not in planned:
        _fail("runtime.application_dependencies_path is not planned")
    sbom_path_value = raw.get("sbom_path")
    if isinstance(sbom_path_value, str):
        sbom_path = _relative(sbom_path_value, field="runtime.sbom_path")
        if sbom_path not in planned:
            _fail("runtime.sbom_path is not planned")
    else:
        reason = str(raw.get("sbom_gap") or "release-scoped SBOM has not been generated")
        sbom_path = "runtime/unqualified-sbom.json"
        item = PlannedFile(
            "generated:runtime:sbom",
            sbom_path,
            _unqualified_runtime_evidence("sbom", reason),
            False,
        )
        planned[sbom_path] = item
        generated.append(item)
        gaps.append({"code": "runtime_sbom_unqualified", "stage": "delivery_recompile", "detail": reason})
    attestations = raw.get("attestations", [])
    if not isinstance(attestations, list):
        _fail("runtime.attestations must be an array")
    attestation_paths = [_relative(path, field="runtime.attestations") for path in attestations]
    if any(path not in planned for path in attestation_paths):
        _fail("runtime.attestations references an unplanned file")

    container_digest = raw.get("container_image_digest")
    if not isinstance(container_digest, str) or not container_digest.startswith("sha256:"):
        reason = str(raw.get("container_gap") or "Worker V3 image digest is not yet pinned")
        item = PlannedFile(
            "generated:runtime:container",
            "runtime/unqualified-container-image.json",
            _unqualified_runtime_evidence("container_image", reason),
            False,
        )
        planned[item.destination] = item
        generated.append(item)
        container_digest = f"sha256:{item.sha256}"
        gaps.append(
            {
                "code": "runtime_container_image_unqualified",
                "stage": "delivery_recompile",
                "detail": reason,
            }
        )
    else:
        _sha256(container_digest.removeprefix("sha256:"), field="runtime.container_image_digest")

    return (
        {
            "python": raw["python"],
            "application_dependencies_sha256": planned[dependency_path].sha256,
            "system_tools": raw.get("system_tools", {}),
            "fonts_sha256": identity("fonts"),
            "tex_sha256": identity("tex"),
            "poppler_sha256": identity("poppler"),
            "container_image_digest": container_digest,
            "sbom_path": sbom_path,
            "attestations": attestation_paths,
        },
        gaps,
        generated,
    )


def _exact_keys(
    value: Mapping[str, Any],
    expected: Iterable[str],
    *,
    field: str,
) -> None:
    expected_set = set(expected)
    actual = set(value)
    if actual != expected_set:
        _fail(
            f"{field} keys mismatch; missing={sorted(expected_set - actual)}, "
            f"extra={sorted(actual - expected_set)}"
        )


def _nonempty_string(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(f"{field} must be a non-empty string")
    return value


def _strict_bool(value: Any, *, field: str) -> bool:
    if not isinstance(value, bool):
        _fail(f"{field} must be boolean")
    return value


def _integer(value: Any, *, field: str, minimum: int = 0) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < minimum
    ):
        _fail(f"{field} must be an integer >= {minimum}")
    return value


def _qualified_at(value: Any, *, field: str) -> tuple[str, datetime]:
    text = _nonempty_string(value, field=field)
    normalized = f"{text[:-1]}+00:00" if text.endswith("Z") else text
    try:
        timestamp = datetime.fromisoformat(normalized)
    except ValueError:
        _fail(f"{field} must be an RFC 3339 date-time")
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        _fail(f"{field} must include an explicit timezone")
    return text, timestamp


def _validate_visual_qualification(proof: Mapping[str, Any], *, field: str) -> None:
    _exact_keys(proof, ("provider", "reviewer", "coverage", "result"), field=field)
    provider = _mapping(proof["provider"], field=f"{field}.provider")
    _exact_keys(
        provider,
        ("name", "model", "endpoint_origin_sha256"),
        field=f"{field}.provider",
    )
    _nonempty_string(provider["name"], field=f"{field}.provider.name")
    _nonempty_string(provider["model"], field=f"{field}.provider.model")
    _sha256(
        provider["endpoint_origin_sha256"],
        field=f"{field}.provider.endpoint_origin_sha256",
    )
    reviewer = _mapping(proof["reviewer"], field=f"{field}.reviewer")
    _exact_keys(
        reviewer,
        ("entrypoint_id", "prompt_sha256", "output_schema_sha256"),
        field=f"{field}.reviewer",
    )
    _nonempty_string(
        reviewer["entrypoint_id"],
        field=f"{field}.reviewer.entrypoint_id",
    )
    for key in ("prompt_sha256", "output_schema_sha256"):
        _sha256(reviewer[key], field=f"{field}.reviewer.{key}")
    coverage = _mapping(proof["coverage"], field=f"{field}.coverage")
    _exact_keys(
        coverage,
        (
            "mode",
            "source_page_count",
            "candidate_page_count",
            "reviewed_source_page_count",
            "reviewed_candidate_page_count",
            "failed_page_count",
        ),
        field=f"{field}.coverage",
    )
    if coverage["mode"] != "all_pages":
        _fail(f"{field}.coverage.mode must be 'all_pages'")
    source_pages = _integer(
        coverage["source_page_count"],
        field=f"{field}.coverage.source_page_count",
        minimum=1,
    )
    candidate_pages = _integer(
        coverage["candidate_page_count"],
        field=f"{field}.coverage.candidate_page_count",
        minimum=1,
    )
    if _integer(
        coverage["reviewed_source_page_count"],
        field=f"{field}.coverage.reviewed_source_page_count",
    ) != source_pages:
        _fail(f"{field}.coverage does not review every source page")
    if _integer(
        coverage["reviewed_candidate_page_count"],
        field=f"{field}.coverage.reviewed_candidate_page_count",
    ) != candidate_pages:
        _fail(f"{field}.coverage does not review every candidate page")
    if _integer(
        coverage["failed_page_count"],
        field=f"{field}.coverage.failed_page_count",
    ) != 0:
        _fail(f"{field}.coverage.failed_page_count must be 0")
    result = _mapping(proof["result"], field=f"{field}.result")
    _exact_keys(
        result,
        ("decision", "schema_valid", "raw_response_hashes_bound"),
        field=f"{field}.result",
    )
    if result["decision"] != "passed":
        _fail(f"{field}.result.decision must be 'passed'")
    if not _strict_bool(result["schema_valid"], field=f"{field}.result.schema_valid"):
        _fail(f"{field}.result.schema_valid must be true")
    if not _strict_bool(
        result["raw_response_hashes_bound"],
        field=f"{field}.result.raw_response_hashes_bound",
    ):
        _fail(f"{field}.result.raw_response_hashes_bound must be true")


def _validate_spec05_qualification(
    proof: Mapping[str, Any],
    *,
    field: str,
    template_archive_sha256: str,
) -> None:
    _exact_keys(proof, ("material", "execution", "delivery", "result"), field=field)
    material = _mapping(proof["material"], field=f"{field}.material")
    _exact_keys(
        material,
        ("material_identity_sha256", "source_pdf_sha256", "popo_manifest_sha256"),
        field=f"{field}.material",
    )
    for key in material:
        _sha256(material[key], field=f"{field}.material.{key}")
    execution = _mapping(proof["execution"], field=f"{field}.execution")
    _exact_keys(
        execution,
        ("entrypoint_id", "exact_final_image", "unchanged_code"),
        field=f"{field}.execution",
    )
    _nonempty_string(
        execution["entrypoint_id"],
        field=f"{field}.execution.entrypoint_id",
    )
    for key in ("exact_final_image", "unchanged_code"):
        if not _strict_bool(execution[key], field=f"{field}.execution.{key}"):
            _fail(f"{field}.execution.{key} must be true")
    delivery = _mapping(proof["delivery"], field=f"{field}.delivery")
    _exact_keys(
        delivery,
        (
            "zip_sha256",
            "pdf_sha256",
            "page_count",
            "xelatex_status",
            "overleaf_status",
            "template_archive_sha256",
        ),
        field=f"{field}.delivery",
    )
    for key in ("zip_sha256", "pdf_sha256", "template_archive_sha256"):
        _sha256(delivery[key], field=f"{field}.delivery.{key}")
    if delivery["template_archive_sha256"] != template_archive_sha256:
        _fail(f"{field}.delivery.template_archive_sha256 mismatch")
    _integer(
        delivery["page_count"],
        field=f"{field}.delivery.page_count",
        minimum=1,
    )
    for key in ("xelatex_status", "overleaf_status"):
        if delivery[key] != "passed":
            _fail(f"{field}.delivery.{key} must be 'passed'")
    result = _mapping(proof["result"], field=f"{field}.result")
    _exact_keys(
        result,
        ("decision", "spec05_gates_passed"),
        field=f"{field}.result",
    )
    if result["decision"] != "passed":
        _fail(f"{field}.result.decision must be 'passed'")
    if not _strict_bool(
        result["spec05_gates_passed"],
        field=f"{field}.result.spec05_gates_passed",
    ):
        _fail(f"{field}.result.spec05_gates_passed must be true")


def _audit_qualification_evidence(
    recipe: Mapping[str, Any],
    *,
    release: Mapping[str, Any],
    runtime: Mapping[str, Any],
    template: Mapping[str, Any],
    executable_baseline: Mapping[str, Any],
    planned_by_path: Mapping[str, PlannedFile],
    source_evidence: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, str]]]:
    raw_value = recipe.get("qualification_evidence", {})
    raw = _mapping(raw_value, field="qualification_evidence")
    unknown = sorted(set(raw) - set(QUALIFICATION_EVIDENCE_TYPES))
    if unknown:
        _fail(f"qualification_evidence contains unknown types: {unknown}")
    source_by_id = {str(row["id"]): row for row in source_evidence}
    evidence_files_by_source: dict[str, list[PlannedFile]] = {}
    for item in planned_by_path.values():
        evidence_files_by_source.setdefault(item.source_id, []).append(item)
    categories: dict[str, list[dict[str, Any]]] = {
        "contract": [],
        "eval": [],
        "uat": [],
    }
    gaps: list[dict[str, str]] = []
    used_sources: set[str] = set()

    for qualification_type in QUALIFICATION_EVIDENCE_TYPES:
        row_value = raw.get(qualification_type)
        if row_value is None:
            code, stage = _QUALIFICATION_GAPS[qualification_type]
            gaps.append(
                {
                    "code": code,
                    "stage": stage,
                    "detail": (
                        f"required immutable {qualification_type} evidence "
                        "is not declared"
                    ),
                }
            )
            continue
        row = _mapping(
            row_value,
            field=f"qualification_evidence.{qualification_type}",
        )
        _exact_keys(
            row,
            ("required", "source_id"),
            field=f"qualification_evidence.{qualification_type}",
        )
        required = _strict_bool(
            row["required"],
            field=f"qualification_evidence.{qualification_type}.required",
        )
        if not required:
            _fail(f"qualification_evidence.{qualification_type}.required must be true")
        source_id = row["source_id"]
        if source_id is None:
            if required:
                code, stage = _QUALIFICATION_GAPS[qualification_type]
                gaps.append(
                    {
                        "code": code,
                        "stage": stage,
                        "detail": (
                            f"required immutable {qualification_type} evidence "
                            "has no source_id"
                        ),
                    }
                )
            continue
        source_id = _nonempty_string(
            source_id,
            field=f"qualification_evidence.{qualification_type}.source_id",
        )
        if source_id in used_sources:
            _fail("one qualification evidence source cannot qualify multiple types")
        used_sources.add(source_id)
        source = source_by_id.get(source_id)
        if source is None:
            _fail(
                f"qualification_evidence.{qualification_type} references unknown "
                f"source {source_id!r}"
            )
        if (
            source["kind"] != "file"
            or source["source_role"] not in {
                "runtime_evidence",
                "supporting_evidence",
            }
            or not str(source["destination"]).startswith("evals/qualification/")
        ):
            _fail(
                f"qualification source {source_id!r} must be one immutable "
                "runtime/supporting evidence file under evals/qualification/"
            )
        source_files = evidence_files_by_source.get(source_id, [])
        if len(source_files) != 1:
            _fail(f"qualification source {source_id!r} must resolve to exactly one file")
        item = source_files[0]
        try:
            payload = json.loads(item.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            _fail(f"qualification source {source_id!r} is not valid UTF-8 JSON: {exc}")
        document = _mapping(
            payload,
            field=f"qualification source {source_id!r}",
        )
        _exact_keys(
            document,
            (
                "schema_version",
                "qualification_type",
                "status",
                "evidence_id",
                "qualified_at",
                "identity",
                "release_binding",
                "proof",
            ),
            field=f"qualification source {source_id!r}",
        )
        expected_schema = QUALIFICATION_EVIDENCE_SCHEMA_VERSIONS[
            qualification_type
        ]
        if document["schema_version"] != expected_schema:
            _fail(
                f"qualification source {source_id!r} schema_version mismatch"
            )
        if document["qualification_type"] != qualification_type:
            _fail(f"qualification source {source_id!r} type mismatch")
        if document["status"] != "passed":
            _fail(f"qualification source {source_id!r} status must be 'passed'")
        evidence_id = _nonempty_string(
            document["evidence_id"],
            field=f"qualification source {source_id!r}.evidence_id",
        )
        qualified_at, qualified_timestamp = _qualified_at(
            document["qualified_at"],
            field=f"qualification source {source_id!r}.qualified_at",
        )
        _, release_timestamp = _qualified_at(
            release["created_at"],
            field="release.created_at",
        )
        if qualified_timestamp > release_timestamp:
            _fail(
                f"qualification source {source_id!r} is newer than the release"
            )
        identity = _mapping(
            document["identity"],
            field=f"qualification source {source_id!r}.identity",
        )
        _exact_keys(
            identity,
            ("runner", "verifier", "run_id"),
            field=f"qualification source {source_id!r}.identity",
        )
        for key in identity:
            _nonempty_string(
                identity[key],
                field=f"qualification source {source_id!r}.identity.{key}",
            )
        binding = _mapping(
            document["release_binding"],
            field=f"qualification source {source_id!r}.release_binding",
        )
        _exact_keys(
            binding,
            (
                "release_id",
                "release_source_git_sha",
                "executable_baseline_hash_algorithm",
                "executable_baseline_sha256",
                "container_image_digest",
            ),
            field=f"qualification source {source_id!r}.release_binding",
        )
        if binding["release_id"] != release["release_id"]:
            _fail(f"qualification source {source_id!r} release_id mismatch")
        if (
            binding["executable_baseline_hash_algorithm"]
            != EXECUTABLE_BASELINE_HASH_ALGORITHM
        ):
            _fail(
                f"qualification source {source_id!r} executable baseline "
                "hash algorithm mismatch"
            )
        git_sha = binding["release_source_git_sha"]
        baseline_sha256 = binding["executable_baseline_sha256"]
        if git_sha is None and baseline_sha256 is None:
            _fail(
                f"qualification source {source_id!r} must bind the release source "
                "or executable baseline"
            )
        if git_sha is not None:
            if git_sha != release["source"]["git_sha"]:
                _fail(f"qualification source {source_id!r} release source mismatch")
        if baseline_sha256 is not None:
            _sha256(
                baseline_sha256,
                field=(
                    f"qualification source {source_id!r}."
                    "release_binding.executable_baseline_sha256"
                ),
            )
            if baseline_sha256 != executable_baseline["sha256"]:
                _fail(
                    f"qualification source {source_id!r} executable baseline mismatch"
                )
        if release["source"]["dirty"] and baseline_sha256 is None:
            _fail(
                f"qualification source {source_id!r} for a dirty source must bind "
                "the executable baseline"
            )
        if binding["container_image_digest"] != runtime["container_image_digest"]:
            _fail(
                f"qualification source {source_id!r} container image mismatch"
            )
        proof = _mapping(
            document["proof"],
            field=f"qualification source {source_id!r}.proof",
        )
        if qualification_type == "visual_full_page_provider":
            _validate_visual_qualification(
                proof,
                field=f"qualification source {source_id!r}.proof",
            )
        elif qualification_type == "spec05_final_image_real_material":
            _validate_spec05_qualification(
                proof,
                field=f"qualification source {source_id!r}.proof",
                template_archive_sha256=str(template["archive_sha256"]),
            )
        manifest_row = {
            "kind": "worker-v3-qualification",
            "qualification_type": qualification_type,
            "schema_version": expected_schema,
            "status": "passed",
            "required": required,
            "evidence_id": evidence_id,
            "qualified_at": qualified_at,
            "identity": dict(identity),
            "path": item.destination,
            "sha256": item.sha256,
            "release_binding": dict(binding),
        }
        categories[_QUALIFICATION_MANIFEST_CATEGORIES[qualification_type]].append(
            manifest_row
        )
    return categories, gaps


def load_release_recipe(path: str | os.PathLike[str]) -> dict[str, Any]:
    recipe_path = Path(path)
    try:
        value = json.loads(recipe_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(f"cannot read release recipe: {exc}")
    if not isinstance(value, dict) or value.get("schema_version") != RECIPE_SCHEMA_VERSION:
        _fail(f"recipe schema_version must be {RECIPE_SCHEMA_VERSION!r}")
    return value


def _validate_release_metadata(raw: Mapping[str, Any]) -> None:
    release_id = raw.get("release_id")
    if (
        not isinstance(release_id, str)
        or not 3 <= len(release_id) <= 128
        or not release_id[0].isalnum()
        or release_id.lower() != release_id
        or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789._-" for character in release_id)
    ):
        _fail("release.release_id is invalid")
    version = raw.get("version")
    if not isinstance(version, str) or not version or version.count(".") < 2:
        _fail("release.version is missing or not version-like")
    if raw.get("channel") not in {"rc", "stable"}:
        _fail("release.channel must be rc or stable")
    if not isinstance(raw.get("created_at"), str) or not raw["created_at"]:
        _fail("release.created_at is missing")
    source = _mapping(raw.get("source"), field="release.source")
    if set(source) != {"git_sha", "git_tag", "dirty"}:
        _fail("release.source must contain exactly git_sha, git_tag, and dirty")
    git_sha = source["git_sha"]
    if (
        not isinstance(git_sha, str)
        or len(git_sha) != 40
        or any(character not in "0123456789abcdef" for character in git_sha)
    ):
        _fail("release.source.git_sha must be a full 40-character Git SHA")
    if source["git_tag"] is not None and not isinstance(source["git_tag"], str):
        _fail("release.source.git_tag must be a string or null")
    if not isinstance(source["dirty"], bool):
        _fail("release.source.dirty must be boolean")


def audit_release_recipe(
    recipe: Mapping[str, Any],
    *,
    root_overrides: Mapping[str, str | os.PathLike[str]] | None = None,
    recipe_dir: str | os.PathLike[str] | None = None,
) -> RecipeAudit:
    roots_raw = _mapping(recipe.get("roots"), field="roots")
    overrides = root_overrides or {}
    base = Path(recipe_dir).resolve() if recipe_dir is not None else None
    if base is not None and (not base.is_dir() or base.is_symlink()):
        _fail(f"recipe_dir is not an existing real directory: {base}")
    roots: dict[str, Path] = {}
    for name, value in roots_raw.items():
        actual = overrides.get(str(name), value)
        if isinstance(actual, Mapping):
            if set(actual) != {"relative_to_recipe"}:
                _fail(
                    f"root {name!r} relative declaration must contain only "
                    "relative_to_recipe"
                )
            relative = actual.get("relative_to_recipe")
            if (
                base is None
                or not isinstance(relative, str)
                or not relative
                or Path(relative).is_absolute()
            ):
                _fail(
                    f"root {name!r} requires a relative path and a loaded recipe file"
                )
            path = (base / relative).resolve()
        else:
            if not isinstance(actual, (str, os.PathLike)):
                _fail(f"root {name!r} is not a path")
            path = Path(actual).expanduser()
        if not path.is_absolute() or path.is_symlink() or not path.is_dir():
            _fail(f"root {name!r} is not an existing real absolute directory: {path}")
        roots[str(name)] = path
    unknown_overrides = sorted(set(overrides) - set(roots))
    if unknown_overrides:
        _fail(f"unknown root overrides: {unknown_overrides}")

    sources = _list(recipe.get("sources"), field="sources")
    planned: list[PlannedFile] = []
    evidence: list[dict[str, Any]] = []
    seen_source_ids: set[str] = set()
    for index, raw in enumerate(sources):
        source = _mapping(raw, field=f"sources[{index}]")
        source_id = source.get("id")
        if source_id in seen_source_ids:
            _fail(f"duplicate source id: {source_id!r}")
        seen_source_ids.add(str(source_id))
        source_files, source_evidence = _resolve_source(source, roots=roots)
        planned.extend(source_files)
        evidence.append(source_evidence)

    stage_request = PlannedFile(
        "generated:stage-request-schema",
        "schemas/stage-request-v1.schema.json",
        _json_payload(_stage_request_schema()),
        False,
    )
    stage_result = PlannedFile(
        "generated:stage-result-schema",
        "schemas/stage-result-v1.schema.json",
        _json_payload(_stage_result_schema()),
        False,
    )
    evaluation_request = PlannedFile(
        "generated:stage-evaluation-request-schema",
        "schemas/stage-evaluation-request-v1.schema.json",
        _json_payload(_stage_evaluation_request_schema()),
        False,
    )
    evaluation_result = PlannedFile(
        "generated:stage-evaluation-result-schema",
        "schemas/stage-evaluation-result-v1.schema.json",
        _json_payload(_stage_evaluation_result_schema()),
        False,
    )
    planned.extend((stage_request, stage_result, evaluation_request, evaluation_result))
    planned_by_path: dict[str, PlannedFile] = {}
    for item in planned:
        if item.destination in planned_by_path:
            _fail(f"release destination collision: {item.destination!r}")
        planned_by_path[item.destination] = item
    forbidden_release_items = sorted(
        item.destination
        for item in planned
        if any(
            marker in f"{item.source_id}/{item.destination}".lower()
            for marker in (
                "codex-expert",
                "codex_expert",
                "expert-broker",
                "expert_broker",
                "expert-capability",
                "expert_capability",
                "expert-live",
                "expert_live",
            )
        )
    )
    if forbidden_release_items:
        _fail(
            "production release contains Codex Expert runtime material: "
            f"{forbidden_release_items}"
        )

    (
        executable_source_ids,
        provenance_source_ids,
        executable_baseline,
    ) = _audit_executable_baseline(recipe, planned_by_path, evidence)
    entrypoint_evidence, allowlists, definitions, gaps = _audit_entrypoints(
        recipe,
        planned_by_path,
        executable_source_ids,
    )
    template, template_gaps, template_file = _template_identity(recipe, planned_by_path)
    gaps.extend(template_gaps)
    if template_file.destination in planned_by_path:
        _fail(f"release destination collision: {template_file.destination!r}")
    planned_by_path[template_file.destination] = template_file
    planned.append(template_file)

    runtime, runtime_gaps, runtime_files = _runtime_identity(recipe, planned_by_path)
    gaps.extend(runtime_gaps)
    planned.extend(runtime_files)
    release = _mapping(recipe.get("release"), field="release")
    _validate_release_metadata(release)
    qualification_evidence, qualification_gaps = _audit_qualification_evidence(
        recipe,
        release=release,
        runtime=runtime,
        template=template,
        executable_baseline=executable_baseline,
        planned_by_path=planned_by_path,
        source_evidence=evidence,
    )
    gaps.extend(qualification_gaps)

    skills = _identity_rows(recipe, "skills", planned_by_path)
    if tuple(row["id"] for row in skills) != REQUIRED_SKILLS:
        gaps.append(
            {
                "code": "required_skill_set_not_exact",
                "stage": "intake_snapshot",
                "detail": f"expected {list(REQUIRED_SKILLS)}, got {[row['id'] for row in skills]}",
            }
        )
    specs = _identity_rows(recipe, "specs", planned_by_path)
    schemas = _identity_rows(recipe, "schemas", planned_by_path)
    schemas.extend(
        [
            {
                "id": "worker-v3-stage-request",
                "version": "1",
                "path": stage_request.destination,
                "sha256": stage_request.sha256,
            },
            {
                "id": "worker-v3-stage-result",
                "version": "1",
                "path": stage_result.destination,
                "sha256": stage_result.sha256,
            },
            {
                "id": "worker-v3-stage-evaluation-request",
                "version": "1",
                "path": evaluation_request.destination,
                "sha256": evaluation_request.sha256,
            },
            {
                "id": "worker-v3-stage-evaluation-result",
                "version": "1",
                "path": evaluation_result.destination,
                "sha256": evaluation_result.sha256,
            },
        ]
    )
    prompts = _prompt_rows(recipe, planned_by_path)
    if not prompts:
        gaps.append(
            {
                "code": "bounded_prompt_registry_missing",
                "stage": "semantic_annotation",
                "detail": "embedded or ad-hoc prompts are not a release-scoped prompt registry",
            }
        )
    mutable_reference_paths: list[str] = []
    for path, item in sorted(planned_by_path.items()):
        if item.source_id in provenance_source_ids:
            continue
        try:
            text = item.payload.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if any(
            marker in text
            for marker in (".codex/skills", "~/.codex", "/Users/", "/home/")
        ):
            mutable_reference_paths.append(path)
    if mutable_reference_paths:
        preview = ",".join(mutable_reference_paths[:10])
        suffix = "" if len(mutable_reference_paths) <= 10 else f",+{len(mutable_reference_paths) - 10} more"
        gaps.append(
            {
                "code": "mutable_host_path_references_present",
                "stage": "release",
                "detail": f"{preview}{suffix}",
            }
        )
    gap_keys = {(row["code"], row["stage"]) for row in gaps}
    for raw in _list(recipe.get("known_gaps", []), field="known_gaps"):
        row = _mapping(raw, field="known_gaps[]")
        gap = {
            "code": str(row["code"]),
            "stage": str(row.get("stage", "release")),
            "detail": str(row["detail"]),
        }
        key = (gap["code"], gap["stage"])
        if key not in gap_keys:
            gaps.append(gap)
            gap_keys.add(key)

    model_policy = _mapping(recipe.get("model_policy", {}), field="model_policy")
    forbidden_runtime_keys = sorted(
        key
        for key in ("expert_models", "expert_capability")
        if key in model_policy
    )
    if forbidden_runtime_keys:
        _fail(
            "model_policy contains production-forbidden Codex Expert keys: "
            f"{forbidden_runtime_keys}"
        )
    try:
        validate_release_pricing(model_policy)
    except PricingError as exc:
        _fail(f"model_policy pricing is invalid: {exc.code}: {exc}")

    requested_status = release.get("requested_status")
    if requested_status not in {"incomplete", "rc", "stable"}:
        _fail("release.requested_status must be incomplete, rc, or stable")
    effective_status = "incomplete" if gaps else requested_status
    if effective_status == "stable" and not bool(release.get("stable_eligible", False)):
        _fail("stable status requires release.stable_eligible=true")
    manifest = {
        "schema_version": RELEASE_SCHEMA_VERSION,
        "release_id": release["release_id"],
        "version": release["version"],
        "channel": release["channel"],
        "status": effective_status,
        "created_at": release["created_at"],
        "source": release["source"],
        "eligibility": {
            "rc_eligible": effective_status in {"rc", "stable"},
            "stable_eligible": effective_status == "stable",
        },
        "tree_hash": {"algorithm": TREE_HASH_ALGORITHM, "sha256": "0" * 64},
        "archive_hash_location": "external-release-registry",
        "files": [],
        "skills": skills,
        "specs": specs,
        "schemas": schemas,
        "entrypoints": {**allowlists, "definitions": definitions},
        "dynamic_closure": {
            "modules": list(recipe.get("dynamic_closure", {}).get("modules", [])),
            "resources": [
                "references/source-qualification.json",
                "references/template-identity.json",
                *list(recipe.get("dynamic_closure", {}).get("resources", [])),
            ],
        },
        "prompts": prompts,
        "model_policy": dict(model_policy),
        "template": template,
        "runtime": runtime,
        "limits": dict(STRICT_LIMITS),
        "evidence": {
            "unit": [],
            "contract": qualification_evidence["contract"],
            "eval": qualification_evidence["eval"],
            "uat": qualification_evidence["uat"],
            "known_gaps": gaps,
        },
        "compatibility": recipe.get(
            "compatibility",
            {"v2_3": "isolated parallel lane", "rollback": "disable Worker V3 admission"},
        ),
    }
    source_report = {
        "schema_version": "luceon.worker-v3-source-qualification/v1",
        "recipe_schema_version": RECIPE_SCHEMA_VERSION,
        "release_id": release["release_id"],
        "status": effective_status,
        "source_tree_hash_algorithm": SOURCE_TREE_HASH_ALGORITHM,
        "sources": evidence,
        "executable_baseline": executable_baseline,
        "entrypoints": entrypoint_evidence,
        "known_gaps": gaps,
        "runtime_host_path_references_absent": not mutable_reference_paths,
        "historical_host_paths_confined_to_provenance": bool(
            provenance_source_ids
        ),
        "host_paths_omitted": True,
    }
    source_report_file = PlannedFile(
        "generated:source-qualification",
        "references/source-qualification.json",
        _json_payload(source_report),
        False,
    )
    if source_report_file.destination in planned_by_path:
        _fail(f"release destination collision: {source_report_file.destination!r}")
    planned_by_path[source_report_file.destination] = source_report_file
    planned.append(source_report_file)

    resolved_recipe = dict(recipe)
    resolved_recipe["release"] = {**release, "requested_status": requested_status}
    resolved_recipe["_generated_manifest"] = manifest
    return RecipeAudit(
        recipe=resolved_recipe,
        source_roots=tuple(sorted(roots.values(), key=str)),
        planned_files=tuple(sorted(planned, key=lambda item: item.destination)),
        source_evidence=tuple(evidence),
        entrypoint_evidence=tuple(entrypoint_evidence),
        known_gaps=tuple(gaps),
    )


def assemble_release_source(
    audit: RecipeAudit,
    destination: str | os.PathLike[str],
) -> dict[str, Any]:
    target = Path(destination)
    if target.exists() or target.is_symlink():
        _fail(f"assembly destination already exists: {target}")
    resolved_target = target.resolve()
    if any(
        root.resolve() == resolved_target or root.resolve() in resolved_target.parents
        for root in audit.source_roots
    ):
        _fail("assembly destination cannot be inside a source root")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.assemble-", dir=target.parent))
    try:
        for directory in REQUIRED_DIRECTORIES:
            (staging / directory).mkdir(parents=True, exist_ok=True)
        for item in audit.planned_files:
            path = staging / item.destination
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(item.payload)
            path.chmod(0o755 if item.executable else 0o644)
        manifest = audit.recipe["_generated_manifest"]
        (staging / "release-manifest.json").write_bytes(_json_payload(manifest))
        staging.rename(target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {
        "release_id": manifest["release_id"],
        "status": manifest["status"],
        "source_count": len(audit.source_evidence),
        "file_count": len(audit.planned_files),
        "known_gap_count": len(audit.known_gaps),
        "destination": str(target.resolve()),
    }


def verify_release_recipe(
    recipe_path: str | os.PathLike[str],
    *,
    root_overrides: Mapping[str, str | os.PathLike[str]] | None = None,
) -> RecipeAudit:
    path = Path(recipe_path).resolve()
    return audit_release_recipe(
        load_release_recipe(path),
        root_overrides=root_overrides,
        recipe_dir=path.parent,
    )
