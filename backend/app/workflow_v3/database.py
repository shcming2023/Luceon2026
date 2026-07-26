from __future__ import annotations

import os
from functools import lru_cache

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.workflow_v3.models import WorkflowV3Base, WorkflowV3SchemaRevision


WORKFLOW_V3_SCHEMA_REVISION = "worker-v3-schema-0007"
EXPERIMENT_ONLY_WORKFLOW_V3_TABLES = frozenset(
    {
        "workflow_v3_expert_runs",
        "workflow_v3_expert_events",
    }
)
REQUIRED_WORKFLOW_V3_TABLES = (
    frozenset(WorkflowV3Base.metadata.tables)
    - EXPERIMENT_ONLY_WORKFLOW_V3_TABLES
)
REQUIRED_WORKFLOW_V3_COLUMNS = {
    "workflow_v3_jobs": {
        "machine_status",
        "spec_status",
        "readiness_status",
        "human_acceptance_status",
        "skill_release_id",
        "current_generation",
    },
    "workflow_v3_stage_runs": {
        "input_promotion_id",
        "input_artifact_sha256",
        "promotion_id",
        "promoted_artifact_sha256",
        "generation",
        "review_resolution_id",
        "review_resolution_sha256",
    },
    "workflow_v3_executions": {
        "heartbeat_at",
        "runtime_identity_sha256",
        "generation",
        "review_resolution_sha256",
    },
    "workflow_v3_candidates": {
        "generation",
        "review_resolution_sha256",
    },
    "workflow_v3_evaluations": {
        "spec_passed",
        "gate_results_json",
        "generation",
        "review_resolution_sha256",
    },
    "workflow_v3_review_resolutions": {
        "workflow_job_id",
        "evaluation_id",
        "idempotency_key",
        "evaluation_sha256",
        "finding_fingerprints_json",
        "authorized_by",
        "recovery_stage_key",
        "source_generation",
        "recovery_generation",
        "manifest_bucket",
        "manifest_object",
        "manifest_sha256",
        "manifest_size_bytes",
        "manifest_json",
    },
    "workflow_v3_operation_attempts": {
        "operation",
        "target_id",
        "attempt",
        "status",
        "owner_identity",
        "owner_token_sha256",
        "max_attempts",
        "lease_seconds",
        "lease_expires_at",
        "heartbeat_at",
        "error_code",
        "error_message",
    },
    "workflow_v3_model_calls": {
        "provider",
        "model",
        "prompt_sha256",
        "schema_sha256",
        "input_sha256",
        "usage_json",
        "pricing_snapshot_sha256",
        "cost_status",
        "cost_currency",
        "cost_micro_units",
        "cost_breakdown_json",
    },
    "workflow_v3_worker_heartbeats": {
        "worker_id",
        "role",
        "status",
        "runtime_identity_sha256",
        "heartbeat_at",
    },
    "workflow_v3_projection_outbox": {
        "workflow_job_id",
        "final_promotion_id",
        "idempotency_key",
        "event_kind",
        "status",
        "payload_json",
        "formal_target_bucket",
        "formal_target_prefix",
        "formal_target_manifest_object",
        "projected_output_id",
        "projected_manifest_sha256",
    },
    "workflow_v3_schema_revisions": {
        "revision",
        "description",
        "applied_at",
    },
}


def workflow_v3_database_url() -> str:
    return os.getenv("WORKFLOW_V3_DATABASE_URL", "").strip()


@lru_cache(maxsize=1)
def workflow_v3_engine() -> Engine:
    url = workflow_v3_database_url()
    if not url:
        raise RuntimeError("WORKFLOW_V3_DATABASE_URL is not configured")
    options = {"pool_pre_ping": True, "pool_recycle": 1800}
    if url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **options)


