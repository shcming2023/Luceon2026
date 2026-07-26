from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import shutil
import stat
import tarfile
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

from app.workflow_v3.pricing import PricingError, validate_release_pricing


MANIFEST_NAME = "release-manifest.json"
SCHEMA_VERSION = "luceon.worker-v3-skill-release/v1"
TREE_HASH_ALGORITHM = "sha256-canonical-file-records-v1"
ENTRYPOINT_CLASSES = ("formal", "legacy", "migration", "diagnostic", "prohibited")
ENTRYPOINT_ROLES = ("producer", "evaluator", "utility")
REQUIRED_FORMAL_STAGES = (
    "intake_snapshot",
    "source_scope_and_order",
    "canonical_block_ledger",
    "outline_reconstruction",
    "semantic_annotation",
    "template_construct_binding",
    "frozen_render_plan",
    "deterministic_elegantbook",
    "readonly_latex_audit",
    "independent_full_page_review",
    "delivery_recompile",
    "ready_for_user_acceptance",
)
REQUIRED_DIRECTORIES = (
    "skills",
    "contracts",
    "schemas",
    "validators",
    "prompts",
    "scripts",
    "references",
    "templates",
    "evals",
    "runtime",
)
STRICT_LIMITS = {
    "delivery_zip_bytes_exclusive_max": 50_000_000,
    "raster_bytes_exclusive_max": 1_000_000,
    "file_count_exclusive_max": 2_000,
    "tex_leaf_bytes_exclusive_max": 900_000,
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SEMVER_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[0-9A-Za-z.-]+)?$")
_RELEASE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,127}$")
_FORBIDDEN_RUNTIME_REFERENCES = (".codex/skills", "~/.codex", "/Users/", "/home/")
QUALIFICATION_ENVIRONMENT = "qualification"


class ReleaseValidationError(ValueError):
    """The release is not safe or complete enough to execute."""


@dataclass(frozen=True)
class ReleaseVerification:
    root: Path
    manifest: dict[str, Any]
    release_id: str
    tree_sha256: str
    archive_sha256: str | None = None


@dataclass(frozen=True)
class QualificationArchiveVerification:
    archive_path: Path
    archive_sha256: str
    manifest: dict[str, Any]
    manifest_sha256: str
    release_id: str
    tree_sha256: str


def _fail(message: str) -> None:
    raise ReleaseValidationError(message)


