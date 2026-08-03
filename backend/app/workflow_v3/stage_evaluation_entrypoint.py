from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence

try:
    from .contracts import contracts_for_version
    from .stage_entrypoint import (
        BUNDLE_PROTOCOL,
        StageEntrypointError,
        sha256_file,
        write_json,
    )
except ImportError:  # Release-local scripts are imported outside the backend package.
    from contracts import contracts_for_version  # type: ignore[no-redef]
    from stage_entrypoint import (  # type: ignore[no-redef]
        BUNDLE_PROTOCOL,
        StageEntrypointError,
        sha256_file,
        write_json,
    )


EVALUATION_REQUEST_PROTOCOL = "luceon.worker-v3-evaluation-request/v1"
EVALUATION_PROTOCOL = "luceon.worker-v3-stage-evaluation/v1"
CONTROL_PLANE_CHAIN_PROTOCOL = "luceon.worker-v3-control-plane-chain/v1"
CONTROL_PLANE_CHAIN_PATH = "control-plane/promotion-chain.json"
READY_FOR_USER_ACCEPTANCE_STAGE = "ready_for_user_acceptance"
_MAX_ARCHIVE_MEMBERS = 20_000
_MAX_ARCHIVE_BYTES = 4_000_000_000


@dataclass(frozen=True)
class EvaluationCandidate:
    candidate_id: str
    path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class ControlPlaneChainSnapshot:
    path: Path
    sha256: str
    size_bytes: int
    _canonical_json: str = field(repr=False)

    @property
    def payload(self) -> Mapping[str, Any]:
        # Return a detached value so evaluator code cannot mutate the verified
        # snapshot held by this request.
        return json.loads(self._canonical_json)


@dataclass(frozen=True)
class StageEvaluationRequest:
    job_id: str
    stage_key: str
    stage_version: str
    attempt: int
    candidate: EvaluationCandidate
    release_manifest_sha256: str
    policy_sha256: str
    required_gates: tuple[str, ...]
    output_manifest: str
    workdir: Path
    control_plane_chain: ControlPlaneChainSnapshot | None = None

    @classmethod
    def load(
        cls,
        path: str | os.PathLike[str],
        *,
        expected_stage: str,
    ) -> StageEvaluationRequest:
        workdir = Path.cwd().resolve()
        request_path = _contained_file(workdir, path, "evaluation request")
        payload = _json_object(request_path, "evaluation request")
        required = {
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
        }
        if expected_stage == READY_FOR_USER_ACCEPTANCE_STAGE:
            required.add("control_plane_chain")
        if set(payload) != required:
            raise StageEntrypointError(
                "evaluation_request_shape_invalid",
                "evaluation request has missing or unknown fields",
            )
        if (
            payload["schema_version"] != EVALUATION_REQUEST_PROTOCOL
            or payload["mode"] != "evaluate"
        ):
            raise StageEntrypointError(
                "evaluation_request_protocol_invalid",
                "evaluation request protocol or mode is invalid",
            )
        if payload["stage_key"] != expected_stage:
            raise StageEntrypointError(
                "evaluation_stage_binding_mismatch",
                "evaluation request is bound to another stage",
            )
        job_id = _text(payload["job_id"], "job_id")
        stage_version = _text(payload["stage_version"], "stage_version")
        attempt = _positive_int(payload["attempt"], "attempt")
        release_manifest_sha256 = _sha256(
            payload["release_manifest_sha256"],
            "release_manifest_sha256",
        )
        candidate = _candidate(payload["candidate"])
        candidate_path = _contained_file(workdir, candidate.path, "candidate")
        if candidate_path.stat().st_size != candidate.size_bytes:
            raise StageEntrypointError(
                "evaluation_candidate_size_mismatch",
                "candidate size differs from the immutable request",
            )
        if sha256_file(candidate_path) != candidate.sha256:
            raise StageEntrypointError(
                "evaluation_candidate_hash_mismatch",
                "candidate bytes differ from the immutable request",
            )
        gates = payload["required_gates"]
        if (
            not isinstance(gates, list)
            or not gates
            or len(gates) != len(set(gates))
            or any(not _identifier(value) for value in gates)
        ):
            raise StageEntrypointError(
                "evaluation_gates_invalid",
                "required_gates must be a non-empty unique identifier list",
            )
        output_manifest = _relative(payload["output_manifest"], "output_manifest")
        if output_manifest != "evaluation-manifest.json":
            raise StageEntrypointError(
                "evaluation_output_invalid",
                "evaluation output must be evaluation-manifest.json",
            )
        control_plane_chain = None
        if expected_stage == READY_FOR_USER_ACCEPTANCE_STAGE:
            control_plane_chain = _control_plane_chain_snapshot(
                payload["control_plane_chain"],
                workdir=workdir,
                job_id=job_id,
                stage_key=expected_stage,
                stage_version=stage_version,
                attempt=attempt,
                release_manifest_sha256=release_manifest_sha256,
            )
        return cls(
            job_id=job_id,
            stage_key=expected_stage,
            stage_version=stage_version,
            attempt=attempt,
            candidate=candidate,
            release_manifest_sha256=release_manifest_sha256,
            policy_sha256=_sha256(payload["policy_sha256"], "policy_sha256"),
            required_gates=tuple(gates),
            output_manifest=output_manifest,
            workdir=workdir,
            control_plane_chain=control_plane_chain,
        )


