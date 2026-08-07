from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models.settings import GpuRuntimeSetting
from app.services.compshare_credentials import (
    CompShareCredentialError,
    load_runtime_credentials,
    project_secret_status,
)


SCHEMA = "luceon.gpu-runtime-setting/v2"
GIB = 1024**3
DEFAULT_MIN_FREE_DISK_BYTES = 12 * GIB
ABSOLUTE_DISK_SAFETY_FLOOR_BYTES = 8 * GIB
DEFAULT_DISK_RESERVE_BYTES = 2 * GIB
MAX_BUDGET_MICRO_CNY = 20_000_000
PROJECT_SECRET_PATH = Path(
    os.getenv("COMPSHARE_PROJECT_SECRET_FILE", "/run/luceon-project-secrets/compshare.json")
)


class GpuRuntimeSettingError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class GpuRuntimeSnapshot:
    schema_version: str
    version: int
    automatic_enabled: bool
    auto_stop: bool
    take_over_running: bool
    credential_provider: str
    credential_status: str
    credential_version: int
    endpoint: str
    region: str
    zone: str
    project_id: str
    uhost_id: str
    ssh_host: str
    ssh_port: int
    budget_micro_cny: int
    min_free_disk_bytes: int
    disk_reserve_bytes: int
    expansion_factor: int
    stop_grace_seconds: int
    kill_switch_active: bool
    effective_automatic: bool
    automation_blockers: tuple[str, ...]
    settings_sha256: str

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


def _defaults() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA,
        "version": 0,
        "automatic_enabled": False,
        "auto_stop": True,
        "take_over_running": False,
        "credential_provider": "project_secret_file",
        "endpoint": "https://api.compshare.cn",
        "region": "",
        "zone": "",
        "project_id": "",
        "uhost_id": "",
        "ssh_host": "",
        "ssh_port": 22,
        "budget_micro_cny": MAX_BUDGET_MICRO_CNY,
        "min_free_disk_bytes": DEFAULT_MIN_FREE_DISK_BYTES,
        "disk_reserve_bytes": DEFAULT_DISK_RESERVE_BYTES,
        "expansion_factor": 12,
        "stop_grace_seconds": 60,
    }


def _row_values(row: GpuRuntimeSetting | None) -> dict[str, Any]:
    if row is None:
        return _defaults()
    return {key: getattr(row, key) for key in _defaults()}


def _validate(value: dict[str, Any]) -> dict[str, Any]:
    provider = str(value.get("credential_provider") or "project_secret_file")
    if provider not in {"project_secret_file", "macos_keychain_secret_file"}:
        raise GpuRuntimeSettingError("credential_provider_invalid", "Unsupported credential provider")
    endpoint = str(value.get("endpoint") or "").strip().rstrip("/")
    if not endpoint.startswith("https://"):
        raise GpuRuntimeSettingError("endpoint_invalid", "Compshare endpoint must use HTTPS")
    budget = int(value.get("budget_micro_cny") or 0)
    if not 1_000_000 <= budget <= MAX_BUDGET_MICRO_CNY:
        raise GpuRuntimeSettingError("budget_invalid", "Per-run budget must be between CNY 1 and CNY 20")
    minimum = int(value.get("min_free_disk_bytes") or 0)
    if minimum < ABSOLUTE_DISK_SAFETY_FLOOR_BYTES:
        raise GpuRuntimeSettingError(
            "disk_floor_invalid", "GPU disk minimum is below the absolute 8 GiB safety floor"
        )
    reserve = int(value.get("disk_reserve_bytes") or 0)
    if reserve < GIB:
        raise GpuRuntimeSettingError("disk_reserve_invalid", "GPU disk reserve must be at least 1 GiB")
    expansion = int(value.get("expansion_factor") or 0)
    if not 2 <= expansion <= 64:
        raise GpuRuntimeSettingError("expansion_factor_invalid", "Expansion factor must be between 2 and 64")
    port = int(value.get("ssh_port") or 0)
    if not 1 <= port <= 65535:
        raise GpuRuntimeSettingError("ssh_port_invalid", "SSH port is invalid")
    stop_grace = int(value.get("stop_grace_seconds") or 0)
    if not 30 <= stop_grace <= 3600:
        raise GpuRuntimeSettingError("auto_stop_policy_invalid", "Stop grace must be between 30 and 3600 seconds")
    return {
        **value,
        "credential_provider": provider,
        "endpoint": endpoint,
        "budget_micro_cny": budget,
        "min_free_disk_bytes": minimum,
        "disk_reserve_bytes": reserve,
        "expansion_factor": expansion,
        "ssh_port": port,
        "stop_grace_seconds": stop_grace,
        # Automatic management is one product switch.  A managed lifecycle
        # always owns safe shutdown; manual mode never acquires ownership.
        "auto_stop": True,
        # The current contract never takes ownership of an instance that was
        # already Running before the pipeline began.
        "take_over_running": False,
    }


