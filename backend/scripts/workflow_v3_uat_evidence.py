#!/usr/bin/env python3
"""Collect read-only Worker V3 UAT evidence as JSON and Markdown."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.utils.minio_client import minio_client  # noqa: E402
from app.workflow_v3.uat_evidence import (  # noqa: E402
    MinioEvidenceReader,
    UatEvidencePolicy,
    WorkerV3UatEvidenceCollector,
    render_markdown,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read dedicated Worker V3 DB, material DB and exact MinIO objects; "
            "never call mutation APIs or update state."
        )
    )
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument(
        "--job-id",
        action="append",
        default=[],
        help="Worker V3 public job ID; repeat for a cohort.",
    )
    selection.add_argument(
        "--cohort-id",
        default="",
        help="Exact cohort value stored in a job payload field.",
    )
    parser.add_argument(
        "--cohort-field",
        default="cohort_id",
        help="Dotted job payload path used with --cohort-id (default: cohort_id).",
    )
    parser.add_argument(
        "--workflow-db-url",
        default=os.getenv("WORKFLOW_V3_DATABASE_URL", ""),
        help="Dedicated Worker V3 SQLAlchemy URL (defaults to WORKFLOW_V3_DATABASE_URL).",
    )
    parser.add_argument(
        "--material-db-url",
        default=os.getenv("DATABASE_URL", "sqlite:///./mineru.db"),
        help="Legacy/material SQLAlchemy URL (defaults to DATABASE_URL).",
    )
    parser.add_argument(
        "--ui-snapshot",
        type=Path,
        help="Canonical browser snapshot JSON; required for a complete verdict.",
    )
    parser.add_argument(
        "--runtime-snapshot",
        type=Path,
        help="Canonical Docker/runtime snapshot JSON; required for a complete verdict.",
    )
    parser.add_argument(
        "--stale-after-seconds",
        type=int,
        default=900,
        help="Heartbeat/candidate staleness threshold (default: 900).",
    )
    parser.add_argument(
        "--allow-missing-ui",
        action="store_true",
        help="Downgrade missing UI evidence to warning; state mismatches still block.",
    )
    parser.add_argument(
        "--allow-missing-runtime",
        action="store_true",
        help="Downgrade missing runtime snapshot to warning; observed OOM/restarts still block.",
    )
    parser.add_argument("--json-out", type=Path, required=True)
    parser.add_argument("--markdown-out", type=Path, required=True)
    return parser


def _load_json(path: Path | None) -> dict | None:
    if path is None:
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"snapshot must be a JSON object: {path}")
    return value


def _read_only_engine(url: str):
    if not url.strip():
        raise ValueError("database URL is required")
    parsed = make_url(url)
    options: dict = {"pool_pre_ping": True}
    if parsed.get_backend_name() == "sqlite":
        database = parsed.database or ""
        if database not in {"", ":memory:"}:
            database_path = Path(database)
            if not database_path.is_absolute():
                database_path = (Path.cwd() / database_path).resolve()
            parsed = make_url(
                f"sqlite:///file:{database_path.as_posix()}?mode=ro&uri=true"
            )
        options["connect_args"] = {"check_same_thread": False}
    engine = create_engine(parsed, **options)

    @event.listens_for(engine, "connect")
    def _set_read_only(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        try:
            if parsed.get_backend_name() == "sqlite":
                cursor.execute("PRAGMA query_only=ON")
            elif parsed.get_backend_name() in {"mysql", "mariadb"}:
                cursor.execute("SET SESSION TRANSACTION READ ONLY")
            elif parsed.get_backend_name().startswith("postgres"):
                cursor.execute("SET default_transaction_read_only = on")
        finally:
            cursor.close()

    return engine


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.stale_after_seconds < 30:
        raise ValueError("--stale-after-seconds must be at least 30")
    if args.json_out.resolve() == args.markdown_out.resolve():
        raise ValueError("--json-out and --markdown-out must be different files")
    workflow_engine = _read_only_engine(args.workflow_db_url)
    material_engine = _read_only_engine(args.material_db_url)
    try:
        with Session(workflow_engine, autoflush=False) as workflow_db, Session(
            material_engine, autoflush=False
        ) as material_db:
            collector = WorkerV3UatEvidenceCollector(
                workflow_db=workflow_db,
                material_db=material_db,
                object_reader=MinioEvidenceReader(minio_client),
                policy=UatEvidencePolicy(
                    stale_after_seconds=args.stale_after_seconds,
                    require_ui_snapshot=not args.allow_missing_ui,
                    require_runtime_snapshot=not args.allow_missing_runtime,
                ),
            )
            report = collector.collect(
                job_ids=args.job_id,
                cohort_id=args.cohort_id,
                cohort_field=args.cohort_field,
                ui_snapshot=_load_json(args.ui_snapshot),
                runtime_snapshot=_load_json(args.runtime_snapshot),
            )
        _write(
            args.json_out,
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        _write(args.markdown_out, render_markdown(report))
        print(
            json.dumps(
                {
                    "status": report["summary"]["status"],
                    "job_count": report["summary"]["job_count"],
                    "json": str(args.json_out),
                    "markdown": str(args.markdown_out),
                    "read_only": True,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0 if report["summary"]["status"] == "passed" else 2
    finally:
        workflow_engine.dispose()
        material_engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
