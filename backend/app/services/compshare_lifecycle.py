from __future__ import annotations

import hashlib
import json
import os
import secrets
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from app.services.compshare_credentials import CompShareCredentialError, load_project_credentials, load_runtime_credentials


TERMINAL_STATES = {"Running", "Stopped"}
TRANSITION_STATES = {"Starting", "Initializing", "Stopping", "Rebooting", "Install"}
KNOWN_STATES = TERMINAL_STATES | TRANSITION_STATES
OPERATION_LOCK_ERROR = "InstanceOperationInProgress"
BILLING_NORMALIZATION_SCHEMA = "luceon.compshare-billing-normalization/v3"
MANAGED_WRAPPER_TRANSPORT_SCHEMA = "luceon.managed-wrapper-transport/v2"


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
    # The scheduler control plane can be eventually consistent after a
    # successful UpdateCompShareStopScheduler.  This is deliberately a small
    # bounded read-only retry budget; it never authorizes another Update.
    scheduler_guard_verify_max_observations: int = 3
    ssh_host: str = ""
    ssh_port: int = 22
    wrapper_remote_port: int = 18080
    ssh_user: str = "root"
    ssh_key_path: str = ""
    ssh_known_hosts_path: str = ""
    remote_service_root: str = "/root/mineru-popo-service"
    credential_source: str = "legacy_environment"
    credentials_expires_at: str = ""
    # Legacy environment callers retain the historical 50 GiB default.  The
    # versioned runtime settings path supplies its own (currently 12 GiB)
    # minimum explicitly via ``from_runtime_snapshot``.
    minimum_disk_bytes: int = 50 * 1024**3
    budget_micro_cny: int = 20_000_000
    settings_sha256: str = ""

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
            wrapper_remote_port=int(os.getenv("GPU_WRAPPER_TUNNEL_REMOTE_PORT", "18080")),
            ssh_user=os.getenv("GPU_SSH_USER", "root"),
            ssh_key_path=os.getenv("GPU_SSH_KEY_PATH_IN_CONTAINER", "/root/.ssh/id_ed25519_trae_dev"),
            ssh_known_hosts_path=os.getenv("GPU_SSH_KNOWN_HOSTS_PATH", "/root/.ssh/known_hosts"),
            remote_service_root=os.getenv("GPU_REMOTE_SERVICE_ROOT", "/root/mineru-popo-service"),
            credential_source=credential_source,
            credentials_expires_at=credentials_expires_at,
        )

    @classmethod
    def from_runtime_snapshot(cls, snapshot: Any, *, project_secret_path: str) -> "CompShareConfig":
        provider = str(snapshot.credential_provider)
        try:
            if provider == "project_secret_file":
                credentials = load_project_credentials(project_secret_path)
            elif provider == "macos_keychain_secret_file":
                credential_file = os.getenv("COMPSHARE_CREDENTIALS_FILE", "").strip()
                if not credential_file:
                    raise CompShareCredentialError("credential_file_missing", "Keychain runtime credential file is missing")
                credentials = load_runtime_credentials(credential_file)
            else:
                raise CompShareCredentialError("credential_provider_invalid", "Unsupported credential provider")
        except CompShareCredentialError as exc:
            raise CompShareLifecycleError(exc.code, str(exc)) from exc
        return cls(
            endpoint=str(snapshot.endpoint).rstrip("/"), public_key=credentials.public_key,
            private_key=credentials.private_key, region=str(snapshot.region), zone=str(snapshot.zone),
            project_id=str(snapshot.project_id), uhost_id=str(snapshot.uhost_id),
            ssh_host=str(snapshot.ssh_host), ssh_port=int(snapshot.ssh_port),
            wrapper_remote_port=int(snapshot.wrapper_remote_port),
            ssh_user=os.getenv("GPU_SSH_USER", "root"),
            ssh_key_path=os.getenv("GPU_SSH_KEY_PATH_IN_CONTAINER", "/root/.ssh/id_ed25519_trae_dev"),
            ssh_known_hosts_path=os.getenv("GPU_SSH_KNOWN_HOSTS_PATH", "/root/.ssh/known_hosts"),
            remote_service_root=os.getenv("GPU_REMOTE_SERVICE_ROOT", "/root/mineru-popo-service"),
            credential_source=credentials.source, credentials_expires_at=credentials.expires_at,
            minimum_disk_bytes=int(snapshot.min_free_disk_bytes), budget_micro_cny=int(snapshot.budget_micro_cny),
            settings_sha256=str(snapshot.settings_sha256),
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
        allowed_origins = {
            value.strip().lower()
            for value in os.getenv("COMPSHARE_ALLOWED_ENDPOINT_ORIGINS", "").split(",")
            if value.strip()
        }
        endpoint_parts = urllib.parse.urlsplit(config.endpoint)
        endpoint_origin = endpoint_parts.netloc.lower()
        if allowed_origins and endpoint_origin not in allowed_origins:
            raise CompShareLifecycleError(
                "cloud_endpoint_not_allowed",
                "Compshare endpoint is not allowed by this runtime",
                evidence={"endpoint_origin": endpoint_origin, "allowed_origin_count": len(allowed_origins)},
            )
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


def cap_derived_scheduler_stop(
    instance: dict[str, Any],
    *,
    budget_micro_cny: int,
    now_epoch: int | None = None,
    safety_seconds: int = 1800,
) -> dict[str, Any]:
    """Derive a conservative scheduler deadline from qualified hourly billing.

    A versioned automatic run must not treat the CNY 20 ceiling as prose.  We
    qualify explicit hourly charge types plus the frozen Compshare ``Postpay``
    hourly shape observed in Task36.  ``Postpay`` is accepted only under this
    versioned provider contract and is rejected when an accompanying unit field
    contradicts hourly billing.  Unknown types, ambiguous units, or missing
    prices fail closed before a cloud Start.  The deadline leaves half
    an hour for normal inventory verification and Stop, while remaining inside
    the last authorised whole billing hour.
    """
    charge_type = str(instance.get("ChargeType") or "").strip().lower()
    unit_fields = {
        key: str(instance[key]).strip().lower()
        for key in ("PriceUnit", "BillingUnit", "ChargeUnit", "Unit", "BillingCycle", "ChargeCycle", "Cycle")
        if instance.get(key) not in (None, "")
    }
    hourly_units = {"hour", "hourly", "cny/hour", "cny/h", "rmb/hour", "rmb/h"}
    explicit_hourly = charge_type in {"hour", "hourly"}
    # The official 2026-08-04 Describe contract defines InstancePrice as the
    # CNY/hour instance price for Postpay.  A normalized historical receipt is
    # not evidence that the provider raw field was named Price, so Postpay must
    # not fall back to that legacy field.
    task36_postpay_hourly = (
        charge_type == "postpay"
        and isinstance(instance.get("InstancePrice"), (int, float))
        and not isinstance(instance.get("InstancePrice"), bool)
        and float(instance.get("InstancePrice")) > 0
        and instance.get("Price") in (None, "")
        and instance.get("DiscountPrice") in (None, "")
        and all(
        value in hourly_units for value in unit_fields.values()
        )
    )
    normalization_method = (
        "explicit_hour_charge_type" if explicit_hourly else
        "compshare_postpay_instance_price_v1" if task36_postpay_hourly else ""
    )
    raw_price = instance.get("InstancePrice") if task36_postpay_hourly else instance.get("DiscountPrice", instance.get("Price"))
    numeric_price = isinstance(raw_price, (int, float)) and not isinstance(raw_price, bool)
    price_micro = int(round(float(raw_price) * 1_000_000)) if numeric_price else 0
    if not normalization_method or price_micro <= 0:
        raise CompShareLifecycleError(
            "cloud_billing_unqualified",
            "Automatic lifecycle requires an exact positive hourly price",
            evidence={
                "billing_normalization_schema": BILLING_NORMALIZATION_SCHEMA,
                "charge_type": charge_type or "missing",
                "price_present": raw_price is not None,
                "unit_fields": unit_fields,
            },
        )
    units = int(budget_micro_cny) // price_micro
    if units < 1:
        raise CompShareLifecycleError("cloud_budget_insufficient", "Per-run budget is below one billing unit")
    now_value = int(time.time() if now_epoch is None else now_epoch)
    runtime_seconds = units * 3600 - max(300, int(safety_seconds))
    if runtime_seconds < 300:
        raise CompShareLifecycleError("cloud_budget_insufficient", "Budget leaves no safe automatic lifecycle window")
    return {
        "scheduler_stop_time": now_value + runtime_seconds,
        "billing_unit_seconds": 3600,
        "authorized_units": units,
        "unit_price_micro_cny": price_micro,
        "budget_micro_cny": int(budget_micro_cny),
        "normal_stop_reserve_seconds": max(300, int(safety_seconds)),
        "billing_normalization_schema": BILLING_NORMALIZATION_SCHEMA,
        "billing_normalization_method": normalization_method,
    }


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
    cost_guard: dict[str, Any] = field(default_factory=dict)
    # The transport commitment contains only a loopback endpoint and SSH
    # forwarding identity.  Credentials deliberately remain in the runtime
    # secret provider and are never serialized into a lease.
    transport: dict[str, Any] = field(default_factory=dict)
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema": "luceon.compshare-lifecycle-lease/v5",
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
            "cost_guard": self.cost_guard,
            "transport": self.transport,
            "updated_at": self.updated_at,
        }
        payload["lease_sha256"] = _canonical_sha(payload)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "LifecycleLease":
        raw = dict(payload or {})
        claimed = str(raw.pop("lease_sha256", ""))
        if raw.get("schema") not in {"luceon.compshare-lifecycle-lease/v1", "luceon.compshare-lifecycle-lease/v2", "luceon.compshare-lifecycle-lease/v3", "luceon.compshare-lifecycle-lease/v4", "luceon.compshare-lifecycle-lease/v5"} or not claimed or _canonical_sha(raw) != claimed:
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
            cost_guard=dict(raw.get("cost_guard") or {}),
            transport=dict(raw.get("transport") or {}),
            updated_at=str(raw.get("updated_at") or ""),
        )


