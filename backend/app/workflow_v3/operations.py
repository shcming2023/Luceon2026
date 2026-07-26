from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Mapping

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.workflow_v3.models import (
    WorkflowV3Candidate,
    WorkflowV3Evaluation,
    WorkflowV3Execution,
    WorkflowV3Job,
    WorkflowV3ProjectionOutbox,
    WorkflowV3Promotion,
    WorkflowV3SkillRelease,
    WorkflowV3StageRun,
    WorkflowV3WorkerHeartbeat,
)


REQUIRED_WORKER_ROLES = ("producer", "evaluator", "promoter", "projector")
WORKER_STATUSES = frozenset({"starting", "idle", "busy", "degraded", "stopped"})


def record_worker_heartbeat(
    db: Session,
    *,
    worker_id: str,
    role: str,
    status: str,
    runtime_identity_sha256: str = "",
    current_job_id: str = "",
    current_stage_key: str = "",
    last_error: str = "",
    metrics: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> WorkflowV3WorkerHeartbeat:
    if not worker_id:
        raise ValueError("worker_id is required")
    if role not in REQUIRED_WORKER_ROLES:
        raise ValueError("unknown Worker V3 role")
    if status not in WORKER_STATUSES:
        raise ValueError("unknown Worker V3 worker status")
    if runtime_identity_sha256 and (
        len(runtime_identity_sha256) != 64
        or any(char not in "0123456789abcdef" for char in runtime_identity_sha256)
    ):
        raise ValueError("runtime_identity_sha256 must be empty or lowercase SHA-256")
    if metrics is not None and not isinstance(metrics, Mapping):
        raise ValueError("metrics must be an object")
    timestamp = now or datetime.utcnow()
    row = (
        db.query(WorkflowV3WorkerHeartbeat)
        .filter(WorkflowV3WorkerHeartbeat.worker_id == worker_id)
        .with_for_update()
        .first()
    )
    if row is None:
        row = WorkflowV3WorkerHeartbeat(
            worker_id=worker_id,
            role=role,
            started_at=timestamp,
        )
        db.add(row)
    elif row.role != role:
        raise ValueError("worker_id is already bound to another role")
    row.status = status
    row.runtime_identity_sha256 = runtime_identity_sha256
    row.current_job_public_id = current_job_id
    row.current_stage_key = current_stage_key
    row.last_error = last_error[:4000]
    row.metrics_json = WorkflowV3WorkerHeartbeat.dump(dict(metrics or {}))
    row.heartbeat_at = timestamp
    db.flush()
    return row


def operational_snapshot(
    db: Session,
    *,
    stale_after_seconds: int | None = None,
    now: datetime | None = None,
) -> dict:
    timestamp = now or datetime.utcnow()
    stale_seconds = (
        int(stale_after_seconds)
        if stale_after_seconds is not None
        else max(15, int(os.getenv("WORKFLOW_V3_WORKER_STALE_AFTER_SECONDS", "45")))
    )
    cutoff = timestamp - timedelta(seconds=stale_seconds)
    workers = (
        db.query(WorkflowV3WorkerHeartbeat)
        .order_by(
            WorkflowV3WorkerHeartbeat.role.asc(),
            WorkflowV3WorkerHeartbeat.heartbeat_at.desc(),
        )
        .all()
    )
    by_role: dict[str, list[WorkflowV3WorkerHeartbeat]] = {
        role: [] for role in REQUIRED_WORKER_ROLES
    }
    for worker in workers:
        if worker.role in by_role:
            by_role[worker.role].append(worker)
    registered_release_rows = (
        db.query(WorkflowV3SkillRelease)
        .filter(WorkflowV3SkillRelease.status == "registered")
        .all()
    )
    registered_runtime_identities = {
        row.runtime_identity_sha256 for row in registered_release_rows
    }
    role_rows: dict[str, dict] = {}
    all_roles_ready = True
    any_role_missing_or_stale = False
    any_role_runtime_mismatch = False
    ready_runtime_sets: list[set[str]] = []
    for role in REQUIRED_WORKER_ROLES:
        rows = by_role[role]
        healthy_fresh = [
            row
            for row in rows
            if row.heartbeat_at >= cutoff and row.status not in {"degraded", "stopped"}
        ]
        fresh = [
            row
            for row in healthy_fresh
            if row.runtime_identity_sha256 in registered_runtime_identities
        ]
        any_role_missing_or_stale = (
            any_role_missing_or_stale or not healthy_fresh
        )
        any_role_runtime_mismatch = (
            any_role_runtime_mismatch
            or bool(healthy_fresh and not fresh)
        )
        all_roles_ready = all_roles_ready and bool(fresh)
        role_runtime_identities = {
            row.runtime_identity_sha256 for row in fresh
        }
        ready_runtime_sets.append(role_runtime_identities)
        role_rows[role] = {
            "ready": bool(fresh),
            "fresh_count": len(fresh),
            "runtime_identities": sorted(role_runtime_identities),
            "healthy_unbound_or_mismatched_count": (
                len(healthy_fresh) - len(fresh)
            ),
            "workers": [row.to_dict() for row in rows],
        }
    common_runtime_identities = (
        set.intersection(*ready_runtime_sets)
        if ready_runtime_sets
        else set()
    )
    if all_roles_ready and not common_runtime_identities:
        all_roles_ready = False
        any_role_runtime_mismatch = True

    registered_releases = len(registered_release_rows)
    queues = {
        "producer": _producer_queue_count(db),
        "evaluation": _evaluation_queue_count(db),
        "promotion": _promotion_queue_count(db),
        "projection": (
            db.query(WorkflowV3ProjectionOutbox)
            .filter(WorkflowV3ProjectionOutbox.status.in_(("pending", "processing")))
            .count()
        ),
    }
    active_executions = (
        db.query(WorkflowV3Execution)
        .filter(WorkflowV3Execution.machine_status == "running")
        .count()
    )
    stale_executions = (
        db.query(WorkflowV3Execution)
        .filter(
            WorkflowV3Execution.machine_status == "running",
            WorkflowV3Execution.heartbeat_at < cutoff,
        )
        .count()
    )
    artifact_backend = os.getenv("WORKFLOW_V3_ARTIFACT_BACKEND", "").strip().lower()
    environment = os.getenv("LUCEON_ENVIRONMENT", "development").strip().lower()
    allow_directory = (
        environment in {"development", "test"}
        and os.getenv("WORKFLOW_V3_ALLOW_DIRECTORY_ARTIFACTS", "false").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    artifact_backend_ready = artifact_backend == "minio" or (
        artifact_backend == "directory" and allow_directory
    )
    execution_enabled = (
        registered_releases > 0
        and all_roles_ready
        and artifact_backend_ready
        and stale_executions == 0
    )
    blockers: list[str] = []
    if registered_releases == 0:
        blockers.append("no_registered_skill_release")
    if any_role_missing_or_stale:
        blockers.append("required_worker_role_missing_or_stale")
    if any_role_runtime_mismatch:
        blockers.append("required_worker_runtime_unbound_or_mismatched")
    if not artifact_backend_ready:
        blockers.append("artifact_backend_not_admitted")
    if stale_executions:
        blockers.append("stale_execution_present")
    return {
        "execution_enabled": execution_enabled,
        "blockers": blockers,
        "checked_at": timestamp.isoformat() + "Z",
        "worker_stale_after_seconds": stale_seconds,
        "workers": role_rows,
        "ready_runtime_identities": sorted(common_runtime_identities),
        "registered_release_count": registered_releases,
        "artifact_backend": {
            "mode": artifact_backend or "unconfigured",
            "ready": artifact_backend_ready,
            "directory_mode_admitted": allow_directory,
        },
        "queues": queues,
        "active_executions": active_executions,
        "stale_executions": stale_executions,
        "jobs": {
            status: db.query(WorkflowV3Job)
            .filter(WorkflowV3Job.machine_status == status)
            .count()
            for status in (
                "queued",
                "running",
                "needs_review",
                "failed",
                "cancelled",
                "succeeded",
            )
        },
        "projection_outbox": {
            status: db.query(WorkflowV3ProjectionOutbox)
            .filter(WorkflowV3ProjectionOutbox.status == status)
            .count()
            for status in ("pending", "processing", "applied", "failed", "suppressed")
        },
    }


def _producer_queue_count(db: Session) -> int:
    return int(
        db.query(func.count(WorkflowV3StageRun.id))
        .join(
            WorkflowV3Job,
            WorkflowV3Job.id == WorkflowV3StageRun.workflow_job_id,
        )
        .filter(
            WorkflowV3Job.machine_status == "queued",
            WorkflowV3StageRun.machine_status == "queued",
            WorkflowV3Job.current_stage_key == WorkflowV3StageRun.stage_key,
        )
        .scalar()
        or 0
    )


def _evaluation_queue_count(db: Session) -> int:
    return int(
        db.query(func.count(WorkflowV3Candidate.id))
        .join(
            WorkflowV3StageRun,
            WorkflowV3StageRun.id == WorkflowV3Candidate.stage_run_id,
        )
        .join(
            WorkflowV3Job,
            WorkflowV3Job.id == WorkflowV3StageRun.workflow_job_id,
        )
        .filter(
            WorkflowV3Job.machine_status == "running",
            WorkflowV3StageRun.machine_status == "awaiting_evaluation",
            WorkflowV3Candidate.status == "candidate",
        )
        .scalar()
        or 0
    )


def _promotion_queue_count(db: Session) -> int:
    return int(
        db.query(func.count(WorkflowV3Evaluation.id))
        .join(
            WorkflowV3StageRun,
            WorkflowV3StageRun.id == WorkflowV3Evaluation.stage_run_id,
        )
        .join(
            WorkflowV3Job,
            WorkflowV3Job.id == WorkflowV3StageRun.workflow_job_id,
        )
        .outerjoin(
            WorkflowV3Promotion,
            WorkflowV3Promotion.evaluation_id == WorkflowV3Evaluation.id,
        )
        .filter(
            WorkflowV3Job.machine_status == "running",
            WorkflowV3StageRun.machine_status == "awaiting_promotion",
            WorkflowV3Evaluation.decision == "passed",
            WorkflowV3Promotion.id.is_(None),
        )
        .scalar()
        or 0
    )
