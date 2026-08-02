from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Protocol, Sequence

from sqlalchemy.orm import Session

from app.workflow_v3.models import (
    WorkflowV3Candidate,
    WorkflowV3Evaluation,
    WorkflowV3Execution,
    WorkflowV3Job,
    WorkflowV3ModelCall,
    WorkflowV3Promotion,
    WorkflowV3ReviewResolution,
    WorkflowV3SkillRelease,
    WorkflowV3StageRun,
)
from app.workflow_v3.llm_gateway import (
    LlmCallResult,
    LlmGatewayError,
    ReleaseBoundLlmCall,
    canonical_json_bytes,
    execute_bounded_call,
    sha256_json,
)
from app.workflow_v3.llm_transport import transport_from_runtime_config
from app.workflow_v3.release import (
    MANIFEST_NAME,
    ReleaseValidationError,
    ReleaseVerification,
    admit_entrypoint,
    require_qualification_environment,
    verify_release_directory,
)
from app.workflow_v3.service import runtime_identity_for_manifest
from app.workflow_v3.spec01_03_atomic_kernel import (
    media_model_evidence,
    outline_model_evidence,
    scope_model_evidence,
    semantic_model_evidence,
)
from app.workflow_v3.stage_entrypoint import (
    _safe_extract_candidate_bundle,
    run_release_python_kernel,
)
from app.workflow_v3.state_machine import (
    WorkflowV3TransitionError,
    claim_current_stage,
    fail_execution,
    retry_failed_stage,
    submit_candidate,
    touch_execution_heartbeat,
)
from app.workflow_v3.telemetry import finish_model_call, start_model_call
from app.workflow_v3.visual_review import build_full_page_review_inputs


CANDIDATE_PROTOCOL = "luceon.worker-v3-stage-candidate/v1"
REQUEST_PROTOCOL = "luceon.worker-v3-stage-request/v1"
_PRODUCER_SUCCESS = "candidate_ready"
_SHA256_CHARS = frozenset("0123456789abcdef")
_MAX_RETRYABLE_LLM_STAGE_ATTEMPTS = 3


class WorkerV3RuntimeError(RuntimeError):
    code = "worker_v3_runtime_error"


class ReleaseBindingError(WorkerV3RuntimeError):
    code = "release_binding_invalid"


class ArtifactIntegrityError(WorkerV3RuntimeError):
    code = "artifact_integrity_failed"


class EntrypointProtocolError(WorkerV3RuntimeError):
    code = "entrypoint_protocol_invalid"


class ExternalCommandFailed(WorkerV3RuntimeError):
    code = "entrypoint_nonzero_exit"


class ExternalCommandTimeout(WorkerV3RuntimeError):
    code = "entrypoint_timeout"


class ExternalCommandCancelled(WorkerV3RuntimeError):
    code = "execution_cancelled"


class ModelCallHeartbeatFailed(WorkerV3RuntimeError):
    code = "model_call_heartbeat_failed"


@dataclass(frozen=True)
class ArtifactRef:
    bucket: str
    object_name: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float


@dataclass(frozen=True)
class BoundRelease:
    verification: ReleaseVerification
    manifest_sha256: str
    runtime_identity_sha256: str


class RuntimeBindingGuardProtocol(Protocol):
    @property
    def runtime_identity_sha256(self) -> str:
        ...

    def assert_bound(
        self,
        release_root: str | os.PathLike[str],
        *,
        job: WorkflowV3Job,
        release: WorkflowV3SkillRelease,
        qualification: bool = False,
    ) -> BoundRelease:
        ...


@dataclass(frozen=True)
class StageInvocation:
    entrypoint_id: str
    definition: Mapping[str, object]
    argv: tuple[str, ...]
    timeout_seconds: int


@dataclass(frozen=True)
class PreparedInputArtifact:
    role: str
    kind: str
    ref: ArtifactRef
    path: str

    def request_value(self) -> dict[str, object]:
        return {
            "role": self.role,
            "kind": self.kind,
            "sha256": self.ref.sha256,
            "size_bytes": self.ref.size_bytes,
            "path": self.path,
            "read_only": True,
        }


@dataclass(frozen=True)
class PreparedStageRequest:
    primary: PreparedInputArtifact
    artifacts: tuple[PreparedInputArtifact, ...]
    predecessor_promotion: Mapping[str, object] | None
    parameters: Mapping[str, object]


class ArtifactStore(Protocol):
    """Candidate-only artifact interface.

    A production MinIO adapter may implement this protocol in a separate
    integration layer.  The runtime itself deliberately receives no MinIO
    client and has no delete or promotion method.
    """

    def materialize(self, artifact: ArtifactRef, destination: Path) -> ArtifactRef:
        ...

    def put_candidate(
        self,
        source: Path,
        *,
        bucket: str,
        object_name: str,
        expected_sha256: str,
    ) -> ArtifactRef:
        ...

    def stat(self, artifact: ArtifactRef) -> ArtifactRef:
        ...


class ReleaseResolver(Protocol):
    def resolve(self, release: WorkflowV3SkillRelease) -> Path:
        ...


class CommandTransport(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: int,
        heartbeat: Callable[[], None],
        cancelled: Callable[[], bool],
    ) -> CommandResult:
        ...


class DirectoryReleaseResolver:
    """Resolve an installed release from a read-only release root."""

    def __init__(self, releases_root: str | os.PathLike[str]):
        self.root = Path(releases_root).resolve()

    def resolve(self, release: WorkflowV3SkillRelease) -> Path:
        direct = self.root
        if (direct / MANIFEST_NAME).is_file():
            return direct
        resolved = (direct / release.release_version).resolve()
        if self.root not in resolved.parents:
            raise ReleaseBindingError("release version escapes the installed release root")
        return resolved


class DirectoryArtifactStore:
    """Filesystem fixture adapter with immutable candidate writes.

    This is useful for protocol tests and local smoke runs.  It is intentionally
    not a substitute for the production MinIO adapter.
    """

    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def materialize(self, artifact: ArtifactRef, destination: Path) -> ArtifactRef:
        source = self._object_path(artifact.bucket, artifact.object_name)
        actual = _artifact_ref_for_file(source, artifact.bucket, artifact.object_name)
        _assert_artifact_identity(artifact, actual)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            existing = _artifact_ref_for_file(destination, artifact.bucket, artifact.object_name)
            _assert_artifact_identity(artifact, existing)
            return existing
        temporary = destination.with_name(f".{destination.name}.partial")
        temporary.unlink(missing_ok=True)
        shutil.copyfile(source, temporary)
        copied = _artifact_ref_for_file(temporary, artifact.bucket, artifact.object_name)
        _assert_artifact_identity(artifact, copied)
        temporary.replace(destination)
        destination.chmod(0o444)
        return copied

    def put_candidate(
        self,
        source: Path,
        *,
        bucket: str,
        object_name: str,
        expected_sha256: str,
    ) -> ArtifactRef:
        expected_sha256 = _require_sha256(expected_sha256, "candidate SHA-256")
        source_ref = _artifact_ref_for_file(source, bucket, object_name)
        if source_ref.sha256 != expected_sha256:
            raise ArtifactIntegrityError("candidate bytes do not match their declared SHA-256")
        target = self._object_path(bucket, object_name)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            current = _artifact_ref_for_file(target, bucket, object_name)
            _assert_artifact_identity(source_ref, current)
            return current
        temporary = target.with_name(f".{target.name}.partial-{os.getpid()}")
        temporary.unlink(missing_ok=True)
        shutil.copyfile(source, temporary)
        copied = _artifact_ref_for_file(temporary, bucket, object_name)
        _assert_artifact_identity(source_ref, copied)
        try:
            os.link(temporary, target)
        except FileExistsError:
            current = _artifact_ref_for_file(target, bucket, object_name)
            _assert_artifact_identity(source_ref, current)
        finally:
            temporary.unlink(missing_ok=True)
        target.chmod(0o444)
        return _artifact_ref_for_file(target, bucket, object_name)

    def stat(self, artifact: ArtifactRef) -> ArtifactRef:
        current = _artifact_ref_for_file(
            self._object_path(artifact.bucket, artifact.object_name),
            artifact.bucket,
            artifact.object_name,
        )
        _assert_artifact_identity(artifact, current)
        return current

    def seed(self, source: Path, *, bucket: str, object_name: str) -> ArtifactRef:
        """Test/local helper for placing a frozen upstream object."""
        digest = _sha256_file(source)
        return self.put_candidate(
            source,
            bucket=bucket,
            object_name=object_name,
            expected_sha256=digest,
        )

    def _object_path(self, bucket: str, object_name: str) -> Path:
        bucket_path = _safe_relative(bucket, "bucket")
        object_path = _safe_relative(object_name, "object name")
        path = (self.root / bucket_path / object_path).resolve()
        if self.root not in path.parents:
            raise ArtifactIntegrityError("artifact path escapes the configured store root")
        return path