@dataclass
class ManagedWrapperTransport:
    """A lease-owned loopback tunnel to the protected wrapper.

    A public/direct wrapper health check is not sufficient evidence that the
    worker subprocess will retain a data path.  The context therefore records
    the loopback endpoint used by readiness, submission, result collection and
    safe-stop inventory.  It intentionally contains no bearer/API credential.
    """

    lease_id: str
    endpoint: str
    local_port: int
    remote_port: int
    ssh_host: str
    settings_sha256: str = ""
    process: Any | None = field(default=None, repr=False, compare=False)
    transport_id: str = ""
    owner_token: str = ""
    process_pid: int = 0
    process_boot_id: str = ""
    process_start_ticks: int = 0
    process_command_sha256: str = ""

    def __post_init__(self) -> None:
        if not self.owner_token:
            self.owner_token = secrets.token_hex(16)
        if not self.process_pid and self.process is not None:
            self.process_pid = int(getattr(self.process, "pid", 0) or 0)
        if self.process_pid > 1 and (not self.process_boot_id or self.process_start_ticks <= 0):
            identity = _read_linux_process_identity(self.process_pid)
            self.process_boot_id = identity["boot_id"]
            self.process_start_ticks = identity["start_ticks"]
        if not self.transport_id:
            self.transport_id = _canonical_sha(
                {
                    "schema": MANAGED_WRAPPER_TRANSPORT_SCHEMA,
                    "lease_id": self.lease_id,
                    "endpoint": self.endpoint,
                    "local_port": self.local_port,
                    "remote_port": self.remote_port,
                    "ssh_host": self.ssh_host,
                    "settings_sha256": self.settings_sha256,
                }
            )

    def active(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def to_dict(self, *, state: str = "active") -> dict[str, Any]:
        payload = {
            "schema": MANAGED_WRAPPER_TRANSPORT_SCHEMA,
            "transport_id": self.transport_id,
            "lease_id": self.lease_id,
            "endpoint": self.endpoint,
            "local_port": self.local_port,
            "remote_port": self.remote_port,
            "ssh_host": self.ssh_host,
            "settings_sha256": self.settings_sha256,
            "owner_token": self.owner_token,
            "process_pid": self.process_pid,
            "process_boot_id": self.process_boot_id,
            "process_start_ticks": self.process_start_ticks,
            "process_command_sha256": self.process_command_sha256,
            "state": state,
        }
        payload["transport_sha256"] = _canonical_sha(payload)
        return payload

    def close(self) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)