@dataclass(frozen=True)
class EvaluationInput:
    bundle_root: Path
    content_manifest: Mapping[str, Any]


@dataclass(frozen=True)
class StageEvaluation:
    gate_results: Mapping[str, bool]
    findings: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    disposition: str | None = None


Evaluator = Callable[
    [StageEvaluationRequest, EvaluationInput, Path],
    StageEvaluation,
]


def run_stage_evaluation_entrypoint(
    *,
    stage_key: str,
    request_path: str | os.PathLike[str],
    result_path: str | os.PathLike[str],
    evaluator: Evaluator,
    release_root: str | os.PathLike[str],
) -> int:
    """Run a read-only evaluator and emit no candidate or promotion artifact."""

    result = _output_path(result_path)
    request: StageEvaluationRequest | None = None
    try:
        request = StageEvaluationRequest.load(
            request_path,
            expected_stage=stage_key,
        )
        release = Path(release_root).resolve()
        manifest = _contained_file(release, "release-manifest.json", "release manifest")
        if sha256_file(manifest) != request.release_manifest_sha256:
            raise StageEntrypointError(
                "evaluation_release_hash_mismatch",
                "installed release differs from the evaluation request",
                exit_code=3,
            )
        evaluation_input = _prepare_candidate(request)
        evaluation = evaluator(request, evaluation_input, release)
        if not isinstance(evaluation, StageEvaluation):
            raise StageEntrypointError(
                "evaluator_contract_invalid",
                "evaluator did not return StageEvaluation",
                exit_code=3,
            )
        gates = dict(evaluation.gate_results)
        if set(gates) != set(request.required_gates) or any(
            type(value) is not bool for value in gates.values()
        ):
            raise StageEntrypointError(
                "evaluation_gate_result_invalid",
                "evaluator results do not exactly match the requested hard gates",
                exit_code=3,
            )
        findings = [dict(item) for item in evaluation.findings]
        decision = evaluation.disposition or (
            "passed" if all(gates.values()) else "failed"
        )
        if decision not in {"passed", "needs_review", "failed"}:
            raise StageEntrypointError(
                "evaluation_disposition_invalid",
                "evaluator disposition must be passed, needs_review, or failed",
                exit_code=3,
            )
        if decision == "passed" and not all(gates.values()):
            raise StageEntrypointError(
                "evaluation_disposition_invalid",
                "passed evaluator disposition contains a failed hard gate",
                exit_code=3,
            )
        if decision in {"needs_review", "failed"} and all(gates.values()):
            raise StageEntrypointError(
                "evaluation_disposition_invalid",
                f"{decision} evaluator disposition has no failed hard gate",
                exit_code=3,
            )
        if decision == "needs_review" and not findings:
            raise StageEntrypointError(
                "evaluation_needs_review_invalid",
                "needs_review requires an evidence-bound finding",
                exit_code=3,
            )
        if decision == "needs_review":
            _validate_needs_review_findings(
                evaluation_input,
                findings,
            )
        payload = {
            "schema_version": EVALUATION_PROTOCOL,
            "job_id": request.job_id,
            "stage_key": request.stage_key,
            "attempt": request.attempt,
            "candidate_sha256": request.candidate.sha256,
            "release_manifest_sha256": request.release_manifest_sha256,
            "policy_sha256": request.policy_sha256,
            "decision": decision,
            "gate_results": gates,
            "findings": findings,
        }
        write_json(request.workdir / request.output_manifest, payload)
        write_json(result, payload)
        return 0
    except StageEntrypointError as exc:
        _write_failure(result, stage_key, request, exc)
        return exc.exit_code
    except Exception as exc:
        wrapped = StageEntrypointError(
            "evaluator_failed",
            f"stage evaluator failed: {type(exc).__name__}: {exc}",
            exit_code=3,
        )
        _write_failure(result, stage_key, request, wrapped)
        return wrapped.exit_code