class SubprocessTransport:
    """Run a release-declared argv without a shell or inherited secrets."""

    def __init__(self, *, poll_seconds: float = 0.1, heartbeat_seconds: float = 5.0):
        self.poll_seconds = max(0.01, poll_seconds)
        self.heartbeat_seconds = max(self.poll_seconds, heartbeat_seconds)

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: int,
        heartbeat: Callable[[], None],
        cancelled: Callable[[], bool],
    ) -> CommandResult:
        if not argv or not Path(argv[0]).is_absolute():
            raise EntrypointProtocolError("release command must have an absolute admitted executable")
        stdout_path = cwd / ".command.stdout.log"
        stderr_path = cwd / ".command.stderr.log"
        started = time.monotonic()
        sanitized_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
            "LUCEON_WORKER_V3_REQUEST": str(cwd / "request.json"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        with stdout_path.open("wb") as stdout_handle, stderr_path.open("wb") as stderr_handle:
            process = subprocess.Popen(
                list(argv),
                cwd=str(cwd),
                env=sanitized_env,
                stdin=subprocess.DEVNULL,
                stdout=stdout_handle,
                stderr=stderr_handle,
                shell=False,
                start_new_session=True,
            )
            next_heartbeat = started
            while process.poll() is None:
                now = time.monotonic()
                if cancelled():
                    _terminate_process(process)
                    raise ExternalCommandCancelled("control plane cancelled the running stage")
                if now - started >= timeout_seconds:
                    _terminate_process(process)
                    raise ExternalCommandTimeout(
                        f"formal entrypoint exceeded {timeout_seconds} seconds"
                    )
                if now >= next_heartbeat:
                    heartbeat()
                    next_heartbeat = now + self.heartbeat_seconds
                time.sleep(self.poll_seconds)
            heartbeat()
        duration = max(0.0, time.monotonic() - started)
        return CommandResult(
            returncode=int(process.returncode or 0),
            stdout=_tail_text(stdout_path),
            stderr=_tail_text(stderr_path),
            duration_seconds=duration,
        )


class WorkflowV3Executor:
    """Execute exactly one queued stage and submit only an immutable candidate."""

    def __init__(
        self,
        *,
        session_factory,
        release_resolver: ReleaseResolver,
        artifact_store: ArtifactStore,
        work_root: str | os.PathLike[str],
        producer_identity: str,
        candidate_bucket: str = "worker-v3-candidates",
        candidate_prefix: str = "v3/candidates",
        transport: CommandTransport | None = None,
        model_transport: Callable[[Mapping[str, Any], float], object] | None = None,
        qualification_mode: bool = False,
        operational_heartbeat: Callable[[str, str, str], None] | None = None,
        runtime_guard: RuntimeBindingGuardProtocol | None = None,
    ):
        if not producer_identity:
            raise ValueError("producer_identity is required")
        self.session_factory = session_factory
        self.release_resolver = release_resolver
        self.artifact_store = artifact_store
        self.work_root = Path(work_root).resolve()
        self.producer_identity = producer_identity
        self.candidate_bucket = candidate_bucket
        self.candidate_prefix = normalize_candidate_prefix(candidate_prefix)
        self.transport = transport or SubprocessTransport()
        if qualification_mode:
            require_qualification_environment()
        self.model_transport = model_transport
        self.qualification_mode = qualification_mode
        self.operational_heartbeat = operational_heartbeat
        self.runtime_guard = runtime_guard

    def run_one_stage(self, public_id: str) -> dict:
        db: Session = self.session_factory()
        execution_id: int | None = None
        try:
            job = db.query(WorkflowV3Job).filter(WorkflowV3Job.public_id == public_id).one()
            release = (
                db.query(WorkflowV3SkillRelease)
                .filter(WorkflowV3SkillRelease.id == job.skill_release_id)
                .one()
            )
            stage = (
                db.query(WorkflowV3StageRun)
                .filter(
                    WorkflowV3StageRun.workflow_job_id == job.id,
                    WorkflowV3StageRun.stage_key == job.current_stage_key,
                )
                .order_by(WorkflowV3StageRun.attempt.desc())
                .first()
            )
            if not stage:
                raise WorkerV3RuntimeError("current stage is missing")
            if self.runtime_guard is not None:
                self.runtime_guard.assert_bound(
                    self.release_resolver.resolve(release),
                    job=job,
                    release=release,
                    qualification=self.qualification_mode,
                )
                runtime_identity_sha256 = (
                    self.runtime_guard.runtime_identity_sha256
                )
            else:
                runtime_identity_sha256 = release.runtime_identity_sha256
            execution_key = _idempotency_key(
                "execute",
                job.public_id,
                str(stage.id),
                str(stage.attempt),
                job.skill_release_sha256,
                self.producer_identity,
            )
            job, stage, execution = claim_current_stage(
                db,
                public_id,
                producer_identity=self.producer_identity,
                idempotency_key=execution_key,
                runtime_identity_sha256=runtime_identity_sha256,
                qualification=self.qualification_mode,
            )
            execution_id = execution.id
            stage_context = {
                "id": stage.id,
                "key": stage.stage_key,
                "version": stage.stage_version,
                "attempt": stage.attempt,
                "input_kind": stage.input_kind,
            }
            if execution.machine_status != "running":
                db.commit()
                return {
                    "ok": execution.machine_status == "succeeded",
                    "job_id": public_id,
                    "stage": stage.stage_key,
                    "attempt": stage.attempt,
                    "execution_id": str(execution.id),
                    "status": execution.machine_status,
                    "idempotent": True,
                }
            db.commit()
        except Exception:
            db.rollback()
            db.close()
            raise
        finally:
            if db.is_active:
                db.close()

        try:
            release, bound, invocation = self._verify_and_admit(public_id, execution_id)
            workdir = self._create_attempt_workspace(
                public_id, stage_context["key"], stage_context["attempt"]
            )
            if workdir is None:
                return {
                    "ok": True,
                    "job_id": public_id,
                    "stage": stage_context["key"],
                    "attempt": stage_context["attempt"],
                    "execution_id": str(execution_id),
                    "status": "already_running",
                    "idempotent": True,
                }
            prepared = self._prepare_stage_request(
                public_id=public_id,
                stage_id=int(stage_context["id"]),
                release_root=bound.verification.root,
                bound=bound,
                workdir=workdir,
            )
            primary = prepared.primary
            request = {
                "schema_version": REQUEST_PROTOCOL,
                "mode": "produce",
                "job_id": public_id,
                "stage_key": stage_context["key"],
                "stage_version": stage_context["version"],
                "attempt": stage_context["attempt"],
                "input": {
                    "kind": primary.kind,
                    "sha256": primary.ref.sha256,
                    "size_bytes": primary.ref.size_bytes,
                    "path": primary.path,
                },
                "input_artifacts": [
                    artifact.request_value() for artifact in prepared.artifacts
                ],
                "predecessor_promotion": prepared.predecessor_promotion,
                "release": {
                    "release_id": bound.verification.release_id,
                    "version": release.release_version,
                    "manifest_sha256": bound.manifest_sha256,
                    "tree_sha256": bound.verification.tree_sha256,
                    "runtime_identity_sha256": bound.runtime_identity_sha256,
                },
                "parameters": dict(prepared.parameters),
                "output_manifest": "candidate-manifest.json",
            }
            _write_json(workdir / "request.json", request)
            result = self.transport.run(
                invocation.argv,
                cwd=workdir,
                timeout_seconds=invocation.timeout_seconds,
                heartbeat=lambda: self._heartbeat(
                    public_id,
                    execution_id,
                    stage_key=str(stage_context["key"]),
                    runtime_identity_sha256=bound.runtime_identity_sha256,
                ),
                cancelled=lambda: self._cancelled(public_id),
            )
            if result.returncode != 0:
                raise ExternalCommandFailed(
                    f"formal entrypoint exited {result.returncode}: {result.stderr[-1000:]}"
                )
            manifest = _load_candidate_manifest(
                workdir / "candidate-manifest.json",
                workdir=workdir,
                public_id=public_id,
                stage_key=stage_context["key"],
                attempt=stage_context["attempt"],
                input_sha256=primary.ref.sha256,
                release_manifest_sha256=bound.manifest_sha256,
            )
            artifact_path = workdir / manifest["artifact"]["path"]
            object_name = (
                f"{self.candidate_prefix}/{public_id}/{stage_context['key']}/"
                f"attempt-{stage_context['attempt']}/"
                f"{manifest['artifact']['sha256']}/artifact"
            )
            candidate_ref = self.artifact_store.put_candidate(
                artifact_path,
                bucket=self.candidate_bucket,
                object_name=object_name,
                expected_sha256=manifest["artifact"]["sha256"],
            )
            verified_candidate = self.artifact_store.stat(candidate_ref)
            _assert_artifact_identity(candidate_ref, verified_candidate)
            db = self.session_factory()
            try:
                job, stage, candidate = submit_candidate(
                    db,
                    public_id,
                    execution_id=execution_id,
                    idempotency_key=_idempotency_key(
                        "candidate",
                        public_id,
                        str(stage_context["id"]),
                        str(stage_context["attempt"]),
                        candidate_ref.sha256,
                    ),
                    artifact_kind=manifest["artifact"]["kind"],
                    bucket=candidate_ref.bucket,
                    object_name=candidate_ref.object_name,
                    sha256=candidate_ref.sha256,
                    size_bytes=candidate_ref.size_bytes,
                    metadata={
                        "entrypoint_id": invocation.entrypoint_id,
                        "release_id": bound.verification.release_id,
                        "release_tree_sha256": bound.verification.tree_sha256,
                        "input_sha256": primary.ref.sha256,
                        "input_artifacts": [
                            {
                                "role": item.role,
                                "kind": item.kind,
                                "sha256": item.ref.sha256,
                                "size_bytes": item.ref.size_bytes,
                            }
                            for item in prepared.artifacts
                        ],
                        "metrics": manifest["metrics"],
                        "command": {
                            "returncode": result.returncode,
                            "duration_seconds": result.duration_seconds,
                            "stdout_tail": result.stdout,
                            "stderr_tail": result.stderr,
                        },
                    },
                )
                db.commit()
                return {
                    "ok": True,
                    "job_id": public_id,
                    "stage": stage.stage_key,
                    "attempt": stage.attempt,
                    "execution_id": str(execution_id),
                    "candidate_id": str(candidate.id),
                    "candidate_sha256": candidate.sha256,
                    "status": stage.machine_status,
                    "workdir": str(workdir),
                }
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()
        except ExternalCommandCancelled:
            return {
                "ok": False,
                "job_id": public_id,
                "execution_id": str(execution_id),
                "status": "cancelled",
                "error_code": ExternalCommandCancelled.code,
            }
        except Exception as exc:
            status = self._record_failure(public_id, execution_id, exc)
            return {
                "ok": False,
                "job_id": public_id,
                "execution_id": str(execution_id),
                "status": status,
                "retry_queued": status == "retrying",
                "error_code": getattr(exc, "code", "worker_v3_runtime_error"),
                "error": str(exc)[:2000],
            }

    def _prepare_stage_request(
        self,
        *,
        public_id: str,
        stage_id: int,
        release_root: Path,
        bound: BoundRelease,
        workdir: Path,
    ) -> PreparedStageRequest:
        db = self.session_factory()
        try:
            job = (
                db.query(WorkflowV3Job)
                .filter(WorkflowV3Job.public_id == public_id)
                .one()
            )
            stage = (
                db.query(WorkflowV3StageRun)
                .filter(
                    WorkflowV3StageRun.id == stage_id,
                    WorkflowV3StageRun.workflow_job_id == job.id,
                )
                .one()
            )
            release = (
                db.query(WorkflowV3SkillRelease)
                .filter(WorkflowV3SkillRelease.id == job.skill_release_id)
                .one()
            )
            return _StageRequestBuilder(
                db=db,
                session_factory=self.session_factory,
                artifact_store=self.artifact_store,
                job=job,
                stage=stage,
                release=release,
                release_root=release_root,
                bound=bound,
                workdir=workdir,
                model_transport=self.model_transport,
                qualification_mode=self.qualification_mode,
                heartbeat=lambda: self._heartbeat(
                    public_id,
                    int(
                        db.query(WorkflowV3Execution.id)
                        .filter(WorkflowV3Execution.stage_run_id == stage.id)
                        .scalar()
                    ),
                    stage_key=stage.stage_key,
                    runtime_identity_sha256=bound.runtime_identity_sha256,
                ),
            ).build()
        finally:
            db.close()

    def _verify_and_admit(
        self, public_id: str, execution_id: int
    ) -> tuple[WorkflowV3SkillRelease, BoundRelease, StageInvocation]:
        db = self.session_factory()
        try:
            job = db.query(WorkflowV3Job).filter(WorkflowV3Job.public_id == public_id).one()
            execution = (
                db.query(WorkflowV3Execution)
                .filter(
                    WorkflowV3Execution.id == execution_id,
                    WorkflowV3Execution.workflow_job_id == job.id,
                    WorkflowV3Execution.machine_status == "running",
                )
                .one_or_none()
            )
            stage = (
                db.query(WorkflowV3StageRun)
                .filter(WorkflowV3StageRun.id == execution.stage_run_id)
                .one_or_none()
                if execution
                else None
            )
            if not stage:
                raise WorkflowV3RuntimeLookupError("active stage is missing")
            release = (
                db.query(WorkflowV3SkillRelease)
                .filter(WorkflowV3SkillRelease.id == job.skill_release_id)
                .one()
            )
            if self.runtime_guard is not None:
                bound = self.runtime_guard.assert_bound(
                    self.release_resolver.resolve(release),
                    job=job,
                    release=release,
                    qualification=self.qualification_mode,
                )
            else:
                bound = verify_bound_release(
                    self.release_resolver.resolve(release),
                    job=job,
                    release=release,
                    qualification=self.qualification_mode,
                )
            invocation = select_formal_invocation(
                bound.verification,
                stage_key=stage.stage_key,
                execution_role="producer",
                success_semantic=_PRODUCER_SUCCESS,
                permission_envelope="candidate-only",
                qualification=self.qualification_mode,
            )
            return release, bound, invocation
        finally:
            db.close()

    def _create_attempt_workspace(self, public_id: str, stage_key: str, attempt: int) -> Path | None:
        workdir = self.work_root / public_id / stage_key / f"attempt-{attempt}"
        workdir.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            workdir.mkdir(mode=0o700)
        except FileExistsError:
            return None
        return workdir

    def _heartbeat(
        self,
        public_id: str,
        execution_id: int,
        *,
        stage_key: str = "",
        runtime_identity_sha256: str = "",
    ) -> None:
        db = self.session_factory()
        try:
            if not touch_execution_heartbeat(
                db,
                public_id,
                execution_id=execution_id,
                producer_identity=self.producer_identity,
            ):
                raise ExternalCommandCancelled("execution lease is no longer active")
            db.commit()
            if self.operational_heartbeat is not None:
                self.operational_heartbeat(
                    public_id,
                    stage_key,
                    runtime_identity_sha256,
                )
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _cancelled(self, public_id: str) -> bool:
        db = self.session_factory()
        try:
            status = (
                db.query(WorkflowV3Job.machine_status)
                .filter(WorkflowV3Job.public_id == public_id)
                .scalar()
            )
            return status == "cancelled"
        finally:
            db.close()

    def _record_failure(self, public_id: str, execution_id: int, exc: Exception) -> str:
        db = self.session_factory()
        try:
            _, stage, _ = fail_execution(
                db,
                public_id,
                execution_id=execution_id,
                error_code=getattr(exc, "code", "worker_v3_runtime_error"),
                error_message=str(exc)[:4000],
            )
            retryable_llm_failure = (
                isinstance(exc, LlmGatewayError)
                and exc.retryable
                and stage.attempt < _MAX_RETRYABLE_LLM_STAGE_ATTEMPTS
            )
            if retryable_llm_failure:
                retry_failed_stage(db, public_id)
            db.commit()
            return "retrying" if retryable_llm_failure else "failed"
        except WorkflowV3TransitionError:
            # Cancellation or a concurrent terminal transition wins.  A late
            # command result must never overwrite it.
            db.rollback()
            return "cancelled"
        finally:
            db.close()


_STAGE_PROMOTION_KIND = {
    "canonical_block_ledger": "spec03_media_contract",
    "outline_reconstruction": "spec04a_structure_contract",
    "semantic_annotation": "spec04b_semantic_span_contract",
    "template_construct_binding": "spec04c_construct_binding_contract",
    "frozen_render_plan": "spec04d_render_plan_contract",
}


def _control_plane_promotion_class(stage_key: str) -> str:
    return "formal_native" if stage_key in _STAGE_PROMOTION_KIND else "standard"


_PROMOTED_ROLE_ALIASES = {
    "canonical_ledger": "ledger_L",
    "decision_index": "decision_index_D",
    "media_evidence_ledger": "media_evidence_ledger",
    "media_representation_plan": "media_representation_plan",
    "source_outline_ledger": "source_outline_ledger",
    "final_toc_plan": "final_toc_plan",
    "semantic_span_ledger": "semantic_span_ledger",
    "teaching_group_ledger": "teaching_column_group_ledger",
    "construct_binding_ledger": "construct_binding_ledger",
    "template_capability_manifest": "template_capability_manifest",
    "frozen_render_plan": "render_plan",
    "volume_partition_plan": "volume_partition_plan",
}
_BOUNDED_REVIEW = {
    "source_scope_and_order": (
        "scope_order_review_bundle",
        "worker-v3.spec02-scope-order-review",
    ),
    "canonical_block_ledger": (
        "media_review_bundle",
        "worker-v3.spec03-media-review",
    ),
    "outline_reconstruction": (
        "outline_review_decision",
        "worker-v3.spec04a-outline-review",
    ),
    "semantic_annotation": (
        "semantic_review_decision",
        "worker-v3.spec04b-semantic-review",
    ),
    "template_construct_binding": (
        "construct_review_decision",
        "worker-v3.spec04c-construct-review",
    ),
    "frozen_render_plan": (
        "render_policy_decision",
        "worker-v3.spec04d-render-policy",
    ),
}


class _StageRequestBuilder:
    """Materialize the release-bound, read-only input set for one Producer.

    This builder is deliberately code-owned.  Neither page payloads nor model
    responses may select commands, release files, or object-store locations.
    """

    def __init__(
        self,
        *,
        db: Session,
        session_factory,
        artifact_store: ArtifactStore,
        job: WorkflowV3Job,
        stage: WorkflowV3StageRun,
        release: WorkflowV3SkillRelease,
        release_root: Path,
        bound: BoundRelease,
        workdir: Path,
        heartbeat: Callable[[], None],
        model_transport: Callable[[Mapping[str, Any], float], object] | None = None,
        qualification_mode: bool = False,
    ) -> None:
        self.db = db
        self.session_factory = session_factory
        self.store = artifact_store
        self.job = job
        self.stage = stage
        self.release = release
        self.release_root = release_root
        self.bound = bound
        self.workdir = workdir
        self.model_transport = model_transport
        self.qualification_mode = qualification_mode
        self.heartbeat = heartbeat
        self.artifacts: list[PreparedInputArtifact] = []
        self.extracted: dict[str, Path] = {}
        self.promotion_manifests: dict[str, PreparedInputArtifact] = {}
        self.promotion_rows: dict[str, tuple[WorkflowV3Promotion, WorkflowV3Evaluation]] = {}

    def build(self) -> PreparedStageRequest:
        recovery_lineage = self._add_review_resolution()
        if self.stage.stage_key == "intake_snapshot":
            return self._build_intake(recovery_lineage=recovery_lineage)
        primary, predecessor = self._add_promotion(
            "promoted_predecessor",
            promotion_id=int(self.stage.input_promotion_id or 0),
            manifest_role="predecessor_promotion_manifest",
        )
        if self._legacy_fixture_release():
            return self._prepared_request(
                primary=primary,
                predecessor=predecessor,
                parameters={},
                recovery_lineage=recovery_lineage,
            )

        source = self._source_evidence()
        key = self.stage.stage_key
        parameters: dict[str, object] = {}
        if key in {"source_scope_and_order", "canonical_block_ledger"}:
            for role in ("source_pdf", "mineru_archive", "popo_archive"):
                self._add_source(role, source)
            self._add_template_archive()
            identity = self._identity_parameters()
            parameters.update(
                {
                    name: identity[name]
                    for name in (
                        "run_id",
                        "decision_snapshot_id",
                        "stage_decision_id",
                    )
                }
            )
            if key == "canonical_block_ledger":
                parameters.update(
                    {
                        "ledger_id": f"{self.job.public_id}-ledger",
                        "ledger_snapshot_id": identity["ledger_snapshot_id"],
                        "ledger_version": identity["ledger_version"],
                    }
                )
        elif key in {
            "outline_reconstruction",
            "semantic_annotation",
            "template_construct_binding",
            "frozen_render_plan",
            "deterministic_elegantbook",
        }:
            self._add_source("source_pdf", source)

        if key in _BOUNDED_REVIEW:
            if key in {
                "outline_reconstruction",
                "semantic_annotation",
                "template_construct_binding",
                "frozen_render_plan",
            }:
                self._add_promotion_registry()
                parameters.update(self._identity_parameters())
            if key == "template_construct_binding":
                self._add_template_archive()
                self._add_prior_candidate_file(
                    "intake_snapshot",
                    "template_intake",
                    "contracts/template_intake.json",
                    "worker-v3-template-intake",
                )
            if key == "frozen_render_plan":
                structure, structure_binding = self._add_stage_candidate(
                    "outline_reconstruction",
                    role="structure_candidate",
                    manifest_role="structure_promotion_manifest",
                )
                media, media_binding = self._add_stage_candidate(
                    "canonical_block_ledger",
                    role="media_candidate",
                    manifest_role="media_promotion_manifest",
                )
                parameters["parent_04c_lineage"] = self._lineage_for(
                    "template_construct_binding"
                )
                parameters["structure_lineage"] = self._lineage_for(
                    "outline_reconstruction"
                )
                parameters["media_lineage"] = self._lineage_for(
                    "canonical_block_ledger"
                )
                del structure, structure_binding, media, media_binding
                self._add_promotion_registry(
                    stage_keys=(
                        "canonical_block_ledger",
                        "outline_reconstruction",
                        "template_construct_binding",
                    )
                )
            elif key in {
                "outline_reconstruction",
                "semantic_annotation",
                "template_construct_binding",
            }:
                parameters["parent_lineage_key"] = self._lineage_for(
                    self._predecessor_stage_key()
                )
            review_role, prompt_id = _BOUNDED_REVIEW[key]
            binding = self._add_bounded_review(
                role=review_role,
                prompt_id=prompt_id,
                primary=primary,
            )
            parameters["review_binding"] = binding
        elif key == "deterministic_elegantbook":
            parameters = self._prepare_spec05_inputs(parameters)
        elif key == "readonly_latex_audit":
            parameters = {}
        elif key == "independent_full_page_review":
            parameters = self._prepare_visual_review_inputs(
                primary,
                predecessor,
            )
        elif key == "delivery_recompile":
            parameters = self._prepare_target_environment()
        elif key == "ready_for_user_acceptance":
            parameters = self._prepare_readiness_inputs()
        else:
            raise EntrypointProtocolError(
                f"no code-owned input provider exists for stage {key!r}"
            )
        return self._prepared_request(
            primary=primary,
            predecessor=predecessor,
            parameters=parameters,
            recovery_lineage=recovery_lineage,
        )

    def _build_intake(
        self,
        *,
        recovery_lineage: Mapping[str, object] | None,
    ) -> PreparedStageRequest:
        source = self._source_evidence(required=False)
        if source is None:
            ref = _stage_input_ref(self.db, self.job, self.stage)
            primary = self._add_store_artifact(
                "frozen_source",
                "popo-manifest",
                ref,
            )
            return self._prepared_request(
                primary=primary,
                predecessor=None,
                parameters={},
                recovery_lineage=recovery_lineage,
            )
        for role in (
            "frozen_source",
            "source_pdf",
            "mineru_manifest",
            "mineru_frozen_marker",
            "mineru_archive",
            "popo_frozen_marker",
            "popo_archive",
        ):
            self._add_source(role, source)
        self._add_template_archive()
        by_role = {item.role: item for item in self.artifacts}
        run_id = str(source.get("run_id") or "")
        if not run_id:
            raise ArtifactIntegrityError("source_evidence.run_id is required")
        parameters = {
            "run_id": run_id,
            "decision_index_id": f"{self.job.public_id}-decisions",
            "decision_snapshot_id": f"{self.job.public_id}-d1",
            "stage_decision_id": f"{self.job.public_id}-intake-a{self.stage.attempt}",
        }
        return self._prepared_request(
            primary=by_role["frozen_source"],
            predecessor=None,
            parameters=parameters,
            recovery_lineage=recovery_lineage,
        )

    def _prepared_request(
        self,
        *,
        primary: PreparedInputArtifact,
        predecessor: Mapping[str, object] | None,
        parameters: Mapping[str, object],
        recovery_lineage: Mapping[str, object] | None,
    ) -> PreparedStageRequest:
        bound_parameters = dict(parameters)
        if recovery_lineage is not None:
            bound_parameters["recovery_lineage"] = dict(recovery_lineage)
        return PreparedStageRequest(
            primary=primary,
            artifacts=tuple(self.artifacts),
            predecessor_promotion=predecessor,
            parameters=bound_parameters,
        )

    def _add_review_resolution(self) -> Mapping[str, object] | None:
        if not self.stage.review_resolution_sha256:
            if self.stage.review_resolution_id is not None:
                raise ArtifactIntegrityError(
                    "recovery stage has a resolution id without its SHA-256"
                )
            return None
        if self.stage.review_resolution_id is None:
            raise ArtifactIntegrityError(
                "recovery stage has a resolution SHA-256 without its record"
            )
        resolution = self.db.get(
            WorkflowV3ReviewResolution,
            self.stage.review_resolution_id,
        )
        if (
            resolution is None
            or resolution.workflow_job_id != self.job.id
            or resolution.manifest_sha256
            != self.stage.review_resolution_sha256
            or resolution.recovery_generation != self.stage.generation
        ):
            raise ArtifactIntegrityError(
                "recovery stage resolution lineage drifted"
            )
        if self.stage.stage_key != resolution.recovery_stage_key:
            return None
        artifact = self._add_store_artifact(
            "review_resolution_manifest",
            "worker-v3-review-resolution-manifest",
            ArtifactRef(
                bucket=resolution.manifest_bucket,
                object_name=resolution.manifest_object,
                sha256=resolution.manifest_sha256,
                size_bytes=resolution.manifest_size_bytes,
            ),
        )
        return {
            "generation": self.stage.generation,
            "review_resolution_id": str(resolution.id),
            "review_resolution_sha256": resolution.manifest_sha256,
            "evaluation_id": str(resolution.evaluation_id),
            "evaluation_sha256": resolution.evaluation_sha256,
            "manifest": {
                "path": artifact.path,
                "sha256": artifact.ref.sha256,
                "size_bytes": artifact.ref.size_bytes,
            },
        }

    def _source_evidence(self, *, required: bool = True) -> Mapping[str, object] | None:
        payload = self.job.load(self.job.payload_json, {})
        source = payload.get("source_evidence") if isinstance(payload, dict) else None
        if not isinstance(source, dict):
            if required:
                raise ArtifactIntegrityError("job has no frozen source_evidence")
            return None
        artifacts = source.get("artifacts")
        if not isinstance(artifacts, list) or len(artifacts) != 7:
            raise ArtifactIntegrityError("source_evidence.artifacts must contain exactly seven objects")
        return source

    def _add_source(
        self,
        role: str,
        source: Mapping[str, object],
    ) -> PreparedInputArtifact:
        rows = [
            row
            for row in source.get("artifacts", [])
            if isinstance(row, dict) and row.get("role") == role
        ]
        if len(rows) != 1:
            raise ArtifactIntegrityError(f"frozen source artifact {role!r} is missing")
        row = rows[0]
        if row.get("read_only") is not True:
            raise ArtifactIntegrityError(f"frozen source artifact {role!r} is not read-only")
        ref = ArtifactRef(
            bucket=str(row.get("bucket") or ""),
            object_name=str(row.get("object") or ""),
            sha256=_require_sha256(row.get("sha256"), f"{role} SHA-256"),
            size_bytes=_require_size(row.get("size_bytes"), f"{role} size"),
        )
        return self._add_store_artifact(role, str(row.get("kind") or ""), ref)

    def _add_store_artifact(
        self,
        role: str,
        kind: str,
        ref: ArtifactRef,
    ) -> PreparedInputArtifact:
        if not role or not kind or any(item.role == role for item in self.artifacts):
            raise ArtifactIntegrityError(f"duplicate or invalid stage input role {role!r}")
        relative = f"inputs/{role}/artifact"
        path = self.workdir / relative
        actual = self.store.materialize(ref, path)
        _assert_artifact_identity(ref, actual)
        normalized = ArtifactRef(
            bucket=actual.bucket,
            object_name=actual.object_name,
            sha256=actual.sha256,
            size_bytes=actual.size_bytes,
        )
        prepared = PreparedInputArtifact(role, kind, normalized, relative)
        self.artifacts.append(prepared)
        return prepared

    def _add_local_file(
        self,
        role: str,
        kind: str,
        source: Path,
    ) -> PreparedInputArtifact:
        if any(item.role == role for item in self.artifacts):
            raise ArtifactIntegrityError(f"duplicate stage input role {role!r}")
        if source.is_symlink() or not source.is_file():
            raise ArtifactIntegrityError(f"local input {role!r} is unavailable")
        relative = f"inputs/{role}/artifact"
        destination = self.workdir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        destination.chmod(0o444)
        ref = _artifact_ref_for_file(destination, "local-stage-input", relative)
        prepared = PreparedInputArtifact(role, kind, ref, relative)
        self.artifacts.append(prepared)
        return prepared

    def _add_json(
        self,
        role: str,
        kind: str,
        value: Mapping[str, object],
    ) -> PreparedInputArtifact:
        path = self.workdir / "generated-inputs" / f"{role}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(path, value)
        return self._add_local_file(role, kind, path)

    def _add_template_archive(self) -> PreparedInputArtifact:
        manifest = self.bound.verification.manifest
        template = manifest.get("template")
        if not isinstance(template, dict):
            raise ReleaseBindingError("release template binding is missing")
        relative = str(template.get("archive_path") or "")
        source = (self.release_root / _safe_relative(relative, "template archive")).resolve()
        if self.release_root not in source.parents:
            raise ReleaseBindingError("release template archive escapes the release")
        if _sha256_file(source) != template.get("archive_sha256"):
            raise ReleaseBindingError("release template archive hash drifted")
        return self._add_local_file(
            "template_archive",
            "approved-template-archive",
            source,
        )

    def _add_promotion(
        self,
        role: str,
        *,
        promotion_id: int,
        manifest_role: str,
    ) -> tuple[PreparedInputArtifact, Mapping[str, object]]:
        if promotion_id <= 0:
            raise ArtifactIntegrityError("predecessor promotion is missing")
        promotion = (
            self.db.query(WorkflowV3Promotion)
            .filter(
                WorkflowV3Promotion.id == promotion_id,
                WorkflowV3Promotion.workflow_job_id == self.job.id,
            )
            .one_or_none()
        )
        if promotion is None:
            raise ArtifactIntegrityError("predecessor promotion belongs to another job")
        stage = (
            self.db.query(WorkflowV3StageRun)
            .filter(WorkflowV3StageRun.id == promotion.stage_run_id)
            .one()
        )
        candidate = (
            self.db.query(WorkflowV3Candidate)
            .filter(WorkflowV3Candidate.id == promotion.candidate_id)
            .one()
        )
        evaluation = (
            self.db.query(WorkflowV3Evaluation)
            .filter(WorkflowV3Evaluation.id == promotion.evaluation_id)
            .one()
        )
        if (
            candidate.status != "promoted"
            or candidate.sha256 != promotion.artifact_sha256
            or evaluation.decision != "passed"
            or not evaluation.spec_passed
        ):
            raise ArtifactIntegrityError("predecessor was not independently passed and promoted")
        primary = self._add_store_artifact(
            role,
            "worker-v3-candidate-bundle",
            ArtifactRef(
                candidate.bucket,
                candidate.object_name,
                candidate.sha256,
                candidate.size_bytes,
            ),
        )
        extracted = self.workdir / "control-plane-bundles" / role
        extracted.parent.mkdir(parents=True, exist_ok=True)
        extracted.mkdir(mode=0o700)
        _safe_extract_candidate_bundle(self.workdir / primary.path, extracted)
        self.extracted[role] = extracted
        evaluation_value = {
            "schema_version": "luceon.worker-v3-evaluation-projection/v1",
            "evaluation_id": str(evaluation.id),
            "candidate_sha256": candidate.sha256,
            "evaluator_identity": evaluation.evaluator_identity,
            "evaluator_version": evaluation.evaluator_version,
            "policy_sha256": evaluation.policy_sha256,
            "decision": evaluation.decision,
            "spec_passed": bool(evaluation.spec_passed),
            "gate_results": evaluation.load(evaluation.gate_results_json, {}),
            "findings": evaluation.load(evaluation.findings_json, []),
            "created_at": _iso(evaluation.created_at),
        }
        evaluation_sha = sha256_json(evaluation_value)
        manifest_value = self._promotion_manifest(
            stage=stage,
            promotion=promotion,
            candidate=candidate,
            evaluation=evaluation,
            extracted=extracted,
            evaluation_sha256=evaluation_sha,
        )
        manifest_artifact = self._add_json(
            manifest_role,
            "worker-v3-promotion-manifest",
            manifest_value,
        )
        self.promotion_manifests[stage.stage_key] = manifest_artifact
        self.promotion_rows[stage.stage_key] = (promotion, evaluation)
        return primary, {
            "promotion_id": str(promotion.id),
            "stage_key": stage.stage_key,
            "artifact_sha256": promotion.artifact_sha256,
            "evaluation_sha256": evaluation_sha,
            "promotion_manifest_sha256": manifest_artifact.ref.sha256,
        }

    def _promotion_manifest(
        self,
        *,
        stage: WorkflowV3StageRun,
        promotion: WorkflowV3Promotion,
        candidate: WorkflowV3Candidate,
        evaluation: WorkflowV3Evaluation,
        extracted: Path,
        evaluation_sha256: str,
    ) -> dict[str, object]:
        content = _read_json_object(
            extracted / "candidate-content-manifest.json",
            "candidate content manifest",
        )
        files = content.get("files")
        if not isinstance(files, list):
            raise ArtifactIntegrityError("candidate content inventory is missing")
        by_role: dict[str, dict[str, object]] = {}
        for row in files:
            if not isinstance(row, dict) or not isinstance(row.get("role"), str):
                continue
            by_role[str(row["role"])] = row
        stage_row = by_role.get("stage_manifest")
        if stage_row is None:
            raise ArtifactIntegrityError("promoted candidate has no stage manifest")
        promoted_artifacts: dict[str, dict[str, object]] = {}
        for source_role, target_role in _PROMOTED_ROLE_ALIASES.items():
            row = by_role.get(source_role)
            if row is None:
                continue
            artifact_path = (extracted / str(row["path"])).resolve()
            promoted_artifacts[target_role] = {
                "path": str(artifact_path),
                "sha256": str(row["sha256"]),
            }
        stage_manifest_path = (extracted / str(stage_row["path"])).resolve()
        lineage = self._lineage_for(stage.stage_key)
        return {
            "schema_version": "stage-promotion-manifest/1.0",
            "promotion_id": f"workflow-v3-promotion-{promotion.id}",
            "lineage_key": lineage,
            "evaluated_at": _iso(evaluation.created_at),
            "evaluator": {
                "identity": evaluation.evaluator_identity,
                "version": evaluation.evaluator_version,
            },
            "stage_kind": _STAGE_PROMOTION_KIND.get(
                stage.stage_key,
                f"workflow_v3_{stage.stage_key}",
            ),
            "run_dir": str(extracted),
            "stage_manifest": {
                "path": str(stage_manifest_path),
                "sha256": str(stage_row["sha256"]),
            },
            "disposition": "promoted",
            "promotion_class": _control_plane_promotion_class(stage.stage_key),
            "producer_execution_provenance": "control_plane_release_bound",
            "checks": [
                {
                    "id": str(name),
                    "status": "passed" if bool(value) else "failed",
                }
                for name, value in sorted(
                    evaluation.load(evaluation.gate_results_json, {}).items()
                )
            ],
            "summary": {
                "candidate_sha256": candidate.sha256,
                "evaluation_sha256": evaluation_sha256,
                "spec_passed": bool(evaluation.spec_passed),
            },
            "promoted_artifacts": promoted_artifacts,
            "consumer_rule": "Consumers must verify exact live path and SHA-256.",
        }

    def _add_stage_candidate(
        self,
        stage_key: str,
        *,
        role: str,
        manifest_role: str,
    ) -> tuple[PreparedInputArtifact, Mapping[str, object]]:
        stage = (
            self.db.query(WorkflowV3StageRun)
            .filter(
                WorkflowV3StageRun.workflow_job_id == self.job.id,
                WorkflowV3StageRun.stage_key == stage_key,
                WorkflowV3StageRun.machine_status == "succeeded",
            )
            .order_by(
                WorkflowV3StageRun.generation.desc(),
                WorkflowV3StageRun.attempt.desc(),
            )
            .first()
        )
        if stage is None or not stage.promotion_id:
            raise ArtifactIntegrityError(f"required promoted stage {stage_key!r} is unavailable")
        return self._add_promotion(
            role,
            promotion_id=int(stage.promotion_id),
            manifest_role=manifest_role,
        )

    def _add_promotion_registry(
        self,
        *,
        stage_keys: Sequence[str] | None = None,
    ) -> PreparedInputArtifact:
        keys = tuple(stage_keys or (self._predecessor_stage_key(),))
        entries: list[dict[str, object]] = []
        active: dict[str, dict[str, object]] = {}
        for key in keys:
            if key not in self.promotion_manifests:
                stage = (
                    self.db.query(WorkflowV3StageRun)
                    .filter(
                        WorkflowV3StageRun.workflow_job_id == self.job.id,
                        WorkflowV3StageRun.stage_key == key,
                        WorkflowV3StageRun.machine_status == "succeeded",
                    )
                    .order_by(
                        WorkflowV3StageRun.generation.desc(),
                        WorkflowV3StageRun.attempt.desc(),
                    )
                    .first()
                )
                if stage is None or not stage.promotion_id:
                    raise ArtifactIntegrityError(f"promotion registry stage {key!r} is missing")
                hidden_candidate, _ = self._add_promotion(
                    f"registry_candidate_{key}",
                    promotion_id=int(stage.promotion_id),
                    manifest_role=f"registry_manifest_{key}",
                )
                hidden_manifest = self.promotion_manifests[key]
                self.artifacts.remove(hidden_candidate)
                self.artifacts.remove(hidden_manifest)
            artifact = self.promotion_manifests[key]
            promotion, _ = self.promotion_rows[key]
            manifest = _read_json_object(
                self.workdir / artifact.path,
                f"{key} promotion manifest",
            )
            entry = {
                "promotion_id": manifest["promotion_id"],
                "lineage_key": manifest["lineage_key"],
                "disposition": "promoted",
                "promotion_class": manifest["promotion_class"],
                "manifest_path": str((self.workdir / artifact.path).resolve()),
                "manifest_sha256": artifact.ref.sha256,
                "run_dir": manifest["run_dir"],
                "stage_manifest_sha256": manifest["stage_manifest"]["sha256"],
            }
            entries.append(entry)
            active[str(manifest["lineage_key"])] = {
                "promotion_id": manifest["promotion_id"],
                "manifest_path": entry["manifest_path"],
                "manifest_sha256": artifact.ref.sha256,
                "promotion_class": manifest["promotion_class"],
            }
            del promotion
        registry: dict[str, object] = {
            "schema_version": "promotion-registry/1.0",
            "registry_id": f"{self.job.public_id}-registry",
            "snapshot_id": f"{self.job.public_id}-{self.stage.stage_key}-a{self.stage.attempt}",
            "version": len(entries),
            "generated_at": _utc_now(),
            "parent_registry_ref": None,
            "parent_registry_sha256": None,
            "entries": entries,
            "active_promotions": dict(sorted(active.items())),
            "selection_rule": (
                "For each lineage_key, the last appended promoted entry is active; "
                "rejected entries never become active."
            ),
            "payload_hash": "",
        }
        registry["payload_hash"] = sha256_json(
            {
                key: value
                for key, value in registry.items()
                if key not in {"generated_at", "payload_hash"}
            }
        )
        existing = next(
            (item for item in self.artifacts if item.role == "promotion_registry"),
            None,
        )
        if existing is not None:
            self.artifacts.remove(existing)
        return self._add_json(
            "promotion_registry",
            "worker-v3-promotion-registry",
            registry,
        )

    def _identity_parameters(self) -> dict[str, object]:
        source = self._source_evidence()
        return {
            "run_id": str(source.get("run_id") or self.job.public_id),
            "decision_snapshot_id": f"{self.job.public_id}-d{self.stage.attempt}",
            "stage_decision_id": (
                f"{self.job.public_id}-{self.stage.stage_key}-a{self.stage.attempt}"
            ),
            "ledger_snapshot_id": f"{self.job.public_id}-ledger-{self.stage.attempt}",
            "ledger_version": max(1, int(self.stage.attempt)),
        }

    def _predecessor_stage_key(self) -> str:
        if not self.stage.input_promotion_id:
            raise ArtifactIntegrityError("stage has no predecessor promotion")
        return (
            self.db.query(WorkflowV3StageRun.stage_key)
            .join(
                WorkflowV3Promotion,
                WorkflowV3Promotion.stage_run_id == WorkflowV3StageRun.id,
            )
            .filter(WorkflowV3Promotion.id == self.stage.input_promotion_id)
            .scalar()
        ) or ""

    def _lineage_for(self, stage_key: str) -> str:
        if not stage_key:
            raise ArtifactIntegrityError("lineage stage key is empty")
        return f"{self.job.public_id}:{stage_key}"

    def _add_prior_candidate_file(
        self,
        stage_key: str,
        role: str,
        relative: str,
        kind: str,
    ) -> PreparedInputArtifact:
        candidate_role = f"prior_candidate_{stage_key}"
        if candidate_role not in self.extracted:
            stage = (
                self.db.query(WorkflowV3StageRun)
                .filter(
                    WorkflowV3StageRun.workflow_job_id == self.job.id,
                    WorkflowV3StageRun.stage_key == stage_key,
                    WorkflowV3StageRun.machine_status == "succeeded",
                )
                .order_by(
                    WorkflowV3StageRun.generation.desc(),
                    WorkflowV3StageRun.attempt.desc(),
                )
                .first()
            )
            if stage is None or not stage.promotion_id:
                raise ArtifactIntegrityError(f"promoted stage {stage_key!r} is unavailable")
            hidden_candidate, _ = self._add_promotion(
                candidate_role,
                promotion_id=int(stage.promotion_id),
                manifest_role=f"prior_manifest_{stage_key}",
            )
            hidden_manifest = self.promotion_manifests[stage_key]
            self.artifacts.remove(hidden_candidate)
            self.artifacts.remove(hidden_manifest)
        source = self.extracted[candidate_role] / _safe_relative(
            relative,
            f"{stage_key} candidate file",
        )
        return self._add_local_file(role, kind, source)

    def _add_bounded_review(
        self,
        *,
        role: str,
        prompt_id: str,
        primary: PreparedInputArtifact,
    ) -> dict[str, str]:
        evidence = self._review_input(prompt_id, primary)
        if prompt_id == "worker-v3.spec02-scope-order-review":
            evidence = scope_model_evidence(evidence)
        elif prompt_id == "worker-v3.spec03-media-review":
            evidence = media_model_evidence(evidence)
        elif prompt_id == "worker-v3.spec04a-outline-review":
            task_path = (
                self.workdir
                / "control-plane-review-task"
                / "spec04a-outline-review-task.json"
            )
            if (
                not task_path.is_file()
                or sha256_json(
                    _read_json_object(
                        task_path,
                        "Spec 04-A deterministic review task",
                    )
                )
                != sha256_json(evidence)
            ):
                raise ArtifactIntegrityError(
                    "Spec 04-A deterministic review task drifted before projection"
                )
            self._add_local_file(
                "outline_review_task",
                "worker-v3-deterministic-review-task",
                task_path,
            )
            evidence = outline_model_evidence(evidence)
        elif prompt_id == "worker-v3.spec04b-semantic-review":
            task_path = (
                self.workdir
                / "control-plane-review-task"
                / "spec04b-semantic-review-task.json"
            )
            if (
                not task_path.is_file()
                or sha256_json(
                    _read_json_object(
                        task_path,
                        "Spec 04-B deterministic review task",
                    )
                )
                != sha256_json(evidence)
            ):
                raise ArtifactIntegrityError(
                    "Spec 04-B deterministic review task drifted before projection"
                )
            self._add_local_file(
                "semantic_review_task",
                "worker-v3-deterministic-review-task",
                task_path,
            )
            evidence = semantic_model_evidence(evidence)
        elif prompt_id == "worker-v3.spec04c-construct-review":
            task_path = (
                self.workdir
                / "control-plane-review-task"
                / "spec04c-construct-review-task.json"
            )
            if (
                not task_path.is_file()
                or sha256_json(
                    _read_json_object(
                        task_path,
                        "Spec 04-C deterministic review task",
                    )
                )
                != sha256_json(evidence)
            ):
                raise ArtifactIntegrityError(
                    "Spec 04-C deterministic review task drifted before projection"
                )
            self._add_local_file(
                "construct_review_task",
                "worker-v3-deterministic-review-task",
                task_path,
            )
        elif prompt_id == "worker-v3.spec04d-render-policy":
            task_path = (
                self.workdir
                / "control-plane-review-task"
                / "spec04d-render-policy-review-task.json"
            )
            if (
                not task_path.is_file()
                or sha256_json(
                    _read_json_object(
                        task_path,
                        "Spec 04-D deterministic review task",
                    )
                )
                != sha256_json(evidence)
            ):
                raise ArtifactIntegrityError(
                    "Spec 04-D deterministic review task drifted before projection"
                )
            self._add_local_file(
                "render_policy_task",
                "worker-v3-deterministic-review-task",
                task_path,
            )
        manifest = self.bound.verification.manifest
        prompts = [
            row
            for row in manifest.get("prompts", [])
            if isinstance(row, dict) and row.get("id") == prompt_id
        ]
        if len(prompts) != 1:
            raise ReleaseBindingError(
                f"release must bind exactly one prompt {prompt_id!r}"
            )
        prompt = prompts[0]
        prompt_path = self._release_bound_path(
            str(prompt.get("path") or ""),
            prompt.get("sha256"),
            f"prompt {prompt_id}",
        )
        schema_path_value = str(prompt.get("output_schema") or "")
        schemas = [
            row
            for row in manifest.get("schemas", [])
            if isinstance(row, dict)
            and row.get("path") == schema_path_value
        ]
        if len(schemas) != 1:
            raise ReleaseBindingError(
                f"release output schema for prompt {prompt_id!r} is missing"
            )
        schema = schemas[0]
        schema_path = self._release_bound_path(
            schema_path_value,
            schema.get("sha256"),
            f"schema for {prompt_id}",
        )
        output_schema = _read_json_object(schema_path, "bounded output schema")
        model_policy = manifest.get("model_policy")
        if not isinstance(model_policy, dict):
            raise ReleaseBindingError("release bounded model policy is missing")
        request_parameters = model_policy.get("request_parameters")
        if not isinstance(request_parameters, dict):
            raise ReleaseBindingError("release bounded model parameters are missing")
        budget = _ordinary_model_budget(model_policy, request_parameters)
        from app.services.runtime_settings import load_runtime_config

        transport = self.model_transport
        if transport is None:
            if self.qualification_mode:
                raise ReleaseBindingError(
                    "qualification forbids live model transport fallback"
                )
            transport = transport_from_runtime_config(
                release_model_policy=model_policy,
                runtime_config=load_runtime_config(include_secrets=True),
            )
        input_sha = sha256_json(evidence)
        call = ReleaseBoundLlmCall(
            call_id=_bounded_model_call_id(
                job_scope_id=self.job.idempotency_key,
                stage_key=self.stage.stage_key,
                attempt=self.stage.attempt,
                prompt_sha256=str(prompt.get("sha256") or ""),
                input_sha256=input_sha,
            ),
            release_id=self.bound.verification.release_id,
            release_sha256=self.bound.manifest_sha256,
            stage_key=self.stage.stage_key,
            prompt_id=prompt_id,
            prompt_version=str(prompt.get("version") or ""),
            prompt_sha256=_require_sha256(
                prompt.get("sha256"),
                f"{prompt_id} prompt SHA-256",
            ),
            prompt_text=prompt_path.read_text(encoding="utf-8"),
            schema_id=str(schema.get("id") or ""),
            schema_version=str(schema.get("version") or ""),
            # The release manifest binds the exact schema file bytes above;
            # the gateway contract binds the parsed schema canonically.  They
            # intentionally differ when the immutable file is pretty-printed.
            schema_sha256=sha256_json(output_schema),
            output_schema=output_schema,
            input_sha256=input_sha,
            input_evidence=evidence,
            provider=str(model_policy.get("provider") or ""),
            model=str(model_policy.get("model") or ""),
            request_parameters=request_parameters,
            allowed_choices=_review_allowed_choices(prompt_id, evidence),
            timeout_seconds=budget["timeout_seconds"],
            attempt_number=self.stage.attempt,
        )
        _enforce_model_request_budget(call, budget)
        start_model_call(self.db, self.job.public_id, call=call)
        self.db.commit()
        try:
            result = _execute_model_call_with_heartbeat(
                call,
                transport,
                heartbeat=self.heartbeat,
            )
            _enforce_model_result_budget(result, budget)
            finish_model_call(self.db, call.call_id, result=result)
            self.db.commit()
        except LlmGatewayError as exc:
            self.db.rollback()
            row = (
                self.db.query(WorkflowV3ModelCall)
                .filter(WorkflowV3ModelCall.call_id == call.call_id)
                .one_or_none()
            )
            if row is not None and row.machine_status == "running":
                finish_model_call(self.db, call.call_id, error=exc)
                self.db.commit()
            raise
        review_path = self.workdir / "generated-inputs" / f"{role}.json"
        review_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(review_path, result.parsed_result)
        audit_path = self.workdir / "generated-inputs" / "llm_call_audit.json"
        _write_json(audit_path, result.audit)
        review = self._add_local_file(
            role,
            "bounded-llm-review",
            review_path,
        )
        audit = self._add_local_file(
            "llm_call_audit",
            "bounded-llm-audit",
            audit_path,
        )
        return {
            "prompt_id": call.prompt_id,
            "prompt_version": call.prompt_version,
            "prompt_sha256": call.prompt_sha256,
            "schema_id": call.schema_id,
            "schema_version": call.schema_version,
            "schema_sha256": call.schema_sha256,
            "input_canonical_sha256": call.input_sha256,
            "result_canonical_sha256": sha256_json(result.parsed_result),
            "audit_sha256": audit.ref.sha256,
        }

    def _review_input(
        self,
        prompt_id: str,
        primary: PreparedInputArtifact,
    ) -> Mapping[str, object]:
        parent = self.extracted.get("promoted_predecessor")
        if parent is None:
            raise ArtifactIntegrityError("bounded review has no extracted predecessor")
        if prompt_id.endswith("spec02-scope-order-review"):
            return self._prepare_atomic_review_task(
                parent,
                command="prepare-scope-review-task",
                filename="spec02-scope-order-review-task.json",
            )
        if prompt_id.endswith("spec03-media-review"):
            return self._prepare_atomic_review_task(
                parent,
                command="prepare-media-review-task",
                filename="spec03-media-review-task.json",
            )
        if prompt_id == "worker-v3.spec04a-outline-review":
            source_pdf = next(
                (
                    artifact
                    for artifact in self.artifacts
                    if artifact.role == "source_pdf"
                ),
                None,
            )
            parent_promotion = next(
                (
                    artifact
                    for artifact in self.artifacts
                    if artifact.role == "predecessor_promotion_manifest"
                ),
                None,
            )
            if source_pdf is None or parent_promotion is None:
                raise ArtifactIntegrityError(
                    "Spec 04-A compact review task lacks source or promotion evidence"
                )
            return self._prepare_atomic_review_task(
                parent,
                command="prepare-outline-review-task",
                filename="spec04a-outline-review-task.json",
                extra_args=(
                    "--source-pdf",
                    str((self.workdir / source_pdf.path).resolve()),
                    "--source-pdf-ref",
                    source_pdf.path,
                    "--parent-promotion",
                    str((self.workdir / parent_promotion.path).resolve()),
                ),
            )
        if prompt_id == "worker-v3.spec04b-semantic-review":
            source_pdf = next(
                (
                    artifact
                    for artifact in self.artifacts
                    if artifact.role == "source_pdf"
                ),
                None,
            )
            parent_promotion = next(
                (
                    artifact
                    for artifact in self.artifacts
                    if artifact.role == "predecessor_promotion_manifest"
                ),
                None,
            )
            if source_pdf is None or parent_promotion is None:
                raise ArtifactIntegrityError(
                    "Spec 04-B compact review task lacks source or promotion evidence"
                )
            return self._prepare_atomic_review_task(
                parent,
                command="prepare-semantic-review-task",
                filename="spec04b-semantic-review-task.json",
                extra_args=(
                    "--source-pdf",
                    str((self.workdir / source_pdf.path).resolve()),
                    "--source-pdf-ref",
                    source_pdf.path,
                    "--parent-promotion",
                    str((self.workdir / parent_promotion.path).resolve()),
                ),
            )
        if prompt_id == "worker-v3.spec04c-construct-review":
            template_intake = next(
                (
                    artifact
                    for artifact in self.artifacts
                    if artifact.role == "template_intake"
                ),
                None,
            )
            template_archive = next(
                (
                    artifact
                    for artifact in self.artifacts
                    if artifact.role == "template_archive"
                ),
                None,
            )
            if template_intake is None or template_archive is None:
                raise ArtifactIntegrityError(
                    "Spec 04-C compact review task lacks frozen template evidence"
                )
            output = (
                self.workdir
                / "control-plane-review-task"
                / "spec04c-construct-review-task.json"
            )
            output.parent.mkdir(parents=True, exist_ok=True)
            semantic_stage_manifest = _read_json_object(
                parent / "manifests/spec04b_semantic_stage_manifest.json",
                "Spec 04-B semantic stage manifest",
            )
            semantic_ledger = semantic_stage_manifest.get("ledger_L")
            if not isinstance(semantic_ledger, dict):
                raise ArtifactIntegrityError(
                    "Spec 04-B semantic stage manifest lacks ledger payload binding"
                )
            predecessor_payload_sha256 = _require_sha256(
                semantic_ledger.get("payload_hash"),
                "Spec 04-B semantic ledger payload SHA-256",
            )
            run_release_python_kernel(
                release_root=self.release_root,
                kernel_relative=(
                    "skills/luceon-popo-to-refined-elegantbook/scripts/"
                    "spec04c_construct_binding_contract.py"
                ),
                args=(
                    "prepare-review-task",
                    "--parent",
                    str(parent),
                    "--template-intake",
                    str((self.workdir / template_intake.path).resolve()),
                    "--template-zip",
                    str((self.workdir / template_archive.path).resolve()),
                    "--predecessor-sha256",
                    predecessor_payload_sha256,
                    "--output",
                    str(output),
                ),
                cwd=self.workdir,
                timeout_seconds=86_400,
            )
            return _read_json_object(
                output,
                "Spec 04-C deterministic review task",
            )
        if prompt_id == "worker-v3.spec04d-render-policy":
            structure = self.extracted.get("structure_candidate")
            media = self.extracted.get("media_candidate")
            if structure is None or media is None:
                raise ArtifactIntegrityError(
                    "Spec 04-D compact review task lacks promoted structure/media evidence"
                )
            output = (
                self.workdir
                / "control-plane-review-task"
                / "spec04d-render-policy-review-task.json"
            )
            output.parent.mkdir(parents=True, exist_ok=True)
            run_release_python_kernel(
                release_root=self.release_root,
                kernel_relative=(
                    "skills/luceon-popo-to-refined-elegantbook/scripts/"
                    "spec04d_render_plan_contract.py"
                ),
                args=(
                    "prepare-policy-review-task",
                    "--parent",
                    str(parent),
                    "--structure",
                    str(structure),
                    "--media",
                    str(media),
                    "--output",
                    str(output),
                ),
                cwd=self.workdir,
                timeout_seconds=86_400,
            )
            return _read_json_object(
                output,
                "Spec 04-D deterministic review task",
            )
        raise EntrypointProtocolError(f"unknown bounded review prompt {prompt_id!r}")

    def _prepare_atomic_review_task(
        self,
        parent: Path,
        *,
        command: str,
        filename: str,
        extra_args: Sequence[str] = (),
    ) -> Mapping[str, object]:
        output = self.workdir / "control-plane-review-task" / filename
        output.parent.mkdir(parents=True, exist_ok=True)
        run_release_python_kernel(
            release_root=self.release_root,
            kernel_relative="scripts/worker-v3/spec01_03_atomic_kernel.py",
            args=(
                command,
                "--parent",
                str(parent),
                *extra_args,
                "--output",
                str(output),
            ),
            cwd=self.workdir,
            timeout_seconds=86_400,
        )
        return _read_json_object(output, "deterministic review task")

    def _release_bound_path(
        self,
        relative: str,
        expected_sha: object,
        label: str,
    ) -> Path:
        path = (self.release_root / _safe_relative(relative, label)).resolve()
        if self.release_root not in path.parents or path.is_symlink() or not path.is_file():
            raise ReleaseBindingError(f"{label} escapes or is missing from the release")
        if _sha256_file(path) != _require_sha256(expected_sha, f"{label} SHA-256"):
            raise ReleaseBindingError(f"{label} hash drifted")
        return path

    def _prepare_spec05_inputs(
        self,
        parameters: Mapping[str, object],
    ) -> dict[str, object]:
        self._add_promotion_registry()
        template_archive = self._add_template_archive()
        self._add_prior_candidate_file(
            "intake_snapshot",
            "template_intake",
            "contracts/template_intake.json",
            "worker-v3-template-intake",
        )
        self._add_prior_candidate_file(
            "template_construct_binding",
            "template_capability_manifest",
            "template/template_capability_manifest.json",
            "worker-v3-template-capability-manifest",
        )
        self._add_prior_candidate_file(
            "canonical_block_ledger",
            "media_evidence_ledger",
            "media/media_evidence_ledger.json",
            "worker-v3-media-evidence-ledger",
        )
        self._add_prior_candidate_file(
            "canonical_block_ledger",
            "media_representation_plan",
            "media/media_representation_plan.json",
            "worker-v3-media-representation-plan",
        )
        self._add_stage_candidate(
            "canonical_block_ledger",
            role="source_asset_bundle",
            manifest_role="source_asset_promotion_manifest",
        )
        source_asset_manifest = next(
            item
            for item in self.artifacts
            if item.role == "source_asset_promotion_manifest"
        )
        self.artifacts.remove(source_asset_manifest)
        source_scope = self.extracted["source_asset_bundle"] / (
            "ledgers/source_scope_ledger.json"
        )
        source_scope_input = self._add_local_file(
            "source_scope_ledger",
            "worker-v3-source-scope-ledger",
            source_scope,
        )
        source_pdf = next(item for item in self.artifacts if item.role == "source_pdf")
        page_render = self._render_metadata_evidence_page(source_pdf)
        metadata_page = self._add_local_file(
            "metadata_page_render",
            "worker-v3-source-page-render",
            page_render,
        )
        self._add_json(
            "metadata_config",
            "spec05-source-grounded-metadata",
            self._spec05_metadata_config(source_pdf, metadata_page),
        )
        self._add_json(
            "presentation_config",
            "spec05-presentation-config",
            self._spec05_presentation_config(
                template_archive,
                source_scope_input,
            ),
        )
        self._add_release_profile_input("build_policy")
        return {
            "parent_lineage_key": self._lineage_for("frozen_render_plan"),
            "run_id": str(self._source_evidence().get("run_id") or self.job.public_id),
        }

    def _render_metadata_evidence_page(
        self,
        source_pdf: PreparedInputArtifact,
    ) -> Path:
        import fitz

        path = (self.workdir / source_pdf.path).resolve()
        output = self.workdir / "generated-inputs/metadata-source-page-001.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        with fitz.open(path) as document:
            if document.page_count < 1:
                raise ArtifactIntegrityError("source PDF has no page for metadata evidence")
            pixmap = document[0].get_pixmap(dpi=110, alpha=False)
            pixmap.save(output)
        return output

    def _spec05_metadata_config(
        self,
        source_pdf: PreparedInputArtifact,
        page_render: PreparedInputArtifact,
    ) -> dict[str, object]:
        source = self._source_evidence()
        source_pdf_evidence = source.get("source_pdf")
        if not isinstance(source_pdf_evidence, Mapping):
            raise ArtifactIntegrityError(
                "source evidence has no source PDF identity for metadata"
            )
        object_name = str(source_pdf_evidence.get("object") or "")
        filename = PurePosixPath(object_name).name
        title = filename[:-4].strip() if filename.lower().endswith(".pdf") else ""
        if not title or any(character in title for character in "\\{}"):
            raise ArtifactIntegrityError(
                "source filename cannot provide a safe source-grounded title"
            )
        return {
            "schema_version": "spec05-metadata/1.0",
            "status": "approved_source_grounded",
            "values": {"title": title},
            "evidence": [
                {
                    "source_ref": f"../{source_pdf.role}/artifact",
                    "source_sha256": source_pdf.ref.sha256,
                    "pdf_physical_page": 1,
                    "page_render_ref": f"../{page_render.role}/artifact",
                    "page_render_sha256": page_render.ref.sha256,
                    "supports": ["title"],
                }
            ],
        }

    def _spec05_presentation_config(
        self,
        template_archive: PreparedInputArtifact,
        source_scope: PreparedInputArtifact,
    ) -> dict[str, object]:
        archive = (self.workdir / template_archive.path).resolve()
        manifest = self.bound.verification.manifest
        template = manifest.get("template")
        if not isinstance(template, dict):
            raise ReleaseBindingError("release template binding is missing")
        main_member = str(template.get("main_member") or "")
        fixed_members = template.get("fixed_asset_members")
        if (
            not main_member
            or not isinstance(fixed_members, list)
            or set(fixed_members) != {"figure/cover.jpg", "figure/logo.jpg"}
        ):
            raise ReleaseBindingError("release template presentation assets are incomplete")
        with zipfile.ZipFile(archive) as frozen:
            main_text = frozen.read(main_member).decode("utf-8")
            rows: dict[str, dict[str, object]] = {}
            for macro in ("cover", "logo"):
                matches = re.findall(
                    rf"\\{macro}\{{([^{{}}]+)\}}",
                    main_text,
                )
                member = f"figure/{macro}.jpg"
                if len(matches) != 1 or matches[0] != Path(member).name:
                    raise ReleaseBindingError(
                        f"frozen template {macro} macro is not uniquely bound"
                    )
                payload = frozen.read(member)
                rows[macro] = {
                    "mode": "template_default",
                    "macro_value": matches[0],
                    "template_member": member,
                    "asset_sha256": hashlib.sha256(payload).hexdigest(),
                    "decision": {
                        "decision_id": f"template-default-{macro}",
                        "status": "closed",
                        "rationale": "Preserve the exact frozen template default asset.",
                        "evidence_refs": [template_archive.path],
                    },
                    "compatibility": {
                        "status": "approved",
                        "assertion": "output_brand",
                    },
                }
        return {
            "schema_version": "spec05-presentation-config/1.0",
            "status": "approved",
            "template_zip_sha256": template_archive.ref.sha256,
            "source_scope_binding": {
                "ledger_ref": f"../{source_scope.role}/artifact",
                "ledger_sha256": source_scope.ref.sha256,
            },
            "assets": rows,
        }

    def _prepare_visual_review_inputs(
        self,
        primary: PreparedInputArtifact,
        predecessor: Mapping[str, object],
    ) -> dict[str, object]:
        parent = self.extracted.get("promoted_predecessor")
        if parent is None:
            raise ArtifactIntegrityError(
                "visual review has no extracted promoted predecessor"
            )
        source = self._source_evidence()
        source_pdf = self._materialize_control_plane_source(
            "source_pdf",
            source,
        )
        from app.services.runtime_settings import load_runtime_config

        if self.qualification_mode and self.model_transport is None:
            raise ReleaseBindingError(
                "qualification forbids live visual model transport fallback"
            )
        generated = build_full_page_review_inputs(
            job_id=self.job.public_id,
            call_scope_id=self.job.idempotency_key,
            stage_key=self.stage.stage_key,
            stage_version=self.stage.stage_version,
            stage_attempt=self.stage.attempt,
            release_id=self.bound.verification.release_id,
            release_manifest_sha256=self.bound.manifest_sha256,
            release_root=self.release_root,
            source_pdf=source_pdf,
            predecessor_root=parent,
            predecessor_sha256=primary.ref.sha256,
            predecessor_promotion_sha256=_require_sha256(
                predecessor.get("promotion_manifest_sha256"),
                "predecessor promotion manifest SHA-256",
            ),
            output_root=self.workdir / "generated-visual-review",
            runtime_config=(
                {}
                if self.model_transport is not None
                else load_runtime_config(include_secrets=True)
            ),
            call_runner=self._run_visual_model_call,
            transport_override=self.model_transport,
            heartbeat=self.heartbeat,
        )
        self._add_local_file(
            "page_review_evidence",
            "full-page-review-evidence",
            generated.evidence_path,
        )
        self._add_local_file(
            "page_render_bundle",
            "worker-v3-candidate-bundle",
            generated.render_bundle_path,
        )
        return {}

    def _materialize_control_plane_source(
        self,
        role: str,
        source: Mapping[str, object],
    ) -> Path:
        rows = [
            row
            for row in source.get("artifacts", [])
            if isinstance(row, dict) and row.get("role") == role
        ]
        if len(rows) != 1 or rows[0].get("read_only") is not True:
            raise ArtifactIntegrityError(
                f"frozen control-plane source {role!r} is unavailable"
            )
        row = rows[0]
        expected = ArtifactRef(
            bucket=str(row.get("bucket") or ""),
            object_name=str(row.get("object") or ""),
            sha256=_require_sha256(row.get("sha256"), f"{role} SHA-256"),
            size_bytes=_require_size(row.get("size_bytes"), f"{role} size"),
        )
        path = self.workdir / "control-plane-evidence" / role
        actual = self.store.materialize(expected, path)
        _assert_artifact_identity(expected, actual)
        path.chmod(0o444)
        return path

    def _run_visual_model_call(
        self,
        call: ReleaseBoundLlmCall,
        transport: Callable[[Mapping[str, Any], float], object],
    ):
        start_model_call(self.db, self.job.public_id, call=call)
        self.db.commit()
        try:
            result = _execute_model_call_with_heartbeat(
                call,
                transport,
                heartbeat=self.heartbeat,
            )
            finish_model_call(self.db, call.call_id, result=result)
            self.db.commit()
            return result
        except LlmGatewayError as exc:
            self.db.rollback()
            row = (
                self.db.query(WorkflowV3ModelCall)
                .filter(WorkflowV3ModelCall.call_id == call.call_id)
                .one_or_none()
            )
            if row is not None and row.machine_status == "running":
                finish_model_call(self.db, call.call_id, error=exc)
                self.db.commit()
            raise

    def _prepare_target_environment(self) -> dict[str, object]:
        self._add_release_profile_input("target_environment")
        return {}

    def _prepare_readiness_inputs(self) -> dict[str, object]:
        chain = self._add_json(
            "promotion_chain",
            "worker-v3-promotion-chain",
            self._promotion_chain(),
        )
        self._add_json(
            "lineage_attestation",
            "worker-v3-lineage-attestation",
            self._lineage_attestation(chain.ref.sha256),
        )
        return {}

    def _add_release_profile_input(self, role: str) -> PreparedInputArtifact:
        manifest = self.bound.verification.manifest
        profile: Mapping[str, object] | None = None
        if role == "target_environment":
            runtime = manifest.get("runtime")
            tools = (
                runtime.get("system_tools")
                if isinstance(runtime, dict)
                else None
            )
            compiler = (
                tools.get("overleaf_compiler")
                if isinstance(tools, dict)
                else None
            )
            if isinstance(compiler, dict):
                profile = {
                    "path": compiler.get("profile_path"),
                    "sha256": compiler.get("profile_sha256"),
                    "kind": "overleaf-target-environment",
                }
        elif role == "build_policy":
            runtime = manifest.get("runtime")
            tools = (
                runtime.get("system_tools")
                if isinstance(runtime, dict)
                else None
            )
            compiler = (
                tools.get("spec05_build")
                if isinstance(tools, dict)
                else None
            )
            if isinstance(compiler, dict):
                profile = {
                    "path": compiler.get("profile_path"),
                    "sha256": compiler.get("profile_sha256"),
                    "kind": "spec05-build-policy",
                }
        if not isinstance(profile, dict):
            raise ReleaseBindingError(
                f"release has no qualified automatic provider for {role!r}"
            )
        path = self._release_bound_path(
            str(profile.get("path") or ""),
            profile.get("sha256"),
            f"automatic stage input {role}",
        )
        expected_kind = str(profile.get("kind") or "")
        if not expected_kind:
            raise ReleaseBindingError(f"automatic stage input {role!r} has no kind")
        return self._add_local_file(role, expected_kind, path)

    def _promotion_chain(self) -> dict[str, object]:
        expected = [
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
        ]
        rows: list[dict[str, object]] = []
        previous_promotion: WorkflowV3Promotion | None = None
        previous_candidate: WorkflowV3Candidate | None = None
        for key in expected:
            matches = (
                self.db.query(
                    WorkflowV3StageRun,
                    WorkflowV3Candidate,
                    WorkflowV3Promotion,
                    WorkflowV3Evaluation,
                )
                .join(
                    WorkflowV3Promotion,
                    WorkflowV3Promotion.stage_run_id == WorkflowV3StageRun.id,
                )
                .join(
                    WorkflowV3Candidate,
                    WorkflowV3Candidate.id == WorkflowV3Promotion.candidate_id,
                )
                .join(
                    WorkflowV3Evaluation,
                    WorkflowV3Evaluation.id == WorkflowV3Promotion.evaluation_id,
                )
                .filter(
                    WorkflowV3StageRun.workflow_job_id == self.job.id,
                    WorkflowV3StageRun.stage_key == key,
                    WorkflowV3StageRun.machine_status == "succeeded",
                )
                .order_by(
                    WorkflowV3StageRun.generation.desc(),
                    WorkflowV3StageRun.attempt.desc(),
                )
                .all()
            )
            if not matches:
                raise ArtifactIntegrityError(f"promotion chain is missing stage {key!r}")
            row = matches[0]
            if any(
                other[0].generation == row[0].generation
                for other in matches[1:]
            ):
                raise ArtifactIntegrityError(
                    f"promotion chain has duplicate generation for stage {key!r}"
                )
            stage, candidate, promotion, evaluation = row
            gate_results = evaluation.load(evaluation.gate_results_json, {})
            findings = evaluation.load(evaluation.findings_json, [])
            if (
                stage.promotion_id != promotion.id
                or stage.promoted_candidate_id != candidate.id
                or stage.promoted_artifact_sha256 != candidate.sha256
                or candidate.status != "promoted"
                or candidate.sha256 != promotion.artifact_sha256
                or evaluation.decision != "passed"
                or evaluation.spec_passed is not True
                or stage.generation > self.job.current_generation
                or candidate.generation != stage.generation
                or evaluation.generation != stage.generation
                or candidate.review_resolution_sha256
                != stage.review_resolution_sha256
                or evaluation.review_resolution_sha256
                != stage.review_resolution_sha256
                or not isinstance(gate_results, dict)
                or any(value is not True for value in gate_results.values())
                or not isinstance(findings, list)
            ):
                raise ArtifactIntegrityError(
                    f"promotion chain record drifted for stage {key!r}"
                )
            expected_input_kind = (
                "frozen_source"
                if previous_promotion is None
                else "promoted_artifact"
            )
            expected_input_promotion_id = (
                None if previous_promotion is None else previous_promotion.id
            )
            expected_input_sha256 = (
                self.job.source_popo_sha256
                if previous_candidate is None
                else previous_candidate.sha256
            )
            if (
                stage.input_kind != expected_input_kind
                or stage.input_promotion_id != expected_input_promotion_id
                or stage.input_artifact_sha256 != expected_input_sha256
            ):
                raise ArtifactIntegrityError(
                    f"promotion chain input drifted for stage {key!r}"
                )
            evaluation_record = {
                "evaluation_id": str(evaluation.id),
                "candidate_id": str(candidate.id),
                "decision": "passed",
                "spec_passed": True,
                "policy_sha256": evaluation.policy_sha256,
                "evaluator_identity": evaluation.evaluator_identity,
                "evaluator_version": evaluation.evaluator_version,
                "gate_results": gate_results,
                "findings": findings,
            }
            promotion_record = {
                "promotion_id": str(promotion.id),
                "candidate_id": str(candidate.id),
                "evaluation_id": str(evaluation.id),
                "artifact_sha256": promotion.artifact_sha256,
                "promoted_by": promotion.promoted_by,
            }
            rows.append(
                {
                    "stage_key": key,
                    "stage_version": stage.stage_version,
                    "stage_run_id": str(stage.id),
                    "candidate_id": str(candidate.id),
                    "evaluation_id": str(evaluation.id),
                    "promotion_id": str(promotion.id),
                    "artifact_sha256": promotion.artifact_sha256,
                    "evaluation_record_sha256": sha256_json(evaluation_record),
                    "promotion_record_sha256": sha256_json(promotion_record),
                    "promotion_status": "promoted",
                    "evaluation_decision": evaluation.decision,
                }
            )
            previous_promotion = promotion
            previous_candidate = candidate
        return {
            "schema_version": "luceon.worker-v3-promotion-chain/v2",
            "job_id": self.job.public_id,
            "workflow_version": self.job.workflow_version,
            "release_manifest_sha256": self.bound.manifest_sha256,
            "source_popo_manifest_sha256": self.job.source_popo_sha256,
            "promotions": rows,
        }

    def _lineage_attestation(
        self,
        promotion_chain_sha256: str,
    ) -> dict[str, object]:
        source = self._source_evidence()
        blockers: list[str] = []
        for row in source.get("artifacts", []):
            if not isinstance(row, dict):
                blockers.append("source_evidence_invalid")
                continue
            ref = ArtifactRef(
                str(row.get("bucket") or ""),
                str(row.get("object") or ""),
                _require_sha256(row.get("sha256"), "lineage source SHA-256"),
                _require_size(row.get("size_bytes"), "lineage source size"),
            )
            try:
                self.store.stat(ref)
            except Exception:
                blockers.append(f"source_drift:{row.get('role')}")
        review_asset = source.get("review_asset")
        if not isinstance(review_asset, dict):
            blockers.append("review_asset_binding_missing")
        if blockers:
            raise ArtifactIntegrityError(
                "Stage 12 lineage is not closed: " + ", ".join(blockers)
            )
        return {
            "schema_version": "luceon.worker-v3-page-db-minio-lineage/v1",
            "job_id": self.job.public_id,
            "release_manifest_sha256": self.bound.manifest_sha256,
            "source_popo_manifest_sha256": self.job.source_popo_sha256,
            "promotion_chain_sha256": _require_sha256(
                promotion_chain_sha256,
                "promotion chain SHA-256",
            ),
            "consistent": True,
            "open_blockers": [],
        }

    def _legacy_fixture_release(self) -> bool:
        manifest = self.bound.verification.manifest
        return (
            manifest.get("model_policy") == {"mode": "none"}
            and (
                self.qualification_mode
                or not isinstance(
                    self.job.load(self.job.payload_json, {}).get(
                        "source_evidence"
                    ),
                    dict,
                )
            )
        )


class WorkflowV3RuntimeLookupError(WorkerV3RuntimeError):
    code = "runtime_state_invalid"


def verify_bound_release(
    release_root: str | os.PathLike[str],
    *,
    job: WorkflowV3Job,
    release: WorkflowV3SkillRelease,
    qualification: bool = False,
    actual_runtime_identity_sha256: str | None = None,
) -> BoundRelease:
    if qualification:
        require_qualification_environment()
    payload = job.load(job.payload_json, {})
    marker = payload.get("qualification") if isinstance(payload, dict) else None
    if qualification:
        if (
            release.status != "qualification"
            or not isinstance(marker, dict)
            or marker.get("enabled") is not True
            or payload.get("submission_path") != "qualification_cli"
        ):
            raise ReleaseBindingError(
                "qualification release/job binding is missing"
            )
    elif release.status != "registered" or isinstance(marker, dict):
        raise ReleaseBindingError(
            "ordinary execution cannot consume qualification state"
        )
    try:
        verification = verify_release_directory(
            release_root,
            allow_incomplete=qualification,
        )
    except ReleaseValidationError as exc:
        raise ReleaseBindingError(str(exc)) from exc
    manifest_sha256 = _sha256_file(verification.root / MANIFEST_NAME)
    manifest = verification.manifest
    try:
        runtime_identity = runtime_identity_for_manifest(manifest)
    except ValueError as exc:
        raise ReleaseBindingError(str(exc)) from exc
    expected = {
        "manifest_sha256": (manifest_sha256, job.skill_release_sha256, release.manifest_sha256),
        "release_version": (
            str(manifest.get("version") or ""),
            job.skill_release_version,
            release.release_version,
        ),
        "workflow_version": (job.workflow_version, release.workflow_version),
        "template_sha256": (
            str((manifest.get("template") or {}).get("tree_sha256") or ""),
            job.template_sha256,
            release.template_sha256,
        ),
        "runtime_identity_sha256": (
            runtime_identity,
            release.runtime_identity_sha256,
            *(
                (actual_runtime_identity_sha256,)
                if actual_runtime_identity_sha256 is not None
                else ()
            ),
        ),
    }
    for field, values in expected.items():
        if len(set(values)) != 1:
            raise ReleaseBindingError(f"installed release {field} does not match the job binding")
    return BoundRelease(
        verification=verification,
        manifest_sha256=manifest_sha256,
        runtime_identity_sha256=runtime_identity,
    )


def select_formal_invocation(
    release: ReleaseVerification,
    *,
    stage_key: str,
    execution_role: str,
    success_semantic: str,
    permission_envelope: str,
    qualification: bool = False,
) -> StageInvocation:
    manifest = release.manifest
    definitions = manifest["entrypoints"]["definitions"]
    matching = []
    for entrypoint_id in manifest["entrypoints"]["formal"]:
        definition = definitions[entrypoint_id]
        exit_semantics = definition.get("exit_semantics") or {}
        if (
            definition.get("stage") == stage_key
            and definition.get("execution_role") == execution_role
            and exit_semantics.get("0") == success_semantic
        ):
            matching.append(entrypoint_id)
    if len(matching) != 1:
        raise EntrypointProtocolError(
            f"stage {stage_key!r} must have exactly one formal {execution_role} "
            f"{success_semantic!r} entrypoint"
        )
    entrypoint_id = matching[0]
    try:
        definition = admit_entrypoint(
            release,
            entrypoint_id,
            requested_class="formal",
            requested_role=execution_role,
            qualification=qualification,
        )
    except ReleaseValidationError as exc:
        raise EntrypointProtocolError(str(exc)) from exc
    if definition.get("permission_envelope") != permission_envelope:
        raise EntrypointProtocolError(
            f"formal entrypoint permission envelope must be {permission_envelope!r}"
        )
    declared = definition.get("argv")
    if not isinstance(declared, list) or not declared:
        raise EntrypointProtocolError("formal entrypoint argv is missing")
    executable = (release.root / declared[0]).resolve()
    if release.root not in executable.parents or not executable.is_file():
        raise EntrypointProtocolError("formal entrypoint executable escapes the release")
    argv = (str(executable), *(str(token) for token in declared[1:]))
    return StageInvocation(
        entrypoint_id=entrypoint_id,
        definition=definition,
        argv=argv,
        timeout_seconds=int(definition["timeout_seconds"]),
    )


def _stage_input_ref(
    db: Session,
    job: WorkflowV3Job,
    stage: WorkflowV3StageRun,
) -> ArtifactRef:
    if stage.input_kind == "frozen_source":
        return ArtifactRef(
            bucket=job.source_popo_bucket,
            object_name=job.source_popo_object,
            sha256=job.source_popo_sha256,
            size_bytes=-1,
        )
    if stage.input_kind != "promoted_artifact" or not stage.input_promotion_id:
        raise ArtifactIntegrityError("stage input is neither frozen nor promoted")
    promotion = (
        db.query(WorkflowV3Promotion)
        .filter(WorkflowV3Promotion.id == stage.input_promotion_id)
        .one_or_none()
    )
    if not promotion or promotion.workflow_job_id != job.id:
        raise ArtifactIntegrityError("stage input promotion is missing or belongs to another job")
    candidate = (
        db.query(WorkflowV3Candidate)
        .filter(WorkflowV3Candidate.id == promotion.candidate_id)
        .one()
    )
    if (
        candidate.status != "promoted"
        or candidate.sha256 != promotion.artifact_sha256
        or stage.input_artifact_sha256 != promotion.artifact_sha256
    ):
        raise ArtifactIntegrityError("stage input identity does not match its promoted candidate")
    return ArtifactRef(
        bucket=candidate.bucket,
        object_name=candidate.object_name,
        sha256=candidate.sha256,
        size_bytes=candidate.size_bytes,
    )


def _load_candidate_manifest(
    path: Path,
    *,
    workdir: Path,
    public_id: str,
    stage_key: str,
    attempt: int,
    input_sha256: str,
    release_manifest_sha256: str,
) -> dict:
    payload = _read_json_object(path, "candidate manifest")
    required = {
        "schema_version",
        "job_id",
        "stage_key",
        "attempt",
        "input_sha256",
        "release_manifest_sha256",
        "artifact",
        "metrics",
    }
    if set(payload) != required:
        raise EntrypointProtocolError("candidate manifest has missing or unknown fields")
    expected = {
        "schema_version": CANDIDATE_PROTOCOL,
        "job_id": public_id,
        "stage_key": stage_key,
        "attempt": attempt,
        "input_sha256": input_sha256,
        "release_manifest_sha256": release_manifest_sha256,
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise EntrypointProtocolError("candidate manifest is not bound to this execution")
    artifact = payload.get("artifact")
    if not isinstance(artifact, dict) or set(artifact) != {
        "kind",
        "path",
        "sha256",
        "size_bytes",
    }:
        raise EntrypointProtocolError("candidate artifact descriptor is malformed")
    if not isinstance(artifact["kind"], str) or not artifact["kind"]:
        raise EntrypointProtocolError("candidate artifact kind is missing")
    relative = _safe_relative(artifact["path"], "candidate artifact path")
    artifact_path = (workdir / relative).resolve()
    if workdir not in artifact_path.parents or artifact_path.is_symlink() or not artifact_path.is_file():
        raise EntrypointProtocolError("candidate artifact is missing or outside the attempt workspace")
    declared_sha = _require_sha256(artifact["sha256"], "candidate artifact SHA-256")
    if (
        not isinstance(artifact["size_bytes"], int)
        or isinstance(artifact["size_bytes"], bool)
        or artifact["size_bytes"] < 0
    ):
        raise EntrypointProtocolError("candidate artifact size is invalid")
    if _sha256_file(artifact_path) != declared_sha:
        raise ArtifactIntegrityError("candidate artifact hash drifted from its manifest")
    if artifact_path.stat().st_size != artifact["size_bytes"]:
        raise ArtifactIntegrityError("candidate artifact size drifted from its manifest")
    if not isinstance(payload["metrics"], dict):
        raise EntrypointProtocolError("candidate metrics must be an object")
    return payload


def _read_json_object(path: Path, label: str) -> dict:
    if path.is_symlink() or not path.is_file():
        raise EntrypointProtocolError(f"{label} is missing")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EntrypointProtocolError(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise EntrypointProtocolError(f"{label} must be an object")
    return payload


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def _artifact_ref_for_file(path: Path, bucket: str, object_name: str) -> ArtifactRef:
    if path.is_symlink() or not path.is_file():
        raise ArtifactIntegrityError(f"artifact object is missing: {bucket}/{object_name}")
    return ArtifactRef(
        bucket=bucket,
        object_name=object_name,
        sha256=_sha256_file(path),
        size_bytes=path.stat().st_size,
    )


def _assert_artifact_identity(expected: ArtifactRef, actual: ArtifactRef) -> None:
    if (
        expected.bucket != actual.bucket
        or expected.object_name != actual.object_name
        or expected.sha256 != actual.sha256
        or (expected.size_bytes >= 0 and expected.size_bytes != actual.size_bytes)
    ):
        raise ArtifactIntegrityError("artifact bytes do not match the frozen control-plane identity")


def _safe_relative(value: str, label: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value or value.startswith("/"):
        raise ArtifactIntegrityError(f"{label} is not a safe relative path")
    parsed = PurePosixPath(value)
    if any(part in {"", ".", ".."} for part in parsed.parts) or str(parsed) != value:
        raise ArtifactIntegrityError(f"{label} is not normalized")
    return Path(*parsed.parts)


def normalize_candidate_prefix(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("candidate prefix must be a string")
    normalized = value.strip()
    if normalized.endswith("/"):
        normalized = normalized[:-1]
    if (
        not normalized
        or normalized.startswith("/")
        or "\\" in normalized
        or "\x00" in normalized
        or len(normalized.encode("utf-8")) > 512
        or any(
            ord(character) < 32 or ord(character) == 127
            for character in normalized
        )
    ):
        raise ValueError("candidate prefix is unsafe")
    parsed = PurePosixPath(normalized)
    if (
        str(parsed) != normalized
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise ValueError("candidate prefix is unsafe")
    return normalized


def _require_sha256(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in _SHA256_CHARS for char in value)
    ):
        raise ArtifactIntegrityError(f"{label} must be a lowercase SHA-256")
    return value


def _require_size(value: object, label: str) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
    ):
        raise ArtifactIntegrityError(f"{label} must be a nonnegative integer")
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _iso(value: datetime | None) -> str:
    if value is None:
        return _utc_now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _allowed_choices(value: object) -> dict[str, tuple[str, ...]]:
    """Extract explicit task choices without allowing the model to invent IDs."""

    result: dict[str, tuple[str, ...]] = {}
    if not isinstance(value, Mapping):
        return result
    for key in ("tasks", "decisions", "review_tasks"):
        rows = value.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            task_id = str(row.get("task_id") or row.get("id") or "")
            options = row.get("options") or row.get("allowed_options")
            if not task_id or not isinstance(options, list):
                continue
            option_ids = tuple(
                str(item.get("option_id") or item.get("id") or "")
                for item in options
                if isinstance(item, Mapping)
                and str(item.get("option_id") or item.get("id") or "")
            )
            if option_ids:
                result[task_id] = option_ids
    return result


def _review_allowed_choices(
    prompt_id: str,
    evidence: object,
) -> dict[str, tuple[str, ...]]:
    """Bind candidate-local semantic choices before a model call can succeed."""

    if prompt_id != "worker-v3.spec04b-semantic-review":
        return _allowed_choices(evidence)
    if not isinstance(evidence, Mapping):
        raise ArtifactIntegrityError("Spec 04-B model evidence must be an object")
    candidates = evidence.get("candidates")
    semantic_roles = evidence.get("semantic_role_choices")
    if (
        not isinstance(candidates, list)
        or not isinstance(semantic_roles, list)
        or not semantic_roles
        or any(not isinstance(role, str) or not role for role in semantic_roles)
    ):
        raise ArtifactIntegrityError(
            "Spec 04-B model evidence lacks bounded candidates or semantic roles"
        )
    non_plain_roles = tuple(
        role for role in semantic_roles if role != "plain_body"
    )
    if not non_plain_roles:
        raise ArtifactIntegrityError(
            "Spec 04-B model evidence has no bounded non-plain semantic roles"
        )
    option_protocol = evidence.get("option_protocol")
    role_count = len(non_plain_roles)
    expected_protocol = {
        "schema_version": (
            "luceon.worker-v3-spec04b-total-option-index/v1"
        ),
        "plain_body_index": 0,
        "standalone_label_role_offset": 1,
        "teaching_group_role_offset": 1 + role_count,
        "option_count": 1 + (2 * role_count),
        "unavailable_teaching_resolution": (
            "standalone_label_then_plain_body"
        ),
    }
    if (
        not isinstance(option_protocol, Mapping)
        or dict(option_protocol) != expected_protocol
    ):
        raise ArtifactIntegrityError(
            "Spec 04-B model evidence has a drifted option protocol"
        )
    frozen_options = tuple(
        str(index) for index in range(expected_protocol["option_count"])
    )
    result: dict[str, tuple[str, ...]] = {}
    for candidate_index, candidate in enumerate(candidates):
        if (
            not isinstance(candidate, Mapping)
            or candidate.get("candidate_index") != candidate_index
        ):
            raise ArtifactIntegrityError(
                "Spec 04-B candidates must be complete and ordered"
            )
        dispositions = candidate.get("allowed_dispositions")
        body_options = candidate.get("body_options")
        expected_dispositions = (
            ["plain_body", "standalone_label", "teaching_group"]
            if isinstance(body_options, list) and body_options
            else ["plain_body", "standalone_label"]
        )
        if (
            not isinstance(body_options, list)
            or dispositions != expected_dispositions
        ):
            raise ArtifactIntegrityError(
                f"Spec 04-B candidate {candidate_index} has invalid choices"
            )
        result[f"candidate:{candidate_index}"] = frozen_options
    return result


def _ordinary_model_budget(
    policy: Mapping[str, object],
    request_parameters: Mapping[str, object],
) -> dict[str, int | float]:
    max_calls = _positive_policy_int(policy, "max_stage_calls")
    if max_calls != 1:
        raise ReleaseBindingError(
            "ordinary model policy must bind exactly one call per stage attempt"
        )
    max_input_tokens = _positive_policy_int(
        policy,
        "max_stage_input_tokens",
    )
    max_output_tokens = _positive_policy_int(
        policy,
        "max_stage_output_tokens",
    )
    max_stage_seconds = _positive_policy_number(
        policy,
        "max_stage_seconds",
    )
    max_request_bytes = _positive_policy_int(
        policy,
        "max_stage_request_bytes",
    )
    max_output_json_bytes_per_token = _positive_policy_int(
        policy,
        "max_output_json_bytes_per_token",
    )
    if max_output_json_bytes_per_token > 64:
        raise ReleaseBindingError(
            "ordinary model max_output_json_bytes_per_token exceeds the fail-closed limit"
        )
    timeout_seconds = _positive_policy_number(policy, "timeout_seconds")
    if timeout_seconds > min(900.0, max_stage_seconds):
        raise ReleaseBindingError(
            "ordinary model timeout exceeds its release-bound stage budget"
        )
    call_output_tokens = request_parameters.get("max_output_tokens")
    if (
        not isinstance(call_output_tokens, int)
        or isinstance(call_output_tokens, bool)
        or call_output_tokens < 1
        or call_output_tokens > max_output_tokens
    ):
        raise ReleaseBindingError(
            "ordinary model max_output_tokens is missing or exceeds its stage budget"
        )
    return {
        "max_stage_calls": max_calls,
        "max_stage_input_tokens": max_input_tokens,
        "max_stage_output_tokens": max_output_tokens,
        "max_stage_request_bytes": max_request_bytes,
        "max_output_json_bytes_per_token": max_output_json_bytes_per_token,
        "max_stage_seconds": max_stage_seconds,
        "timeout_seconds": timeout_seconds,
    }


def _enforce_model_request_budget(
    call: ReleaseBoundLlmCall,
    budget: Mapping[str, int | float],
) -> None:
    request = {
        "call_id": call.call_id,
        "binding": {
            "release_id": call.release_id,
            "release_sha256": call.release_sha256,
            "stage_key": call.stage_key,
            "prompt_id": call.prompt_id,
            "prompt_version": call.prompt_version,
            "prompt_sha256": call.prompt_sha256,
            "schema_id": call.schema_id,
            "schema_version": call.schema_version,
            "schema_sha256": call.schema_sha256,
            "input_sha256": call.input_sha256,
        },
        "provider": call.provider,
        "model": call.model,
        "parameters": dict(call.request_parameters),
        "prompt": call.prompt_text,
        "input": call.input_evidence,
        "output_schema": call.output_schema,
    }
    request_bytes = len(canonical_json_bytes(request))
    max_request_bytes = int(budget["max_stage_request_bytes"])
    if request_bytes > max_request_bytes:
        raise LlmGatewayError(
            "model_request_budget_exceeded",
            "ordinary model request exceeds its release-bound byte budget before transmission",
            audit={
                "status": "failed",
                "stage_key": call.stage_key,
                "request_sha256": sha256_json(request),
                "request_bytes": request_bytes,
                "max_stage_request_bytes": max_request_bytes,
                "error_code": "model_request_budget_exceeded",
                "provider_call_started": False,
            },
        )
    capacity = (
        call.input_evidence.get("capacity")
        if isinstance(call.input_evidence, Mapping)
        else None
    )
    if not isinstance(capacity, Mapping):
        return
    minimum_response_bytes = capacity.get("minimum_response_bytes")
    if (
        not isinstance(minimum_response_bytes, int)
        or isinstance(minimum_response_bytes, bool)
        or minimum_response_bytes < 1
    ):
        raise LlmGatewayError(
            "model_capacity_evidence_invalid",
            "bounded model task capacity evidence is missing or invalid",
            audit={
                "status": "failed",
                "stage_key": call.stage_key,
                "request_sha256": sha256_json(request),
                "error_code": "model_capacity_evidence_invalid",
                "provider_call_started": False,
            },
        )
    maximum_response_bytes = (
        int(call.request_parameters["max_output_tokens"])
        * int(budget["max_output_json_bytes_per_token"])
    )
    if minimum_response_bytes > maximum_response_bytes:
        raise LlmGatewayError(
            "model_minimum_output_budget_exceeded",
            "minimum schema-complete response exceeds the release-bound output capacity before transmission",
            audit={
                "status": "failed",
                "stage_key": call.stage_key,
                "request_sha256": sha256_json(request),
                "minimum_response_bytes": minimum_response_bytes,
                "maximum_response_bytes": maximum_response_bytes,
                "max_output_tokens": int(
                    call.request_parameters["max_output_tokens"]
                ),
                "max_output_json_bytes_per_token": int(
                    budget["max_output_json_bytes_per_token"]
                ),
                "error_code": "model_minimum_output_budget_exceeded",
                "provider_call_started": False,
            },
        )
    declared_maximum_response_bytes = capacity.get(
        "maximum_response_bytes"
    )
    if declared_maximum_response_bytes is None:
        return
    if (
        not isinstance(declared_maximum_response_bytes, int)
        or isinstance(declared_maximum_response_bytes, bool)
        or declared_maximum_response_bytes < minimum_response_bytes
    ):
        raise LlmGatewayError(
            "model_capacity_evidence_invalid",
            "bounded model task maximum response capacity is invalid",
            audit={
                "status": "failed",
                "stage_key": call.stage_key,
                "request_sha256": sha256_json(request),
                "error_code": "model_capacity_evidence_invalid",
                "provider_call_started": False,
            },
        )
    if declared_maximum_response_bytes > maximum_response_bytes:
        raise LlmGatewayError(
            "model_maximum_output_budget_exceeded",
            "worst-case schema-complete response exceeds the release-bound output capacity before transmission",
            audit={
                "status": "failed",
                "stage_key": call.stage_key,
                "request_sha256": sha256_json(request),
                "maximum_response_bytes": declared_maximum_response_bytes,
                "maximum_output_bytes": maximum_response_bytes,
                "max_output_tokens": int(
                    call.request_parameters["max_output_tokens"]
                ),
                "max_output_json_bytes_per_token": int(
                    budget["max_output_json_bytes_per_token"]
                ),
                "error_code": "model_maximum_output_budget_exceeded",
                "provider_call_started": False,
            },
        )


def _enforce_model_result_budget(
    result: LlmCallResult,
    budget: Mapping[str, int | float],
) -> None:
    usage = result.audit.get("usage")
    if not isinstance(usage, Mapping):
        raise LlmGatewayError(
            "model_budget_unattributable",
            "ordinary model call has no attributable token usage",
            audit=result.audit,
        )
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    if (
        not isinstance(input_tokens, int)
        or isinstance(input_tokens, bool)
        or input_tokens < 0
        or not isinstance(output_tokens, int)
        or isinstance(output_tokens, bool)
        or output_tokens < 0
    ):
        raise LlmGatewayError(
            "model_budget_unattributable",
            "ordinary model usage is not a nonnegative token count",
            audit=result.audit,
        )
    if input_tokens > int(budget["max_stage_input_tokens"]):
        raise LlmGatewayError(
            "model_input_budget_exceeded",
            "ordinary model call exceeded its release-bound input-token budget",
            audit=result.audit,
        )
    if output_tokens > int(budget["max_stage_output_tokens"]):
        raise LlmGatewayError(
            "model_output_budget_exceeded",
            "ordinary model call exceeded its release-bound output-token budget",
            audit=result.audit,
        )
    latency_ms = result.audit.get("latency_ms")
    if (
        not isinstance(latency_ms, int)
        or isinstance(latency_ms, bool)
        or latency_ms < 0
    ):
        raise LlmGatewayError(
            "model_budget_unattributable",
            "ordinary model call has no attributable latency",
            audit=result.audit,
        )
    if latency_ms > round(float(budget["max_stage_seconds"]) * 1000):
        raise LlmGatewayError(
            "model_time_budget_exceeded",
            "ordinary model call exceeded its release-bound time budget",
            audit=result.audit,
        )


def _execute_model_call_with_heartbeat(
    call: ReleaseBoundLlmCall,
    transport,
    *,
    heartbeat: Callable[[], None],
    heartbeat_seconds: float = 5.0,
) -> LlmCallResult:
    interval = min(
        max(0.01, float(heartbeat_seconds)),
        max(0.01, float(call.timeout_seconds) / 2),
    )
    heartbeat()
    stopped = threading.Event()
    heartbeat_errors: list[Exception] = []

    def pulse() -> None:
        while not stopped.wait(interval):
            try:
                heartbeat()
            except Exception as exc:
                heartbeat_errors.append(exc)
                stopped.set()

    thread = threading.Thread(
        target=pulse,
        name=f"worker-v3-model-heartbeat-{call.call_id[:12]}",
        daemon=True,
    )
    thread.start()
    try:
        result = execute_bounded_call(call, transport)
    finally:
        stopped.set()
        thread.join(timeout=interval + 1.0)
    if heartbeat_errors:
        raise ModelCallHeartbeatFailed(
            "model call completed after its execution heartbeat was rejected"
        ) from heartbeat_errors[0]
    heartbeat()
    return result


def _positive_policy_int(policy: Mapping[str, object], key: str) -> int:
    value = policy.get(key)
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 1
    ):
        raise ReleaseBindingError(
            f"release model policy {key} must be a positive integer"
        )
    return value


def _positive_policy_number(
    policy: Mapping[str, object],
    key: str,
) -> float:
    value = policy.get(key)
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or float(value) <= 0
    ):
        raise ReleaseBindingError(
            f"release model policy {key} must be positive"
        )
    return float(value)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _idempotency_key(*parts: str) -> str:
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _bounded_model_call_id(
    *,
    job_scope_id: str,
    stage_key: str,
    attempt: int,
    prompt_sha256: str,
    input_sha256: str,
) -> str:
    """Bind replay identity to the stable job scope, not its random public ID."""

    return _idempotency_key(
        "bounded-llm",
        job_scope_id,
        stage_key,
        str(attempt),
        prompt_sha256,
        input_sha256,
    )


def _tail_text(path: Path, limit: int = 32_768) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - limit))
            return handle.read(limit).decode("utf-8", errors="replace")
    except OSError:
        return ""


def _terminate_process(process: subprocess.Popen) -> None:
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=2)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass


def runtime_result_json(result: Mapping[str, object]) -> str:
    return json.dumps(dict(result), ensure_ascii=False, sort_keys=True)
