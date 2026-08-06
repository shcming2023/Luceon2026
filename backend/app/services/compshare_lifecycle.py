from __future__ import annotations

import hashlib
import json
import os
import secrets
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from app.services.compshare_credentials import CompShareCredentialError, load_runtime_credentials


TERMINAL_STATES = {"Running", "Stopped"}
TRANSITION_STATES = {"Starting", "Initializing", "Stopping", "Rebooting", "Install"}
KNOWN_STATES = TERMINAL_STATES | TRANSITION_STATES
OPERATION_LOCK_ERROR = "InstanceOperationInProgress"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_sha(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class CompShareLifecycleError(RuntimeError):
    def __init__(self, code: str, message: str, *, evidence: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.evidence = evidence or {}


@dataclass(frozen=True)
class CompShareConfig:
    endpoint: str
    public_key: str
    private_key: str
    region: str
    zone: str
    project_id: str
    uhost_id: str
    poll_seconds: float = 10.0
    operation_timeout_seconds: float = 900.0
    state_lag_grace_seconds: float = 60.0
    state_lag_max_observations: int = 3
    ssh_host: str = ""
    ssh_port: int = 22
    ssh_user: str = "root"
    ssh_key_path: str = ""
    ssh_known_hosts_path: str = ""
    remote_service_root: str = "/root/mineru-popo-service"
    credential_source: str = "legacy_environment"
    credentials_expires_at: str = ""

    @classmethod
    def from_env(cls) -> "CompShareConfig":
        credential_file = os.getenv("COMPSHARE_CREDENTIALS_FILE", "").strip()
        if credential_file:
            try:
                credentials = load_runtime_credentials(credential_file)
            except CompShareCredentialError as exc:
                raise CompShareLifecycleError(exc.code, str(exc)) from exc
            public_key = credentials.public_key
            private_key = credentials.private_key
            credential_source = credentials.source
            credentials_expires_at = credentials.expires_at
        else:
            public_key = os.getenv("COMPSHARE_PUBLIC_KEY", "")
            private_key = os.getenv("COMPSHARE_PRIVATE_KEY", "")
            legacy_allowed = os.getenv("COMPSHARE_ALLOW_LEGACY_ENV", "false").lower() in {"1", "true", "yes", "on"}
            if (public_key or private_key) and not legacy_allowed:
                raise CompShareLifecycleError(
                    "credential_legacy_environment_disabled",
                    "Plain environment credentials require explicit legacy opt-in",
                )
            credential_source = "legacy_environment" if public_key or private_key else "missing"
            credentials_expires_at = ""
        return cls(
            endpoint=os.getenv("COMPSHARE_API_ENDPOINT", "https://api.compshare.cn").rstrip("/"),
            public_key=public_key,
            private_key=private_key,
            region=os.getenv("COMPSHARE_REGION", ""),
            zone=os.getenv("COMPSHARE_ZONE", ""),
            project_id=os.getenv("COMPSHARE_PROJECT_ID", ""),
            uhost_id=os.getenv("COMPSHARE_UHOST_ID", ""),
            poll_seconds=max(1.0, float(os.getenv("COMPSHARE_POLL_SECONDS", "10"))),
            operation_timeout_seconds=max(60.0, float(os.getenv("COMPSHARE_OPERATION_TIMEOUT_SECONDS", "900"))),
            state_lag_grace_seconds=max(0.0, float(os.getenv("COMPSHARE_STATE_LAG_GRACE_SECONDS", "60"))),
            state_lag_max_observations=max(0, int(os.getenv("COMPSHARE_STATE_LAG_MAX_OBSERVATIONS", "3"))),
            ssh_host=os.getenv("GPU_SSH_HOST", ""),
            ssh_port=int(os.getenv("GPU_SSH_PORT", "22")),
            ssh_user=os.getenv("GPU_SSH_USER", "root"),
            ssh_key_path=os.getenv("GPU_SSH_KEY_PATH_IN_CONTAINER", "/root/.ssh/id_ed25519_trae_dev"),
            ssh_known_hosts_path=os.getenv("GPU_SSH_KNOWN_HOSTS_PATH", "/root/.ssh/known_hosts"),
            remote_service_root=os.getenv("GPU_REMOTE_SERVICE_ROOT", "/root/mineru-popo-service"),
            credential_source=credential_source,
            credentials_expires_at=credentials_expires_at,
        )

    def missing_fields(self, *, require_credentials: bool = True) -> list[str]:
        required = {
            "endpoint": self.endpoint,
            "region": self.region,
            "zone": self.zone,
            "project_id": self.project_id,
            "uhost_id": self.uhost_id,
        }
        if require_credentials:
            required.update({"public_key": self.public_key, "private_key": self.private_key})
        return [key for key, value in required.items() if not str(value or "").strip()]

    def public_identity(self) -> dict[str, Any]:
        return {
            "endpoint_origin": urllib.parse.urlsplit(self.endpoint).netloc,
            "region": self.region,
            "zone": self.zone,
            "project_id_sha256": hashlib.sha256(self.project_id.encode()).hexdigest() if self.project_id else "",
            "uhost_id": self.uhost_id,
            "ssh_host": self.ssh_host,
            "ssh_port": self.ssh_port,
            "credential_source": self.credential_source,
            "credentials_expires_at": self.credentials_expires_at,
        }


class CompShareClient(Protocol):
    def describe(self) -> dict[str, Any]: ...

    def start(self) -> dict[str, Any]: ...

    def stop(self) -> dict[str, Any]: ...

    def update_stop_scheduler(self, stop_time: int) -> dict[str, Any]: ...


class UCloudCompShareClient:
    """Minimal signed Compshare adapter.

    The signature follows the UCloud public API canonical parameter contract:
    sort request keys, concatenate key/value pairs, append the private key, and
    SHA1 the resulting UTF-8 bytes. Secrets never enter returned audit data.
    """

    def __init__(self, config: CompShareConfig, *, timeout_seconds: float = 30.0):
        missing = config.missing_fields()
        if missing:
            raise CompShareLifecycleError("cloud_config_incomplete", f"Compshare config missing: {', '.join(missing)}")
        self.config = config
        self.timeout_seconds = timeout_seconds
        self.last_sanitized_request: dict[str, Any] = {}

    def _signature(self, params: dict[str, Any]) -> str:
        canonical = "".join(f"{key}{params[key]}" for key in sorted(params)) + self.config.private_key
        return hashlib.sha1(canonical.encode("utf-8")).hexdigest()

    def _safe_error_text(self, value: Any, *, signature: str = "") -> str:
        text = str(value or "Compshare request failed")
        for secret in (self.config.public_key, self.config.private_key, signature):
            if secret:
                text = text.replace(secret, "[redacted]")
        for marker in ("Signature=", "PrivateKey=", "PublicKey=", "Password="):
            if marker in text:
                text = text.split(marker, 1)[0] + marker + "[redacted]"
        return text[:1000]

    def _call(self, action: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {
            "Action": action,
            "PublicKey": self.config.public_key,
            "Region": self.config.region,
            "Zone": self.config.zone,
            "ProjectId": self.config.project_id,
        }
        params.update(extra or {})
        params["Signature"] = self._signature(params)
        encoded = urllib.parse.urlencode(params).encode("utf-8")
        self.last_sanitized_request = {
            "schema": "luceon.compshare-sanitized-request/v1",
            "method": "POST",
            "endpoint_origin": urllib.parse.urlsplit(self.config.endpoint).netloc,
            "action": action,
            "parameter_names": sorted(params),
            "body_size_bytes": len(encoded),
            "uhost_id": self.config.uhost_id,
            "region": self.config.region,
            "zone": self.config.zone,
            "project_id_sha256": hashlib.sha256(self.config.project_id.encode()).hexdigest(),
        }
        request = urllib.request.Request(
            f"{self.config.endpoint}/",
            data=encoded,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "Luceon-Compshare-Lifecycle/2.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = {}
            code = str(payload.get("ErrCode") or payload.get("ErrorCode") or f"http_{exc.code}")
            message = self._safe_error_text(
                payload.get("Message") or payload.get("ErrMsg") or exc.reason,
                signature=params["Signature"],
            )
            raise CompShareLifecycleError(code, message) from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CompShareLifecycleError("cloud_response_invalid", "Compshare returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise CompShareLifecycleError("cloud_response_invalid", "Compshare returned a non-object response")
        ret_code = payload.get("RetCode", 0)
        if ret_code not in (0, "0", None):
            code = str(payload.get("ErrCode") or payload.get("ErrorCode") or ret_code)
            message = self._safe_error_text(
                payload.get("Message") or payload.get("ErrMsg") or code,
                signature=params["Signature"],
            )
            raise CompShareLifecycleError(code, message)
        return payload

    def describe(self) -> dict[str, Any]:
        return self._call("DescribeCompShareInstance", {"UHostIds.0": self.config.uhost_id})

    def start(self) -> dict[str, Any]:
        # WithoutGpuSpec is intentionally omitted: this is a GPU workload.
        return self._call("StartCompShareInstance", {"UHostId": self.config.uhost_id})

    def stop(self) -> dict[str, Any]:
        return self._call("StopCompShareInstance", {"UHostId": self.config.uhost_id})

    def update_stop_scheduler(self, stop_time: int) -> dict[str, Any]:
        scheduler_stop_time = int(stop_time)
        if scheduler_stop_time < int(time.time()) + 300:
            raise CompShareLifecycleError(
                "scheduler_stop_time_invalid",
                "SchedulerStopTime must be at least 300 seconds in the future",
            )
        return self._call(
            "UpdateCompShareStopScheduler",
            {"UHostId": self.config.uhost_id, "SchedulerStopTime": scheduler_stop_time},
        )


def _instance_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("UHostSet", "InstanceSet", "DataSet"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    data = payload.get("Data")
    if isinstance(data, dict):
        for key in ("UHostSet", "InstanceSet"):
            value = data.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    return []


def exact_instance(payload: dict[str, Any], uhost_id: str) -> dict[str, Any]:
    rows = _instance_rows(payload)
    matches = [row for row in rows if str(row.get("UHostId") or row.get("InstanceId") or "") == uhost_id]
    if len(matches) != 1:
        raise CompShareLifecycleError(
            "cloud_instance_identity_mismatch",
            f"Describe returned {len(matches)} exact matches for {uhost_id}",
            evidence={"returned_count": len(rows), "exact_match_count": len(matches)},
        )
    state = str(matches[0].get("State") or "")
    if state not in KNOWN_STATES:
        raise CompShareLifecycleError("cloud_instance_state_unknown", f"Unsupported Compshare state: {state or '<empty>'}")
    return matches[0]


@dataclass
class LifecycleLease:
    lease_id: str
    uhost_id: str
    prior_state: str
    current_state: str
    lifecycle_owned: bool
    started_by_pipeline: bool
    acquired_at: str
    timeline: list[dict[str, Any]] = field(default_factory=list)
    readiness: dict[str, Any] = field(default_factory=dict)
    phase: str = "described"
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema": "luceon.compshare-lifecycle-lease/v2",
            "lease_id": self.lease_id,
            "uhost_id": self.uhost_id,
            "prior_state": self.prior_state,
            "current_state": self.current_state,
            "lifecycle_owned": self.lifecycle_owned,
            "started_by_pipeline": self.started_by_pipeline,
            "acquired_at": self.acquired_at,
            "timeline": self.timeline,
            "readiness": self.readiness,
            "phase": self.phase,
            "updated_at": self.updated_at,
        }
        payload["lease_sha256"] = _canonical_sha(payload)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "LifecycleLease":
        raw = dict(payload or {})
        claimed = str(raw.pop("lease_sha256", ""))
        if raw.get("schema") not in {"luceon.compshare-lifecycle-lease/v1", "luceon.compshare-lifecycle-lease/v2"} or not claimed or _canonical_sha(raw) != claimed:
            raise CompShareLifecycleError("lifecycle_lease_invalid", "Lifecycle lease hash or schema is invalid")
        return cls(
            lease_id=str(raw.get("lease_id") or ""),
            uhost_id=str(raw.get("uhost_id") or ""),
            prior_state=str(raw.get("prior_state") or ""),
            current_state=str(raw.get("current_state") or ""),
            lifecycle_owned=bool(raw.get("lifecycle_owned")),
            started_by_pipeline=bool(raw.get("started_by_pipeline")),
            acquired_at=str(raw.get("acquired_at") or ""),
            timeline=list(raw.get("timeline") or []),
            readiness=dict(raw.get("readiness") or {}),
            phase=str(raw.get("phase") or "described"),
            updated_at=str(raw.get("updated_at") or ""),
        )


def _timeline(action: str, state: str, **extra: Any) -> dict[str, Any]:
    return {"at": _utc_now(), "action": action, "state": state, **extra}


def _wait_for_state(
    client: CompShareClient,
    config: CompShareConfig,
    target: str,
    *,
    sleep: Callable[[float], None],
    monotonic: Callable[[], float],
    timeline: list[dict[str, Any]],
    lease: LifecycleLease | None = None,
    checkpoint: Callable[[LifecycleLease], None] | None = None,
    source_state: str = "",
    action_accepted: bool = False,
    operation_locked: bool = False,
) -> dict[str, Any]:
    deadline = monotonic() + config.operation_timeout_seconds
    lag_deadline = monotonic() + config.state_lag_grace_seconds
    source_observations = 0
    transition_seen = False
    while monotonic() <= deadline:
        row = exact_instance(client.describe(), config.uhost_id)
        state = str(row["State"])
        timeline.append(_timeline("describe", state))
        if lease is not None:
            lease.current_state = state
            lease.phase = "waiting_for_" + target.lower()
            lease.updated_at = _utc_now()
            if checkpoint:
                checkpoint(lease)
        if state == target:
            return row
        if state in TRANSITION_STATES:
            transition_seen = True
        if source_state and state == source_state:
            source_observations += 1
            if transition_seen:
                code = "cloud_start_reverted" if target == "Running" else "cloud_stop_reverted"
                raise CompShareLifecycleError(code, f"Instance returned to {source_state} after transition began")
            if action_accepted:
                within_count = source_observations <= config.state_lag_max_observations
                within_time = monotonic() <= lag_deadline
                if not (within_count and within_time):
                    code = "cloud_start_reverted" if target == "Running" else "cloud_stop_reverted"
                    raise CompShareLifecycleError(code, f"Instance remained {source_state} beyond eventual-consistency allowance")
            elif not operation_locked:
                code = "cloud_start_reverted" if target == "Running" else "cloud_stop_reverted"
                raise CompShareLifecycleError(code, f"Instance remained {source_state} without an accepted action")
        sleep(config.poll_seconds)
    raise CompShareLifecycleError("cloud_operation_timeout", f"Timed out waiting for {target}", evidence={"timeline": timeline})


def ensure_running(
    client: CompShareClient,
    config: CompShareConfig,
    readiness_probe: Callable[[], dict[str, Any]],
    *,
    lease_id: str = "",
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    checkpoint: Callable[[LifecycleLease], None] | None = None,
) -> LifecycleLease:
    timeline: list[dict[str, Any]] = []
    first = exact_instance(client.describe(), config.uhost_id)
    prior_state = str(first["State"])
    timeline.append(_timeline("describe", prior_state))
    lease = LifecycleLease(
        lease_id=lease_id or secrets.token_hex(16),
        uhost_id=config.uhost_id,
        prior_state=prior_state,
        current_state=prior_state,
        lifecycle_owned=False,
        started_by_pipeline=False,
        acquired_at=_utc_now(),
        timeline=timeline,
        phase="described",
        updated_at=_utc_now(),
    )
    if checkpoint:
        checkpoint(lease)

    if prior_state == "Stopped":
        action_accepted = False
        operation_locked = False
        try:
            client.start()
            action_accepted = True
            lease.lifecycle_owned = True
            lease.started_by_pipeline = True
            lease.current_state = "Starting"
            lease.phase = "start_accepted"
            timeline.append(_timeline("start_requested", "Starting"))
        except CompShareLifecycleError as exc:
            if exc.code != OPERATION_LOCK_ERROR:
                raise
            operation_locked = True
            lease.phase = "start_operation_locked"
            timeline.append(_timeline("start_operation_locked", prior_state, error_code=exc.code))
        lease.updated_at = _utc_now()
        if checkpoint:
            checkpoint(lease)
        current = _wait_for_state(
            client,
            config,
            "Running",
            sleep=sleep,
            monotonic=monotonic,
            timeline=timeline,
            lease=lease,
            checkpoint=checkpoint,
            source_state="Stopped",
            action_accepted=action_accepted,
            operation_locked=operation_locked,
        )
    elif prior_state == "Running":
        current = first
    elif prior_state in TRANSITION_STATES:
        # A transition not initiated by this pipeline has unknown ownership.
        current = _wait_for_state(
            client,
            config,
            "Running" if prior_state in {"Starting", "Initializing", "Rebooting", "Install"} else "Stopped",
            sleep=sleep,
            monotonic=monotonic,
            timeline=timeline,
            lease=lease,
            checkpoint=checkpoint,
            source_state=prior_state,
            operation_locked=True,
        )
        if str(current["State"]) != "Running":
            raise CompShareLifecycleError("cloud_instance_not_running", "Instance became Stopped; retry may start it explicitly")
    else:  # guarded by exact_instance; retained for readability.
        raise CompShareLifecycleError("cloud_instance_state_unknown", f"Unsupported state {prior_state}")

    readiness = readiness_probe()
    lease.readiness = readiness
    if not bool(readiness.get("ready")):
        lease.phase = "readiness_failed"
        lease.updated_at = _utc_now()
        timeline.append(_timeline("readiness_failed", "Running", evidence_sha256=_canonical_sha(readiness)))
        if checkpoint:
            checkpoint(lease)
        raise CompShareLifecycleError(
            "gpu_readiness_failed",
            "Instance is Running but GPU services are not ready",
            evidence={"readiness": readiness, "lease": lease.to_dict()},
        )
    timeline.append(_timeline("readiness_passed", "Running", evidence_sha256=_canonical_sha(readiness)))
    lease.current_state = "Running"
    lease.phase = "ready"
    lease.updated_at = _utc_now()
    if checkpoint:
        checkpoint(lease)
    return lease


@dataclass(frozen=True)
class SafeStopContext:
    queue_empty: bool
    remote_active_jobs: int
    remote_idle_verified: bool
    all_results_frozen_local: bool
    grace_elapsed: bool


def stop_when_safe(
    client: CompShareClient,
    config: CompShareConfig,
    lease: LifecycleLease,
    context: SafeStopContext,
    *,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    checkpoint: Callable[[LifecycleLease], None] | None = None,
) -> dict[str, Any]:
    blockers = []
    if not lease.lifecycle_owned or not lease.started_by_pipeline:
        blockers.append("lifecycle_not_owned")
    if not context.queue_empty:
        blockers.append("queue_not_empty")
    if context.remote_active_jobs:
        blockers.append("remote_jobs_active")
    if not context.remote_idle_verified:
        blockers.append("remote_idle_unverified")
    if not context.all_results_frozen_local:
        blockers.append("results_not_frozen_local")
    if not context.grace_elapsed:
        blockers.append("grace_period_not_elapsed")
    if blockers:
        return {"stopped": False, "status": "retained_running", "blockers": blockers, "lease": lease.to_dict()}

    row = exact_instance(client.describe(), config.uhost_id)
    state = str(row["State"])
    lease.timeline.append(_timeline("describe_before_stop", state))
    lease.current_state = state
    lease.phase = "stop_preflight"
    lease.updated_at = _utc_now()
    if checkpoint:
        checkpoint(lease)
    if state == "Stopped":
        lease.current_state = "Stopped"
        return {"stopped": True, "status": "already_stopped", "blockers": [], "lease": lease.to_dict()}
    if state != "Running":
        return {"stopped": False, "status": "retained_transitioning", "blockers": [f"state_{state}"], "lease": lease.to_dict()}
    try:
        client.stop()
        action_accepted = True
        lease.timeline.append(_timeline("stop_requested", "Stopping"))
    except CompShareLifecycleError as exc:
        if exc.code != OPERATION_LOCK_ERROR:
            raise
        action_accepted = False
        lease.timeline.append(_timeline("stop_operation_locked", state, error_code=exc.code))
    lease.phase = "stop_accepted" if action_accepted else "stop_operation_locked"
    lease.updated_at = _utc_now()
    if checkpoint:
        checkpoint(lease)
    _wait_for_state(
        client,
        config,
        "Stopped",
        sleep=sleep,
        monotonic=monotonic,
        timeline=lease.timeline,
        lease=lease,
        checkpoint=checkpoint,
        source_state="Running",
        action_accepted=action_accepted,
        operation_locked=not action_accepted,
    )
    lease.current_state = "Stopped"
    lease.phase = "stopped"
    lease.updated_at = _utc_now()
    if checkpoint:
        checkpoint(lease)
    return {"stopped": True, "status": "stopped", "blockers": [], "lease": lease.to_dict()}


def ssh_readiness_probe(config: CompShareConfig, wrapper_url: str) -> dict[str, Any]:
    if not config.ssh_host:
        return {"ready": False, "error_domain": "ssh", "reason": "ssh_host_missing"}
    known_hosts = config.ssh_known_hosts_path
    if not known_hosts or not os.path.isfile(known_hosts):
        return {"ready": False, "error_domain": "ssh", "reason": "known_hosts_missing"}
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={known_hosts}",
        "-p",
        str(config.ssh_port),
    ]
    if config.ssh_key_path:
        command.extend(["-i", config.ssh_key_path])
    remote = (
        "set -eu; "
        f"test -d {json.dumps(config.remote_service_root)}; "
        "command -v nvidia-smi >/dev/null; "
        "nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits; "
        f"df -Pk {json.dumps(config.remote_service_root)} | tail -1"
    )
    completed = subprocess.run(
        [*command, f"{config.ssh_user}@{config.ssh_host}", remote],
        text=True,
        capture_output=True,
        timeout=45,
    )
    if completed.returncode:
        return {
            "ready": False,
            "error_domain": "ssh",
            "reason": "ssh_probe_failed",
            "returncode": completed.returncode,
            "stderr_tail": (completed.stderr or "")[-1000:],
        }
    output_lines = [line.strip() for line in (completed.stdout or "").splitlines() if line.strip()]
    if len(output_lines) < 2:
        return {"ready": False, "error_domain": "gpu_disk", "reason": "gpu_or_disk_probe_output_missing"}
    disk_fields = output_lines[-1].split()
    try:
        disk_available_bytes = int(disk_fields[3]) * 1024
    except (IndexError, TypeError, ValueError):
        return {"ready": False, "error_domain": "disk", "reason": "disk_probe_invalid"}
    minimum_disk_bytes = max(1, int(os.getenv("GPU_MIN_FREE_DISK_BYTES", str(10 * 1024**3))))
    if disk_available_bytes < minimum_disk_bytes:
        return {
            "ready": False,
            "error_domain": "disk",
            "reason": "disk_headroom_insufficient",
            "disk_available_bytes": disk_available_bytes,
            "disk_required_bytes": minimum_disk_bytes,
        }
    wrapper_health: dict[str, Any] = {}
    try:
        request = urllib.request.Request(wrapper_url.rstrip("/") + "/api/v1/health", headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=20) as response:
            wrapper_health = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return {
            "ready": False,
            "error_domain": "service_health",
            "reason": "wrapper_health_failed",
            "error_type": type(exc).__name__,
        }
    health_status = str(wrapper_health.get("status") or "").lower()
    if health_status not in {"ok", "healthy", "ready", "pass"}:
        return {
            "ready": False,
            "error_domain": "service_health",
            "reason": "wrapper_not_ready",
            "wrapper_status": health_status or "missing",
        }
    return {
        "ready": True,
        "ssh": {
            "host": config.ssh_host,
            "port": config.ssh_port,
            "probe_sha256": _canonical_sha(completed.stdout),
            "gpu_inventory": output_lines[:-1],
            "disk_available_bytes": disk_available_bytes,
            "disk_required_bytes": minimum_disk_bytes,
        },
        "gpu_disk_probe_sha256": _canonical_sha(completed.stdout),
        "wrapper": {"status": health_status, "evidence_sha256": _canonical_sha(wrapper_health)},
    }