def _prepare_candidate(request: StageEvaluationRequest) -> EvaluationInput:
    root = request.workdir / "evaluation-input"
    if root.exists() or root.is_symlink():
        raise StageEntrypointError(
            "evaluation_input_exists",
            "evaluation extraction directory already exists",
        )
    root.mkdir(mode=0o700)
    source = _contained_file(request.workdir, request.candidate.path, "candidate")
    bundle = root / "bundle"
    bundle.mkdir(mode=0o700)
    _extract_candidate(source, bundle)
    manifest = _json_object(
        bundle / "candidate-content-manifest.json",
        "candidate content manifest",
    )
    expected = {
        "schema_version": BUNDLE_PROTOCOL,
        "job_id": request.job_id,
        "stage_key": request.stage_key,
        "attempt": request.attempt,
        "release_manifest_sha256": request.release_manifest_sha256,
    }
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise StageEntrypointError(
            "evaluation_candidate_binding_mismatch",
            "candidate bundle is not bound to this evaluation request",
        )
    _make_read_only(bundle)
    return EvaluationInput(bundle_root=bundle, content_manifest=manifest)


def _validate_needs_review_findings(
    evaluation_input: EvaluationInput,
    findings: Sequence[Mapping[str, Any]],
) -> None:
    for index, finding in enumerate(findings):
        if (
            not _identifier(finding.get("code"))
            or finding.get("blocking") is not True
            or not _identifier(finding.get("responsible_stage"))
            or not _identifier(finding.get("recovery_stage"))
        ):
            raise StageEntrypointError(
                "evaluation_needs_review_invalid",
                f"needs_review finding {index} has an invalid responsibility",
                exit_code=3,
            )
        evidence_refs = finding.get("evidence_refs")
        if not isinstance(evidence_refs, list) or not evidence_refs:
            raise StageEntrypointError(
                "evaluation_needs_review_invalid",
                f"needs_review finding {index} has no evidence",
                exit_code=3,
            )
        for evidence_index, reference in enumerate(evidence_refs):
            if not isinstance(reference, dict):
                raise StageEntrypointError(
                    "evaluation_needs_review_invalid",
                    (
                        f"needs_review finding {index} evidence "
                        f"{evidence_index} is invalid"
                    ),
                    exit_code=3,
                )
            path = reference.get("path")
            if not isinstance(path, str) or not path:
                raise StageEntrypointError(
                    "evaluation_needs_review_invalid",
                    (
                        "formal evaluator needs_review evidence must be a "
                        "candidate-relative path"
                    ),
                    exit_code=3,
                )
            evidence = _contained_file(
                evaluation_input.bundle_root,
                path,
                "needs_review evidence",
            )
            if sha256_file(evidence) != _sha256(
                reference.get("sha256"),
                "needs_review evidence sha256",
            ):
                raise StageEntrypointError(
                    "evaluation_needs_review_invalid",
                    (
                        f"needs_review finding {index} evidence "
                        f"{evidence_index} hash mismatch"
                    ),
                    exit_code=3,
                )
        handoff = finding.get("handoff")
        recovery_stage = str(finding.get("recovery_stage") or "")
        if (
            not isinstance(handoff, dict)
            or not str(handoff.get("summary") or "").strip()
            or not str(handoff.get("required_action") or "").strip()
            or handoff.get("resume_stage") != recovery_stage
        ):
            raise StageEntrypointError(
                "evaluation_needs_review_invalid",
                f"needs_review finding {index} has an invalid handoff",
                exit_code=3,
            )


