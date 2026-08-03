#!/usr/bin/env python3
"""Explicit Worker V3 schema bootstrap and SQLite backup/restore operations.

Normal API and worker startup only validate the schema.  This script is the
only supported local-review path that creates the V3 tables.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import make_url


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.workflow_v3.database import (  # noqa: E402
    bootstrap_workflow_v3_database,
    workflow_v3_database_url,
    workflow_v3_schema_status,
)


BOOTSTRAP_CONFIRMATION = "bootstrap-worker-v3"
ROLLBACK_CONFIRMATION = "restore-worker-v3"


def _json(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _configured_url():
    value = workflow_v3_database_url()
    if not value:
        raise RuntimeError("WORKFLOW_V3_DATABASE_URL is not configured")
    return make_url(value)


def _sqlite_path(url) -> Path:
    if url.get_backend_name() != "sqlite":
        raise RuntimeError("this operation supports only a dedicated SQLite Worker V3 database")
    database = url.database or ""
    if database in {"", ":memory:"}:
        raise RuntimeError("a persistent SQLite database path is required")
    path = Path(database)
    if not path.is_absolute():
        raise RuntimeError("WORKFLOW_V3_DATABASE_URL must use an absolute SQLite path")
    return path


def _sqlite_tables(path: Path) -> set[str]:
    if not path.exists() or path.stat().st_size == 0:
        return set()
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }


def _assert_dedicated_sqlite(path: Path) -> None:
    foreign = sorted(
        table for table in _sqlite_tables(path) if not table.startswith("workflow_v3_")
    )
    if foreign:
        raise RuntimeError(
            "refusing a non-dedicated database containing non-V3 tables: "
            + ", ".join(foreign)
        )


def _integrity_check(path: Path) -> None:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()
    if not result or result[0] != "ok":
        raise RuntimeError(f"SQLite integrity_check failed for {path}")


def _backup_sqlite(source: Path, backup_dir: Path, *, label: str) -> dict:
    if not source.is_file():
        raise RuntimeError(f"SQLite source does not exist: {source}")
    _assert_dedicated_sqlite(source)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = backup_dir / f"{source.stem}-{label}-{stamp}.db"
    if target.exists():
        raise RuntimeError(f"backup destination already exists: {target}")
    with sqlite3.connect(str(source)) as source_connection:
        with sqlite3.connect(str(target)) as target_connection:
            source_connection.backup(target_connection)
    target.chmod(0o600)
    _integrity_check(target)
    manifest = {
        "schema": "luceon.worker-v3-sqlite-backup/v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": str(source),
        "backup": str(target),
        "sha256": _sha256(target),
        "size_bytes": target.stat().st_size,
        "tables": sorted(_sqlite_tables(target)),
    }
    manifest_path = target.with_suffix(".manifest.json")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest_path.chmod(0o600)
    return manifest


def status() -> dict:
    url = _configured_url()
    engine = create_engine(
        url,
        connect_args={"check_same_thread": False}
        if url.get_backend_name() == "sqlite"
        else {},
    )
    try:
        ready, detail = workflow_v3_schema_status(engine)
        tables = sorted(inspect(engine).get_table_names())
        return {
            "ok": True,
            "operation": "status",
            "dialect": engine.dialect.name,
            "ready": ready,
            "detail": detail,
            "tables": tables,
        }
    finally:
        engine.dispose()


def bootstrap(*, confirmation: str, backup_dir: Path | None) -> dict:
    if confirmation != BOOTSTRAP_CONFIRMATION:
        raise RuntimeError(f"--confirm must equal {BOOTSTRAP_CONFIRMATION!r}")
    url = _configured_url()
    backup = None
    if url.get_backend_name() == "sqlite":
        database = _sqlite_path(url)
        _assert_dedicated_sqlite(database)
        if database.is_file() and database.stat().st_size:
            if backup_dir is None:
                raise RuntimeError("--backup-dir is required before changing an existing database")
            backup = _backup_sqlite(database, backup_dir, label="pre-bootstrap")
        database.parent.mkdir(parents=True, exist_ok=True)
    elif not os.getenv("WORKFLOW_V3_EXTERNAL_BACKUP_EVIDENCE", "").strip():
        raise RuntimeError(
            "non-SQLite bootstrap requires WORKFLOW_V3_EXTERNAL_BACKUP_EVIDENCE"
        )
    engine = create_engine(
        url,
        connect_args={"check_same_thread": False}
        if url.get_backend_name() == "sqlite"
        else {},
    )
    try:
        existing = set(inspect(engine).get_table_names())
        foreign = sorted(table for table in existing if not table.startswith("workflow_v3_"))
        if foreign:
            raise RuntimeError(
                "refusing to bootstrap a database shared with non-V3 tables: "
                + ", ".join(foreign)
            )
        result = bootstrap_workflow_v3_database(engine)
        if not result.get("ready"):
            raise RuntimeError(str(result.get("detail") or "V3 schema verification failed"))
        return {
            "ok": True,
            "operation": "bootstrap",
            "database": result,
            "backup": backup,
        }
    finally:
        engine.dispose()


def backup(*, backup_dir: Path) -> dict:
    path = _sqlite_path(_configured_url())
    return {
        "ok": True,
        "operation": "backup",
        "backup": _backup_sqlite(path, backup_dir, label="manual"),
    }


def rollback(
    *,
    backup_path: Path,
    safety_backup_dir: Path,
    confirmation: str,
    services_stopped: bool,
) -> dict:
    if confirmation != ROLLBACK_CONFIRMATION:
        raise RuntimeError(f"--confirm must equal {ROLLBACK_CONFIRMATION!r}")
    if not services_stopped:
        raise RuntimeError("--services-stopped is required for an offline restore")
    target = _sqlite_path(_configured_url())
    if not backup_path.is_absolute() or not backup_path.is_file() or backup_path.is_symlink():
        raise RuntimeError("--backup must be an absolute regular file")
    _integrity_check(backup_path)
    _assert_dedicated_sqlite(backup_path)
    current_backup = (
        _backup_sqlite(target, safety_backup_dir, label="pre-rollback")
        if target.is_file() and target.stat().st_size
        else None
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.restore-{os.getpid()}")
    temporary.unlink(missing_ok=True)
    shutil.copyfile(backup_path, temporary)
    temporary.chmod(0o600)
    _integrity_check(temporary)
    os.replace(temporary, target)
    return {
        "ok": True,
        "operation": "rollback",
        "restored_from": str(backup_path),
        "restored_sha256": _sha256(target),
        "safety_backup": current_backup,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    subparsers.add_parser("status")
    bootstrap_parser = subparsers.add_parser("bootstrap")
    bootstrap_parser.add_argument("--confirm", required=True)
    bootstrap_parser.add_argument("--backup-dir", type=Path)
    backup_parser = subparsers.add_parser("backup")
    backup_parser.add_argument("--backup-dir", type=Path, required=True)
    rollback_parser = subparsers.add_parser("rollback")
    rollback_parser.add_argument("--backup", type=Path, required=True)
    rollback_parser.add_argument("--safety-backup-dir", type=Path, required=True)
    rollback_parser.add_argument("--confirm", required=True)
    rollback_parser.add_argument("--services-stopped", action="store_true")
    args = parser.parse_args()
    try:
        if args.operation == "status":
            result = status()
        elif args.operation == "bootstrap":
            result = bootstrap(
                confirmation=args.confirm,
                backup_dir=args.backup_dir.resolve() if args.backup_dir else None,
            )
        elif args.operation == "backup":
            result = backup(backup_dir=args.backup_dir.resolve())
        else:
            result = rollback(
                backup_path=args.backup.resolve(),
                safety_backup_dir=args.safety_backup_dir.resolve(),
                confirmation=args.confirm,
                services_stopped=args.services_stopped,
            )
        _json(result)
        return 0
    except Exception as exc:
        _json(
            {
                "ok": False,
                "operation": args.operation,
                "error": type(exc).__name__,
                "detail": str(exc),
            }
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