def _transport_remote_port(config: CompShareConfig) -> int:
    value = int(config.wrapper_remote_port)
    if not 1 <= value <= 65535:
        raise CompShareLifecycleError("wrapper_transport_port_invalid", "Configured wrapper tunnel port is invalid")
    return value


def _reserve_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _ssh_exit_evidence(process: Any) -> dict[str, Any]:
    """Return only an opaque, bounded diagnostic for a failed SSH child."""
    try:
        raw = process.stderr.read() if getattr(process, "stderr", None) is not None else ""
    except Exception:
        raw = ""
    encoded = str(raw).encode("utf-8", "replace")[:4096]
    return {"exit_code": process.poll(), "stderr_sha256": hashlib.sha256(encoded).hexdigest(), "stderr_bytes": len(encoded)}


def _argv_bytes(argv: list[str]) -> bytes:
    """Encode argv exactly as Linux exposes it through ``/proc/PID/cmdline``."""
    if not argv or any("\x00" in value for value in argv):
        raise CompShareLifecycleError("wrapper_transport_command_invalid", "Managed SSH argv is invalid")
    return b"\x00".join(os.fsencode(value) for value in argv) + b"\x00"


def _argv_sha256(argv: list[str]) -> str:
    return hashlib.sha256(_argv_bytes(argv)).hexdigest()