def _extract_candidate(source: Path, destination: Path) -> None:
    seen: set[str] = set()
    total = 0
    try:
        with tarfile.open(source, "r:gz") as archive:
            members = archive.getmembers()
            if len(members) > _MAX_ARCHIVE_MEMBERS:
                raise StageEntrypointError(
                    "evaluation_candidate_too_many_files",
                    "candidate bundle exceeds the file-count budget",
                )
            for member in members:
                name = _relative(member.name, "candidate member")
                if name in seen:
                    raise StageEntrypointError(
                        "evaluation_candidate_duplicate_member",
                        f"candidate bundle repeats {name!r}",
                    )
                seen.add(name)
                if not (member.isdir() or member.isfile()):
                    raise StageEntrypointError(
                        "evaluation_candidate_unsafe_member",
                        f"candidate member {name!r} is not a regular file or directory",
                    )
                total += max(0, int(member.size))
                if total > _MAX_ARCHIVE_BYTES:
                    raise StageEntrypointError(
                        "evaluation_candidate_too_large",
                        "candidate bundle exceeds the extraction budget",
                    )
                target = (destination / name).resolve()
                if destination.resolve() not in target.parents:
                    raise StageEntrypointError(
                        "evaluation_candidate_path_escape",
                        f"candidate member {name!r} escapes the extraction root",
                    )
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True, mode=0o700)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                stream = archive.extractfile(member)
                if stream is None:
                    raise StageEntrypointError(
                        "evaluation_candidate_read_failed",
                        f"candidate member {name!r} cannot be read",
                    )
                with target.open("xb") as output:
                    shutil.copyfileobj(stream, output)
    except (OSError, tarfile.TarError) as exc:
        raise StageEntrypointError(
            "evaluation_candidate_invalid",
            f"candidate bundle cannot be extracted: {exc}",
        ) from exc
    _verify_inventory(destination)


def _verify_inventory(root: Path) -> None:
    manifest = _json_object(
        root / "candidate-content-manifest.json",
        "candidate content manifest",
    )
    if manifest.get("schema_version") != BUNDLE_PROTOCOL:
        raise StageEntrypointError(
            "evaluation_candidate_protocol_invalid",
            "candidate content manifest uses an unsupported protocol",
        )
    declared = manifest.get("files")
    if not isinstance(declared, list):
        raise StageEntrypointError(
            "evaluation_candidate_inventory_invalid",
            "candidate content manifest has no file inventory",
        )
    expected: dict[str, tuple[str, int]] = {}
    for index, row in enumerate(declared):
        if not isinstance(row, dict) or set(row) != {
            "path",
            "role",
            "sha256",
            "size_bytes",
        }:
            raise StageEntrypointError(
                "evaluation_candidate_inventory_invalid",
                f"candidate inventory row {index} is malformed",
            )
        path = _relative(row["path"], f"candidate inventory row {index}")
        if path in expected or path == "candidate-content-manifest.json":
            raise StageEntrypointError(
                "evaluation_candidate_inventory_invalid",
                "candidate inventory paths must be unique and cannot self-declare",
            )
        expected[path] = (
            _sha256(row["sha256"], f"candidate inventory row {index}.sha256"),
            _nonnegative_int(
                row["size_bytes"],
                f"candidate inventory row {index}.size_bytes",
            ),
        )
    actual = {
        path.relative_to(root).as_posix(): (
            sha256_file(path),
            path.stat().st_size,
        )
        for path in root.rglob("*")
        if path.is_file() and path.name != "candidate-content-manifest.json"
    }
    if actual != expected:
        raise StageEntrypointError(
            "evaluation_candidate_inventory_mismatch",
            "candidate files do not match their immutable inventory",
        )


def _candidate(raw: Any) -> EvaluationCandidate:
    fields = {"id", "path", "sha256", "size_bytes"}
    if not isinstance(raw, dict) or set(raw) != fields:
        raise StageEntrypointError(
            "evaluation_candidate_invalid",
            "candidate descriptor has missing or unknown fields",
        )
    return EvaluationCandidate(
        candidate_id=_text(raw["id"], "candidate.id"),
        path=_relative(raw["path"], "candidate.path"),
        sha256=_sha256(raw["sha256"], "candidate.sha256"),
        size_bytes=_nonnegative_int(raw["size_bytes"], "candidate.size_bytes"),
    )