def _kill_switch_active() -> bool:
    return os.getenv("COMPSHARE_LIFECYCLE_KILL_SWITCH", "false").lower() in {
        "1", "true", "yes", "on",
    }


def _automation_blockers(values: dict[str, Any], secret: dict[str, Any], kill: bool) -> tuple[str, ...]:
    blockers: list[str] = []
    if not secret.get("present"):
        blockers.append("credential")
    for key in ("region", "zone", "project_id", "uhost_id", "ssh_host"):
        if not str(values.get(key) or "").strip():
            blockers.append(key)
    if not 1 <= int(values.get("ssh_port") or 0) <= 65535:
        blockers.append("ssh_port")
    if not bool(values.get("auto_stop")):
        blockers.append("auto_stop_policy")
    if kill:
        blockers.append("kill_switch")
    return tuple(blockers)


def get_setting(db: Session) -> GpuRuntimeSetting | None:
    return db.query(GpuRuntimeSetting).filter(GpuRuntimeSetting.id == 1).first()


def _credential_status(provider: str) -> dict[str, Any]:
    if provider == "project_secret_file":
        return project_secret_status(PROJECT_SECRET_PATH)
    path = os.getenv("COMPSHARE_CREDENTIALS_FILE", "").strip()
    if not path:
        return {
            "status": "missing",
            "present": False,
            "source": "macos_keychain_secret_file",
            "version": 0,
        }
    try:
        load_runtime_credentials(path)
    except CompShareCredentialError as exc:
        return {
            "status": "missing" if exc.code == "credential_file_missing" else "invalid",
            "present": False,
            "source": "macos_keychain_secret_file",
            "version": 0,
            "error_code": exc.code,
        }
    return {
        "status": "present",
        "present": True,
        "source": "macos_keychain_secret_file",
        "version": 1,
    }


def save_setting(db: Session, payload: dict[str, Any], *, user_id: str) -> GpuRuntimeSetting:
    row = get_setting(db)
    current = _row_values(row)
    expected = payload.get("expected_version")
    if expected is not None and int(expected) != int(current["version"]):
        raise GpuRuntimeSettingError("settings_version_conflict", "GPU runtime settings changed concurrently")
    allowed = set(_defaults()) - {"schema_version", "version"}
    unknown = set(payload) - allowed - {"expected_version"}
    if unknown:
        raise GpuRuntimeSettingError("settings_fields_invalid", "Unknown GPU runtime setting fields")
    merged = _validate({**current, **{key: payload[key] for key in allowed if key in payload}})
    secret = _credential_status(merged["credential_provider"])
    if bool(merged.get("automatic_enabled")):
        blockers = _automation_blockers(merged, secret, _kill_switch_active())
        if "credential" in blockers:
            raise GpuRuntimeSettingError("credential_missing", "Save credentials before enabling automatic GPU management")
        identity = [key for key in ("region", "zone", "project_id", "uhost_id", "ssh_host") if key in blockers]
        if identity:
            raise GpuRuntimeSettingError(
                "automatic_identity_incomplete",
                "Automatic GPU identity is incomplete: " + ", ".join(identity),
            )
        if "kill_switch" in blockers:
            raise GpuRuntimeSettingError("automatic_kill_switch_active", "Host fail-safe kill switch blocks automatic GPU management")
        if blockers:
            raise GpuRuntimeSettingError("automatic_policy_invalid", "Automatic GPU policy is incomplete")
    if row is None:
        row = GpuRuntimeSetting(id=1)
        db.add(row)
    for key in allowed:
        setattr(row, key, merged[key])
    row.schema_version = SCHEMA
    row.version = int(current["version"]) + 1
    row.updated_by_user_id = user_id
    db.flush()
    return row


def required_gpu_disk_bytes(snapshot: GpuRuntimeSnapshot, selected_input_bytes: int) -> int:
    return max(
        snapshot.min_free_disk_bytes,
        int(selected_input_bytes) * snapshot.expansion_factor + snapshot.disk_reserve_bytes,
    )


def load_snapshot(db: Session) -> GpuRuntimeSnapshot:
    values = _validate(_row_values(get_setting(db)))
    secret = _credential_status(values["credential_provider"])
    kill = _kill_switch_active()
    blockers = _automation_blockers(values, secret, kill)
    automatic = bool(values["automatic_enabled"] and not blockers)
    # Legacy rows that were saved as enabled while incomplete are exposed as
    # manual with explicit blockers; the API never claims configured-on while
    # execution is disabled.
    values["automatic_enabled"] = automatic
    values["auto_stop"] = True
    canonical = {
        **values,
        "credential_status": secret.get("status"),
        "credential_version": int(secret.get("version") or 0),
        "kill_switch_active": kill,
        "automation_blockers": blockers,
    }
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return GpuRuntimeSnapshot(
        **canonical,
        effective_automatic=automatic,
        settings_sha256=digest,
    )
