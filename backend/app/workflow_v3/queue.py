from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.workflow_v3.models import (
    WorkflowV3Candidate,
    WorkflowV3Evaluation,
    WorkflowV3Job,
    WorkflowV3Promotion,
    WorkflowV3SkillRelease,
    WorkflowV3StageRun,
)
from app.workflow_v3.state_machine import cancel_job, recover_stale_executions
from app.workflow_v3.state_machine import (
    WorkflowV3TransitionError,
    claim_operation_attempt,
    recover_stale_operation_attempts,
)


@dataclass(frozen=True)
class ProducerQueueItem:
    public_id: str
    stage_key: str
    attempt: int


@dataclass(frozen=True)
class EvaluationQueueItem:
    public_id: str
    candidate_id: int
    stage_key: str
    operation_attempt_id: int | None = None
    owner_token: str = ""
    operation_attempt: int = 0


@dataclass(frozen=True)
class PromotionQueueItem:
    public_id: str
    evaluation_id: int
    stage_key: str
    operation_attempt_id: int | None = None
    owner_token: str = ""
    operation_attempt: int = 0


def _validated_runtime_identity(value: str | None) -> str | None:
    if value is None:
        return None
    if len(value) != 64 or any(
        char not in "0123456789abcdef" for char in value
    ):
        raise ValueError("runtime_identity_sha256 must be a lowercase SHA-256")
    return value


def next_producer_item(
    db: Session,
    *,
    runtime_identity_sha256: str | None = None,
) -> ProducerQueueItem | None:
    """Read the next producer item; claim_current_stage owns the real lease."""
    runtime_identity_sha256 = _validated_runtime_identity(
        runtime_identity_sha256
    )
    query = (
        db.query(WorkflowV3Job, WorkflowV3StageRun)
        .join(
            WorkflowV3StageRun,
            (WorkflowV3StageRun.workflow_job_id == WorkflowV3Job.id)
            & (WorkflowV3StageRun.stage_key == WorkflowV3Job.current_stage_key),
        )
        .filter(
            WorkflowV3Job.machine_status == "queued",
            WorkflowV3StageRun.machine_status == "queued",
        )
    )
    if runtime_identity_sha256 is not None:
        query = query.join(
            WorkflowV3SkillRelease,
            WorkflowV3SkillRelease.id == WorkflowV3Job.skill_release_id,
        ).filter(
            WorkflowV3SkillRelease.runtime_identity_sha256
            == runtime_identity_sha256
        )
    row = (
        query.order_by(
            WorkflowV3Job.priority.desc(),
            WorkflowV3Job.created_at.asc(),
            WorkflowV3StageRun.attempt.desc(),
        )
        .with_for_update(skip_locked=True)
        .first()
    )
    if not row:
        return None
    job, stage = row
    return ProducerQueueItem(job.public_id, stage.stage_key, stage.attempt)


def next_evaluation_item(db: Session) -> EvaluationQueueItem | None:
    row = (
        db.query(WorkflowV3Job, WorkflowV3StageRun, WorkflowV3Candidate)
        .join(
            WorkflowV3StageRun,
            WorkflowV3StageRun.workflow_job_id == WorkflowV3Job.id,
        )
        .join(
            WorkflowV3Candidate,
            WorkflowV3Candidate.stage_run_id == WorkflowV3StageRun.id,
        )
        .filter(
            WorkflowV3Job.machine_status == "running",
            WorkflowV3Job.current_stage_key == WorkflowV3StageRun.stage_key,
            WorkflowV3StageRun.machine_status == "awaiting_evaluation",
            WorkflowV3Candidate.status == "candidate",
        )
        .order_by(WorkflowV3Candidate.id.asc())
        .with_for_update(skip_locked=True)
        .first()
    )
    if not row:
        return None
    job, stage, candidate = row
    return EvaluationQueueItem(job.public_id, candidate.id, stage.stage_key)


