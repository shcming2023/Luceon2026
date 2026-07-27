from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

try:
    from .release_identity import runtime_identity_for_manifest
except ImportError:  # Release-local scripts are imported outside the backend package.
    from release_identity import runtime_identity_for_manifest  # type: ignore[no-redef]


ENTRYPOINT_PROTOCOL = "luceon.worker-v3-stage-entrypoint/v1"
REQUEST_PROTOCOL = "luceon.worker-v3-stage-request/v1"
RESULT_PROTOCOL = "luceon.worker-v3-stage-result/v1"
CANDIDATE_PROTOCOL = "luceon.worker-v3-stage-candidate/v1"
BUNDLE_PROTOCOL = "luceon.worker-v3-candidate-bundle/v1"

_SHA256_CHARS = frozenset("0123456789abcdef")
_MAX_INPUT_ARTIFACTS = 128
_MAX_ARCHIVE_MEMBERS = 20_000
_MAX_ARCHIVE_BYTES = 4_000_000_000
_FORBIDDEN_PARAMETER_KEYS = {
    "api_key",
    "apikey",
    "argv",
    "authorization",
    "bearer_token",
    "command",
    "docker_socket",
    "executable",
    "password",
    "refresh_token",
    "secret",
    "shell",
    "token",
}


class StageEntrypointError(RuntimeError):
    """Fail-closed error emitted by a release-local stage producer."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        findings: Sequence[Mapping[str, Any]] | None = None,
        exit_code: int = 2,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.findings = tuple(dict(item) for item in (findings or ()))
        self.exit_code = exit_code


@dataclass(frozen=True)
class InputDescriptor:
    kind: str
    sha256: str
    size_bytes: int
    path: str


@dataclass(frozen=True)
class InputArtifact:
    role: str
    kind: str
    sha256: str
    size_bytes: int
    path: str
    read_only: bool


@dataclass(frozen=True)
class PredecessorPromotion:
    promotion_id: str
    stage_key: str
    artifact_sha256: str
    evaluation_sha256: str
    promotion_manifest_sha256: str


@dataclass(frozen=True)
class ReleaseBinding:
    release_id: str
    version: str
    manifest_sha256: str
    tree_sha256: str
    runtime_identity_sha256: str


@dataclass(frozen=True)
class StageRequest:
    job_id: str
    stage_key: str
    stage_version: str
    attempt: int
    primary_input: InputDescriptor
    input_artifacts: tuple[InputArtifact, ...]
    predecessor_promotion: PredecessorPromotion | None
    release: ReleaseBinding
    parameters: Mapping[str, Any]
    output_manifest: str
    request_path: Path
    workdir: Path

    @classmethod
    def load(
        cls,
        path: str | os.PathLike[str],
        *,
        expected_stage: str,
        first_stage: bool = False,
    ) -> StageRequest:
        request_path = Path(path)
        if not request_path.is_absolute():
            request_path = Path.cwd() / request_path
        workdir = Path.cwd().resolve()
        request_path = _require_contained_file(request_path, workdir, "request")
        payload = _load_json_object(request_path, "stage request")
        required = {
            "schema_version",
            "mode",
            "job_id",
            "stage_key",
            "stage_version",
            "attempt",
            "input",
            "input_artifacts",
            "predecessor_promotion",
            "release",
            "parameters",
            "output_manifest",
        }
        if set(payload) != required:
            raise StageEntrypointError(
                "request_shape_invalid",
                "stage request has missing or unknown fields",
            )
        if payload["schema_version"] != REQUEST_PROTOCOL or payload["mode"] != "produce":
            raise StageEntrypointError(
                "request_protocol_invalid",
                f"request must use {REQUEST_PROTOCOL!r} in produce mode",
            )
        if payload["stage_key"] != expected_stage:
            raise StageEntrypointError(
                "stage_binding_mismatch",
                f"request stage {payload['stage_key']!r} is not {expected_stage!r}",
            )
        job_id = _require_text(payload["job_id"], "job_id")
        stage_version = _require_text(payload["stage_version"], "stage_version")
        attempt = _require_positive_int(payload["attempt"], "attempt")
        primary = _input_descriptor(payload["input"])
        artifacts = _input_artifacts(payload["input_artifacts"])
        primary_matches = [
            item
            for item in artifacts
            if item.path == primary.path
            and item.sha256 == primary.sha256
            and item.size_bytes == primary.size_bytes
        ]
        if len(primary_matches) != 1:
            raise StageEntrypointError(
                "primary_input_not_in_artifact_set",
                "primary input must appear exactly once in input_artifacts",
            )
        primary_artifact = primary_matches[0]
        expected_primary_role = "frozen_source" if first_stage else "promoted_predecessor"
        if primary_artifact.role != expected_primary_role:
            raise StageEntrypointError(
                "primary_input_role_invalid",
                f"primary input must use role {expected_primary_role!r}",
            )
        if (
            not first_stage
            and primary_artifact.kind != "worker-v3-candidate-bundle"
        ):
            raise StageEntrypointError(
                "promoted_input_kind_invalid",
                "a non-initial stage must consume a Worker V3 candidate bundle",
            )
        predecessor = _predecessor_promotion(payload["predecessor_promotion"])
        if first_stage:
            if predecessor is not None:
                raise StageEntrypointError(
                    "unexpected_predecessor_promotion",
                    "the first stage cannot consume a predecessor promotion",
                )
        else:
            if predecessor is None:
                raise StageEntrypointError(
                    "predecessor_promotion_missing",
                    "a non-initial stage requires exact predecessor promotion evidence",
                )
            if predecessor.artifact_sha256 != primary.sha256:
                raise StageEntrypointError(
                    "predecessor_artifact_mismatch",
                    "primary input SHA-256 does not match the promoted predecessor",
                )
            promotion_artifacts = [
                item
                for item in artifacts
                if item.role == "predecessor_promotion_manifest"
                and item.sha256 == predecessor.promotion_manifest_sha256
            ]
            if len(promotion_artifacts) != 1:
                raise StageEntrypointError(
                    "predecessor_promotion_manifest_missing",
                    "input_artifacts must bind the exact predecessor promotion manifest",
                )
        release = _release_binding(payload["release"])
        parameters = payload["parameters"]
        if not isinstance(parameters, dict):
            raise StageEntrypointError("parameters_invalid", "parameters must be an object")
        forbidden = _forbidden_parameter_paths(parameters)
        if forbidden:
            raise StageEntrypointError(
                "unsafe_parameter_control",
                "request parameters cannot select commands, executables, shells, or secrets: "
                + ", ".join(forbidden),
            )
        output_manifest = _safe_relative(payload["output_manifest"], "output_manifest")
        if output_manifest != "candidate-manifest.json":
            raise StageEntrypointError(
                "output_manifest_invalid",
                "output_manifest must be candidate-manifest.json",
            )
        for artifact in artifacts:
            artifact_path = _require_contained_file(
                workdir / artifact.path,
                workdir,
                f"input artifact {artifact.role}",
            )
            if _sha256_file(artifact_path) != artifact.sha256:
                raise StageEntrypointError(
                    "input_artifact_hash_mismatch",
                    f"input artifact {artifact.role!r} does not match its frozen SHA-256",
                )
            if artifact_path.stat().st_size != artifact.size_bytes:
                raise StageEntrypointError(
                    "input_artifact_size_mismatch",
                    f"input artifact {artifact.role!r} does not match its frozen size",
                )
            if not artifact.read_only:
                raise StageEntrypointError(
                    "input_artifact_not_read_only",
                    f"input artifact {artifact.role!r} is not declared read-only",
                )
        return cls(
            job_id=job_id,
            stage_key=expected_stage,
            stage_version=stage_version,
            attempt=attempt,
            primary_input=primary,
            input_artifacts=artifacts,
            predecessor_promotion=predecessor,
            release=release,
            parameters=MappingProxyType(_canonical_copy(parameters)),
            output_manifest=output_manifest,
            request_path=request_path,
            workdir=workdir,
        )

    def artifact(self, role: str) -> InputArtifact:
        matches = [item for item in self.input_artifacts if item.role == role]
        if len(matches) != 1:
            raise StageEntrypointError(
                "input_role_missing",
                f"expected exactly one input artifact with role {role!r}",
            )
        return matches[0]


@dataclass(frozen=True)
class StageInputRoot:
    root: Path
    files_by_role: Mapping[str, Path]
    extracted_by_role: Mapping[str, Path]

    def file(self, role: str) -> Path:
        try:
            return self.files_by_role[role]
        except KeyError as exc:
            raise StageEntrypointError(
                "input_role_missing",
                f"input artifact role {role!r} was not materialized",
            ) from exc

    def extracted(self, role: str) -> Path:
        try:
            return self.extracted_by_role[role]
        except KeyError as exc:
            raise StageEntrypointError(
                "input_bundle_required",
                f"input artifact role {role!r} is not a candidate bundle",
            ) from exc


@dataclass(frozen=True)
class StageProduction:
    artifact_kind: str
    metrics: Mapping[str, Any] = field(default_factory=dict)
    findings: tuple[Mapping[str, Any], ...] = ()
    artifact_roles: Mapping[str, str] = field(default_factory=dict)


Producer = Callable[
    [StageRequest, StageInputRoot, Path, Path],
    StageProduction,
]


@dataclass(frozen=True)
class KernelExecution:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


def run_stage_entrypoint(
    *,
    stage_key: str,
    request_path: str | os.PathLike[str],
    result_path: str | os.PathLike[str],
    producer: Producer,
    release_root: str | os.PathLike[str],
    first_stage: bool = False,
) -> int:
    """Run one producer and emit candidate evidence only.

    The function never evaluates or promotes its own candidate.  A zero exit
    means only that an immutable candidate bundle and both protocol manifests
    were written.
    """

    result = Path(result_path)
    if not result.is_absolute():
        result = (Path.cwd() / result).resolve()
    request: StageRequest | None = None
    try:
        _require_output_path(result, Path.cwd().resolve(), "result")
        release_path = Path(release_root)
        if release_path.is_symlink():
            raise StageEntrypointError(
                "release_root_invalid",
                "release root cannot be a symlink",
            )
        release = release_path.resolve()
        if not (release / "release-manifest.json").is_file():
            raise StageEntrypointError(
                "release_root_invalid",
                "release root is missing its immutable manifest",
            )
        request = StageRequest.load(
            request_path,
            expected_stage=stage_key,
            first_stage=first_stage,
        )
        _verify_release_binding(release, request.release)
        input_root = prepare_input_root(request)
        candidate_root = request.workdir / "candidate-output"
        if candidate_root.exists() or candidate_root.is_symlink():
            raise StageEntrypointError(
                "candidate_output_exists",
                "candidate output directory must not already exist",
            )
        candidate_root.mkdir(mode=0o700)
        production = producer(request, input_root, candidate_root, release)
        if not isinstance(production, StageProduction):
            raise StageEntrypointError(
                "producer_contract_invalid",
                "producer did not return StageProduction",
                exit_code=3,
            )
        artifact_kind = _require_text(production.artifact_kind, "artifact_kind")
        bundle = build_candidate_bundle(
            request,
            candidate_root=candidate_root,
            artifact_kind=artifact_kind,
            artifact_roles=production.artifact_roles,
        )
        candidate_manifest = {
            "schema_version": CANDIDATE_PROTOCOL,
            "job_id": request.job_id,
            "stage_key": request.stage_key,
            "attempt": request.attempt,
            "input_sha256": request.primary_input.sha256,
            "release_manifest_sha256": request.release.manifest_sha256,
            "artifact": {
                "kind": artifact_kind,
                "path": bundle["path"],
                "sha256": bundle["sha256"],
                "size_bytes": bundle["size_bytes"],
            },
            "metrics": _canonical_copy(dict(production.metrics)),
        }
        _write_json(request.workdir / request.output_manifest, candidate_manifest)
        stage_result = {
            "schema_version": RESULT_PROTOCOL,
            "job_id": request.job_id,
            "stage_key": request.stage_key,
            "stage_version": request.stage_version,
            "attempt": request.attempt,
            "status": "candidate_ready",
            "input_sha256": request.primary_input.sha256,
            "release_manifest_sha256": request.release.manifest_sha256,
            "candidate_artifacts": [candidate_manifest["artifact"]],
            "findings": [_canonical_copy(dict(item)) for item in production.findings],
            "metrics": _canonical_copy(dict(production.metrics)),
        }
        _write_json(result, stage_result)
        return 0
    except StageEntrypointError as exc:
        _write_failure_result(result, stage_key, request, exc)
        return exc.exit_code
    except Exception as exc:
        wrapped = StageEntrypointError(
            "producer_failed",
            f"stage producer failed: {type(exc).__name__}: {exc}",
            exit_code=3,
        )
        _write_failure_result(result, stage_key, request, wrapped)
        return wrapped.exit_code


def prepare_input_root(request: StageRequest) -> StageInputRoot:
    root = request.workdir / "stage-inputs"
    if root.exists() or root.is_symlink():
        raise StageEntrypointError(
            "stage_input_root_exists",
            "stage input root must not already exist",
        )
    root.mkdir(mode=0o700)
    files: dict[str, Path] = {}
    extracted: dict[str, Path] = {}
    for artifact in request.input_artifacts:
        source = _require_contained_file(
            request.workdir / artifact.path,
            request.workdir,
            f"input artifact {artifact.role}",
        )
        role_dir = root / artifact.role
        role_dir.mkdir(mode=0o700)
        destination = role_dir / "artifact"
        shutil.copyfile(source, destination)
        if _sha256_file(destination) != artifact.sha256:
            raise StageEntrypointError(
                "materialized_input_hash_mismatch",
                f"copied input {artifact.role!r} drifted",
            )
        destination.chmod(0o444)
        files[artifact.role] = destination
        if artifact.kind == "worker-v3-candidate-bundle":
            bundle_root = role_dir / "bundle"
            bundle_root.mkdir(mode=0o700)
            _safe_extract_candidate_bundle(destination, bundle_root)
            _verify_extracted_bundle_binding(request, artifact.role, bundle_root)
            _make_tree_read_only(bundle_root)
            extracted[artifact.role] = bundle_root
    return StageInputRoot(
        root=root,
        files_by_role=MappingProxyType(files),
        extracted_by_role=MappingProxyType(extracted),
    )


def build_candidate_bundle(
    request: StageRequest,
    *,
    candidate_root: Path,
    artifact_kind: str,
    artifact_roles: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    roles = dict(artifact_roles or {})
    inventory = _candidate_inventory(candidate_root, roles)
    if not inventory:
        raise StageEntrypointError(
            "candidate_empty",
            "producer created no candidate files",
            exit_code=3,
        )
    content_manifest = {
        "schema_version": BUNDLE_PROTOCOL,
        "job_id": request.job_id,
        "stage_key": request.stage_key,
        "stage_version": request.stage_version,
        "attempt": request.attempt,
        "artifact_kind": artifact_kind,
        "input_sha256": request.primary_input.sha256,
        "predecessor_promotion_sha256": (
            request.predecessor_promotion.promotion_manifest_sha256
            if request.predecessor_promotion
            else None
        ),
        "release_manifest_sha256": request.release.manifest_sha256,
        "files": inventory,
    }
    _write_json(candidate_root / "candidate-content-manifest.json", content_manifest)
    inventory = _candidate_inventory(
        candidate_root,
        {**roles, "candidate-content-manifest.json": "candidate_manifest"},
    )
    bundle_dir = request.workdir / "candidate"
    bundle_dir.mkdir(mode=0o700)
    bundle_path = bundle_dir / f"{request.stage_key}.tar.gz"
    _write_deterministic_tar_gz(candidate_root, bundle_path)
    return {
        "path": bundle_path.relative_to(request.workdir).as_posix(),
        "sha256": _sha256_file(bundle_path),
        "size_bytes": bundle_path.stat().st_size,
        "files": inventory,
    }


def require_parameter_keys(
    request: StageRequest,
    *,
    required: Sequence[str] = (),
    optional: Sequence[str] = (),
) -> Mapping[str, Any]:
    allowed = set(required) | set(optional)
    actual = set(request.parameters)
    if actual - allowed or set(required) - actual:
        raise StageEntrypointError(
            "stage_parameters_invalid",
            "stage parameters have missing or unknown keys",
            findings=(
                {
                    "code": "stage_parameters_invalid",
                    "missing": sorted(set(required) - actual),
                    "unknown": sorted(actual - allowed),
                },
            ),
        )
    return request.parameters


def run_release_python_kernel(
    *,
    release_root: Path,
    kernel_relative: str,
    args: Sequence[str],
    cwd: Path,
    timeout_seconds: int,
    accepted_returncodes: Sequence[int] = (0,),
) -> KernelExecution:
    """Invoke one code-selected release-local Python kernel without a shell."""

    relative = _safe_relative(kernel_relative, "kernel_relative")
    if not (
        relative.startswith("scripts/")
        or (relative.startswith("skills/") and "/scripts/" in relative)
    ):
        raise StageEntrypointError(
            "kernel_outside_scripts",
            "release kernel must be under scripts/ or a release-scoped skill scripts/ directory",
            exit_code=3,
        )
    kernel = _require_contained_file(
        release_root / relative,
        release_root,
        f"release kernel {relative}",
        code="kernel_missing",
        exit_code=3,
    )
    if kernel.suffix != ".py":
        raise StageEntrypointError(
            "kernel_missing",
            f"release-local kernel {relative!r} is unavailable",
            exit_code=3,
        )
    manifest = _load_json_object(release_root / "release-manifest.json", "release manifest")
    file_rows = manifest.get("files")
    if not isinstance(file_rows, list):
        raise StageEntrypointError(
            "release_file_inventory_missing",
            "release manifest has no file inventory",
            exit_code=3,
        )
    declarations = [
        row
        for row in file_rows
        if isinstance(row, dict) and row.get("path") == relative
    ]
    if len(declarations) != 1 or declarations[0].get("sha256") != _sha256_file(kernel):
        raise StageEntrypointError(
            "kernel_release_binding_mismatch",
            f"kernel {relative!r} is not hash-bound by the release manifest",
            exit_code=3,
        )
    if (
        not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or not 1 <= timeout_seconds <= 86_400
    ):
        raise StageEntrypointError(
            "kernel_timeout_invalid",
            "kernel timeout must be 1..86400 seconds",
            exit_code=3,
        )
    if any(not isinstance(token, str) or "\x00" in token for token in args):
        raise StageEntrypointError(
            "kernel_args_invalid",
            "kernel argv contains a non-string or NUL",
            exit_code=3,
        )
    invocation = (sys.executable, str(kernel), *tuple(args))
    try:
        completed = subprocess.run(
            invocation,
            cwd=str(cwd),
            env={
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "LANG": os.environ.get("LANG", "C.UTF-8"),
                "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise StageEntrypointError(
            "kernel_timeout",
            f"release kernel {relative!r} exceeded {timeout_seconds} seconds",
            exit_code=3,
        ) from exc
    execution = KernelExecution(
        argv=(relative, *tuple(args)),
        returncode=completed.returncode,
        stdout=completed.stdout[-32_768:],
        stderr=completed.stderr[-32_768:],
    )
    if execution.returncode not in set(accepted_returncodes):
        raise StageEntrypointError(
            "kernel_failed",
            f"release kernel {relative!r} exited {execution.returncode}: "
            f"{execution.stderr[-2000:]}",
            exit_code=3,
        )
    return execution


def sha256_file(path: str | os.PathLike[str]) -> str:
    return _sha256_file(Path(path))


def write_json(path: str | os.PathLike[str], value: Mapping[str, Any]) -> None:
    _write_json(Path(path), value)


def _input_descriptor(raw: Any) -> InputDescriptor:
    value = _exact_object(raw, {"kind", "sha256", "size_bytes", "path"}, "input")
    return InputDescriptor(
        kind=_require_text(value["kind"], "input.kind"),
        sha256=_require_sha256(value["sha256"], "input.sha256"),
        size_bytes=_require_nonnegative_int(value["size_bytes"], "input.size_bytes"),
        path=_safe_relative(value["path"], "input.path"),
    )


def _input_artifacts(raw: Any) -> tuple[InputArtifact, ...]:
    if not isinstance(raw, list) or not 1 <= len(raw) <= _MAX_INPUT_ARTIFACTS:
        raise StageEntrypointError(
            "input_artifacts_invalid",
            f"input_artifacts must contain 1..{_MAX_INPUT_ARTIFACTS} items",
        )
    result: list[InputArtifact] = []
    roles: set[str] = set()
    paths: set[str] = set()
    for index, item in enumerate(raw):
        value = _exact_object(
            item,
            {"role", "kind", "sha256", "size_bytes", "path", "read_only"},
            f"input_artifacts[{index}]",
        )
        role = _require_identifier(value["role"], f"input_artifacts[{index}].role")
        path = _safe_relative(value["path"], f"input_artifacts[{index}].path")
        if role in roles or path in paths:
            raise StageEntrypointError(
                "input_artifacts_duplicate",
                "input artifact roles and paths must be unique",
            )
        if value["read_only"] is not True:
            raise StageEntrypointError(
                "input_artifact_not_read_only",
                f"input artifact {role!r} must be read-only",
            )
        roles.add(role)
        paths.add(path)
        result.append(
            InputArtifact(
                role=role,
                kind=_require_text(value["kind"], f"input_artifacts[{index}].kind"),
                sha256=_require_sha256(
                    value["sha256"],
                    f"input_artifacts[{index}].sha256",
                ),
                size_bytes=_require_nonnegative_int(
                    value["size_bytes"],
                    f"input_artifacts[{index}].size_bytes",
                ),
                path=path,
                read_only=True,
            )
        )
    return tuple(result)


def _predecessor_promotion(raw: Any) -> PredecessorPromotion | None:
    if raw is None:
        return None
    value = _exact_object(
        raw,
        {
            "promotion_id",
            "stage_key",
            "artifact_sha256",
            "evaluation_sha256",
            "promotion_manifest_sha256",
        },
        "predecessor_promotion",
    )
    return PredecessorPromotion(
        promotion_id=_require_text(value["promotion_id"], "predecessor_promotion.promotion_id"),
        stage_key=_require_identifier(value["stage_key"], "predecessor_promotion.stage_key"),
        artifact_sha256=_require_sha256(
            value["artifact_sha256"],
            "predecessor_promotion.artifact_sha256",
        ),
        evaluation_sha256=_require_sha256(
            value["evaluation_sha256"],
            "predecessor_promotion.evaluation_sha256",
        ),
        promotion_manifest_sha256=_require_sha256(
            value["promotion_manifest_sha256"],
            "predecessor_promotion.promotion_manifest_sha256",
        ),
    )


def _release_binding(raw: Any) -> ReleaseBinding:
    value = _exact_object(
        raw,
        {
            "release_id",
            "version",
            "manifest_sha256",
            "tree_sha256",
            "runtime_identity_sha256",
        },
        "release",
    )
    return ReleaseBinding(
        release_id=_require_text(value["release_id"], "release.release_id"),
        version=_require_text(value["version"], "release.version"),
        manifest_sha256=_require_sha256(
            value["manifest_sha256"],
            "release.manifest_sha256",
        ),
        tree_sha256=_require_sha256(value["tree_sha256"], "release.tree_sha256"),
        runtime_identity_sha256=_require_sha256(
            value["runtime_identity_sha256"],
            "release.runtime_identity_sha256",
        ),
    )


def _safe_extract_candidate_bundle(source: Path, destination: Path) -> None:
    seen: set[str] = set()
    total = 0
    try:
        with tarfile.open(source, "r:gz") as archive:
            members = archive.getmembers()
            if len(members) > _MAX_ARCHIVE_MEMBERS:
                raise StageEntrypointError(
                    "input_bundle_too_many_members",
                    "candidate bundle has too many members",
                )
            for member in members:
                name = _safe_relative(member.name, "candidate bundle member")
                if name in seen:
                    raise StageEntrypointError(
                        "input_bundle_duplicate_member",
                        f"candidate bundle contains duplicate member {name!r}",
                    )
                seen.add(name)
                if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                    raise StageEntrypointError(
                        "input_bundle_unsafe_member",
                        f"candidate bundle member {name!r} is not a regular file or directory",
                    )
                total += max(0, int(member.size))
                if total > _MAX_ARCHIVE_BYTES:
                    raise StageEntrypointError(
                        "input_bundle_too_large",
                        "candidate bundle exceeds the extraction budget",
                    )
                target = (destination / name).resolve()
                if destination not in target.parents:
                    raise StageEntrypointError(
                        "input_bundle_path_escape",
                        f"candidate bundle member {name!r} escapes extraction root",
                    )
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True, mode=0o700)
                    continue
                if not member.isfile():
                    raise StageEntrypointError(
                        "input_bundle_unsafe_member",
                        f"candidate bundle member {name!r} is unsupported",
                    )
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise StageEntrypointError(
                        "input_bundle_read_failed",
                        f"candidate bundle member {name!r} cannot be read",
                    )
                with target.open("xb") as handle:
                    shutil.copyfileobj(extracted, handle)
    except (OSError, tarfile.TarError) as exc:
        raise StageEntrypointError(
            "input_bundle_invalid",
            f"candidate bundle is invalid: {exc}",
        ) from exc
    manifest_path = destination / "candidate-content-manifest.json"
    manifest = _load_json_object(manifest_path, "candidate content manifest")
    if manifest.get("schema_version") != BUNDLE_PROTOCOL:
        raise StageEntrypointError(
            "input_bundle_protocol_invalid",
            "candidate bundle content manifest has an unsupported protocol",
        )
    manifest_fields = {
        "schema_version",
        "job_id",
        "stage_key",
        "stage_version",
        "attempt",
        "artifact_kind",
        "input_sha256",
        "predecessor_promotion_sha256",
        "release_manifest_sha256",
        "files",
    }
    if set(manifest) != manifest_fields:
        raise StageEntrypointError(
            "input_bundle_manifest_invalid",
            "candidate bundle content manifest has missing or unknown fields",
        )
    declared = manifest.get("files")
    if not isinstance(declared, list):
        raise StageEntrypointError(
            "input_bundle_inventory_invalid",
            "candidate bundle content manifest has no file inventory",
        )
    expected: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(declared):
        if not isinstance(row, dict) or set(row) != {
            "path",
            "role",
            "sha256",
            "size_bytes",
        }:
            raise StageEntrypointError(
                "input_bundle_inventory_invalid",
                f"candidate bundle inventory row {index} is malformed",
            )
        path = _safe_relative(row["path"], f"candidate inventory row {index}")
        if path in expected or path == "candidate-content-manifest.json":
            raise StageEntrypointError(
                "input_bundle_inventory_invalid",
                "candidate bundle inventory paths must be unique and cannot self-declare",
            )
        expected[path] = {
            "path": path,
            "role": _require_identifier(
                row["role"],
                f"candidate inventory row {index}.role",
            ),
            "sha256": _require_sha256(
                row["sha256"],
                f"candidate inventory row {index}.sha256",
            ),
            "size_bytes": _require_nonnegative_int(
                row["size_bytes"],
                f"candidate inventory row {index}.size_bytes",
            ),
        }
    actual = {
        row["path"]: row
        for row in _candidate_inventory(
            destination,
            {"candidate-content-manifest.json": "candidate_manifest"},
            exclude_manifest=True,
        )
    }
    if set(expected) != set(actual):
        raise StageEntrypointError(
            "input_bundle_inventory_mismatch",
            "candidate bundle file inventory does not match extracted files",
        )
    for path, current in actual.items():
        prior = expected[path]
        if (
            prior.get("sha256") != current["sha256"]
            or prior.get("size_bytes") != current["size_bytes"]
        ):
            raise StageEntrypointError(
                "input_bundle_hash_mismatch",
                f"candidate bundle member {path!r} does not match its inventory",
            )


def _verify_extracted_bundle_binding(
    request: StageRequest,
    role: str,
    bundle_root: Path,
) -> None:
    manifest = _load_json_object(
        bundle_root / "candidate-content-manifest.json",
        "candidate content manifest",
    )
    expected_stage = {
        "structure_candidate": "outline_reconstruction",
        "media_candidate": "canonical_block_ledger",
    }.get(role)
    if role == "promoted_predecessor":
        predecessor = request.predecessor_promotion
        if predecessor is None:
            raise StageEntrypointError(
                "input_bundle_predecessor_missing",
                "a promoted predecessor bundle has no promotion binding",
            )
        expected_stage = predecessor.stage_key
    expected = {
        "job_id": request.job_id,
        "release_manifest_sha256": request.release.manifest_sha256,
    }
    if expected_stage is not None:
        expected["stage_key"] = expected_stage
    if any(manifest.get(name) != value for name, value in expected.items()):
        raise StageEntrypointError(
            "input_bundle_binding_mismatch",
            f"candidate bundle role {role!r} is not bound to the expected job, stage, and release",
        )


def _candidate_inventory(
    root: Path,
    roles: Mapping[str, str],
    *,
    exclude_manifest: bool = False,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    total_bytes = 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise StageEntrypointError(
                "candidate_symlink_forbidden",
                f"candidate contains a symlink: {path.relative_to(root)}",
                exit_code=3,
            )
        if path.is_dir():
            continue
        if not path.is_file():
            raise StageEntrypointError(
                "candidate_nonregular_forbidden",
                f"candidate contains a non-regular file: {path.relative_to(root)}",
                exit_code=3,
            )
        relative = path.relative_to(root).as_posix()
        if exclude_manifest and relative == "candidate-content-manifest.json":
            continue
        size_bytes = path.stat().st_size
        total_bytes += size_bytes
        if (
            len(rows) >= _MAX_ARCHIVE_MEMBERS
            or total_bytes > _MAX_ARCHIVE_BYTES
        ):
            raise StageEntrypointError(
                "candidate_budget_exceeded",
                "candidate exceeds the file-count or byte budget",
                exit_code=3,
            )
        rows.append(
            {
                "path": relative,
                "role": roles.get(relative, "evidence"),
                "sha256": _sha256_file(path),
                "size_bytes": size_bytes,
            }
        )
    return rows


def _write_deterministic_tar_gz(source_root: Path, output: Path) -> None:
    with output.open("xb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for path in sorted(source_root.rglob("*")):
                    relative = path.relative_to(source_root).as_posix()
                    info = tarfile.TarInfo(relative)
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mtime = 0
                    if path.is_dir():
                        info.type = tarfile.DIRTYPE
                        info.mode = 0o755
                        archive.addfile(info)
                    elif path.is_file() and not path.is_symlink():
                        info.type = tarfile.REGTYPE
                        info.mode = 0o644
                        info.size = path.stat().st_size
                        with path.open("rb") as handle:
                            archive.addfile(info, handle)
                    else:
                        raise StageEntrypointError(
                            "candidate_unsafe_file",
                            f"candidate path {relative!r} is not a regular file or directory",
                            exit_code=3,
                        )


def _make_tree_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    root.chmod(0o555)


def _forbidden_parameter_paths(value: Any, prefix: str = "$") -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            current = f"{prefix}.{key}"
            if normalized in _FORBIDDEN_PARAMETER_KEYS:
                findings.append(current)
            findings.extend(_forbidden_parameter_paths(item, current))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            findings.extend(_forbidden_parameter_paths(item, f"{prefix}[{index}]"))
    elif isinstance(value, str):
        expanded = value.strip()
        mutable_skill_fragment = f".codex{os.sep}skills"
        if (
            expanded.startswith("/")
            or expanded.startswith("~/")
            or mutable_skill_fragment in expanded
        ):
            findings.append(prefix)
    return findings


def _write_failure_result(
    path: Path,
    stage_key: str,
    request: StageRequest | None,
    error: StageEntrypointError,
) -> None:
    try:
        root = Path.cwd().resolve()
        if root not in path.parents or path.exists() or path.is_symlink():
            return
        payload = {
            "schema_version": RESULT_PROTOCOL,
            "job_id": request.job_id if request else None,
            "stage_key": stage_key,
            "stage_version": request.stage_version if request else None,
            "attempt": request.attempt if request else None,
            "status": "failed",
            "input_sha256": request.primary_input.sha256 if request else None,
            "release_manifest_sha256": (
                request.release.manifest_sha256 if request else None
            ),
            "candidate_artifacts": [],
            "findings": [
                {
                    "code": error.code,
                    "message": str(error),
                    "blocking": True,
                },
                *[_canonical_copy(dict(item)) for item in error.findings],
            ],
            "metrics": {},
        }
        _write_json(path, payload)
    except OSError:
        pass


def _verify_release_binding(root: Path, binding: ReleaseBinding) -> None:
    manifest_path = root / "release-manifest.json"
    if _sha256_file(manifest_path) != binding.manifest_sha256:
        raise StageEntrypointError(
            "release_manifest_hash_mismatch",
            "installed release manifest does not match the request binding",
            exit_code=3,
        )
    manifest = _load_json_object(manifest_path, "release manifest")
    try:
        runtime_identity = runtime_identity_for_manifest(manifest)
    except ValueError as exc:
        raise StageEntrypointError(
            "release_runtime_identity_missing",
            f"installed release has no canonical runtime identity: {exc}",
            exit_code=3,
        ) from exc
    expected = {
        "release_id": (manifest.get("release_id"), binding.release_id),
        "version": (manifest.get("version"), binding.version),
        "tree_sha256": (
            (manifest.get("tree_hash") or {}).get("sha256"),
            binding.tree_sha256,
        ),
        "runtime_identity_sha256": (
            runtime_identity,
            binding.runtime_identity_sha256,
        ),
    }
    for label, values in expected.items():
        if values[0] != values[1]:
            raise StageEntrypointError(
                "release_binding_mismatch",
                f"installed release {label} does not match the request",
                exit_code=3,
            )


def _exact_object(raw: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != fields:
        raise StageEntrypointError(
            "request_shape_invalid",
            f"{label} has missing or unknown fields",
        )
    return raw


def _safe_relative(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or value.startswith("/"):
        raise StageEntrypointError(
            "unsafe_relative_path",
            f"{label} must be a non-empty relative POSIX path",
        )
    parsed = PurePosixPath(value)
    if str(parsed) != value or any(part in {"", ".", ".."} for part in parsed.parts):
        raise StageEntrypointError(
            "unsafe_relative_path",
            f"{label} is not normalized",
        )
    return value


def _require_contained_file(
    path: Path,
    root: Path,
    label: str,
    *,
    code: str = "unsafe_input_path",
    exit_code: int = 2,
) -> Path:
    root = root.resolve()
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise StageEntrypointError(
            code,
            f"{label} is missing, linked, or outside the attempt workspace",
            exit_code=exit_code,
        )
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise StageEntrypointError(
                code,
                f"{label} is missing, linked, or outside the attempt workspace",
                exit_code=exit_code,
            )
    resolved = path.resolve()
    if root not in resolved.parents or not resolved.is_file():
        raise StageEntrypointError(
            code,
            f"{label} is missing, linked, or outside the attempt workspace",
            exit_code=exit_code,
        )
    return resolved


def _require_output_path(path: Path, root: Path, label: str) -> None:
    if root not in path.parents or path.is_symlink() or path.exists():
        raise StageEntrypointError(
            "unsafe_output_path",
            f"{label} must be a new file inside the attempt workspace",
        )
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-finite number {token}")
            ),
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise StageEntrypointError(
            "json_invalid",
            f"{label} is not valid canonical JSON: {exc}",
        ) from exc
    if not isinstance(value, dict):
        raise StageEntrypointError("json_invalid", f"{label} must be an object")
    _canonical_copy(value)
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(
            value,
            handle,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        handle.write("\n")
    path.chmod(0o600)


def _canonical_copy(value: Any) -> Any:
    try:
        return json.loads(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        )
    except (TypeError, ValueError) as exc:
        raise StageEntrypointError(
            "non_canonical_json",
            "protocol values must be finite canonical JSON",
        ) from exc


def _require_sha256(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _SHA256_CHARS for character in value)
    ):
        raise StageEntrypointError(
            "sha256_invalid",
            f"{label} must be a lowercase SHA-256",
        )
    return value


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StageEntrypointError("text_invalid", f"{label} must be a non-empty string")
    return value


def _require_identifier(value: Any, label: str) -> str:
    text = _require_text(value, label)
    if any(character not in "abcdefghijklmnopqrstuvwxyz0123456789._-" for character in text):
        raise StageEntrypointError(
            "identifier_invalid",
            f"{label} contains unsupported characters",
        )
    return text


def _require_positive_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise StageEntrypointError(
            "integer_invalid",
            f"{label} must be a positive integer",
        )
    return value


def _require_nonnegative_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise StageEntrypointError(
            "integer_invalid",
            f"{label} must be a non-negative integer",
        )
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
