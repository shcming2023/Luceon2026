from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RUNTIME_SECRET_SCHEMA = "luceon.compshare-runtime-secret/v1"
KEYCHAIN_CONFIG_SCHEMA = "luceon.compshare-keychain-config/v1"
KEYCHAIN_SERVICE = "com.luceonweb2026.compshare.v1"
PUBLIC_KEY_ACCOUNT = "api-public-key"
PRIVATE_KEY_ACCOUNT = "api-private-key"


class CompShareCredentialError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RuntimeCredentials:
    public_key: str
    private_key: str
    source: str
    expires_at: str


def _parse_timestamp(value: Any, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise CompShareCredentialError("credential_schema_invalid", f"Invalid {field}") from exc
    if parsed.tzinfo is None:
        raise CompShareCredentialError("credential_schema_invalid", f"Invalid {field}")
    return parsed.astimezone(timezone.utc)


def load_runtime_credentials(path_value: str | os.PathLike[str], *, now: datetime | None = None) -> RuntimeCredentials:
    path = Path(path_value)
    if not path.is_absolute():
        raise CompShareCredentialError("credential_file_invalid", "Credential file path must be absolute")
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise CompShareCredentialError("credential_file_missing", "Credential file is missing") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise CompShareCredentialError("credential_file_invalid", "Credential file must be a regular non-symlink")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise CompShareCredentialError("credential_file_permissions", "Credential file permissions must be 0600 or stricter")
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompShareCredentialError("credential_file_invalid", "Credential file is unreadable or invalid") from exc
    expected_fields = {"schema", "credential_source", "owner_uid", "created_at", "expires_at", "credentials"}
    if (
        not isinstance(payload, dict)
        or set(payload) != expected_fields
        or payload.get("schema") != RUNTIME_SECRET_SCHEMA
    ):
        raise CompShareCredentialError("credential_schema_invalid", "Credential file schema is not supported")
    owner_uid = payload.get("owner_uid")
    docker_secret_owner = path.parent == Path("/run/secrets") and info.st_uid == os.geteuid()
    if isinstance(owner_uid, bool) or not isinstance(owner_uid, int) or not (owner_uid == info.st_uid or docker_secret_owner):
        raise CompShareCredentialError("credential_file_owner", "Credential file owner does not match its signed metadata")
    if payload.get("credential_source") != "macos_keychain":
        raise CompShareCredentialError("credential_source_invalid", "Credential source is not macOS Keychain")
    created_at = _parse_timestamp(payload.get("created_at"), "created_at")
    expires_at = _parse_timestamp(payload.get("expires_at"), "expires_at")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if created_at > current or expires_at <= current:
        raise CompShareCredentialError("credential_expired", "Credential file is not currently valid")
    credentials = payload.get("credentials")
    if not isinstance(credentials, dict) or set(credentials) != {"public_key", "private_key"}:
        raise CompShareCredentialError("credential_schema_invalid", "Credential fields are incomplete")
    public_key = credentials.get("public_key")
    private_key = credentials.get("private_key")
    if not isinstance(public_key, str) or not public_key or not isinstance(private_key, str) or not private_key:
        raise CompShareCredentialError("credential_missing", "Credential values are missing")
    return RuntimeCredentials(
        public_key=public_key,
        private_key=private_key,
        source="macos_keychain_secret_file",
        expires_at=expires_at.isoformat(),
    )


def load_keychain_config(path_value: str | os.PathLike[str]) -> dict[str, Any]:
    path = Path(path_value).expanduser()
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise CompShareCredentialError("keychain_config_invalid", "Keychain identity config must be a regular non-symlink")
        if stat.S_IMODE(info.st_mode) & 0o077 or info.st_uid != os.getuid():
            raise CompShareCredentialError("keychain_config_invalid", "Keychain identity config permissions or owner are invalid")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CompShareCredentialError("keychain_config_missing", "Keychain identity config is missing") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompShareCredentialError("keychain_config_invalid", "Keychain identity config is invalid") from exc
    required = {"schema", "region", "zone", "project_id", "uhost_id"}
    optional = {"endpoint", "ssh_host", "ssh_port", "updated_at"}
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != KEYCHAIN_CONFIG_SCHEMA
        or not required.issubset(payload)
        or not set(payload).issubset(required | optional)
    ):
        raise CompShareCredentialError("keychain_config_invalid", "Keychain identity config schema is invalid")
    for key in required - {"schema"}:
        if not isinstance(payload.get(key), str) or not payload[key].strip():
            raise CompShareCredentialError("keychain_config_invalid", f"Keychain identity field is missing: {key}")
    return payload
