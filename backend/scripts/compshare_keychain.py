#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import getpass
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.compshare_credentials import (
    KEYCHAIN_CONFIG_SCHEMA,
    KEYCHAIN_SERVICE,
    PRIVATE_KEY_ACCOUNT,
    PUBLIC_KEY_ACCOUNT,
    RUNTIME_SECRET_SCHEMA,
    CompShareCredentialError,
    load_keychain_config,
)


DEFAULT_CONFIG = Path.home() / "Library" / "Application Support" / "LuceonWeb2026" / "compshare-keychain-v1.json"
DEFAULT_RUNTIME_BASE = Path.home() / ".codex" / "runtime-secrets" / "luceonweb2026"
RUNTIME_PREFIX = "compshare-runtime-v1-"
DELETE_CONFIRMATION = "DELETE_LUCEON_COMPSHARE_KEYCHAIN_ITEMS"


def _keyring_backend():
    try:
        import keyring
        from keyring.backends.macOS import Keyring
    except Exception as exc:
        raise CompShareCredentialError("keychain_backend_unavailable", "macOS Keychain backend is unavailable") from exc
    backend = keyring.get_keyring()
    if not isinstance(backend, Keyring):
        raise CompShareCredentialError("keychain_backend_invalid", "Active keyring is not macOS Keychain")
    return backend


def _keychain_values() -> tuple[str, str]:
    backend = _keyring_backend()
    try:
        public_key = backend.get_password(KEYCHAIN_SERVICE, PUBLIC_KEY_ACCOUNT)
        private_key = backend.get_password(KEYCHAIN_SERVICE, PRIVATE_KEY_ACCOUNT)
    except Exception as exc:
        raise CompShareCredentialError("keychain_access_denied", "Keychain access was locked or denied") from exc
    if not public_key or not private_key:
        raise CompShareCredentialError("keychain_item_missing", "Project Keychain items are missing")
    return str(public_key), str(private_key)


def _status(config_path: Path) -> tuple[dict[str, Any], int]:
    status = "present"
    public_present = False
    private_present = False
    try:
        backend = _keyring_backend()
        public_present = bool(backend.get_password(KEYCHAIN_SERVICE, PUBLIC_KEY_ACCOUNT))
        private_present = bool(backend.get_password(KEYCHAIN_SERVICE, PRIVATE_KEY_ACCOUNT))
        if not (public_present and private_present):
            status = "missing"
    except CompShareCredentialError as exc:
        status = exc.code
    except Exception:
        status = "locked_or_denied"
    identity: dict[str, Any] = {"configured": False}
    try:
        config = load_keychain_config(config_path)
        identity = {
            "configured": True,
            "region": config["region"],
            "zone": config["zone"],
            "uhost_id": config["uhost_id"],
            "project_id_sha256": hashlib.sha256(config["project_id"].encode()).hexdigest(),
        }
    except CompShareCredentialError as exc:
        identity = {"configured": False, "status": exc.code}
    payload = {
        "schema": "luceon.compshare-keychain-status/v1",
        "credential_source": "macos_keychain",
        "status": status,
        "present": {"public_key": public_present, "private_key": private_present},
        "identity": identity,
    }
    return payload, 0 if status == "present" and identity.get("configured") else 2