def _read_linux_process_identity(pid: int) -> dict[str, Any]:
    """Read the kernel identity and exact argv of one Linux process.

    PID alone is not an identity because it can be reused.  The system boot ID
    plus ``/proc/PID/stat`` starttime ticks is stable for the process lifetime.
    No ``ps`` text or application timestamp is accepted as a substitute.
    """
    try:
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except FileNotFoundError:
        raise
    except (OSError, UnicodeError) as exc:
        raise CompShareLifecycleError(
            "wrapper_transport_orphan_identity_unreadable",
            "Kernel process identity is unreadable",
            evidence={"process_pid": pid},
        ) from exc
    close_paren = stat.rfind(")")
    fields = stat[close_paren + 2 :].split() if close_paren > 0 else []
    # fields[0] is kernel field 3 (state); starttime is kernel field 22.
    if not boot_id or len(fields) <= 19 or not cmdline:
        raise CompShareLifecycleError(
            "wrapper_transport_orphan_identity_unreadable",
            "Kernel process identity is incomplete",
            evidence={"process_pid": pid},
        )
    try:
        start_ticks = int(fields[19])
    except (TypeError, ValueError) as exc:
        raise CompShareLifecycleError(
            "wrapper_transport_orphan_identity_unreadable",
            "Kernel process start identity is invalid",
            evidence={"process_pid": pid},
        ) from exc
    if start_ticks <= 0:
        raise CompShareLifecycleError(
            "wrapper_transport_orphan_identity_unreadable",
            "Kernel process start identity is invalid",
            evidence={"process_pid": pid},
        )
    return {
        "boot_id": boot_id,
        "start_ticks": start_ticks,
        "state": fields[0],
        "argv_sha256": hashlib.sha256(cmdline).hexdigest(),
    }


def _managed_wrapper_ssh_argv(
    config: CompShareConfig, *, local_port: int, remote_port: int, owner_token: str
) -> list[str]:
    command = [
        "ssh", "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
        "-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=yes",
        "-o", f"UserKnownHostsFile={config.ssh_known_hosts_path}", "-p", str(config.ssh_port),
        "-L", f"127.0.0.1:{local_port}:127.0.0.1:{remote_port}",
        "-o", f"SetEnv=LUCEON_WRAPPER_TRANSPORT_OWNER={owner_token}",
    ]
    if config.ssh_key_path:
        command.extend(["-i", config.ssh_key_path])
    command.append(f"{config.ssh_user}@{config.ssh_host}")
    return command