def _control_plane_chain_snapshot(
    raw: Any,
    *,
    workdir: Path,
    job_id: str,
    stage_key: str,
    stage_version: str,
    attempt: int,
    release_manifest_sha256: str,
) -> ControlPlaneChainSnapshot:
    fields = {"path", "sha256", "size_bytes"}
    if not isinstance(raw, dict) or set(raw) != fields:
        raise StageEntrypointError(
            "evaluation_control_plane_chain_invalid",
            "control-plane chain descriptor has missing or unknown fields",
        )
    relative = _relative(raw["path"], "control_plane_chain.path")
    if relative != CONTROL_PLANE_CHAIN_PATH:
        raise StageEntrypointError(
            "evaluation_control_plane_chain_invalid",
            "control-plane chain must use its canonical snapshot path",
        )
    expected_sha256 = _sha256(
        raw["sha256"],
        "control_plane_chain.sha256",
    )
    expected_size_bytes = _nonnegative_int(
        raw["size_bytes"],
        "control_plane_chain.size_bytes",
    )
    path = _contained_file(workdir, relative, "control-plane chain")
    if path.stat().st_mode & 0o222:
        raise StageEntrypointError(
            "evaluation_control_plane_chain_writable",
            "control-plane chain snapshot must be read-only",
        )
    if path.stat().st_size != expected_size_bytes:
        raise StageEntrypointError(
            "evaluation_control_plane_chain_size_mismatch",
            "control-plane chain size differs from the immutable request",
        )
    if sha256_file(path) != expected_sha256:
        raise StageEntrypointError(
            "evaluation_control_plane_chain_hash_mismatch",
            "control-plane chain bytes differ from the immutable request",
        )
    payload = _json_object(path, "control-plane chain")
    _validate_control_plane_chain_payload(
        payload,
        job_id=job_id,
        stage_key=stage_key,
        stage_version=stage_version,
        attempt=attempt,
        release_manifest_sha256=release_manifest_sha256,
    )
    return ControlPlaneChainSnapshot(
        path=path,
        sha256=expected_sha256,
        size_bytes=expected_size_bytes,
        _canonical_json=json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def _validate_control_plane_chain_payload(
    payload: Mapping[str, Any],
    *,
    job_id: str,
    stage_key: str,
    stage_version: str,
    attempt: int,
    release_manifest_sha256: str,
) -> None:
    fields = {
        "schema_version",
        "job_id",
        "workflow_version",
        "stage_key",
        "stage_version",
        "stage_run_id",
        "stage_attempt",
        "release_manifest_sha256",
        "source_popo_manifest_sha256",
        "promotions",
    }
    if set(payload) != fields:
        raise StageEntrypointError(
            "evaluation_control_plane_chain_invalid",
            "control-plane chain has missing or unknown fields",
        )
    if (
        payload["schema_version"] != CONTROL_PLANE_CHAIN_PROTOCOL
        or payload["job_id"] != job_id
        or payload["stage_key"] != stage_key
        or payload["stage_version"] != stage_version
        or payload["stage_attempt"] != attempt
        or payload["release_manifest_sha256"] != release_manifest_sha256
        or stage_key != READY_FOR_USER_ACCEPTANCE_STAGE
    ):
        raise StageEntrypointError(
            "evaluation_control_plane_chain_binding_mismatch",
            "control-plane chain is not bound to this Stage 12 request",
        )
    _database_id(payload["stage_run_id"], "control_plane_chain.stage_run_id")
    _sha256(
        payload["source_popo_manifest_sha256"],
        "control_plane_chain.source_popo_manifest_sha256",
    )
    workflow_version = _text(
        payload["workflow_version"],
        "control_plane_chain.workflow_version",
    )
    try:
        contracts = contracts_for_version(workflow_version)
    except (KeyError, ValueError) as exc:
        raise StageEntrypointError(
            "evaluation_control_plane_chain_invalid",
            "control-plane chain uses an unregistered workflow version",
        ) from exc
    if (
        not contracts
        or contracts[-1].key != stage_key
        or contracts[-1].stage_version != stage_version
    ):
        raise StageEntrypointError(
            "evaluation_control_plane_chain_invalid",
            "control-plane chain does not terminate at the registered Stage 12",
        )
    rows = payload["promotions"]
    prior_contracts = contracts[:-1]
    if not isinstance(rows, list) or len(rows) != len(prior_contracts):
        raise StageEntrypointError(
            "evaluation_control_plane_chain_invalid",
            "control-plane chain must contain every prior stage exactly once",
        )

    previous_promotion_id: str | None = None
    previous_artifact_sha256 = payload["source_popo_manifest_sha256"]
    for index, (row, contract) in enumerate(zip(rows, prior_contracts)):
        _validate_control_plane_chain_row(
            row,
            contract=contract,
            index=index,
            expected_input_promotion_id=previous_promotion_id,
            expected_input_sha256=previous_artifact_sha256,
        )
        previous_promotion_id = row["promotion"]["promotion_id"]
        previous_artifact_sha256 = row["artifact_version"]["artifact_sha256"]


def _validate_control_plane_chain_row(
    row: Any,
    *,
    contract,
    index: int,
    expected_input_promotion_id: str | None,
    expected_input_sha256: str,
) -> None:
    fields = {
        "order",
        "stage_key",
        "stage_version",
        "stage_run_id",
        "stage_attempt",
        "stage_machine_status",
        "stage_spec_status",
        "input",
        "artifact_version",
        "evaluation",
        "promotion",
        "record_sha256",
    }
    if not isinstance(row, dict) or set(row) != fields:
        raise StageEntrypointError(
            "evaluation_control_plane_chain_invalid",
            f"control-plane chain row {index} is malformed",
        )
    _assert_record_sha256(row, f"control-plane chain row {index}")
    if (
        row["order"] != contract.order
        or row["stage_key"] != contract.key
        or row["stage_version"] != contract.stage_version
        or row["stage_machine_status"] != "succeeded"
        or row["stage_spec_status"] != "passed"
    ):
        raise StageEntrypointError(
            "evaluation_control_plane_chain_invalid",
            f"control-plane chain row {index} conflicts with the stage contract",
        )
    _database_id(row["stage_run_id"], f"control_plane_chain.promotions[{index}].stage_run_id")
    _positive_int(
        row["stage_attempt"],
        f"control_plane_chain.promotions[{index}].stage_attempt",
    )

    input_record = row["input"]
    if not isinstance(input_record, dict) or set(input_record) != {
        "kind",
        "promotion_id",
        "artifact_sha256",
    }:
        raise StageEntrypointError(
            "evaluation_control_plane_chain_invalid",
            f"control-plane chain row {index} has an invalid input binding",
        )
    expected_kind = (
        "frozen_source"
        if expected_input_promotion_id is None
        else "promoted_artifact"
    )
    if (
        input_record["kind"] != expected_kind
        or input_record["promotion_id"] != expected_input_promotion_id
        or input_record["artifact_sha256"] != expected_input_sha256
    ):
        raise StageEntrypointError(
            "evaluation_control_plane_chain_invalid",
            f"control-plane chain row {index} breaks the upstream promotion link",
        )
    _sha256(
        input_record["artifact_sha256"],
        f"control_plane_chain.promotions[{index}].input.artifact_sha256",
    )

    artifact = row["artifact_version"]
    artifact_fields = {
        "candidate_id",
        "kind",
        "bucket",
        "object",
        "object_identity_sha256",
        "artifact_sha256",
        "size_bytes",
        "immutable",
        "status",
        "record_sha256",
    }
    if not isinstance(artifact, dict) or set(artifact) != artifact_fields:
        raise StageEntrypointError(
            "evaluation_control_plane_chain_invalid",
            f"control-plane chain row {index} has an invalid artifact version",
        )
    _assert_record_sha256(artifact, f"control-plane chain artifact {index}")
    candidate_id = _database_id(
        artifact["candidate_id"],
        f"control_plane_chain.promotions[{index}].artifact_version.candidate_id",
    )
    artifact_sha256 = _sha256(
        artifact["artifact_sha256"],
        f"control_plane_chain.promotions[{index}].artifact_version.artifact_sha256",
    )
    object_identity = _sha256(
        artifact["object_identity_sha256"],
        (
            f"control_plane_chain.promotions[{index}]"
            ".artifact_version.object_identity_sha256"
        ),
    )
    kind = _text(
        artifact["kind"],
        f"control_plane_chain.promotions[{index}].artifact_version.kind",
    )
    bucket = _text(
        artifact["bucket"],
        f"control_plane_chain.promotions[{index}].artifact_version.bucket",
    )
    object_name = _text(
        artifact["object"],
        f"control_plane_chain.promotions[{index}].artifact_version.object",
    )
    _nonnegative_int(
        artifact["size_bytes"],
        f"control_plane_chain.promotions[{index}].artifact_version.size_bytes",
    )
    if artifact["immutable"] is not True or artifact["status"] != "promoted":
        raise StageEntrypointError(
            "evaluation_control_plane_chain_invalid",
            f"control-plane chain artifact {index} is not an immutable promotion",
        )
    expected_object_identity = hashlib.sha256(
        f"{bucket}\n{object_name}\n{artifact_sha256}".encode("utf-8")
    ).hexdigest()
    if object_identity != expected_object_identity or not kind:
        raise StageEntrypointError(
            "evaluation_control_plane_chain_invalid",
            f"control-plane chain artifact {index} identity drifted",
        )

    evaluation = row["evaluation"]
    evaluation_fields = {
        "evaluation_id",
        "candidate_id",
        "decision",
        "spec_passed",
        "policy_sha256",
        "evaluator_identity",
        "evaluator_version",
        "gate_results",
        "findings",
        "record_sha256",
    }
    if not isinstance(evaluation, dict) or set(evaluation) != evaluation_fields:
        raise StageEntrypointError(
            "evaluation_control_plane_chain_invalid",
            f"control-plane chain row {index} has an invalid evaluation",
        )
    _assert_record_sha256(evaluation, f"control-plane chain evaluation {index}")
    evaluation_id = _database_id(
        evaluation["evaluation_id"],
        f"control_plane_chain.promotions[{index}].evaluation.evaluation_id",
    )
    if (
        evaluation["candidate_id"] != candidate_id
        or evaluation["decision"] != "passed"
        or evaluation["spec_passed"] is not True
        or not isinstance(evaluation["gate_results"], dict)
        or set(evaluation["gate_results"]) != set(contract.acceptance_gates)
        or any(value is not True for value in evaluation["gate_results"].values())
        or not isinstance(evaluation["findings"], list)
        or any(not isinstance(value, dict) for value in evaluation["findings"])
    ):
        raise StageEntrypointError(
            "evaluation_control_plane_chain_invalid",
            f"control-plane chain evaluation {index} did not pass its exact gates",
        )
    _sha256(
        evaluation["policy_sha256"],
        f"control_plane_chain.promotions[{index}].evaluation.policy_sha256",
    )
    _text(
        evaluation["evaluator_identity"],
        f"control_plane_chain.promotions[{index}].evaluation.evaluator_identity",
    )
    _text(
        evaluation["evaluator_version"],
        f"control_plane_chain.promotions[{index}].evaluation.evaluator_version",
    )

    promotion = row["promotion"]
    promotion_fields = {
        "promotion_id",
        "candidate_id",
        "evaluation_id",
        "artifact_sha256",
        "promoted_by",
        "record_sha256",
    }
    if not isinstance(promotion, dict) or set(promotion) != promotion_fields:
        raise StageEntrypointError(
            "evaluation_control_plane_chain_invalid",
            f"control-plane chain row {index} has an invalid promotion",
        )
    _assert_record_sha256(promotion, f"control-plane chain promotion {index}")
    _database_id(
        promotion["promotion_id"],
        f"control_plane_chain.promotions[{index}].promotion.promotion_id",
    )
    if (
        promotion["candidate_id"] != candidate_id
        or promotion["evaluation_id"] != evaluation_id
        or promotion["artifact_sha256"] != artifact_sha256
    ):
        raise StageEntrypointError(
            "evaluation_control_plane_chain_invalid",
            f"control-plane chain promotion {index} breaks record lineage",
        )
    _text(
        promotion["promoted_by"],
        f"control_plane_chain.promotions[{index}].promotion.promoted_by",
    )


def _assert_record_sha256(record: Mapping[str, Any], label: str) -> None:
    claimed = _sha256(record.get("record_sha256"), f"{label}.record_sha256")
    payload = dict(record)
    payload.pop("record_sha256")
    if claimed != _canonical_json_sha256(payload):
        raise StageEntrypointError(
            "evaluation_control_plane_chain_record_hash_mismatch",
            f"{label} record hash does not match its content",
        )


def _canonical_json_sha256(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _database_id(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value.isdigit()
        or value.startswith("0")
    ):
        raise StageEntrypointError(
            "evaluation_control_plane_chain_invalid",
            f"{field} must be a positive database identifier",
        )
    return value


def _contained_file(root: Path, raw: str | os.PathLike[str], label: str) -> Path:
    relative = _relative(os.fspath(raw), label)
    current = root.resolve()
    for part in PurePosixPath(relative).parts:
        current /= part
        if current.is_symlink():
            raise StageEntrypointError(
                "evaluation_path_invalid",
                f"{label} cannot be a symlink",
            )
    path = (root / relative).resolve()
    if root.resolve() not in path.parents or not path.is_file():
        raise StageEntrypointError(
            "evaluation_path_invalid",
            f"{label} is not a contained regular file",
        )
    return path


def _output_path(raw: str | os.PathLike[str]) -> Path:
    root = Path.cwd().resolve()
    relative = _relative(os.fspath(raw), "result")
    path = (root / relative).resolve()
    if root not in path.parents or path.exists() or path.is_symlink():
        raise StageEntrypointError(
            "evaluation_result_path_invalid",
            "evaluation result path must be a new contained file",
        )
    return path


def _relative(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value:
        raise StageEntrypointError(
            "evaluation_path_invalid",
            f"{field} must be a relative POSIX path",
        )
    path = PurePosixPath(value)
    if str(path) != value or any(part in {"", ".", ".."} for part in path.parts):
        raise StageEntrypointError(
            "evaluation_path_invalid",
            f"{field} must be normalized",
        )
    return value


def _json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StageEntrypointError(
            "evaluation_json_invalid",
            f"{label} is not valid UTF-8 JSON",
        ) from exc
    if not isinstance(value, dict):
        raise StageEntrypointError(
            "evaluation_json_invalid",
            f"{label} must be a JSON object",
        )
    return value


def _sha256(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise StageEntrypointError(
            "evaluation_sha256_invalid",
            f"{field} must be a lowercase SHA-256",
        )
    return value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StageEntrypointError(
            "evaluation_text_invalid",
            f"{field} must be non-empty text",
        )
    return value


def _identifier(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value[0].isalpha()
        and all(character.isalnum() or character == "_" for character in value)
    )


def _positive_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise StageEntrypointError(
            "evaluation_integer_invalid",
            f"{field} must be a positive integer",
        )
    return value


def _nonnegative_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise StageEntrypointError(
            "evaluation_integer_invalid",
            f"{field} must be a non-negative integer",
        )
    return value


def _make_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    root.chmod(0o555)


def _write_failure(
    path: Path,
    stage_key: str,
    request: StageEvaluationRequest | None,
    error: StageEntrypointError,
) -> None:
    try:
        payload = {
            "schema_version": EVALUATION_PROTOCOL,
            "job_id": request.job_id if request else None,
            "stage_key": stage_key,
            "attempt": request.attempt if request else None,
            "candidate_sha256": request.candidate.sha256 if request else None,
            "release_manifest_sha256": (
                request.release_manifest_sha256 if request else None
            ),
            "policy_sha256": request.policy_sha256 if request else None,
            "decision": "failed",
            "gate_results": (
                {gate: False for gate in request.required_gates}
                if request
                else {}
            ),
            "findings": [
                {
                    "code": error.code,
                    "message": str(error),
                    "blocking": True,
                }
            ],
        }
        write_json(path, payload)
    except (OSError, StageEntrypointError):
        pass


__all__ = [
    "CONTROL_PLANE_CHAIN_PATH",
    "CONTROL_PLANE_CHAIN_PROTOCOL",
    "EVALUATION_PROTOCOL",
    "EVALUATION_REQUEST_PROTOCOL",
    "ControlPlaneChainSnapshot",
    "EvaluationInput",
    "StageEvaluation",
    "StageEvaluationRequest",
    "run_stage_evaluation_entrypoint",
]