@lru_cache(maxsize=1)
def workflow_v3_session_factory():
    return sessionmaker(
        bind=workflow_v3_engine(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )


def workflow_v3_schema_status(engine: Engine) -> tuple[bool, str]:
    existing = set(inspect(engine).get_table_names())
    experiment_tables = sorted(EXPERIMENT_ONLY_WORKFLOW_V3_TABLES & existing)
    if experiment_tables:
        return False, (
            "experiment-only tables are present in the Worker V3 production "
            f"database: {', '.join(experiment_tables)}"
        )
    missing_tables = sorted(REQUIRED_WORKFLOW_V3_TABLES - existing)
    if missing_tables:
        return False, f"missing Worker V3 tables: {', '.join(missing_tables)}"
    for table_name, required_columns in REQUIRED_WORKFLOW_V3_COLUMNS.items():
        actual = {row["name"] for row in inspect(engine).get_columns(table_name)}
        missing_columns = sorted(required_columns - actual)
        if missing_columns:
            return False, f"missing {table_name} columns: {', '.join(missing_columns)}"
    with Session(bind=engine) as db:
        revision = db.get(WorkflowV3SchemaRevision, WORKFLOW_V3_SCHEMA_REVISION)
        if revision is None:
            return False, (
                "missing Worker V3 schema revision: "
                f"{WORKFLOW_V3_SCHEMA_REVISION}"
            )
    return True, "ok"


def initialize_workflow_v3_database() -> dict[str, str | bool]:
    """Validate a provisioned V3 schema without mutating it."""
    if not workflow_v3_database_url():
        return {
            "configured": False,
            "ready": False,
            "detail": "WORKFLOW_V3_DATABASE_URL is not configured",
        }
    try:
        engine = workflow_v3_engine()
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        ready, detail = workflow_v3_schema_status(engine)
        return {
            "configured": True,
            "ready": ready,
            "dialect": engine.dialect.name,
            "detail": detail,
        }
    except Exception as exc:
        return {"configured": True, "ready": False, "dialect": "", "detail": str(exc)}


def bootstrap_workflow_v3_database(engine: Engine) -> dict[str, str | bool]:
    """Explicit migration/bootstrap hook; never called by normal startup."""
    WorkflowV3Base.metadata.create_all(
        bind=engine,
        tables=[
            WorkflowV3Base.metadata.tables[name]
            for name in sorted(REQUIRED_WORKFLOW_V3_TABLES)
        ],
    )
    _upgrade_projection_target_binding(engine)
    _upgrade_review_resolution_binding(engine)
    _upgrade_model_cost_binding(engine)
    with Session(bind=engine) as db:
        revision = db.get(WorkflowV3SchemaRevision, WORKFLOW_V3_SCHEMA_REVISION)
        if revision is None:
            db.add(
                WorkflowV3SchemaRevision(
                    revision=WORKFLOW_V3_SCHEMA_REVISION,
                    description=(
                        "Exclude Expert Lane experiment tables and fields from "
                        "the pure Worker production schema."
                    ),
                )
            )
        db.commit()
    ready, detail = workflow_v3_schema_status(engine)
    return {"ready": ready, "dialect": engine.dialect.name, "detail": detail}


def _upgrade_projection_target_binding(engine: Engine) -> None:
    table = "workflow_v3_projection_outbox"
    if table not in inspect(engine).get_table_names():
        return
    existing = {row["name"] for row in inspect(engine).get_columns(table)}
    definitions = {
        "formal_target_bucket": "VARCHAR(128) NOT NULL DEFAULT ''",
        "formal_target_prefix": "VARCHAR(1024) NOT NULL DEFAULT ''",
        "formal_target_manifest_object": "VARCHAR(1024) NOT NULL DEFAULT ''",
    }
    with engine.begin() as connection:
        for name, definition in definitions.items():
            if name not in existing:
                connection.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
                )


def _upgrade_review_resolution_binding(engine: Engine) -> None:
    definitions_by_table = {
        "workflow_v3_jobs": {
            "current_generation": "INTEGER NOT NULL DEFAULT 1",
        },
        "workflow_v3_stage_runs": {
            "generation": "INTEGER NOT NULL DEFAULT 1",
            "review_resolution_id": "INTEGER",
            "review_resolution_sha256": "VARCHAR(64) NOT NULL DEFAULT ''",
        },
        "workflow_v3_executions": {
            "generation": "INTEGER NOT NULL DEFAULT 1",
            "review_resolution_sha256": "VARCHAR(64) NOT NULL DEFAULT ''",
        },
        "workflow_v3_candidates": {
            "generation": "INTEGER NOT NULL DEFAULT 1",
            "review_resolution_sha256": "VARCHAR(64) NOT NULL DEFAULT ''",
        },
        "workflow_v3_evaluations": {
            "generation": "INTEGER NOT NULL DEFAULT 1",
            "review_resolution_sha256": "VARCHAR(64) NOT NULL DEFAULT ''",
        },
    }
    tables = set(inspect(engine).get_table_names())
    with engine.begin() as connection:
        for table, definitions in definitions_by_table.items():
            if table not in tables:
                continue
            existing = {
                row["name"] for row in inspect(engine).get_columns(table)
            }
            for name, definition in definitions.items():
                if name not in existing:
                    connection.execute(
                        text(
                            f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
                        )
                    )


def _upgrade_model_cost_binding(engine: Engine) -> None:
    table = "workflow_v3_model_calls"
    if table not in inspect(engine).get_table_names():
        return
    existing = {row["name"] for row in inspect(engine).get_columns(table)}
    definitions = {
        "pricing_snapshot_sha256": "VARCHAR(64) NOT NULL DEFAULT ''",
        "cost_status": (
            "VARCHAR(48) NOT NULL DEFAULT 'legacy_unaccounted'"
        ),
        "cost_currency": "VARCHAR(8) NOT NULL DEFAULT ''",
        "cost_micro_units": "INTEGER",
        "cost_breakdown_json": "TEXT NOT NULL DEFAULT '{}'",
    }
    with engine.begin() as connection:
        for name, definition in definitions.items():
            if name not in existing:
                connection.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
                )


def get_workflow_v3_db():
    try:
        factory = workflow_v3_session_factory()
    except Exception as exc:
        raise RuntimeError(str(exc)) from exc
    db: Session = factory()
    try:
        yield db
    finally:
        db.close()