def _reap_exact_owned_orphan(previous: dict[str, Any], config: CompShareConfig) -> None:
    """Reap only a prior tunnel whose process proves this exact lease identity.

    The owner token is non-secret and is attached to the SSH invocation solely
    for crash recovery.  A matching port alone is never authority to kill a
    process, so an unrelated developer tunnel remains untouched.
    """
    pid = int(previous.get("process_pid") or 0)
    token = str(previous.get("owner_token") or "")
    if pid <= 1 or not token:
        return
    expected_argv = _managed_wrapper_ssh_argv(
        config,
        local_port=int(previous["local_port"]),
        remote_port=int(previous["remote_port"]),
        owner_token=token,
    )
    expected_command_sha256 = _argv_sha256(expected_argv)
    try:
        identity = _read_linux_process_identity(pid)
    except FileNotFoundError:
        return
    persisted_boot_id = str(previous.get("process_boot_id") or "")
    try:
        persisted_start_ticks = int(previous.get("process_start_ticks") or 0)
    except (TypeError, ValueError):
        persisted_start_ticks = 0
    persisted_command_sha256 = str(previous.get("process_command_sha256") or "")
    if (
        identity["state"] == "Z"
        or not persisted_boot_id
        or persisted_start_ticks <= 0
        or identity["boot_id"] != persisted_boot_id
        or identity["start_ticks"] != persisted_start_ticks
        or persisted_command_sha256 != expected_command_sha256
        or identity["argv_sha256"] != expected_command_sha256
    ):
        raise CompShareLifecycleError(
            "wrapper_transport_orphan_identity_unverified",
            "Existing local tunnel cannot be proven to belong to this lifecycle lease",
            evidence={"process_pid": pid},
        )
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 5.0
    while time.monotonic() <= deadline:
        try:
            current = _read_linux_process_identity(pid)
        except FileNotFoundError:
            return
        if current["boot_id"] != persisted_boot_id or current["start_ticks"] != persisted_start_ticks:
            return
        time.sleep(0.1)
    raise CompShareLifecycleError(
        "wrapper_transport_orphan_reap_timeout",
        "Exact lease-owned local tunnel did not exit after SIGTERM",
        evidence={"process_pid": pid},
    )