def require_qualification_environment() -> None:
    if os.getenv("LUCEON_ENVIRONMENT", "").strip() != QUALIFICATION_ENVIRONMENT:
        _fail(
            "qualification execution requires "
            "LUCEON_ENVIRONMENT=qualification"
        )


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_path(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(f"{field} must be a non-empty relative POSIX path")
    if "\\" in value or "\x00" in value or value.startswith("/"):
        _fail(f"{field} is not a safe relative POSIX path: {value!r}")
    parsed = PurePosixPath(value)
    if any(part in {"", ".", ".."} for part in parsed.parts) or str(parsed) != value:
        _fail(f"{field} is not normalized: {value!r}")
    return value


def _sha256(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        _fail(f"{field} must be a lowercase SHA-256")
    return value


def _expect_mapping(value: Any, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{field} must be an object")
    return value


def _expect_list(value: Any, *, field: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(f"{field} must be an array")
    return value


def _expect_exact_keys(value: Mapping[str, Any], expected: Iterable[str], *, field: str) -> None:
    expected_set = set(expected)
    actual = set(value)
    if actual != expected_set:
        missing = sorted(expected_set - actual)
        extra = sorted(actual - expected_set)
        _fail(f"{field} keys mismatch; missing={missing}, extra={extra}")


def canonical_tree_sha256(file_records: Iterable[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    records = sorted(file_records, key=lambda item: str(item["path"]))
    for item in records:
        canonical = {
            "bytes": item["bytes"],
            "mode": item["mode"],
            "path": item["path"],
            "role": item["role"],
            "sha256": item["sha256"],
        }
        digest.update(
            json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        digest.update(b"\n")
    return digest.hexdigest()


def _validate_identity_list(
    manifest: Mapping[str, Any],
    name: str,
    file_by_path: Mapping[str, Mapping[str, Any]],
) -> None:
    rows = _expect_list(manifest.get(name), field=name)
    if not rows:
        _fail(f"{name} must contain at least one immutable identity")
    ids: set[str] = set()
    for index, raw in enumerate(rows):
        row = _expect_mapping(raw, field=f"{name}[{index}]")
        _expect_exact_keys(row, ("id", "version", "path", "sha256"), field=f"{name}[{index}]")
        identity = row["id"]
        version = row["version"]
        if not isinstance(identity, str) or not identity or identity in ids:
            _fail(f"{name}[{index}].id must be non-empty and unique")
        if not isinstance(version, str) or not version:
            _fail(f"{name}[{index}].version must be non-empty")
        ids.add(identity)
        path = _relative_path(row["path"], field=f"{name}[{index}].path")
        digest = _sha256(row["sha256"], field=f"{name}[{index}].sha256")
        declared = file_by_path.get(path)
        if declared is None or declared["sha256"] != digest:
            _fail(f"{name}[{index}] does not match declared file {path!r}")


def _validate_manifest(
    manifest: Mapping[str, Any],
    *,
    allow_incomplete: bool,
) -> dict[str, Mapping[str, Any]]:
    required = (
        "schema_version",
        "release_id",
        "version",
        "channel",
        "status",
        "created_at",
        "source",
        "eligibility",
        "tree_hash",
        "archive_hash_location",
        "files",
        "skills",
        "specs",
        "schemas",
        "entrypoints",
        "dynamic_closure",
        "prompts",
        "model_policy",
        "template",
        "runtime",
        "limits",
        "evidence",
        "compatibility",
    )
    _expect_exact_keys(manifest, required, field="manifest")
    if manifest["schema_version"] != SCHEMA_VERSION:
        _fail(f"unknown schema_version: {manifest['schema_version']!r}")
    if not isinstance(manifest["release_id"], str) or not _RELEASE_ID_RE.fullmatch(manifest["release_id"]):
        _fail("release_id is invalid")
    if not isinstance(manifest["version"], str) or not _SEMVER_RE.fullmatch(manifest["version"]):
        _fail("version is not SemVer")
    if manifest["channel"] not in {"rc", "stable"}:
        _fail("channel must be rc or stable")
    if manifest["status"] not in {"incomplete", "rc", "stable"}:
        _fail(f"unknown release status: {manifest['status']!r}")
    if manifest["status"] == "incomplete" and not allow_incomplete:
        _fail("release status incomplete is not executable")
    if not isinstance(manifest["created_at"], str) or not manifest["created_at"]:
        _fail("created_at must be present")
    if manifest["archive_hash_location"] != "external-release-registry":
        _fail("archive SHA-256 must be bound by the external release registry")

    source = _expect_mapping(manifest["source"], field="source")
    _expect_exact_keys(source, ("git_sha", "git_tag", "dirty"), field="source")
    if not isinstance(source["git_sha"], str) or not re.fullmatch(r"[0-9a-f]{40}", source["git_sha"]):
        _fail("source.git_sha must be a full lowercase Git SHA")
    if source["git_tag"] is not None and not isinstance(source["git_tag"], str):
        _fail("source.git_tag must be a string or null")
    if not isinstance(source["dirty"], bool):
        _fail("source.dirty must be boolean")

    eligibility = _expect_mapping(manifest["eligibility"], field="eligibility")
    _expect_exact_keys(eligibility, ("rc_eligible", "stable_eligible"), field="eligibility")
    if not all(isinstance(eligibility[key], bool) for key in eligibility):
        _fail("eligibility flags must be boolean")
    if manifest["status"] == "rc" and not eligibility["rc_eligible"]:
        _fail("rc release is not rc_eligible")
    if manifest["status"] == "stable" and not eligibility["stable_eligible"]:
        _fail("stable release is not stable_eligible")

    file_rows = _expect_list(manifest["files"], field="files")
    file_by_path: dict[str, Mapping[str, Any]] = {}
    for index, raw in enumerate(file_rows):
        row = _expect_mapping(raw, field=f"files[{index}]")
        _expect_exact_keys(row, ("path", "bytes", "sha256", "mode", "role"), field=f"files[{index}]")
        path = _relative_path(row["path"], field=f"files[{index}].path")
        if path == MANIFEST_NAME or path in file_by_path:
            _fail(f"duplicate or self-referential file path: {path!r}")
        if not isinstance(row["bytes"], int) or isinstance(row["bytes"], bool) or row["bytes"] < 0:
            _fail(f"files[{index}].bytes must be a non-negative integer")
        _sha256(row["sha256"], field=f"files[{index}].sha256")
        if row["mode"] not in {"0444", "0555"}:
            _fail(f"files[{index}].mode must be 0444 or 0555")
        if row["role"] not in REQUIRED_DIRECTORIES or path.split("/", 1)[0] != row["role"]:
            _fail(f"files[{index}].role must match its top-level directory")
        file_by_path[path] = row

    tree_hash = _expect_mapping(manifest["tree_hash"], field="tree_hash")
    _expect_exact_keys(tree_hash, ("algorithm", "sha256"), field="tree_hash")
    if tree_hash["algorithm"] != TREE_HASH_ALGORITHM:
        _fail(f"unknown tree hash algorithm: {tree_hash['algorithm']!r}")
    expected_tree = _sha256(tree_hash["sha256"], field="tree_hash.sha256")
    actual_tree = canonical_tree_sha256(file_rows)
    if expected_tree != actual_tree:
        _fail(f"tree hash mismatch: expected {expected_tree}, got {actual_tree}")

    for identity_name in ("skills", "specs", "schemas"):
        _validate_identity_list(manifest, identity_name, file_by_path)

    entrypoints = _expect_mapping(manifest["entrypoints"], field="entrypoints")
    _expect_exact_keys(entrypoints, (*ENTRYPOINT_CLASSES, "definitions"), field="entrypoints")
    definitions = _expect_mapping(entrypoints["definitions"], field="entrypoints.definitions")
    classified: dict[str, str] = {}
    for classification in ENTRYPOINT_CLASSES:
        identifiers = _expect_list(entrypoints[classification], field=f"entrypoints.{classification}")
        if len(identifiers) != len(set(identifiers)):
            _fail(f"entrypoints.{classification} contains duplicate IDs")
        for identifier in identifiers:
            if not isinstance(identifier, str) or not identifier or identifier in classified:
                _fail(f"entrypoint {identifier!r} is invalid or classified more than once")
            classified[identifier] = classification
    if set(definitions) != set(classified):
        _fail("entrypoint definitions and classification allowlists differ")
    formal_by_stage: dict[str, dict[str, tuple[str, Mapping[str, Any]]]] = {}
    for identifier, raw in definitions.items():
        definition = _expect_mapping(raw, field=f"entrypoints.definitions.{identifier}")
        keys = (
            "classification",
            "execution_role",
            "stage",
            "argv",
            "input_schema",
            "output_schema",
            "permission_envelope",
            "timeout_seconds",
            "exit_semantics",
        )
        _expect_exact_keys(definition, keys, field=f"entrypoints.definitions.{identifier}")
        if definition["classification"] != classified[identifier]:
            _fail(f"entrypoint {identifier!r} classification does not match its allowlist")
        execution_role = definition["execution_role"]
        if execution_role not in ENTRYPOINT_ROLES:
            _fail(f"entrypoint {identifier!r} execution role is invalid")
        stage = definition["stage"]
        if stage not in REQUIRED_FORMAL_STAGES:
            _fail(f"entrypoint {identifier!r} stage is not registered by Worker V3")
        argv = _expect_list(definition["argv"], field=f"entrypoint {identifier}.argv")
        if not argv or any(not isinstance(token, str) or not token for token in argv):
            _fail(f"entrypoint {identifier!r} argv must contain non-empty strings")
        executable = _relative_path(argv[0], field=f"entrypoint {identifier}.argv[0]")
        executable_file = file_by_path.get(executable)
        if executable_file is None or executable_file["role"] != "scripts" or executable_file["mode"] != "0555":
            _fail(f"entrypoint {identifier!r} executable is not a declared executable script")
        for token in argv:
            if any(marker in token for marker in _FORBIDDEN_RUNTIME_REFERENCES):
                _fail(f"entrypoint {identifier!r} references a mutable host path")
        for schema_field in ("input_schema", "output_schema"):
            schema_path = _relative_path(
                definition[schema_field],
                field=f"entrypoint {identifier}.{schema_field}",
            )
            if schema_path not in file_by_path or file_by_path[schema_path]["role"] != "schemas":
                _fail(f"entrypoint {identifier!r} references undeclared schema {schema_path!r}")
        if not isinstance(definition["permission_envelope"], str) or not definition["permission_envelope"]:
            _fail(f"entrypoint {identifier!r} permission envelope is missing")
        if (
            not isinstance(definition["timeout_seconds"], int)
            or isinstance(definition["timeout_seconds"], bool)
            or not 1 <= definition["timeout_seconds"] <= 86_400
        ):
            _fail(f"entrypoint {identifier!r} timeout is invalid")
        exit_semantics = _expect_mapping(
            definition["exit_semantics"],
            field=f"entrypoint {identifier}.exit_semantics",
        )
        if classified[identifier] == "formal":
            if execution_role == "utility":
                _fail(f"formal entrypoint {identifier!r} cannot have utility execution role")
            expected_envelope = (
                "candidate-only" if execution_role == "producer" else "read-only-evaluator"
            )
            expected_success = (
                "candidate_ready" if execution_role == "producer" else "evaluation_ready"
            )
            if definition["permission_envelope"] != expected_envelope:
                _fail(
                    f"formal {execution_role} entrypoint {identifier!r} must use "
                    f"{expected_envelope!r} permission envelope"
                )
            if exit_semantics.get("0") != expected_success:
                _fail(
                    f"formal {execution_role} entrypoint {identifier!r} must declare "
                    f"exit 0 as {expected_success!r}"
                )
            stage_roles = formal_by_stage.setdefault(stage, {})
            if execution_role in stage_roles:
                _fail(
                    f"stage {stage!r} has more than one formal {execution_role} entrypoint"
                )
            stage_roles[execution_role] = (identifier, definition)

    if manifest["status"] in {"rc", "stable"}:
        if set(formal_by_stage) != set(REQUIRED_FORMAL_STAGES):
            _fail(
                "formal entrypoints must cover exactly the registered Worker V3 stages "
                "with producer/evaluator pairs"
            )
        for stage in REQUIRED_FORMAL_STAGES:
            stage_roles = formal_by_stage[stage]
            if set(stage_roles) != {"producer", "evaluator"}:
                _fail(
                    f"stage {stage!r} must have exactly one formal producer and evaluator"
                )
            producer = stage_roles["producer"]
            evaluator = stage_roles["evaluator"]
            if producer[0] == evaluator[0]:
                _fail(f"stage {stage!r} producer and evaluator IDs must differ")
            if producer[1]["argv"][0] == evaluator[1]["argv"][0]:
                _fail(
                    f"stage {stage!r} producer and evaluator must use separate executable entrypoints"
                )

    closure = _expect_mapping(manifest["dynamic_closure"], field="dynamic_closure")
    _expect_exact_keys(closure, ("modules", "resources"), field="dynamic_closure")
    modules = _expect_list(closure["modules"], field="dynamic_closure.modules")
    if any(not isinstance(module, str) or not module for module in modules):
        _fail("dynamic_closure.modules contains an invalid module")
    for index, resource in enumerate(_expect_list(closure["resources"], field="dynamic_closure.resources")):
        path = _relative_path(resource, field=f"dynamic_closure.resources[{index}]")
        if path not in file_by_path:
            _fail(f"dynamic closure resource is undeclared: {path!r}")

    prompt_ids: set[tuple[str, str]] = set()
    for index, raw in enumerate(_expect_list(manifest["prompts"], field="prompts")):
        prompt = _expect_mapping(raw, field=f"prompts[{index}]")
        _expect_exact_keys(prompt, ("id", "version", "path", "sha256", "output_schema"), field=f"prompts[{index}]")
        identity = (prompt["id"], prompt["version"])
        if not all(isinstance(item, str) and item for item in identity) or identity in prompt_ids:
            _fail(f"prompts[{index}] identity is invalid or duplicate")
        prompt_ids.add(identity)
        path = _relative_path(prompt["path"], field=f"prompts[{index}].path")
        digest = _sha256(prompt["sha256"], field=f"prompts[{index}].sha256")
        if path not in file_by_path or file_by_path[path]["role"] != "prompts":
            _fail(f"prompt file is undeclared: {path!r}")
        if file_by_path[path]["sha256"] != digest:
            _fail(f"prompt hash does not match declared file: {path!r}")
        output_schema = _relative_path(prompt["output_schema"], field=f"prompts[{index}].output_schema")
        if output_schema not in file_by_path or file_by_path[output_schema]["role"] != "schemas":
            _fail(f"prompt output schema is undeclared: {output_schema!r}")
    model_policy = _expect_mapping(manifest["model_policy"], field="model_policy")
    try:
        validate_release_pricing(model_policy)
    except PricingError as exc:
        _fail(f"model_policy pricing is invalid: {exc.code}: {exc}")

    template = _expect_mapping(manifest["template"], field="template")
    template_keys = (
        "id",
        "version",
        "archive_path",
        "archive_sha256",
        "tree_sha256",
        "main_sha256",
        "class_sha256",
        "fixed_assets_sha256",
        "capabilities_sha256",
    )
    _expect_exact_keys(template, template_keys, field="template")
    if not isinstance(template["id"], str) or not template["id"]:
        _fail("template.id is missing")
    if not isinstance(template["version"], str) or not template["version"]:
        _fail("template.version is missing")
    archive_path = _relative_path(template["archive_path"], field="template.archive_path")
    if archive_path not in file_by_path or file_by_path[archive_path]["role"] != "templates":
        _fail("template archive is not declared")
    for field in template_keys[3:]:
        _sha256(template[field], field=f"template.{field}")
    if template["archive_sha256"] != file_by_path[archive_path]["sha256"]:
        _fail("template archive hash does not match declared file")

    runtime = _expect_mapping(manifest["runtime"], field="runtime")
    runtime_keys = (
        "python",
        "application_dependencies_sha256",
        "system_tools",
        "fonts_sha256",
        "tex_sha256",
        "poppler_sha256",
        "container_image_digest",
        "sbom_path",
        "attestations",
    )
    _expect_exact_keys(runtime, runtime_keys, field="runtime")
    if not isinstance(runtime["python"], str) or not runtime["python"]:
        _fail("runtime.python is missing")
    for field in ("application_dependencies_sha256", "fonts_sha256", "tex_sha256", "poppler_sha256"):
        _sha256(runtime[field], field=f"runtime.{field}")
    _expect_mapping(runtime["system_tools"], field="runtime.system_tools")
    if not isinstance(runtime["container_image_digest"], str) or not re.fullmatch(
        r"sha256:[0-9a-f]{64}", runtime["container_image_digest"]
    ):
        _fail("runtime.container_image_digest is invalid")
    sbom_path = _relative_path(runtime["sbom_path"], field="runtime.sbom_path")
    if sbom_path not in file_by_path or file_by_path[sbom_path]["role"] != "runtime":
        _fail("runtime SBOM is not declared")
    for index, attestation in enumerate(_expect_list(runtime["attestations"], field="runtime.attestations")):
        path = _relative_path(attestation, field=f"runtime.attestations[{index}]")
        if path not in file_by_path or file_by_path[path]["role"] != "runtime":
            _fail(f"runtime attestation is undeclared: {path!r}")

    limits = _expect_mapping(manifest["limits"], field="limits")
    _expect_exact_keys(limits, STRICT_LIMITS, field="limits")
    if dict(limits) != STRICT_LIMITS:
        _fail(f"strict delivery limits must be exactly {STRICT_LIMITS}")

    evidence = _expect_mapping(manifest["evidence"], field="evidence")
    _expect_exact_keys(evidence, ("unit", "contract", "eval", "uat", "known_gaps"), field="evidence")
    for field in evidence:
        _expect_list(evidence[field], field=f"evidence.{field}")
    compatibility = _expect_mapping(manifest["compatibility"], field="compatibility")
    _expect_exact_keys(compatibility, ("v2_3", "rollback"), field="compatibility")
    if any(not isinstance(compatibility[field], str) or not compatibility[field] for field in compatibility):
        _fail("compatibility statements must be non-empty strings")
    return file_by_path


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(f"cannot read {MANIFEST_NAME}: {exc}")
    if not isinstance(payload, dict):
        _fail(f"{MANIFEST_NAME} must contain an object")
    return payload


def _walk_release(root: Path) -> tuple[set[str], set[str]]:
    files: set[str] = set()
    directories: set[str] = set()
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in [*dirnames, *filenames]:
            path = current_path / name
            if path.is_symlink():
                _fail(f"links are forbidden in a release: {path.relative_to(root)}")
        for name in dirnames:
            path = current_path / name
            if not path.is_dir():
                _fail(f"non-directory entry found: {path.relative_to(root)}")
            directories.add(path.relative_to(root).as_posix())
        for name in filenames:
            path = current_path / name
            if not stat.S_ISREG(path.stat(follow_symlinks=False).st_mode):
                _fail(f"non-regular file found: {path.relative_to(root)}")
            files.add(path.relative_to(root).as_posix())
    return files, directories


def verify_release_directory(root: str | os.PathLike[str], *, allow_incomplete: bool = False) -> ReleaseVerification:
    release_root = Path(root)
    if release_root.is_symlink() or not release_root.is_dir():
        _fail(f"release root is not a real directory: {release_root}")
    if ".codex" in release_root.parts and "skills" in release_root.parts:
        _fail("active ~/.codex/skills cannot be used as a production release")
    manifest_path = release_root / MANIFEST_NAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        _fail(f"{MANIFEST_NAME} is missing")
    manifest = _load_manifest(manifest_path)
    file_by_path = _validate_manifest(manifest, allow_incomplete=allow_incomplete)
    actual_files, actual_directories = _walk_release(release_root)
    expected_files = {MANIFEST_NAME, *file_by_path}
    if actual_files != expected_files:
        _fail(
            f"declared files differ from package files; missing={sorted(expected_files - actual_files)}, "
            f"undeclared={sorted(actual_files - expected_files)}"
        )
    expected_directories = set(REQUIRED_DIRECTORIES)
    for path in file_by_path:
        parent = PurePosixPath(path).parent
        while str(parent) != ".":
            expected_directories.add(str(parent))
            parent = parent.parent
    if actual_directories != expected_directories:
        _fail(
            f"release directories differ; missing={sorted(expected_directories - actual_directories)}, "
            f"undeclared={sorted(actual_directories - expected_directories)}"
        )
    for path, record in file_by_path.items():
        file_path = release_root / path
        actual_size = file_path.stat().st_size
        actual_hash = _sha256_file(file_path)
        actual_mode = f"{stat.S_IMODE(file_path.stat().st_mode):04o}"
        if actual_size != record["bytes"]:
            _fail(f"size mismatch for {path!r}: expected {record['bytes']}, got {actual_size}")
        if actual_hash != record["sha256"]:
            _fail(f"SHA-256 mismatch for {path!r}")
        if actual_mode != record["mode"]:
            _fail(f"mode mismatch for {path!r}: expected {record['mode']}, got {actual_mode}")
    manifest_mode = f"{stat.S_IMODE(manifest_path.stat().st_mode):04o}"
    if manifest_mode != "0444":
        _fail(f"{MANIFEST_NAME} must be mode 0444, got {manifest_mode}")
    root_mode = f"{stat.S_IMODE(release_root.stat().st_mode):04o}"
    if root_mode != "0555":
        _fail(f"release root must be mode 0555, got {root_mode}")
    for directory in actual_directories:
        actual_mode = f"{stat.S_IMODE((release_root / directory).stat().st_mode):04o}"
        if actual_mode != "0555":
            _fail(f"release directory {directory!r} must be mode 0555, got {actual_mode}")
    return ReleaseVerification(
        root=release_root,
        manifest=manifest,
        release_id=manifest["release_id"],
        tree_sha256=manifest["tree_hash"]["sha256"],
    )


def admit_entrypoint(
    release: ReleaseVerification,
    entrypoint_id: str,
    *,
    requested_class: str = "formal",
    requested_role: str | None = None,
    qualification: bool = False,
) -> dict[str, Any]:
    if not isinstance(release, ReleaseVerification):
        _fail("entrypoint admission requires a verified installed release")
    if requested_class not in ENTRYPOINT_CLASSES or requested_class == "prohibited":
        _fail(f"entrypoint admission class is not executable: {requested_class!r}")
    if requested_role is not None and requested_role not in ENTRYPOINT_ROLES:
        _fail(f"entrypoint admission role is invalid: {requested_role!r}")
    if qualification:
        require_qualification_environment()
    current = verify_release_directory(
        release.root,
        allow_incomplete=qualification,
    )
    if qualification and current.manifest.get("status") != "incomplete":
        _fail("qualification admission is only valid for an incomplete release")
    if current.release_id != release.release_id or current.tree_sha256 != release.tree_sha256:
        _fail("installed release identity changed since verification")
    manifest = current.manifest
    definitions = manifest["entrypoints"]["definitions"]
    definition = definitions.get(entrypoint_id)
    if definition is None:
        _fail(f"unknown entrypoint: {entrypoint_id!r}")
    classification = definition["classification"]
    if classification == "prohibited":
        _fail(f"entrypoint is prohibited: {entrypoint_id!r}")
    if classification != requested_class:
        _fail(
            f"entrypoint {entrypoint_id!r} is classified {classification!r}, "
            f"not requested class {requested_class!r}"
        )
    if requested_role is not None and definition["execution_role"] != requested_role:
        _fail(
            f"entrypoint {entrypoint_id!r} has execution role "
            f"{definition['execution_role']!r}, not requested role {requested_role!r}"
        )
    return dict(definition)


def enforce_delivery_limits(
    release: ReleaseVerification | Mapping[str, Any],
    *,
    delivery_zip_bytes: int,
    raster_bytes: Iterable[int],
    file_count: int,
    tex_leaf_bytes: Iterable[int],
) -> None:
    """Enforce release-bound exclusive delivery limits; equality is a failure."""

    manifest = release.manifest if isinstance(release, ReleaseVerification) else release
    _validate_manifest(manifest, allow_incomplete=False)
    values = {
        "delivery_zip_bytes": delivery_zip_bytes,
        "file_count": file_count,
    }
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in values.values()):
        _fail("delivery sizes and counts must be non-negative integers")
    raster_sizes = list(raster_bytes)
    tex_sizes = list(tex_leaf_bytes)
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in [*raster_sizes, *tex_sizes]):
        _fail("raster and TeX leaf sizes must be non-negative integers")
    limits = manifest["limits"]
    if delivery_zip_bytes >= limits["delivery_zip_bytes_exclusive_max"]:
        _fail("delivery ZIP size must be strictly below its release limit")
    if file_count >= limits["file_count_exclusive_max"]:
        _fail("delivery file count must be strictly below its release limit")
    if any(value >= limits["raster_bytes_exclusive_max"] for value in raster_sizes):
        _fail("every raster size must be strictly below its release limit")
    if any(value >= limits["tex_leaf_bytes_exclusive_max"] for value in tex_sizes):
        _fail("every TeX leaf size must be strictly below its release limit")


def _archive_member_path(name: str) -> str:
    normalized = name[:-1] if name.endswith("/") else name
    return _relative_path(normalized, field="archive member")


def _validate_open_archive(
    archive: tarfile.TarFile,
    *,
    allow_incomplete: bool,
) -> tuple[dict[str, Any], list[tarfile.TarInfo]]:
    members = archive.getmembers()
    names: list[str] = []
    member_by_name: dict[str, tarfile.TarInfo] = {}
    for member in members:
        name = _archive_member_path(member.name)
        if name in member_by_name:
            _fail(f"duplicate archive member: {name!r}")
        if member.issym() or member.islnk():
            _fail(f"archive links are forbidden: {name!r}")
        if not member.isfile() and not member.isdir():
            _fail(f"unsupported archive member type: {name!r}")
        if member.mtime != 0 or member.uid != 0 or member.gid != 0 or member.uname or member.gname:
            _fail(f"archive metadata is not normalized: {name!r}")
        names.append(name)
        member_by_name[name] = member
    if names != sorted(names):
        _fail("archive members are not in lexical order")
    manifest_member = member_by_name.get(MANIFEST_NAME)
    if manifest_member is None or not manifest_member.isfile():
        _fail(f"{MANIFEST_NAME} is missing from archive")
    extracted = archive.extractfile(manifest_member)
    if extracted is None:
        _fail(f"cannot read {MANIFEST_NAME} from archive")
    try:
        manifest = json.loads(extracted.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(f"invalid {MANIFEST_NAME}: {exc}")
    if not isinstance(manifest, dict):
        _fail(f"{MANIFEST_NAME} must contain an object")
    file_by_path = _validate_manifest(manifest, allow_incomplete=allow_incomplete)
    expected_files = {MANIFEST_NAME, *file_by_path}
    expected_directories = set(REQUIRED_DIRECTORIES)
    for path in file_by_path:
        parent = PurePosixPath(path).parent
        while str(parent) != ".":
            expected_directories.add(str(parent))
            parent = parent.parent
    actual_files = {name for name, member in member_by_name.items() if member.isfile()}
    actual_directories = {name for name, member in member_by_name.items() if member.isdir()}
    if actual_files != expected_files or actual_directories != expected_directories:
        _fail("archive members do not exactly match the manifest and required layout")
    if f"{stat.S_IMODE(manifest_member.mode):04o}" != "0444":
        _fail(f"{MANIFEST_NAME} archive mode must be 0444")
    for path, record in file_by_path.items():
        member = member_by_name[path]
        if f"{stat.S_IMODE(member.mode):04o}" != record["mode"]:
            _fail(f"archive mode mismatch for {path!r}")
        if member.size != record["bytes"]:
            _fail(f"archive size mismatch for {path!r}")
    for path in expected_directories:
        if f"{stat.S_IMODE(member_by_name[path].mode):04o}" != "0555":
            _fail(f"archive directory mode must be 0555: {path!r}")
    return manifest, members


def _archive_manifest_and_members(
    archive_path: Path,
    *,
    allow_incomplete: bool,
) -> tuple[dict[str, Any], list[tarfile.TarInfo]]:
    try:
        with tarfile.open(archive_path, mode="r:*") as archive:
            return _validate_open_archive(archive, allow_incomplete=allow_incomplete)
    except (OSError, tarfile.TarError) as exc:
        _fail(f"cannot open release archive: {exc}")


def _apply_readonly_modes(root: Path, manifest: Mapping[str, Any]) -> None:
    for record in manifest["files"]:
        (root / record["path"]).chmod(int(record["mode"], 8))
    (root / MANIFEST_NAME).chmod(0o444)
    directories = sorted(
        (path for path in root.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for directory in directories:
        directory.chmod(0o555)
    root.chmod(0o555)


def install_release_archive(
    archive: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    *,
    expected_archive_sha256: str,
) -> ReleaseVerification:
    archive_path = Path(archive)
    expected_digest = _sha256(expected_archive_sha256, field="expected_archive_sha256")
    target = Path(destination)
    if target.exists() or target.is_symlink():
        _fail(f"release destination already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging: Path | None = None
    try:
        with archive_path.open("rb") as raw:
            digest = hashlib.sha256()
            for chunk in iter(lambda: raw.read(1024 * 1024), b""):
                digest.update(chunk)
            actual_digest = digest.hexdigest()
            if actual_digest != expected_digest:
                _fail(f"archive SHA-256 mismatch: expected {expected_digest}, got {actual_digest}")
            raw.seek(0)
            with tarfile.open(fileobj=raw, mode="r:*") as source:
                manifest, members = _validate_open_archive(source, allow_incomplete=False)
                staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.install-", dir=target.parent))
                for member in members:
                    name = _archive_member_path(member.name)
                    path = staging / name
                    if member.isdir():
                        path.mkdir(parents=True, exist_ok=False)
                        continue
                    path.parent.mkdir(parents=True, exist_ok=True)
                    payload = source.extractfile(member)
                    if payload is None:
                        _fail(f"cannot extract archive member: {name!r}")
                    with path.open("xb") as handle:
                        shutil.copyfileobj(payload, handle)
        assert staging is not None
        _apply_readonly_modes(staging, manifest)
        verification = verify_release_directory(staging)
        staging.rename(target)
        return replace(verification, root=target, archive_sha256=actual_digest)
    except Exception:
        if staging is not None:
            try:
                staging.chmod(0o755)
                for path in staging.rglob("*"):
                    if path.exists() and not path.is_symlink():
                        path.chmod(0o755 if path.is_dir() else 0o644)
            finally:
                shutil.rmtree(staging, ignore_errors=True)
        raise


def verify_release_archive_matches_directory(
    archive: str | os.PathLike[str],
    installed_directory: str | os.PathLike[str],
    *,
    expected_archive_sha256: str,
) -> ReleaseVerification:
    """Verify that one exact external archive materializes to the installed tree.

    Registration uses this stronger check after downloading the registry-bound
    package object.  A valid local directory alone is insufficient: both the
    archive bytes and the independently extracted manifest/tree must match the
    installed release.
    """

    installed = verify_release_directory(installed_directory)
    installed_manifest_sha256 = _sha256_file(installed.root / MANIFEST_NAME)
    with tempfile.TemporaryDirectory(prefix=".worker-v3-release-verify-") as raw:
        materialized = install_release_archive(
            archive,
            Path(raw) / "release",
            expected_archive_sha256=expected_archive_sha256,
        )
        materialized_manifest_sha256 = _sha256_file(
            materialized.root / MANIFEST_NAME
        )
        if (
            materialized.release_id != installed.release_id
            or materialized.tree_sha256 != installed.tree_sha256
            or materialized_manifest_sha256 != installed_manifest_sha256
            or materialized.manifest != installed.manifest
        ):
            _fail(
                "registry package archive does not materialize to the "
                "installed release"
            )
    return replace(
        installed,
        archive_sha256=_sha256(
            expected_archive_sha256,
            field="expected_archive_sha256",
        ),
    )


def verify_qualification_release_archive(
    archive: str | os.PathLike[str],
    *,
    expected_archive_sha256: str,
) -> QualificationArchiveVerification:
    """Verify one incomplete archive without materializing any member."""

    require_qualification_environment()
    archive_path = Path(archive)
    if archive_path.is_symlink() or not archive_path.is_file():
        _fail(f"qualification release archive is not a regular file: {archive_path}")
    expected_digest = _sha256(
        expected_archive_sha256,
        field="expected_archive_sha256",
    )
    actual_digest = _sha256_file(archive_path)
    if actual_digest != expected_digest:
        _fail(
            "qualification archive SHA-256 mismatch: "
            f"expected {expected_digest}, got {actual_digest}"
        )
    try:
        with tarfile.open(archive_path, mode="r:*") as source:
            manifest, members = _validate_open_archive(
                source,
                allow_incomplete=True,
            )
            manifest_member = next(
                member for member in members if member.name == MANIFEST_NAME
            )
            payload = source.extractfile(manifest_member)
            if payload is None:
                _fail(f"cannot read {MANIFEST_NAME} from qualification archive")
            manifest_bytes = payload.read()
    except (OSError, tarfile.TarError) as exc:
        _fail(f"cannot open qualification release archive: {exc}")
    eligibility = manifest.get("eligibility")
    if (
        manifest.get("status") != "incomplete"
        or not isinstance(eligibility, Mapping)
        or eligibility.get("rc_eligible") is not False
        or eligibility.get("stable_eligible") is not False
    ):
        _fail(
            "qualification archive must remain incomplete and ineligible "
            "for RC/Stable"
        )
    return QualificationArchiveVerification(
        archive_path=archive_path.resolve(),
        archive_sha256=actual_digest,
        manifest=manifest,
        manifest_sha256=_sha256_bytes(manifest_bytes),
        release_id=manifest["release_id"],
        tree_sha256=manifest["tree_hash"]["sha256"],
    )


def materialize_qualification_release_archive(
    archive: QualificationArchiveVerification,
    destination: str | os.PathLike[str],
    *,
    run_root: str | os.PathLike[str],
) -> ReleaseVerification:
    """Materialize a preverified incomplete archive only inside a fresh run."""

    require_qualification_environment()
    if not isinstance(archive, QualificationArchiveVerification):
        _fail("qualification archive must be preverified")
    allowed_root = Path(run_root)
    if allowed_root.is_symlink() or not allowed_root.is_dir():
        _fail("qualification run root is unavailable")
    allowed_root = allowed_root.resolve()
    target = Path(destination)
    if target.exists() or target.is_symlink():
        _fail(f"qualification release destination already exists: {target}")
    if target.parent.resolve() != allowed_root:
        _fail("qualification release destination must be a direct run-root child")
    current = verify_qualification_release_archive(
        archive.archive_path,
        expected_archive_sha256=archive.archive_sha256,
    )
    if (
        current.release_id != archive.release_id
        or current.tree_sha256 != archive.tree_sha256
        or current.manifest_sha256 != archive.manifest_sha256
    ):
        _fail("qualification archive identity changed after preflight")

    staging: Path | None = None
    try:
        with tarfile.open(current.archive_path, mode="r:*") as source:
            manifest, members = _validate_open_archive(
                source,
                allow_incomplete=True,
            )
            staging = Path(
                tempfile.mkdtemp(
                    prefix=f".{target.name}.qualification-",
                    dir=allowed_root,
                )
            )
            for member in members:
                name = _archive_member_path(member.name)
                path = staging / name
                if member.isdir():
                    path.mkdir(parents=True, exist_ok=False)
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                payload = source.extractfile(member)
                if payload is None:
                    _fail(f"cannot read qualification archive member: {name!r}")
                with path.open("xb") as handle:
                    shutil.copyfileobj(payload, handle)
        assert staging is not None
        _apply_readonly_modes(staging, manifest)
        verification = verify_release_directory(
            staging,
            allow_incomplete=True,
        )
        if (
            verification.release_id != current.release_id
            or verification.tree_sha256 != current.tree_sha256
            or _sha256_file(staging / MANIFEST_NAME)
            != current.manifest_sha256
        ):
            _fail("materialized qualification release differs from its archive")
        staging.rename(target)
        return replace(
            verification,
            root=target,
            archive_sha256=current.archive_sha256,
        )
    except Exception:
        if staging is not None:
            try:
                staging.chmod(0o755)
                for path in staging.rglob("*"):
                    if path.exists() and not path.is_symlink():
                        path.chmod(0o755 if path.is_dir() else 0o644)
            finally:
                shutil.rmtree(staging, ignore_errors=True)
        raise


def _source_file_records(source_root: Path) -> list[dict[str, Any]]:
    actual_files, actual_directories = _walk_release(source_root)
    actual_files.discard(MANIFEST_NAME)
    required = set(REQUIRED_DIRECTORIES)
    if not required.issubset(actual_directories):
        _fail(f"release source is missing required directories: {sorted(required - actual_directories)}")
    records: list[dict[str, Any]] = []
    for path in sorted(actual_files):
        source = source_root / path
        role = path.split("/", 1)[0]
        if role not in REQUIRED_DIRECTORIES:
            _fail(f"release file is outside a required top-level directory: {path!r}")
        executable = bool(source.stat().st_mode & 0o111)
        records.append(
            {
                "path": path,
                "bytes": source.stat().st_size,
                "sha256": _sha256_file(source),
                "mode": "0555" if executable else "0444",
                "role": role,
            }
        )
    return records


def _tar_info(name: str, *, mode: int, size: int = 0, directory: bool = False) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name=name)
    info.type = tarfile.DIRTYPE if directory else tarfile.REGTYPE
    info.mode = mode
    info.size = 0 if directory else size
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    return info


def build_release_archive(
    source: str | os.PathLike[str],
    output: str | os.PathLike[str],
) -> dict[str, str]:
    source_root = Path(source)
    output_path = Path(output)
    if source_root.is_symlink() or not source_root.is_dir():
        _fail(f"release source is not a real directory: {source_root}")
    if ".codex" in source_root.parts and "skills" in source_root.parts:
        _fail("active ~/.codex/skills cannot be packaged directly")
    if output_path.resolve() == source_root.resolve() or source_root.resolve() in output_path.resolve().parents:
        _fail("release archive output cannot be inside the release source")
    manifest = _load_manifest(source_root / MANIFEST_NAME)
    records = _source_file_records(source_root)
    manifest["files"] = records
    manifest["tree_hash"] = {
        "algorithm": TREE_HASH_ALGORITHM,
        "sha256": canonical_tree_sha256(records),
    }
    _validate_manifest(manifest, allow_incomplete=True)
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")

    directories = set(REQUIRED_DIRECTORIES)
    for record in records:
        parent = PurePosixPath(record["path"]).parent
        while str(parent) != ".":
            directories.add(str(parent))
            parent = parent.parent
    entries = sorted([MANIFEST_NAME, *directories, *(record["path"] for record in records)])
    record_by_path = {record["path"]: record for record in records}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.tmp")
    if temporary.exists():
        temporary.unlink()
    try:
        raw = temporary.open("wb")
        compressed: gzip.GzipFile | None = None
        if output_path.suffix == ".gz" or output_path.name.endswith(".tar.gz"):
            compressed = gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0)
            fileobj: Any = compressed
        else:
            fileobj = raw
        try:
            with tarfile.open(fileobj=fileobj, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for name in entries:
                    if name in directories:
                        archive.addfile(_tar_info(name, mode=0o555, directory=True))
                    elif name == MANIFEST_NAME:
                        archive.addfile(
                            _tar_info(name, mode=0o444, size=len(manifest_bytes)),
                            io.BytesIO(manifest_bytes),
                        )
                    else:
                        record = record_by_path[name]
                        with (source_root / name).open("rb") as payload:
                            archive.addfile(
                                _tar_info(name, mode=int(record["mode"], 8), size=record["bytes"]),
                                payload,
                            )
        finally:
            if compressed is not None:
                compressed.close()
            raw.close()
        temporary.replace(output_path)
        archive_sha256 = _sha256_file(output_path)
        _archive_manifest_and_members(output_path, allow_incomplete=True)
        return {
            "release_id": manifest["release_id"],
            "tree_sha256": manifest["tree_hash"]["sha256"],
            "archive_sha256": archive_sha256,
        }
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
