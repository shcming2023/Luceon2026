from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.services.compshare_credentials import (
    KEYCHAIN_CONFIG_SCHEMA,
    RUNTIME_SECRET_SCHEMA,
    CompShareCredentialError,
    load_keychain_config,
    load_runtime_credentials,
)
from app.services.compshare_lifecycle import CompShareConfig, CompShareLifecycleError


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "compshare_keychain.py"


def _script_module():
    spec = importlib.util.spec_from_file_location("task37_compshare_keychain", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _runtime_file(path: Path, *, expires_delta: int = 600, mode: int = 0o600, source: str = "macos_keychain") -> Path:
    now = datetime.now(timezone.utc)
    payload = {
        "schema": RUNTIME_SECRET_SCHEMA,
        "credential_source": source,
        "owner_uid": os.getuid(),
        "created_at": (now - timedelta(seconds=1)).isoformat(),
        "expires_at": (now + timedelta(seconds=expires_delta)).isoformat(),
        "credentials": {"public_key": "dummy-public", "private_key": "dummy-private"},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(mode)
    return path


def _config_file(path: Path) -> Path:
    payload = {
        "schema": KEYCHAIN_CONFIG_SCHEMA,
        "region": "cn-test",
        "zone": "cn-test-01",
        "project_id": "project-dummy",
        "uhost_id": "uhost-dummy",
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)
    return path


def test_runtime_secret_file_is_strictly_validated(tmp_path: Path):
    valid = _runtime_file(tmp_path / "credentials.json")
    credentials = load_runtime_credentials(valid)
    assert credentials.source == "macos_keychain_secret_file"
    assert credentials.public_key == "dummy-public"

    valid.chmod(0o644)
    with pytest.raises(CompShareCredentialError) as permissions:
        load_runtime_credentials(valid)
    assert permissions.value.code == "credential_file_permissions"

    target = _runtime_file(tmp_path / "target.json")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(CompShareCredentialError) as symlink:
        load_runtime_credentials(link)
    assert symlink.value.code == "credential_file_invalid"


def test_runtime_secret_expiry_schema_source_and_owner_fail_closed(tmp_path: Path):
    expired = _runtime_file(tmp_path / "expired.json", expires_delta=-1)
    with pytest.raises(CompShareCredentialError) as expiry:
        load_runtime_credentials(expired)
    assert expiry.value.code == "credential_expired"

    wrong_source = _runtime_file(tmp_path / "source.json", source="legacy_environment")
    with pytest.raises(CompShareCredentialError) as source:
        load_runtime_credentials(wrong_source)
    assert source.value.code == "credential_source_invalid"

    owner = _runtime_file(tmp_path / "owner.json")
    payload = json.loads(owner.read_text())
    payload["owner_uid"] += 1
    owner.write_text(json.dumps(payload))
    owner.chmod(0o600)
    with pytest.raises(CompShareCredentialError) as mismatch:
        load_runtime_credentials(owner)
    assert mismatch.value.code == "credential_file_owner"

    extra = _runtime_file(tmp_path / "extra.json")
    payload = json.loads(extra.read_text())
    payload["self_asserted_status"] = "passed"
    extra.write_text(json.dumps(payload))
    extra.chmod(0o600)
    with pytest.raises(CompShareCredentialError) as schema:
        load_runtime_credentials(extra)
    assert schema.value.code == "credential_schema_invalid"


def test_compshare_config_prefers_secret_file_and_never_returns_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    secret = _runtime_file(tmp_path / "credentials.json")
    monkeypatch.setenv("COMPSHARE_CREDENTIALS_FILE", str(secret))
    monkeypatch.setenv("COMPSHARE_PUBLIC_KEY", "legacy-public")
    monkeypatch.setenv("COMPSHARE_PRIVATE_KEY", "legacy-private")
    for key, value in {
        "COMPSHARE_REGION": "cn-test",
        "COMPSHARE_ZONE": "cn-test-01",
        "COMPSHARE_PROJECT_ID": "project-dummy",
        "COMPSHARE_UHOST_ID": "uhost-dummy",
    }.items():
        monkeypatch.setenv(key, value)
    config = CompShareConfig.from_env()
    assert config.public_key == "dummy-public"
    assert config.private_key == "dummy-private"
    public = json.dumps(config.public_identity())
    assert "dummy-public" not in public
    assert "dummy-private" not in public
    assert config.credential_source == "macos_keychain_secret_file"


def test_legacy_environment_requires_explicit_opt_in(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("COMPSHARE_CREDENTIALS_FILE", raising=False)
    monkeypatch.setenv("COMPSHARE_PUBLIC_KEY", "legacy-public")
    monkeypatch.setenv("COMPSHARE_PRIVATE_KEY", "legacy-private")
    monkeypatch.delenv("COMPSHARE_ALLOW_LEGACY_ENV", raising=False)
    with pytest.raises(CompShareLifecycleError) as blocked:
        CompShareConfig.from_env()
    assert blocked.value.code == "credential_legacy_environment_disabled"
    monkeypatch.setenv("COMPSHARE_ALLOW_LEGACY_ENV", "true")
    assert CompShareConfig.from_env().credential_source == "legacy_environment"


def test_missing_or_invalid_secret_file_blocks_lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("COMPSHARE_CREDENTIALS_FILE", str(tmp_path / "missing.json"))
    with pytest.raises(CompShareLifecycleError) as missing:
        CompShareConfig.from_env()
    assert missing.value.code == "credential_file_missing"


def test_keychain_config_is_versioned_and_nonsecret(tmp_path: Path):
    config = _config_file(tmp_path / "identity.json")
    value = load_keychain_config(config)
    assert value["uhost_id"] == "uhost-dummy"
    value["schema"] = "old"
    config.write_text(json.dumps(value))
    with pytest.raises(CompShareCredentialError) as drift:
        load_keychain_config(config)
    assert drift.value.code == "keychain_config_invalid"

    target = _config_file(tmp_path / "target.json")
    link = tmp_path / "config-link.json"
    link.symlink_to(target)
    with pytest.raises(CompShareCredentialError) as symlink:
        load_keychain_config(link)
    assert symlink.value.code == "keychain_config_invalid"


def test_launcher_creates_0700_dir_0600_file_and_cleans_after_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    module = _script_module()
    config = _config_file(tmp_path / "identity.json")
    runtime_base = tmp_path / "runtime"
    monkeypatch.setattr(module, "_keychain_values", lambda: ("dummy-public", "dummy-private"))
    observed: dict[str, object] = {}

    class Child:
        def __init__(self, command, env, **kwargs):
            path = Path(env["COMPSHARE_CREDENTIALS_FILE"])
            observed["dir_mode"] = stat.S_IMODE(path.parent.stat().st_mode)
            observed["file_mode"] = stat.S_IMODE(path.stat().st_mode)
            observed["secret_in_argv"] = "dummy-private" in " ".join(command)
            observed["secret_in_env"] = "dummy-private" in json.dumps(env)

        def wait(self):
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr(module.subprocess, "Popen", Child)
    assert module._run(config, runtime_base, 600, ["true"]) == 0
    assert observed == {"dir_mode": 0o700, "file_mode": 0o600, "secret_in_argv": False, "secret_in_env": False}
    assert list(runtime_base.glob(module.RUNTIME_PREFIX + "*")) == []


def test_launcher_cleans_after_child_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    module = _script_module()
    config = _config_file(tmp_path / "identity.json")
    runtime_base = tmp_path / "runtime"
    monkeypatch.setattr(module, "_keychain_values", lambda: ("dummy-public", "dummy-private"))

    class Failure:
        def __init__(self, command, env, **kwargs):
            pass

        def wait(self):
            return 7

        def poll(self):
            return 7

    monkeypatch.setattr(module.subprocess, "Popen", Failure)
    assert module._run(config, runtime_base, 600, ["false"]) == 7
    assert list(runtime_base.glob(module.RUNTIME_PREFIX + "*")) == []


def test_stale_cleanup_requires_owned_marker_and_unlocked_lease(tmp_path: Path):
    module = _script_module()
    module._docker_path_usage = lambda path: False
    runtime_base = tmp_path / "runtime"
    runtime_base.mkdir()
    stale = runtime_base / f"{module.RUNTIME_PREFIX}stale"
    stale.mkdir()
    (stale / "lease.lock").write_bytes(b"")
    (stale / "runtime-owner.json").write_text(
        json.dumps({"schema": "luceon.compshare-runtime-owner/v1", "owner_uid": os.getuid()})
    )
    assert module._cleanup_stale(runtime_base) == [stale.name]
    assert not stale.exists()

    foreign = runtime_base / f"{module.RUNTIME_PREFIX}foreign"
    foreign.mkdir()
    (foreign / "lease.lock").write_bytes(b"")
    (foreign / "runtime-owner.json").write_text(json.dumps({"schema": "other", "owner_uid": os.getuid()}))
    assert module._cleanup_stale(runtime_base) == []
    assert foreign.exists()


def test_stale_cleanup_fails_closed_when_docker_usage_is_unknown_or_active(tmp_path: Path):
    module = _script_module()
    runtime_base = tmp_path / "runtime"
    runtime_base.mkdir()
    for suffix, usage in (("unknown", None), ("active", True)):
        stale = runtime_base / f"{module.RUNTIME_PREFIX}{suffix}"
        stale.mkdir()
        (stale / "lease.lock").write_bytes(b"")
        (stale / "runtime-owner.json").write_text(
            json.dumps({"schema": "luceon.compshare-runtime-owner/v1", "owner_uid": os.getuid()})
        )
        module._docker_path_usage = lambda _path, value=usage: value
        assert module._cleanup_stale(runtime_base) == []
        assert stale.exists()


def test_detached_compose_is_rejected_before_keychain_read(tmp_path: Path):
    module = _script_module()
    config = _config_file(tmp_path / "identity.json")
    with pytest.raises(CompShareCredentialError) as blocked:
        module._run(config, tmp_path / "runtime", 600, ["docker", "compose", "up", "-d"])
    assert blocked.value.code == "launcher_detached_compose_forbidden"


def test_foreground_compose_is_torn_down_before_secret_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    module = _script_module()
    config = _config_file(tmp_path / "identity.json")
    runtime_base = tmp_path / "runtime"
    monkeypatch.setattr(module, "_keychain_values", lambda: ("dummy-public", "dummy-private"))
    calls: list[list[str]] = []

    class Child:
        pid = 0

        def __init__(self, command, env, **kwargs):
            calls.append(list(command))

        def wait(self):
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr(module.subprocess, "Popen", Child)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda command, **_kwargs: calls.append(list(command)) or subprocess.CompletedProcess(command, 0),
    )
    command = ["docker", "compose", "--project-name", "task37", "-f", "compose.yml", "up", "--abort-on-container-exit"]
    assert module._run(config, runtime_base, 600, command) == 0
    assert calls == [
        command,
        ["docker", "compose", "--project-name", "task37", "-f", "compose.yml", "down", "--remove-orphans"],
    ]
    assert list(runtime_base.glob(module.RUNTIME_PREFIX + "*")) == []


def test_preflight_output_never_contains_dummy_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    module = _script_module()
    config = _config_file(tmp_path / "identity.json")

    class Backend:
        def get_password(self, service, account):
            return "dummy-private-secret" if "private" in account else "dummy-public-secret"

    monkeypatch.setattr(module, "_keyring_backend", lambda: Backend())
    payload, code = module._status(config)
    rendered = json.dumps(payload)
    assert code == 0
    assert "dummy-private-secret" not in rendered
    assert "dummy-public-secret" not in rendered


def test_cli_missing_keychain_fails_without_leaking_values(tmp_path: Path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--config", str(tmp_path / "missing.json"), "status"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert "dummy-public" not in result.stdout + result.stderr
    assert "dummy-private" not in result.stdout + result.stderr


def test_runtime_preflight_invalid_file_is_sanitized(tmp_path: Path):
    invalid = tmp_path / "credential.json"
    invalid.write_text('{"secret":"must-not-appear"}', encoding="utf-8")
    invalid.chmod(0o600)
    environment = os.environ.copy()
    environment["COMPSHARE_CREDENTIALS_FILE"] = str(invalid)
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH.parent / "compshare_runtime_preflight.py")],
        cwd=SCRIPT_PATH.parents[1],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["ready"] is False
    assert payload["secret_returned"] is False
    assert "must-not-appear" not in result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr


def test_status_reports_locked_or_denied_without_exception_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    module = _script_module()
    config = _config_file(tmp_path / "identity.json")

    class Backend:
        def get_password(self, _service, _account):
            raise RuntimeError("sensitive keychain diagnostic")

    monkeypatch.setattr(module, "_keyring_backend", lambda: Backend())
    payload, code = module._status(config)
    rendered = json.dumps(payload)
    assert code == 2
    assert payload["status"] == "locked_or_denied"
    assert "sensitive keychain diagnostic" not in rendered