def next_promotion_item(db: Session) -> PromotionQueueItem | None:
    row = (
        db.query(WorkflowV3Job, WorkflowV3StageRun, WorkflowV3Evaluation)
        .join(
            WorkflowV3StageRun,
            WorkflowV3StageRun.workflow_job_id == WorkflowV3Job.id,
        )
        .join(
            WorkflowV3Evaluation,
            WorkflowV3Evaluation.stage_run_id == WorkflowV3StageRun.id,
        )
        .outerjoin(
            WorkflowV3Promotion,
            WorkflowV3Promotion.evaluation_id == WorkflowV3Evaluation.id,
        )
        .filter(
            WorkflowV3Job.machine_status == "running",
            WorkflowV3Job.current_stage_key == WorkflowV3StageRun.stage_key,
            WorkflowV3StageRun.machine_status == "awaiting_promotion",
            WorkflowV3Evaluation.decision == "passed",
            WorkflowV3Evaluation.spec_passed.is_(True),
            WorkflowV3Promotion.id.is_(None),
        )
        .order_by(WorkflowV3Evaluation.id.asc())
        .with_for_update(skip_locked=True)
        .first()
    )
    if not row:
        return None
    job, stage, evaluation = row
    return PromotionQueueItem(job.public_id, evaluation.id, stage.stage_key)


def claim_next_evaluation_item(
    db: Session,
    *,
    owner_identity: str,
    lease_seconds: int = 300,
    max_attempts: int = 3,
    runtime_identity_sha256: str | None = None,
) -> EvaluationQueueItem | None:
    runtime_identity_sha256 = _validated_runtime_identity(
        runtime_identity_sha256
    )
    query = (
        db.query(WorkflowV3Job, WorkflowV3StageRun, WorkflowV3Candidate)
        .join(
            WorkflowV3StageRun,
            WorkflowV3StageRun.workflow_job_id == WorkflowV3Job.id,
        )
        .join(
            WorkflowV3Candidate,
            WorkflowV3Candidate.stage_run_id == WorkflowV3StageRun.id,
        )
        .filter(
            WorkflowV3Job.machine_status == "running",
            WorkflowV3Job.current_stage_key == WorkflowV3StageRun.stage_key,
            WorkflowV3StageRun.machine_status == "awaiting_evaluation",
            WorkflowV3Candidate.status == "candidate",
        )
    )
    if runtime_identity_sha256 is not None:
        query = query.join(
            WorkflowV3SkillRelease,
            WorkflowV3SkillRelease.id == WorkflowV3Job.skill_release_id,
        ).filter(
            WorkflowV3SkillRelease.runtime_identity_sha256
            == runtime_identity_sha256
        )
    rows = (
        query.order_by(
            WorkflowV3Job.priority.desc(),
            WorkflowV3Candidate.id.asc(),
        )
        .with_for_update(skip_locked=True)
        .all()
    )
    for job, stage, candidate in rows:
        try:
            with db.begin_nested():
                _job, _stage, attempt, owner_token = claim_operation_attempt(
                    db,
                    job.public_id,
                    operation="evaluation",
                    target_id=candidate.id,
                    owner_identity=owner_identity,
                    lease_seconds=lease_seconds,
                    max_attempts=max_attempts,
                )
        except (IntegrityError, OperationalError, WorkflowV3TransitionError):
            continue
        return EvaluationQueueItem(
            job.public_id,
            candidate.id,
            stage.stage_key,
            operation_attempt_id=attempt.id,
            owner_token=owner_token,
            operation_attempt=attempt.attempt,
        )
    return None


def claim_evaluation_item(
    db: Session,
    *,
    public_id: str,
    candidate_id: int,
    owner_identity: str,
    lease_seconds: int = 300,
    max_attempts: int = 3,
) -> EvaluationQueueItem:
    _job, stage, attempt, owner_token = claim_operation_attempt(
        db,
        public_id,
        operation="evaluation",
        target_id=candidate_id,
        owner_identity=owner_identity,
        lease_seconds=lease_seconds,
        max_attempts=max_attempts,
    )
    return EvaluationQueueItem(
        public_id,
        candidate_id,
        stage.stage_key,
        operation_attempt_id=attempt.id,
        owner_token=owner_token,
        operation_attempt=attempt.attempt,
    )