def open_managed_wrapper_transport(
    config: CompShareConfig,
    *,
    lease_id: str,
    prior: dict[str, Any] | None = None,
    process_factory: Callable[..., Any] = subprocess.Popen,
    health_probe: Callable[[str], dict[str, Any]] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    port_allocator: Callable[[], int] = _reserve_loopback_port,
) -> ManagedWrapperTransport:
    """Create and prove the one tunnel used for a lifecycle-owned job.

    A persisted context can be re-established after a worker crash, but only
    for the same lease and only as a loopback SSH forward with strict host-key
    verification.  A dead/stale context is never accepted as ready.
    """
    if not config.ssh_host or not config.ssh_known_hosts_path or not os.path.isfile(config.ssh_known_hosts_path):
        raise CompShareLifecycleError("wrapper_transport_ssh_unavailable", "Managed wrapper transport requires verified SSH host keys")
    previous = dict(prior or {})
    if previous and previous.get("schema") != MANAGED_WRAPPER_TRANSPORT_SCHEMA:
        raise CompShareLifecycleError("wrapper_transport_context_legacy", "Legacy managed wrapper transport context has no kernel process identity authority")
    if previous and previous.get("lease_id") != lease_id:
        raise CompShareLifecycleError("wrapper_transport_context_invalid", "Managed wrapper transport context does not match lifecycle lease")
    if previous:
        supplied_sha = str(previous.pop("transport_sha256", ""))
        if not supplied_sha or _canonical_sha(previous) != supplied_sha:
            raise CompShareLifecycleError("wrapper_transport_binding_drift", "Managed wrapper transport hash is invalid")
        required = ("transport_id", "endpoint", "local_port", "remote_port", "ssh_host", "settings_sha256", "owner_token", "process_pid", "process_boot_id", "process_start_ticks", "process_command_sha256")
        if any(not previous.get(key) for key in required):
            raise CompShareLifecycleError("wrapper_transport_binding_drift", "Managed wrapper transport commitment is incomplete")
        expected_endpoint = f"http://127.0.0.1:{int(previous['local_port'])}"
        if (
            previous["endpoint"] != expected_endpoint
            or int(previous["remote_port"]) != int(config.wrapper_remote_port)
            or previous["ssh_host"] != config.ssh_host
            or previous["settings_sha256"] != config.settings_sha256
        ):
            raise CompShareLifecycleError("wrapper_transport_binding_drift", "Managed wrapper transport drifted from the frozen lifecycle settings")
        expected_id = _canonical_sha({
            "schema": MANAGED_WRAPPER_TRANSPORT_SCHEMA, "lease_id": lease_id,
            "endpoint": previous["endpoint"], "local_port": int(previous["local_port"]),
            "remote_port": int(previous["remote_port"]), "ssh_host": previous["ssh_host"],
            "settings_sha256": previous["settings_sha256"],
        })
        if previous["transport_id"] != expected_id:
            raise CompShareLifecycleError("wrapper_transport_binding_drift", "Managed wrapper transport identity is invalid")
        _reap_exact_owned_orphan(previous, config)
    # A prior commitment may never select another port: changing it would make
    # a restarted worker silently use a different endpoint than the one whose
    # hash was persisted in the lifecycle lease.  New leases may make at most
    # three initial reservations, because bind/release is necessarily a race
    # with ssh's later bind on POSIX.
    local_ports = [int(previous["local_port"])] if previous else [port_allocator() for _ in range(3)]
    remote_port = _transport_remote_port(config)
    last_error: CompShareLifecycleError | None = None
    for index, local_port in enumerate(local_ports):
        endpoint = f"http://127.0.0.1:{local_port}"
        owner_token = str(previous.get("owner_token") or secrets.token_hex(16))
        command = _managed_wrapper_ssh_argv(
            config, local_port=local_port, remote_port=remote_port, owner_token=owner_token
        )
        process = process_factory(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        transport = ManagedWrapperTransport(
            lease_id=lease_id, endpoint=endpoint, local_port=local_port,
            remote_port=remote_port, ssh_host=config.ssh_host, settings_sha256=config.settings_sha256, process=process,
            owner_token=owner_token,
            process_command_sha256=_argv_sha256(command),
        )
        deadline = monotonic() + min(30.0, max(1.0, float(config.operation_timeout_seconds)))
        probe: dict[str, Any] = {}
        while monotonic() <= deadline:
            if not transport.active():
                evidence = _ssh_exit_evidence(process)
                transport.close()
                last_error = CompShareLifecycleError("wrapper_transport_start_failed", "Managed SSH wrapper transport exited before readiness", evidence=evidence)
                break
            try:
                probe = health_probe(endpoint) if health_probe else _wrapper_health(endpoint)
            except Exception:
                probe = {"ready": False, "status": "unreachable"}
            if bool(probe.get("ready")):
                return transport
            sleep(min(1.0, max(0.05, float(config.poll_seconds))))
        else:
            transport.close()
            last_error = CompShareLifecycleError("wrapper_transport_readiness_timeout", "Managed SSH wrapper transport did not become ready within the bounded deadline", evidence={"probe": probe})
        # Re-select only before any commitment exists and only for a child that
        # demonstrably exited.  A healthy but unready child is never replaced.
        if previous or last_error is None or last_error.code != "wrapper_transport_start_failed" or index == len(local_ports) - 1:
            break
    if last_error is not None:
        raise last_error
    raise CompShareLifecycleError("wrapper_transport_unreachable", "Managed SSH wrapper transport could not reach wrapper health")


def _wrapper_health(endpoint: str) -> dict[str, Any]:
    request = urllib.request.Request(endpoint.rstrip("/") + "/api/v1/health", headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    status = str(payload.get("status") or "").lower() if isinstance(payload, dict) else ""
    return {"ready": status in {"ok", "healthy", "ready", "pass"}, "status": status}


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


def _confirm_scheduler_guard(
    client: CompShareClient,
    config: CompShareConfig,
    *,
    scheduler_stop_time: int,
    wall_time: Callable[[], float],
    sleep: Callable[[float], None],
    timeline: list[dict[str, Any]],
) -> dict[str, Any]:
    """Read back an already-accepted scheduler guard before Start.

    The official scheduler response confirms only acceptance, not the stored
    deadline.  A delayed Describe must therefore be tolerated briefly, while
    a state/identity/deadline mismatch remains fail-closed.  This helper is
    intentionally read-only so that retries cannot duplicate scheduler writes.
    """
    attempts = max(1, int(config.scheduler_guard_verify_max_observations))
    last_reason = ""
    for observation in range(1, attempts + 1):
        verified = exact_instance(client.describe(), config.uhost_id)
        state = str(verified.get("State") or "")
        verified_deadline = verified.get("SchedulerStopTime")
        deadline_valid = (
            isinstance(verified_deadline, int)
            and not isinstance(verified_deadline, bool)
            and verified_deadline == scheduler_stop_time
            and verified_deadline >= int(wall_time()) + 300
        )
        if state == "Stopped" and deadline_valid:
            timeline.append(
                _timeline(
                    "guard_verification_describe",
                    "Stopped",
                    scheduler_stop_time=verified_deadline,
                    observation=observation,
                )
            )
            return verified
        if state != "Stopped":
            raise CompShareLifecycleError(
                "cloud_scheduler_not_confirmed",
                "Post-update Describe left the exact instance outside Stopped before Start",
                evidence={"state": state, "observation": observation},
            )
        last_reason = "scheduler_stop_time_missing_or_drifted"
        timeline.append(
            _timeline(
                "guard_verification_pending",
                "Stopped",
                observation=observation,
                expected_scheduler_stop_time=scheduler_stop_time,
            )
        )
        if observation < attempts:
            sleep(config.poll_seconds)
    raise CompShareLifecycleError(
        "cloud_scheduler_not_confirmed",
        "Post-update Describe did not confirm the exact active stop guard",
        evidence={"reason": last_reason, "observations": attempts},
    )


def ensure_running(
    client: CompShareClient,
    config: CompShareConfig,
    readiness_probe: Callable[[], dict[str, Any]],
    *,
    lease_id: str = "",
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    wall_time: Callable[[], float] = time.time,
    checkpoint: Callable[[LifecycleLease], None] | None = None,
    resume_lease: LifecycleLease | None = None,
) -> LifecycleLease:
    timeline: list[dict[str, Any]] = []
    first = exact_instance(client.describe(), config.uhost_id)
    prior_state = str(first["State"])
    timeline.append(_timeline("describe", prior_state))
    resumable_guard = bool(
        resume_lease
        and resume_lease.uhost_id == config.uhost_id
        and resume_lease.prior_state == "Stopped"
        and resume_lease.phase == "guard_accepted_before_start"
        and resume_lease.cost_guard
        and prior_state == "Stopped"
    )
    if resumable_guard:
        lease = resume_lease
        timeline = lease.timeline
        timeline.append(_timeline("resume_after_guard", prior_state))
        lease.current_state = prior_state
        lease.updated_at = _utc_now()
    else:
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
        cost_guard = None
        guard_is_current = False
        if config.settings_sha256:
            if resumable_guard:
                persisted_deadline = resume_lease.cost_guard.get("scheduler_stop_time")
                cloud_deadline = first.get("SchedulerStopTime")
                guard_is_current = (
                    isinstance(persisted_deadline, int)
                    and not isinstance(persisted_deadline, bool)
                    and isinstance(cloud_deadline, int)
                    and not isinstance(cloud_deadline, bool)
                    and cloud_deadline == persisted_deadline
                    and cloud_deadline >= int(wall_time()) + 300
                )
                if guard_is_current:
                    cost_guard = dict(resume_lease.cost_guard)
                    timeline.append(_timeline("resume_guard_verified", "Stopped", scheduler_stop_time=cloud_deadline))
                else:
                    timeline.append(_timeline("resume_guard_rearm_required", "Stopped"))
            if not guard_is_current:
                cost_guard = cap_derived_scheduler_stop(
                    first,
                    budget_micro_cny=config.budget_micro_cny,
                    now_epoch=int(wall_time()),
                )
        action_accepted = False
        operation_locked = False
        try:
            if cost_guard is not None and not guard_is_current:
                response = client.update_stop_scheduler(int(cost_guard["scheduler_stop_time"]))
                if int(response.get("RetCode", -1)) != 0:
                    raise CompShareLifecycleError(
                        "cloud_scheduler_not_confirmed",
                        "Compshare stop scheduler update was rejected",
                    )
                if str(response.get("Action") or "") != "UpdateCompShareStopSchedulerResponse":
                    raise CompShareLifecycleError(
                        "cloud_scheduler_not_confirmed",
                        "Compshare stop scheduler returned an unexpected response identity",
                    )
                if str(response.get("UHostId") or "") != config.uhost_id:
                    raise CompShareLifecycleError(
                        "cloud_scheduler_identity_mismatch",
                        "Compshare stop scheduler response did not match the exact instance",
                    )
                _confirm_scheduler_guard(
                    client,
                    config,
                    scheduler_stop_time=int(cost_guard["scheduler_stop_time"]),
                    wall_time=wall_time,
                    sleep=sleep,
                    timeline=timeline,
                )
                lease.cost_guard = dict(cost_guard)
                lease.phase = "guard_accepted_before_start"
                timeline.append(_timeline("guard_accepted_before_start", "Stopped", **cost_guard))
                lease.updated_at = _utc_now()
                if checkpoint:
                    checkpoint(lease)
            client.start()
            action_accepted = True
            lease.lifecycle_owned = True
            lease.started_by_pipeline = True
            lease.current_state = "Starting"
            lease.phase = "start_accepted"
            timeline.append(_timeline("start_accepted", "Starting"))
            lease.updated_at = _utc_now()
            if checkpoint:
                checkpoint(lease)
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
    minimum_disk_bytes = max(1, int(config.minimum_disk_bytes))
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
