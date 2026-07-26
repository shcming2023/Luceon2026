from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.workflow_v3.executor import (
    BoundRelease,
    DirectoryArtifactStore,
    normalize_candidate_prefix,
    verify_bound_release,
)
from app.workflow_v3.minio_artifacts import (
    MinioCandidateArtifactStore,
    MinioFormalArtifactStore,
    MinioReadonlyArtifactStore,
)
from app.workflow_v3.models import WorkflowV3Job, WorkflowV3SkillRelease
from app.workflow_v3.minio_role_policy import (
    MINIO_ROLES,
    credential_fingerprint,
    parse_credential_fingerprints,
)
from app.workflow_v3.operations import record_worker_heartbeat


class WorkflowV3RuntimeConfigurationError(RuntimeError):
    """Worker V3 runtime capabilities are absent or unsafe."""


class WorkflowV3RuntimeBindingError(WorkflowV3RuntimeConfigurationError):
    """The measured ordinary runtime is not the release-bound runtime."""


@dataclass(frozen=True)
class RuntimeAttestation:
    identity: Mapping[str, Any]
    runtime_identity_sha256: str
    image_digest: str
    control_plane_tree_sha256: str


def _identity_bytes(identity: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            dict(identity),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def load_actual_runtime_attestation(
    *,
    identity_script: Path | None = None,
) -> RuntimeAttestation:
    script = identity_script or (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "workflow_v3_runtime_identity.py"
    )
    result = subprocess.run(
        [sys.executable, str(script), "--check"],
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-2000:]
        raise WorkflowV3RuntimeBindingError(
            f"ordinary runtime attestation failed closed: {detail}"
        )
    try:
        identity = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise WorkflowV3RuntimeBindingError(
            "ordinary runtime attestation did not return JSON"
        ) from exc
    if not isinstance(identity, dict):
        raise WorkflowV3RuntimeBindingError(
            "ordinary runtime attestation must be an object"
        )
    validation = identity.get("validation")
    if (
        not isinstance(validation, dict)
        or validation.get("status") != "passed"
        or validation.get("errors") != []
    ):
        raise WorkflowV3RuntimeBindingError(
            "ordinary runtime attestation validation did not pass"
        )
    image_digest = str(identity.get("image_digest") or "")
    control_plane = identity.get("control_plane")
    tree_sha256 = (
        str(control_plane.get("actual_tree_sha256") or "")
        if isinstance(control_plane, dict)
        else ""
    )
    if (
        len(image_digest) != 71
        or not image_digest.startswith("sha256:")
        or any(char not in "0123456789abcdef" for char in image_digest[7:])
        or len(tree_sha256) != 64
        or any(char not in "0123456789abcdef" for char in tree_sha256)
        or not isinstance(control_plane, dict)
        or control_plane.get("matches") is not True
    ):
        raise WorkflowV3RuntimeBindingError(
            "ordinary runtime image or control-plane measurement is invalid"
        )
    return RuntimeAttestation(
        identity=identity,
        runtime_identity_sha256=hashlib.sha256(
            _identity_bytes(identity)
        ).hexdigest(),
        image_digest=image_digest,
        control_plane_tree_sha256=tree_sha256,
    )


class RuntimeBindingGuard:
    """Bind live process code and its image to one immutable skill release."""

    def __init__(self, attestation: RuntimeAttestation):
        self.attestation = attestation

    @property
    def runtime_identity_sha256(self) -> str:
        return self.attestation.runtime_identity_sha256

    def assert_bound(
        self,
        release_root: str | os.PathLike[str],
        *,
        job: WorkflowV3Job,
        release: WorkflowV3SkillRelease,
        qualification: bool = False,
    ) -> BoundRelease:
        bound = verify_bound_release(
            release_root,
            job=job,
            release=release,
            qualification=qualification,
            actual_runtime_identity_sha256=self.runtime_identity_sha256,
        )
        runtime = bound.verification.manifest.get("runtime")
        system_tools = (
            runtime.get("system_tools")
            if isinstance(runtime, dict)
            else None
        )
        identity_path = (
            system_tools.get("identity")
            if isinstance(system_tools, dict)
            else None
        )
        if (
            not isinstance(identity_path, str)
            or not identity_path
            or PurePosixPath(identity_path).is_absolute()
            or any(
                part in {"", ".", ".."}
                for part in PurePosixPath(identity_path).parts
            )
        ):
            raise WorkflowV3RuntimeBindingError(
                "release has no safe ordinary runtime identity path"
            )
        packaged_identity = bound.verification.root / identity_path
        try:
            raw_identity = packaged_identity.read_bytes()
            declared_identity = json.loads(raw_identity)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorkflowV3RuntimeBindingError(
                "release ordinary runtime identity is unreadable"
            ) from exc
        if (
            raw_identity != _identity_bytes(self.attestation.identity)
            or declared_identity != dict(self.attestation.identity)
            or hashlib.sha256(raw_identity).hexdigest()
            != self.runtime_identity_sha256
        ):
            raise WorkflowV3RuntimeBindingError(
                "live runtime attestation differs from the release identity"
            )
        expected_image_digest = (
            str(runtime.get("container_image_digest") or "")
            if isinstance(runtime, dict)
            else ""
        )
        if expected_image_digest != self.attestation.image_digest:
            raise WorkflowV3RuntimeBindingError(
                "live container image digest differs from the release binding"
            )
        return bound


def load_runtime_binding_guard() -> RuntimeBindingGuard:
    return RuntimeBindingGuard(load_actual_runtime_attestation())


_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_DEFAULT_READABLE_BUCKETS = (
    "eduassets-input",
    "eduassets-mineru",
    "eduassets-minerupopo",
    "worker-v3-candidates",
)


def artifact_backend_mode() -> str:
    value = os.getenv("WORKFLOW_V3_ARTIFACT_BACKEND", "").strip().lower()
    if value not in {"minio", "directory"}:
        raise WorkflowV3RuntimeConfigurationError(
            "WORKFLOW_V3_ARTIFACT_BACKEND must be explicitly set to minio or directory"
        )
    if value == "directory" and not _directory_mode_admitted():
        raise WorkflowV3RuntimeConfigurationError(
            "directory artifacts are admitted only in development/test with "
            "WORKFLOW_V3_ALLOW_DIRECTORY_ARTIFACTS=true"
        )
    return value


def producer_artifact_store():
    """Return only the capabilities needed by the Producer.

    Production gets exact-hash reads plus candidate-prefix writes.  It never
    receives a formal-output writer, delete, list, or promotion capability.
    """

    mode = artifact_backend_mode()
    if mode == "directory":
        return DirectoryArtifactStore(_required_path("WORKFLOW_V3_ARTIFACT_ROOT"))
    client = _minio_client("producer")
    candidate_bucket = _required_text(
        "WORKFLOW_V3_CANDIDATE_BUCKET",
        default="worker-v3-candidates",
    )
    try:
        candidate_prefix = (
            normalize_candidate_prefix(
                _required_text(
                    "WORKFLOW_V3_CANDIDATE_PREFIX",
                    default="v3/candidates",
                )
            )
            + "/"
        )
    except ValueError as exc:
        raise WorkflowV3RuntimeConfigurationError(
            "WORKFLOW_V3_CANDIDATE_PREFIX is unsafe"
        ) from exc
    return MinioCandidateArtifactStore(
        client,
        readable_buckets=_readable_buckets(candidate_bucket),
        candidate_scopes={candidate_bucket: {candidate_prefix}},
    )


def readonly_artifact_store(role: str = "evaluator"):
    """Return a store with no write surface for independent evaluators."""

    if role not in {"evaluator", "promoter"}:
        raise WorkflowV3RuntimeConfigurationError(
            "read-only artifact role must be evaluator or promoter"
        )
    mode = artifact_backend_mode()
    if mode == "directory":
        return DirectoryArtifactStore(_required_path("WORKFLOW_V3_ARTIFACT_ROOT"))
    candidate_bucket = _required_text(
        "WORKFLOW_V3_CANDIDATE_BUCKET",
        default="worker-v3-candidates",
    )
    return MinioReadonlyArtifactStore(
        _minio_client(role),
        readable_buckets={candidate_bucket},
    )


@dataclass(frozen=True)
class ProjectorArtifactStores:
    candidate_reader: object
    formal_writer: object
    formal_bucket: str
    formal_prefix: str


def projector_artifact_stores() -> ProjectorArtifactStores:
    """Return separate candidate-read and formal-write capabilities.

    The projector cannot write candidates, and the formal writer cannot write
    outside its configured immutable formal prefix.
    """

    mode = artifact_backend_mode()
    if mode != "minio":
        raise WorkflowV3RuntimeConfigurationError(
            "formal projection requires the MinIO artifact backend"
        )
    client = _minio_client("projector")
    candidate_bucket = _required_text(
        "WORKFLOW_V3_CANDIDATE_BUCKET",
        default="worker-v3-candidates",
    )
    formal_bucket = _required_text(
        "WORKFLOW_V3_FORMAL_BUCKET",
        default="eduassets-elegantbook",
    )
    formal_prefix = _required_prefix(
        "WORKFLOW_V3_FORMAL_PREFIX",
        default="elegantbook/v3",
    )
    return ProjectorArtifactStores(
        candidate_reader=MinioReadonlyArtifactStore(
            client,
            readable_buckets={candidate_bucket},
        ),
        formal_writer=MinioFormalArtifactStore(
            client,
            readable_buckets={formal_bucket},
            formal_scopes={formal_bucket: {formal_prefix}},
        ),
        formal_bucket=formal_bucket,
        formal_prefix=formal_prefix.rstrip("/"),
    )


class WorkerHeartbeatLoop:
    """Persist one role heartbeat at most every configured interval.

    Every write opens its own SQLAlchemy session, so a long subprocess or
    projection never shares a Session across threads.
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        worker_id: str,
        role: str,
        interval_seconds: float = 5.0,
        runtime_identity_sha256: str = "",
    ) -> None:
        self._session_factory = session_factory
        self.worker_id = worker_id
        self.role = role
        self.interval_seconds = min(10.0, max(1.0, float(interval_seconds)))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._state: dict[str, object] = {
            "status": "starting",
            "runtime_identity_sha256": runtime_identity_sha256,
            "current_job_id": "",
            "current_stage_key": "",
            "last_error": "",
            "metrics": {},
        }

    def start(self) -> None:
        with self._lock:
            if self._thread is not None:
                return
            thread = threading.Thread(
                target=self._run,
                name=f"workflow-v3-{self.role}-heartbeat",
                daemon=True,
            )
            self._thread = thread
        try:
            # ``_write`` snapshots state through the same lifecycle lock, so
            # the initial durable write must happen after releasing it.
            self._write()
        except Exception:
            with self._lock:
                self._thread = None
            raise
        thread.start()

    def update(
        self,
        *,
        status: str,
        runtime_identity_sha256: str | None = None,
        current_job_id: str = "",
        current_stage_key: str = "",
        last_error: str = "",
        metrics: Mapping[str, object] | None = None,
        write_now: bool = True,
    ) -> None:
        with self._lock:
            current_runtime = str(
                self._state.get("runtime_identity_sha256") or ""
            )
            self._state = {
                "status": status,
                "runtime_identity_sha256": (
                    current_runtime
                    if runtime_identity_sha256 is None
                    else runtime_identity_sha256
                ),
                "current_job_id": current_job_id,
                "current_stage_key": current_stage_key,
                "last_error": last_error,
                "metrics": dict(metrics or {}),
            }
        if write_now:
            self._write()

    def pulse(self) -> None:
        self._write()

    def stop(self, *, status: str = "stopped", last_error: str = "") -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=self.interval_seconds + 1.0)
        self.update(status=status, last_error=last_error, write_now=True)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self._write()
            except Exception:
                # A transient database outage must not permanently kill the
                # liveness loop; the next bounded interval retries.
                continue

    def _write(self) -> None:
        with self._lock:
            state = dict(self._state)
        db = self._session_factory()
        try:
            record_worker_heartbeat(
                db,
                worker_id=self.worker_id,
                role=self.role,
                status=str(state["status"]),
                runtime_identity_sha256=str(state["runtime_identity_sha256"]),
                current_job_id=str(state["current_job_id"]),
                current_stage_key=str(state["current_stage_key"]),
                last_error=str(state["last_error"]),
                metrics=state["metrics"] if isinstance(state["metrics"], Mapping) else {},
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


def _directory_mode_admitted() -> bool:
    environment = os.getenv("LUCEON_ENVIRONMENT", "development").strip().lower()
    allowed = (
        os.getenv("WORKFLOW_V3_ALLOW_DIRECTORY_ARTIFACTS", "")
        .strip()
        .lower()
        in _TRUE_VALUES
    )
    return environment in {"development", "test"} and allowed


def _readable_buckets(candidate_bucket: str) -> set[str]:
    raw = os.getenv("WORKFLOW_V3_READABLE_BUCKETS", "").strip()
    values = {
        item.strip()
        for item in raw.split(",")
        if item.strip()
    }
    values.update(_DEFAULT_READABLE_BUCKETS)
    values.add(candidate_bucket)
    return values


def _required_text(name: str, *, default: str = "") -> str:
    value = os.getenv(name, default).strip()
    if not value:
        raise WorkflowV3RuntimeConfigurationError(f"{name} is required")
    return value


def _required_prefix(name: str, *, default: str) -> str:
    value = _required_text(name, default=default).strip("/")
    if not value:
        raise WorkflowV3RuntimeConfigurationError(f"{name} cannot be the bucket root")
    return f"{value}/"


def _required_path(name: str) -> Path:
    return Path(_required_text(name))


class _ConditionalMinioClient:
    """Pinned MinIO SDK adapter exposing only exact reads and create-only PUT.

    minio-py 7.2.15 does not expose PutObject request preconditions on its
    public method. The Worker V3 runtime pins that exact SDK and uses its
    single-request helper so ``If-None-Match: *`` is signed by the SDK.
    """

    def __init__(self, client: object):
        if not callable(getattr(client, "_put_object", None)):
            raise WorkflowV3RuntimeConfigurationError(
                "pinned MinIO client lacks conditional PutObject support"
            )
        self._client = client

    def stat_object(self, bucket_name: str, object_name: str):
        return self._client.stat_object(bucket_name, object_name)

    def get_object(self, bucket_name: str, object_name: str):
        return self._client.get_object(bucket_name, object_name)

    def put_object_if_absent(
        self,
        bucket_name: str,
        object_name: str,
        data,
        length: int,
        content_type: str = "application/octet-stream",
        metadata: Mapping[str, str] | None = None,
    ):
        if not isinstance(length, int) or isinstance(length, bool) or length < 0:
            raise ValueError("conditional PutObject length must be non-negative")
        payload = data.read(length + 1)
        if not isinstance(payload, bytes) or len(payload) != length:
            raise IOError("conditional PutObject source length changed")
        headers: dict[str, str] = {
            "Content-Type": content_type,
            "If-None-Match": "*",
        }
        for key, value in (metadata or {}).items():
            normalized = str(key).strip()
            if not normalized or any(
                character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-"
                for character in normalized
            ):
                raise ValueError("conditional PutObject metadata key is unsafe")
            headers[f"X-Amz-Meta-{normalized}"] = str(value)
        return self._client._put_object(
            bucket_name,
            object_name,
            payload,
            headers,
        )


def _minio_client(role: str):
    if role not in MINIO_ROLES:
        raise WorkflowV3RuntimeConfigurationError(
            f"unknown Worker V3 MinIO role: {role!r}"
        )
    prefix = f"WORKFLOW_V3_{role.upper()}_MINIO"
    endpoint = _required_text(f"{prefix}_ENDPOINT")
    access_key = _required_text(f"{prefix}_ACCESS_KEY")
    secret_key = _required_text(f"{prefix}_SECRET_KEY")
    region = _required_text(f"{prefix}_REGION", default="us-east-1")
    secure = _env_bool(f"{prefix}_SECURE", default=False)
    endpoint, secure = _parse_minio_endpoint(endpoint, secure=secure)
    _validate_minio_credential_boundary(
        role=role,
        access_key=access_key,
        secret_key=secret_key,
    )

    from minio import Minio

    return _ConditionalMinioClient(
        Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
            region=region,
        )
    )


def _validate_minio_credential_boundary(
    *,
    role: str,
    access_key: str,
    secret_key: str,
) -> None:
    environment = os.getenv("LUCEON_ENVIRONMENT", "development").strip().lower()
    production_like = environment in {
        "production",
        "prod",
        "rc",
        "release-candidate",
        "stable",
    }
    global_access_key = os.getenv("MINIO_ACCESS_KEY", "").strip()
    global_secret_key = os.getenv("MINIO_SECRET_KEY", "").strip()
    if production_like and (
        (global_access_key and access_key == global_access_key)
        or (global_secret_key and secret_key == global_secret_key)
    ):
        raise WorkflowV3RuntimeConfigurationError(
            f"{role} MinIO credentials reuse a global credential"
        )
    raw_fingerprints = os.getenv(
        "WORKFLOW_V3_MINIO_CREDENTIAL_FINGERPRINTS",
        "",
    ).strip()
    if not raw_fingerprints:
        if production_like:
            raise WorkflowV3RuntimeConfigurationError(
                "WORKFLOW_V3_MINIO_CREDENTIAL_FINGERPRINTS is required for "
                "production/RC"
            )
        return
    try:
        fingerprints = parse_credential_fingerprints(raw_fingerprints)
    except ValueError as exc:
        raise WorkflowV3RuntimeConfigurationError(str(exc)) from exc
    if fingerprints[role] != credential_fingerprint(access_key, secret_key):
        raise WorkflowV3RuntimeConfigurationError(
            f"{role} MinIO credential does not match the distinct-role matrix"
        )


def _parse_minio_endpoint(value: str, *, secure: bool) -> tuple[str, bool]:
    if "://" not in value:
        if "/" in value or "@" in value:
            raise WorkflowV3RuntimeConfigurationError(
                "Worker V3 MinIO endpoint must be host:port"
            )
        return value, secure
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise WorkflowV3RuntimeConfigurationError(
            "Worker V3 MinIO endpoint is unsafe"
        )
    return parsed.netloc, parsed.scheme == "https"


def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized not in _TRUE_VALUES | {"0", "false", "no", "off"}:
        raise WorkflowV3RuntimeConfigurationError(f"{name} must be a boolean")
    return normalized in _TRUE_VALUES