def claim_next_promotion_item(
    db: Session,
    *,
    owner_identity: str,
    lease_seconds: int = 300,
    max_attempts: int = 3,
    runtime_identity_sha256: str | None = None,
) -> PromotionQueueItem | None:
    runtime_identity_sha256 = _validated_runtime_identity(
        runtime_identity_sha256
    )
    query = (
        db.query(WorkflowV3Job, WorkflowV3StageRun, WorkflowV3Evaluation)
        .join(
            WorkflowV3StageRun,
            WorkflowV3StageRun.workflow_job_id == WorkflowV3Job.id,
        )
        .join(
            WorkflowV3Evaluation,
            WorkflowV3Evaluation.stage_run_id == WorkflowV3StageRun.id,
        )
        .outerjoin(
            WorkflowV3Promotion,
            WorkflowV3Promotion.evaluation_id == WorkflowV3Evaluation.id,
        )
        .filter(
            WorkflowV3Job.machine_status == "running",
            WorkflowV3Job.current_stage_key == WorkflowV3StageRun.stage_key,
            WorkflowV3StageRun.machine_status == "awaiting_promotion",
            WorkflowV3Evaluation.decision == "passed",
            WorkflowV3Evaluation.spec_passed.is_(True),
            WorkflowV3Promotion.id.is_(None),
        )
    )
    if runtime_identity_sha256 is not None:
        query = query.join(
            WorkflowV3SkillRelease,
            WorkflowV3SkillRelease.id == WorkflowV3Job.skill_release_id,
        ).filter(
            WorkflowV3SkillRelease.runtime_identity_sha256
            == runtime_identity_sha256
        )
    rows = (
        query.order_by(
            WorkflowV3Job.priority.desc(),
            WorkflowV3Evaluation.id.asc(),
        )
        .with_for_update(skip_locked=True)
        .all()
    )
    for job, stage, evaluation in rows:
        try:
            with db.begin_nested():
                _job, _stage, attempt, owner_token = claim_operation_attempt(
                    db,
                    job.public_id,
                    operation="promotion",
                    target_id=evaluation.id,
                    owner_identity=owner_identity,
                    lease_seconds=lease_seconds,
                    max_attempts=max_attempts,
                )
        except (IntegrityError, OperationalError, WorkflowV3TransitionError):
            continue
        return PromotionQueueItem(
            job.public_id,
            evaluation.id,
            stage.stage_key,
            operation_attempt_id=attempt.id,
            owner_token=owner_token,
            operation_attempt=attempt.attempt,
        )
    return None


def claim_promotion_item(
    db: Session,
    *,
    public_id: str,
    evaluation_id: int,
    owner_identity: str,
    lease_seconds: int = 300,
    max_attempts: int = 3,
) -> PromotionQueueItem:
    _job, stage, attempt, owner_token = claim_operation_attempt(
        db,
        public_id,
        operation="promotion",
        target_id=evaluation_id,
        owner_identity=owner_identity,
        lease_seconds=lease_seconds,
        max_attempts=max_attempts,
    )
    return PromotionQueueItem(
        public_id,
        evaluation_id,
        stage.stage_key,
        operation_attempt_id=attempt.id,
        owner_token=owner_token,
        operation_attempt=attempt.attempt,
    )


def recover_stale(
    session_factory,
    *,
    stale_after_seconds: int,
) -> list[str]:
    db: Session = session_factory()
    try:
        recovered = recover_stale_executions(
            db,
            stale_after_seconds=stale_after_seconds,
            requeue=True,
        )
        db.commit()
        return recovered
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def recover_stale_operations(
    session_factory,
    *,
    operation: str | None = None,
) -> list[int]:
    db: Session = session_factory()
    try:
        recovered = recover_stale_operation_attempts(db, operation=operation)
        db.commit()
        return recovered
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def cancel(
    session_factory,
    public_id: str,
    *,
    cancelled_by: str,
    reason: str = "",
) -> dict:
    db: Session = session_factory()
    try:
        job, stage, execution = cancel_job(
            db,
            public_id,
            cancelled_by=cancelled_by,
            reason=reason,
        )
        db.commit()
        return {
            "job_id": public_id,
            "job_status": job.machine_status,
            "stage": stage.stage_key if stage else "",
            "execution_id": str(execution.id) if execution else "",
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