def _write_config(path: Path, args: argparse.Namespace) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    payload = {
        "schema": KEYCHAIN_CONFIG_SCHEMA,
        "region": args.region,
        "zone": args.zone,
        "project_id": args.project_id,
        "uhost_id": args.uhost_id,
        "endpoint": args.endpoint,
        "ssh_host": args.ssh_host,
        "ssh_port": args.ssh_port,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _bootstrap(config_path: Path, args: argparse.Namespace) -> int:
    public_key = getpass.getpass("Compshare public key (hidden): ")
    private_key = getpass.getpass("Compshare private key (hidden): ")
    if not public_key or not private_key:
        raise CompShareCredentialError("credential_missing", "Both Keychain values are required")
    backend = _keyring_backend()
    try:
        backend.set_password(KEYCHAIN_SERVICE, PUBLIC_KEY_ACCOUNT, public_key)
        backend.set_password(KEYCHAIN_SERVICE, PRIVATE_KEY_ACCOUNT, private_key)
    finally:
        public_key = ""
        private_key = ""
    _write_config(config_path, args)
    print(json.dumps({"status": "stored", "credential_source": "macos_keychain", "values_returned": False}))
    return 0


def _delete(config_path: Path, confirmation: str) -> int:
    if confirmation != DELETE_CONFIRMATION:
        raise CompShareCredentialError("delete_confirmation_required", "Exact delete confirmation is required")
    backend = _keyring_backend()
    failures = []
    for account in (PUBLIC_KEY_ACCOUNT, PRIVATE_KEY_ACCOUNT):
        try:
            if backend.get_password(KEYCHAIN_SERVICE, account):
                backend.delete_password(KEYCHAIN_SERVICE, account)
        except Exception:
            failures.append(account)
    if failures:
        raise CompShareCredentialError("keychain_delete_failed", "One or more project Keychain items could not be deleted")
    if config_path.exists():
        config_path.unlink()
    print(json.dumps({"status": "deleted", "service": KEYCHAIN_SERVICE, "console_key_deleted": False}))
    return 0


def _secure_runtime_dir(base: Path, ttl_seconds: int) -> tuple[Path, Any, Path]:
    base.mkdir(parents=True, exist_ok=True)
    base.chmod(0o700)
    _cleanup_stale(base)
    runtime_dir = Path(tempfile.mkdtemp(prefix=RUNTIME_PREFIX, dir=base))
    runtime_dir.chmod(0o700)
    lock_path = runtime_dir / "lease.lock"
    lock_handle = lock_path.open("a+b")
    os.chmod(lock_path, 0o600)
    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    marker_path = runtime_dir / "runtime-owner.json"
    marker = {
        "schema": "luceon.compshare-runtime-owner/v1",
        "owner_uid": os.getuid(),
        "owner_pid": os.getpid(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    marker_descriptor = os.open(marker_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(marker_descriptor, "wb") as marker_handle:
        marker_handle.write((json.dumps(marker, sort_keys=True) + "\n").encode())
        marker_handle.flush()
        os.fsync(marker_handle.fileno())
    public_key, private_key = _keychain_values()
    now = datetime.now(timezone.utc)
    payload = {
        "schema": RUNTIME_SECRET_SCHEMA,
        "credential_source": "macos_keychain",
        "owner_uid": os.getuid(),
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
        "credentials": {"public_key": public_key, "private_key": private_key},
    }
    secret_path = runtime_dir / "compshare-credentials.json"
    descriptor = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write((json.dumps(payload, sort_keys=True) + "\n").encode())
        handle.flush()
        os.fsync(handle.fileno())
    public_key = ""
    private_key = ""
    return runtime_dir, lock_handle, secret_path


def _cleanup_stale(base: Path) -> list[str]:
    cleaned: list[str] = []
    for path in sorted(base.glob(RUNTIME_PREFIX + "*")):
        if not path.is_dir() or path.is_symlink() or path.stat().st_uid != os.getuid():
            continue
        lock_path = path / "lease.lock"
        marker_path = path / "runtime-owner.json"
        if not lock_path.is_file() or lock_path.is_symlink() or not marker_path.is_file() or marker_path.is_symlink():
            continue
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if (
            not isinstance(marker, dict)
            or marker.get("schema") != "luceon.compshare-runtime-owner/v1"
            or marker.get("owner_uid") != os.getuid()
        ):
            continue
        child_pid = marker.get("child_pid")
        child_identity = str(marker.get("child_start_identity") or "")
        if isinstance(child_pid, int) and child_pid > 0 and child_identity:
            if _process_start_identity(child_pid) == child_identity:
                continue
        docker_usage = _docker_path_usage(path / "compshare-credentials.json")
        if docker_usage is not False:
            continue
        with lock_path.open("a+b") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                continue
            shutil.rmtree(path)
            cleaned.append(path.name)
    return cleaned


def _process_start_identity(pid: int) -> str:
    completed = subprocess.run(
        ["ps", "-p", str(pid), "-o", "lstart="],
        text=True,
        capture_output=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _docker_path_usage(secret_path: Path) -> bool | None:
    try:
        listed = subprocess.run(
            ["docker", "ps", "-q"],
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if listed.returncode != 0:
        return None
    container_ids = [row for row in listed.stdout.splitlines() if row.strip()]
    if not container_ids:
        return False
    inspected = subprocess.run(
        ["docker", "inspect", *container_ids],
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    if inspected.returncode != 0:
        return None
    try:
        payload = json.loads(inspected.stdout)
    except json.JSONDecodeError:
        return None
    exact = str(secret_path.resolve())
    return any(
        str(mount.get("Source") or "") == exact
        for container in payload
        if isinstance(container, dict)
        for mount in (container.get("Mounts") or [])
        if isinstance(mount, dict)
    )


def _compose_down_command(command: list[str]) -> list[str] | None:
    if not command or "docker" not in Path(command[0]).name or "compose" not in command:
        return None
    try:
        up_index = command.index("up")
    except ValueError:
        return None
    compose_index = command.index("compose")
    if up_index <= compose_index:
        return None
    return [*command[:up_index], "down", "--remove-orphans"]


def _run(config_path: Path, runtime_base: Path, ttl_seconds: int, command: list[str]) -> int:
    if not command:
        raise CompShareCredentialError("launcher_command_missing", "A child command is required")
    if "docker" in Path(command[0]).name and "compose" in command and any(arg in {"-d", "--detach"} for arg in command):
        raise CompShareCredentialError(
            "launcher_detached_compose_forbidden",
            "Detached Compose would outlive the credential lease",
        )
    config = load_keychain_config(config_path)
    runtime_dir: Path | None = None
    lock_handle: Any | None = None
    child: subprocess.Popen[bytes] | None = None
    previous_handlers: dict[int, Any] = {}
    pending_signal = 0

    def forward(signum: int, _frame: Any) -> None:
        nonlocal pending_signal
        pending_signal = signum
        if child and child.poll() is None:
            child_pid = int(getattr(child, "pid", 0) or 0)
            if child_pid:
                os.killpg(child_pid, signum)
            else:
                child.send_signal(signum)

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.signal(signum, forward)
        runtime_dir, lock_handle, secret_path = _secure_runtime_dir(runtime_base, ttl_seconds)
        if pending_signal:
            return 128 + pending_signal
        env = os.environ.copy()
        env.update(
            {
                "COMPSHARE_CREDENTIALS_FILE": str(secret_path),
                "COMPSHARE_HOST_CREDENTIALS_FILE": str(secret_path),
                "COMPSHARE_CREDENTIAL_SOURCE": "macos_keychain_secret_file",
                "COMPSHARE_API_ENDPOINT": str(config.get("endpoint") or "https://api.compshare.cn"),
                "COMPSHARE_REGION": config["region"],
                "COMPSHARE_ZONE": config["zone"],
                "COMPSHARE_PROJECT_ID": config["project_id"],
                "COMPSHARE_UHOST_ID": config["uhost_id"],
                "GPU_SSH_HOST": str(config.get("ssh_host") or ""),
                "GPU_SSH_PORT": str(config.get("ssh_port") or 22),
            }
        )
        env.pop("COMPSHARE_PUBLIC_KEY", None)
        env.pop("COMPSHARE_PRIVATE_KEY", None)
        os.set_inheritable(lock_handle.fileno(), True)
        try:
            child = subprocess.Popen(
                command,
                env=env,
                pass_fds=(lock_handle.fileno(),),
                start_new_session=True,
            )
        finally:
            os.set_inheritable(lock_handle.fileno(), False)
        child_pid = int(getattr(child, "pid", 0) or 0)
        if child_pid:
            marker_path = runtime_dir / "runtime-owner.json"
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            marker["child_pid"] = child_pid
            marker["child_start_identity"] = _process_start_identity(child_pid)
            marker_descriptor = os.open(marker_path, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
            with os.fdopen(marker_descriptor, "wb") as marker_handle:
                marker_handle.write((json.dumps(marker, sort_keys=True) + "\n").encode())
                marker_handle.flush()
                os.fsync(marker_handle.fileno())
        if pending_signal and child.poll() is None:
            os.killpg(child_pid, pending_signal)
        child_exit = int(child.wait())
        compose_down = _compose_down_command(command)
        if compose_down:
            down = subprocess.run(compose_down, env=env, check=False)
            if down.returncode != 0 and child_exit == 0:
                return int(down.returncode or 2)
        return child_exit
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        if lock_handle is not None:
            lock_handle.close()
        if runtime_dir is not None and runtime_dir.exists():
            shutil.rmtree(runtime_dir)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Luceon macOS Keychain-backed Compshare credential launcher")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("bootstrap", "update"):
        command = sub.add_parser(name)
        command.add_argument("--region", required=True)
        command.add_argument("--zone", required=True)
        command.add_argument("--project-id", required=True)
        command.add_argument("--uhost-id", required=True)
        command.add_argument("--endpoint", default="https://api.compshare.cn")
        command.add_argument("--ssh-host", default="")
        command.add_argument("--ssh-port", type=int, default=22)
    sub.add_parser("status")
    sub.add_parser("preflight")
    delete = sub.add_parser("delete")
    delete.add_argument("--confirm", default="")
    run = sub.add_parser("run")
    run.add_argument("--runtime-base", type=Path, default=DEFAULT_RUNTIME_BASE)
    run.add_argument("--ttl-seconds", type=int, default=8 * 3600)
    run.add_argument("child", nargs=argparse.REMAINDER)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command in {"bootstrap", "update"}:
            return _bootstrap(args.config.expanduser(), args)
        if args.command == "delete":
            return _delete(args.config.expanduser(), args.confirm)
        if args.command in {"status", "preflight"}:
            payload, code = _status(args.config.expanduser())
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return code
        if args.command == "run":
            child = list(args.child)
            if child and child[0] == "--":
                child = child[1:]
            return _run(args.config.expanduser(), args.runtime_base.expanduser(), max(300, args.ttl_seconds), child)
    except CompShareCredentialError as exc:
        print(json.dumps({"status": "failed", "error_code": exc.code, "secret_returned": False}), file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
