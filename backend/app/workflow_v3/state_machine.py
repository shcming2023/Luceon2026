from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.workflow_v3.contracts import contract_for, contracts_for_version
from app.workflow_v3.models import (
    WorkflowV3Candidate,
    WorkflowV3Evaluation,
    WorkflowV3Event,
    WorkflowV3Execution,
    WorkflowV3Job,
    WorkflowV3OperationAttempt,
    WorkflowV3ProjectionOutbox,
    WorkflowV3Promotion,
    WorkflowV3ReviewResolution,
    WorkflowV3SkillRelease,
    WorkflowV3StageRun,
)
from app.workflow_v3.review_resolution import (
    ReviewResolutionManifestError,
    evaluation_fingerprint,
    finding_fingerprint,
    validate_review_resolution_manifest,
)
from app.workflow_v3.release import require_qualification_environment


class WorkflowV3TransitionError(ValueError):
    pass


def claim_current_stage(
    db: Session,
    public_id: str,
    *,
    producer_identity: str,
    idempotency_key: str,
    runtime_identity_sha256: str,
    qualification: bool = False,
) -> tuple[WorkflowV3Job, WorkflowV3StageRun, WorkflowV3Execution]:
    """Lease the current stage to a producer without granting publish authority."""
    if qualification:
        require_qualification_environment()
    runtime_identity_sha256 = _require_sha256(runtime_identity_sha256, "runtime_identity_sha256")
    if not producer_identity or not idempotency_key:
        raise WorkflowV3TransitionError("producer identity and idempotency key are required")
    duplicate = _execution_by_idempotency(db, idempotency_key)
    if duplicate:
        stage = db.query(WorkflowV3StageRun).filter(WorkflowV3StageRun.id == duplicate.stage_run_id).one()
        job = db.query(WorkflowV3Job).filter(WorkflowV3Job.id == duplicate.workflow_job_id).one()
        if (
            job.public_id != public_id
            or duplicate.producer_identity != producer_identity
            or duplicate.runtime_identity_sha256 != runtime_identity_sha256
        ):
            raise WorkflowV3TransitionError("execution idempotency key conflicts with another request")
        return job, stage, duplicate

    job = _locked_job(db, public_id)
    contracts = contracts_for_version(job.workflow_version)
    if job.machine_status in {"needs_review", "failed", "cancelled", "succeeded"}:
        raise WorkflowV3TransitionError(f"job is already {job.machine_status}")
    stage = _latest_stage(db, job.id, job.current_stage_key)
    if not stage or stage.machine_status != "queued":
        current = stage.machine_status if stage else "missing"
        raise WorkflowV3TransitionError(f"current stage is not claimable: {current}")

    release = (
        db.query(WorkflowV3SkillRelease)
        .filter(WorkflowV3SkillRelease.id == job.skill_release_id)
        .one()
    )
    expected_release_status = "qualification" if qualification else "registered"
    if (
        release.status != expected_release_status
        or release.manifest_sha256 != job.skill_release_sha256
        or release.runtime_identity_sha256 != runtime_identity_sha256
    ):
        raise WorkflowV3TransitionError(
            f"{expected_release_status} release or runtime identity "
            "no longer matches the job"
        )
    _verify_stage_input_is_promoted(db, job, stage, contracts)

    now = datetime.utcnow()
    execution = WorkflowV3Execution(
        workflow_job_id=job.id,
        stage_run_id=stage.id,
        producer_identity=producer_identity,
        idempotency_key=idempotency_key,
        machine_status="running",
        skill_release_sha256=job.skill_release_sha256,
        runtime_identity_sha256=runtime_identity_sha256,
        generation=stage.generation,
        review_resolution_sha256=stage.review_resolution_sha256,
        started_at=now,
        heartbeat_at=now,
    )
    db.add(execution)
    stage.machine_status = "running"
    stage.started_at = stage.started_at or now
    job.machine_status = "running"
    job.spec_status = "in_progress" if job.spec_status != "passed" else job.spec_status
    job.started_at = job.started_at or now
    _event(
        db,
        job,
        stage,
        "execution_started",
        "Stage execution leased to a candidate-only producer.",
        {
            "producer_identity": producer_identity,
            "runtime_identity_sha256": runtime_identity_sha256,
            "input_kind": stage.input_kind,
            "input_sha256": stage.input_artifact_sha256,
            "generation": stage.generation,
            "review_resolution_sha256": stage.review_resolution_sha256,
        },
    )
    db.flush()
    return job, stage, execution


def touch_execution_heartbeat(
    db: Session,
    public_id: str,
    *,
    execution_id: int,
    producer_identity: str,
) -> bool:
    job = db.query(WorkflowV3Job).filter(WorkflowV3Job.public_id == public_id).first()
    execution = db.query(WorkflowV3Execution).filter(WorkflowV3Execution.id == execution_id).first()
    if (
        not job
        or not execution
        or execution.workflow_job_id != job.id
        or execution.producer_identity != producer_identity
        or execution.machine_status != "running"
    ):
        return False
    execution.heartbeat_at = datetime.utcnow()
    db.flush()
    return True


def claim_operation_attempt(
    db: Session,
    public_id: str,
    *,
    operation: str,
    target_id: int,
    owner_identity: str,
    max_attempts: int = 3,
    lease_seconds: int = 300,
) -> tuple[
    WorkflowV3Job,
    WorkflowV3StageRun,
    WorkflowV3OperationAttempt,
    str,
]:
    """Atomically lease one evaluator or promotion operation.

    The returned raw token is deliberately never persisted.  Only its SHA-256
    is stored, so a database/UI reader cannot impersonate the current owner.
    """

    if operation not in {"evaluation", "promotion"}:
        raise WorkflowV3TransitionError("unknown operation attempt type")
    if not isinstance(target_id, int) or isinstance(target_id, bool) or target_id <= 0:
        raise WorkflowV3TransitionError("operation target_id must be a positive integer")
    if not owner_identity:
        raise WorkflowV3TransitionError("operation owner identity is required")
    if max_attempts <= 0 or lease_seconds <= 0:
        raise WorkflowV3TransitionError("operation retry and lease limits must be positive")

    job = _locked_job(db, public_id)
    if job.machine_status != "running":
        if operation == "promotion":
            raise WorkflowV3TransitionError(
                "evaluation and candidate are not promotion-ready"
            )
        raise WorkflowV3TransitionError(
            "candidate is not awaiting evaluation for this job"
        )
    stage = _operation_target_stage(
        db,
        job=job,
        operation=operation,
        target_id=target_id,
        lock=True,
    )
    expected_stage_status = (
        "awaiting_evaluation" if operation == "evaluation" else "awaiting_promotion"
    )
    if (
        stage.stage_key != job.current_stage_key
        or stage.machine_status != expected_stage_status
    ):
        raise WorkflowV3TransitionError(
            f"target is not awaiting {operation}: {stage.machine_status}"
        )

    attempts = (
        db.query(WorkflowV3OperationAttempt)
        .filter(
            WorkflowV3OperationAttempt.operation == operation,
            WorkflowV3OperationAttempt.target_id == target_id,
        )
        .order_by(WorkflowV3OperationAttempt.attempt.asc())
        .with_for_update()
        .all()
    )
    running = next((row for row in attempts if row.status == "running"), None)
    if running:
        if running.lease_expires_at <= datetime.utcnow():
            raise WorkflowV3TransitionError(
                "stale operation attempt must be recovered before a new claim"
            )
        raise WorkflowV3TransitionError("operation target already has an active lease")
    if any(row.status == "succeeded" for row in attempts):
        raise WorkflowV3TransitionError("operation target was already completed")
    if attempts and any(row.max_attempts != max_attempts for row in attempts):
        raise WorkflowV3TransitionError(
            "operation max_attempts cannot change between retries"
        )
    next_attempt = len(attempts) + 1
    if next_attempt > max_attempts:
        raise WorkflowV3TransitionError("operation retry budget is exhausted")

    now = datetime.utcnow()
    owner_token = secrets.token_hex(32)
    attempt = WorkflowV3OperationAttempt(
        workflow_job_id=job.id,
        stage_run_id=stage.id,
        operation=operation,
        target_id=target_id,
        attempt=next_attempt,
        status="running",
        owner_identity=owner_identity,
        owner_token_sha256=_owner_token_sha256(owner_token),
        max_attempts=max_attempts,
        lease_seconds=lease_seconds,
        lease_expires_at=now + timedelta(seconds=lease_seconds),
        heartbeat_at=now,
        metadata_json=WorkflowV3OperationAttempt.dump({}),
        started_at=now,
    )
    db.add(attempt)
    db.flush()
    _event(
        db,
        job,
        stage,
        f"{operation}_operation_started",
        f"{operation.capitalize()} operation leased to one control-plane owner.",
        {
            "operation_attempt_id": str(attempt.id),
            "operation_attempt": attempt.attempt,
            "target_id": str(target_id),
            "owner_identity": owner_identity,
            "lease_expires_at": attempt.lease_expires_at.isoformat(),
        },
    )
    db.flush()
    return job, stage, attempt, owner_token


def assert_operation_attempt(
    db: Session,
    public_id: str,
    *,
    operation_attempt_id: int,
    operation: str,
    target_id: int,
    owner_identity: str,
    owner_token: str,
    require_unexpired: bool = True,
) -> WorkflowV3OperationAttempt:
    job = _locked_job(db, public_id)
    attempt = (
        db.query(WorkflowV3OperationAttempt)
        .filter(WorkflowV3OperationAttempt.id == operation_attempt_id)
        .with_for_update()
        .first()
    )
    if (
        attempt is None
        or attempt.workflow_job_id != job.id
        or attempt.operation != operation
        or attempt.target_id != target_id
        or attempt.owner_identity != owner_identity
        or not hmac.compare_digest(
            attempt.owner_token_sha256,
            _owner_token_sha256(owner_token),
        )
    ):
        raise WorkflowV3TransitionError("operation owner token is invalid")
    if attempt.status != "running":
        raise WorkflowV3TransitionError(
            f"operation attempt is not running: {attempt.status}"
        )
    if require_unexpired and attempt.lease_expires_at <= datetime.utcnow():
        raise WorkflowV3TransitionError("operation lease has expired")
    return attempt


def touch_operation_heartbeat(
    db: Session,
    public_id: str,
    *,
    operation_attempt_id: int,
    operation: str,
    target_id: int,
    owner_identity: str,
    owner_token: str,
) -> bool:
    try:
        attempt = assert_operation_attempt(
            db,
            public_id,
            operation_attempt_id=operation_attempt_id,
            operation=operation,
            target_id=target_id,
            owner_identity=owner_identity,
            owner_token=owner_token,
        )
    except WorkflowV3TransitionError:
        return False
    now = datetime.utcnow()
    attempt.heartbeat_at = now
    attempt.lease_expires_at = now + timedelta(seconds=attempt.lease_seconds)
    db.flush()
    return True


def finish_operation_attempt(
    db: Session,
    public_id: str,
    *,
    operation_attempt_id: int,
    operation: str,
    target_id: int,
    owner_identity: str,
    owner_token: str,
    status: str,
    error_code: str = "",
    error_message: str = "",
    retryable: bool = True,
) -> tuple[WorkflowV3OperationAttempt, bool]:
    if status not in {"succeeded", "failed", "cancelled"}:
        raise WorkflowV3TransitionError("invalid operation terminal status")
    attempt = assert_operation_attempt(
        db,
        public_id,
        operation_attempt_id=operation_attempt_id,
        operation=operation,
        target_id=target_id,
        owner_identity=owner_identity,
        owner_token=owner_token,
        require_unexpired=status == "succeeded",
    )
    job = db.query(WorkflowV3Job).filter(
        WorkflowV3Job.id == attempt.workflow_job_id
    ).one()
    stage = db.query(WorkflowV3StageRun).filter(
        WorkflowV3StageRun.id == attempt.stage_run_id
    ).one()
    exhausted = _finish_operation_attempt_row(
        db,
        job=job,
        stage=stage,
        attempt=attempt,
        status=status,
        error_code=error_code,
        error_message=error_message,
        retryable=retryable,
    )
    db.flush()
    return attempt, exhausted


def recover_stale_operation_attempts(
    db: Session,
    *,
    operation: str | None = None,
) -> list[int]:
    if operation is not None and operation not in {"evaluation", "promotion"}:
        raise WorkflowV3TransitionError("unknown operation attempt type")
    query = db.query(WorkflowV3OperationAttempt).filter(
        WorkflowV3OperationAttempt.status == "running",
        WorkflowV3OperationAttempt.lease_expires_at <= datetime.utcnow(),
    )
    if operation is not None:
        query = query.filter(WorkflowV3OperationAttempt.operation == operation)
    stale = query.order_by(WorkflowV3OperationAttempt.id.asc()).with_for_update().all()
    recovered: list[int] = []
    for attempt in stale:
        job = db.query(WorkflowV3Job).filter(
            WorkflowV3Job.id == attempt.workflow_job_id
        ).one()
        stage = db.query(WorkflowV3StageRun).filter(
            WorkflowV3StageRun.id == attempt.stage_run_id
        ).one()
        if job.machine_status == "cancelled" or stage.machine_status == "cancelled":
            _finish_operation_attempt_row(
                db,
                job=job,
                stage=stage,
                attempt=attempt,
                status="cancelled",
                error_code="operation_cancelled",
                error_message="control plane cancelled the operation",
                retryable=False,
            )
        else:
            _finish_operation_attempt_row(
                db,
                job=job,
                stage=stage,
                attempt=attempt,
                status="failed",
                error_code=f"{attempt.operation}_lease_expired",
                error_message=(
                    f"{attempt.operation} heartbeat expired before a durable result"
                ),
                retryable=True,
            )
        recovered.append(attempt.id)
    db.flush()
    return recovered


def submit_candidate(
    db: Session,
    public_id: str,
    *,
    execution_id: int,
    idempotency_key: str,
    artifact_kind: str,
    bucket: str,
    object_name: str,
    sha256: str,
    size_bytes: int,
    metadata: dict | None = None,
) -> tuple[WorkflowV3Job, WorkflowV3StageRun, WorkflowV3Candidate]:
    """Persist an immutable candidate; this cannot mark a stage as succeeded."""
    sha256 = _require_sha256(sha256, "candidate sha256")
    if not idempotency_key or not artifact_kind or not bucket or not object_name:
        raise WorkflowV3TransitionError("candidate identity and object reference are required")
    if size_bytes < 0:
        raise WorkflowV3TransitionError("candidate size cannot be negative")
    candidate_metadata = dict(metadata or {})
    duplicate = (
        db.query(WorkflowV3Candidate)
        .filter(WorkflowV3Candidate.idempotency_key == idempotency_key)
        .first()
    )
    if duplicate:
        job = db.query(WorkflowV3Job).filter(WorkflowV3Job.id == duplicate.workflow_job_id).one()
        stage = db.query(WorkflowV3StageRun).filter(WorkflowV3StageRun.id == duplicate.stage_run_id).one()
        expected_metadata = dict(candidate_metadata)
        if duplicate.review_resolution_sha256:
            expected_metadata["recovery_lineage"] = {
                "generation": duplicate.generation,
                "review_resolution_sha256": duplicate.review_resolution_sha256,
            }
        if (
            job.public_id != public_id
            or duplicate.execution_id != execution_id
            or duplicate.sha256 != sha256
            or duplicate.bucket != bucket
            or duplicate.object_name != object_name
            or duplicate.artifact_kind != artifact_kind
            or duplicate.size_bytes != size_bytes
            or duplicate.load(duplicate.metadata_json, {}) != expected_metadata
        ):
            raise WorkflowV3TransitionError("candidate idempotency key conflicts with another request")
        return job, stage, duplicate

    job = _locked_job(db, public_id)
    execution = (
        db.query(WorkflowV3Execution)
        .filter(WorkflowV3Execution.id == execution_id)
        .with_for_update()
        .first()
    )
    if not execution or execution.workflow_job_id != job.id or execution.machine_status != "running":
        raise WorkflowV3TransitionError("execution is not active for this job")
    stage = db.query(WorkflowV3StageRun).filter(WorkflowV3StageRun.id == execution.stage_run_id).one()
    if stage.stage_key != job.current_stage_key or stage.machine_status != "running":
        raise WorkflowV3TransitionError("execution is not attached to the active stage")
    if (
        execution.generation != stage.generation
        or execution.review_resolution_sha256 != stage.review_resolution_sha256
    ):
        raise WorkflowV3TransitionError("execution recovery lineage drifted from its stage")
    if stage.review_resolution_sha256:
        candidate_metadata["recovery_lineage"] = {
            "generation": stage.generation,
            "review_resolution_sha256": stage.review_resolution_sha256,
        }

    identity_hash = hashlib.sha256(f"{bucket}\n{object_name}\n{sha256}".encode("utf-8")).hexdigest()
    candidate = WorkflowV3Candidate(
        workflow_job_id=job.id,
        stage_run_id=stage.id,
        execution_id=execution.id,
        idempotency_key=idempotency_key,
        artifact_kind=artifact_kind,
        bucket=bucket,
        object_name=object_name,
        object_identity_hash=identity_hash,
        sha256=sha256,
        size_bytes=size_bytes,
        immutable=True,
        generation=stage.generation,
        review_resolution_sha256=stage.review_resolution_sha256,
        status="candidate",
        metadata_json=WorkflowV3Candidate.dump(candidate_metadata),
    )
    db.add(candidate)
    now = datetime.utcnow()
    execution.machine_status = "succeeded"
    execution.finished_at = now
    execution.heartbeat_at = now
    stage.machine_status = "awaiting_evaluation"
    _event(
        db,
        job,
        stage,
        "candidate_submitted",
        "Producer completed execution; immutable candidate awaits independent evaluation.",
        {
            "execution_id": str(execution.id),
            "sha256": sha256,
            "artifact_kind": artifact_kind,
            "generation": stage.generation,
            "review_resolution_sha256": stage.review_resolution_sha256,
        },
    )
    db.flush()
    return job, stage, candidate


def record_evaluation(
    db: Session,
    public_id: str,
    *,
    candidate_id: int,
    idempotency_key: str,
    evaluator_identity: str,
    evaluator_version: str,
    policy_sha256: str,
    decision: str,
    gate_results: dict,
    findings: list | None = None,
    operation_attempt_id: int | None = None,
    owner_token: str = "",
) -> tuple[WorkflowV3Job, WorkflowV3StageRun, WorkflowV3Evaluation]:
    policy_sha256 = _require_sha256(policy_sha256, "policy_sha256")
    if decision not in {"passed", "needs_review", "failed"}:
        raise WorkflowV3TransitionError(
            "evaluation decision must be passed, needs_review, or failed"
        )
    if not idempotency_key or not evaluator_identity or not evaluator_version:
        raise WorkflowV3TransitionError("evaluation identity is required")
    if not isinstance(gate_results, dict):
        raise WorkflowV3TransitionError("gate_results must be an object")
    duplicate = (
        db.query(WorkflowV3Evaluation)
        .filter(WorkflowV3Evaluation.idempotency_key == idempotency_key)
        .first()
    )
    if duplicate:
        job = db.query(WorkflowV3Job).filter(WorkflowV3Job.id == duplicate.workflow_job_id).one()
        stage = db.query(WorkflowV3StageRun).filter(WorkflowV3StageRun.id == duplicate.stage_run_id).one()
        if (
            job.public_id != public_id
            or duplicate.candidate_id != candidate_id
            or duplicate.evaluator_identity != evaluator_identity
            or duplicate.evaluator_version != evaluator_version
            or duplicate.policy_sha256 != policy_sha256
            or duplicate.decision != decision
            or duplicate.load(duplicate.gate_results_json, {}) != gate_results
            or duplicate.load(duplicate.findings_json, []) != (findings or [])
        ):
            raise WorkflowV3TransitionError("evaluation idempotency key conflicts with another request")
        return job, stage, duplicate

    job = _locked_job(db, public_id)
    if operation_attempt_id is not None:
        assert_operation_attempt(
            db,
            public_id,
            operation_attempt_id=operation_attempt_id,
            operation="evaluation",
            target_id=candidate_id,
            owner_identity=evaluator_identity,
            owner_token=owner_token,
        )
    candidate = (
        db.query(WorkflowV3Candidate)
        .filter(WorkflowV3Candidate.id == candidate_id)
        .with_for_update()
        .first()
    )
    if not candidate or candidate.workflow_job_id != job.id or not candidate.immutable:
        raise WorkflowV3TransitionError("candidate is not an immutable artifact for this job")
    stage = db.query(WorkflowV3StageRun).filter(WorkflowV3StageRun.id == candidate.stage_run_id).one()
    execution = db.query(WorkflowV3Execution).filter(WorkflowV3Execution.id == candidate.execution_id).one()
    if stage.stage_key != job.current_stage_key or stage.machine_status != "awaiting_evaluation":
        raise WorkflowV3TransitionError("candidate is not awaiting evaluation")
    if execution.producer_identity == evaluator_identity:
        raise WorkflowV3TransitionError("producer cannot independently evaluate its own candidate")
    if (
        candidate.generation != stage.generation
        or candidate.review_resolution_sha256 != stage.review_resolution_sha256
    ):
        raise WorkflowV3TransitionError("candidate recovery lineage drifted from its stage")

    contract = contract_for(job.workflow_version, stage.stage_key)
    required_gates_pass = all(gate_results.get(gate) is True for gate in contract.acceptance_gates)
    if decision == "passed" and not required_gates_pass:
        missing = [gate for gate in contract.acceptance_gates if gate_results.get(gate) is not True]
        raise WorkflowV3TransitionError(f"passed evaluation is missing required gates: {', '.join(missing)}")
    if decision == "needs_review":
        _validate_needs_review_findings(
            findings or [],
            workflow_version=job.workflow_version,
            current_stage_key=stage.stage_key,
        )

    evaluation = WorkflowV3Evaluation(
        workflow_job_id=job.id,
        stage_run_id=stage.id,
        candidate_id=candidate.id,
        idempotency_key=idempotency_key,
        evaluator_identity=evaluator_identity,
        evaluator_version=evaluator_version,
        policy_sha256=policy_sha256,
        decision=decision,
        spec_passed=decision == "passed",
        gate_results_json=WorkflowV3Evaluation.dump(gate_results),
        findings_json=WorkflowV3Evaluation.dump(findings or []),
        generation=stage.generation,
        review_resolution_sha256=stage.review_resolution_sha256,
    )
    db.add(evaluation)
    if decision == "passed":
        candidate.status = "evaluated_passed"
        stage.machine_status = "awaiting_promotion"
        stage.spec_status = "passed"
        event_type = "evaluation_passed"
        level = "info"
        message = "Independent evaluator passed every registered stage gate."
    elif decision == "needs_review":
        now = datetime.utcnow()
        candidate.status = "needs_review"
        stage.machine_status = "needs_review"
        stage.spec_status = "needs_review"
        stage.error_code = "human_review_required"
        stage.error_message = (
            "independent evaluator found a source-bound ambiguity requiring human review"
        )
        stage.finished_at = now
        job.machine_status = "needs_review"
        job.spec_status = "needs_review"
        job.error_code = stage.error_code
        job.error_message = stage.error_message
        job.finished_at = now
        event_type = "evaluation_needs_review"
        level = "warning"
        message = (
            "Independent evaluator paused the run with an evidence-bound human "
            "handoff; no promotion was created."
        )
    else:
        now = datetime.utcnow()
        candidate.status = "rejected"
        stage.machine_status = "failed"
        stage.spec_status = "failed"
        stage.error_code = "spec_evaluation_failed"
        stage.error_message = "independent evaluator rejected the candidate"
        stage.finished_at = now
        job.machine_status = "failed"
        job.spec_status = "failed"
        job.error_code = stage.error_code
        job.error_message = stage.error_message
        job.finished_at = now
        event_type = "evaluation_failed"
        level = "error"
        message = "Independent evaluator rejected the candidate; no promotion was created."
    _event(
        db,
        job,
        stage,
        event_type,
        message,
        {
            "candidate_id": str(candidate.id),
            "evaluator_identity": evaluator_identity,
            "decision": decision,
            "generation": stage.generation,
            "review_resolution_sha256": stage.review_resolution_sha256,
        },
        level=level,
    )
    if operation_attempt_id is not None:
        finish_operation_attempt(
            db,
            public_id,
            operation_attempt_id=operation_attempt_id,
            operation="evaluation",
            target_id=candidate_id,
            owner_identity=evaluator_identity,
            owner_token=owner_token,
            status="succeeded",
        )
    db.flush()
    return job, stage, evaluation


def promote_candidate(
    db: Session,
    public_id: str,
    *,
    evaluation_id: int,
    idempotency_key: str,
    promoted_by: str,
    operation_attempt_id: int | None = None,
    owner_token: str = "",
) -> tuple[WorkflowV3Job, WorkflowV3StageRun, WorkflowV3Promotion]:
    """Promote only an independently-passed candidate and advance its SHA."""
    duplicate = (
        db.query(WorkflowV3Promotion)
        .filter(WorkflowV3Promotion.idempotency_key == idempotency_key)
        .first()
    )
    if duplicate:
        job = db.query(WorkflowV3Job).filter(WorkflowV3Job.id == duplicate.workflow_job_id).one()
        stage = db.query(WorkflowV3StageRun).filter(WorkflowV3StageRun.id == duplicate.stage_run_id).one()
        if (
            job.public_id != public_id
            or duplicate.evaluation_id != evaluation_id
            or duplicate.promoted_by != promoted_by
        ):
            raise WorkflowV3TransitionError("promotion idempotency key conflicts with another request")
        return job, stage, duplicate

    if not idempotency_key or not promoted_by:
        raise WorkflowV3TransitionError("promotion identity is required")
    job = _locked_job(db, public_id)
    if operation_attempt_id is not None:
        assert_operation_attempt(
            db,
            public_id,
            operation_attempt_id=operation_attempt_id,
            operation="promotion",
            target_id=evaluation_id,
            owner_identity=promoted_by,
            owner_token=owner_token,
        )
    evaluation = (
        db.query(WorkflowV3Evaluation)
        .filter(WorkflowV3Evaluation.id == evaluation_id)
        .with_for_update()
        .first()
    )
    if (
        not evaluation
        or evaluation.workflow_job_id != job.id
        or evaluation.decision != "passed"
        or not evaluation.spec_passed
    ):
        raise WorkflowV3TransitionError("only a passed independent evaluation can be promoted")
    candidate = db.query(WorkflowV3Candidate).filter(WorkflowV3Candidate.id == evaluation.candidate_id).one()
    stage = db.query(WorkflowV3StageRun).filter(WorkflowV3StageRun.id == evaluation.stage_run_id).one()
    execution = db.query(WorkflowV3Execution).filter(WorkflowV3Execution.id == candidate.execution_id).one()
    if promoted_by == execution.producer_identity:
        raise WorkflowV3TransitionError("producer cannot promote its own candidate")
    if (
        stage.stage_key != job.current_stage_key
        or stage.machine_status != "awaiting_promotion"
        or stage.spec_status != "passed"
        or candidate.status != "evaluated_passed"
        or stage.generation != job.current_generation
        or candidate.generation != stage.generation
        or evaluation.generation != stage.generation
        or candidate.review_resolution_sha256
        != stage.review_resolution_sha256
        or evaluation.review_resolution_sha256
        != stage.review_resolution_sha256
    ):
        raise WorkflowV3TransitionError("evaluation and candidate are not promotion-ready")

    promotion = WorkflowV3Promotion(
        workflow_job_id=job.id,
        stage_run_id=stage.id,
        candidate_id=candidate.id,
        evaluation_id=evaluation.id,
        idempotency_key=idempotency_key,
        artifact_sha256=candidate.sha256,
        promoted_by=promoted_by,
    )
    db.add(promotion)
    db.flush()
    now = datetime.utcnow()
    candidate.status = "promoted"
    stage.machine_status = "succeeded"
    stage.promoted_candidate_id = candidate.id
    stage.promotion_id = promotion.id
    stage.promoted_artifact_sha256 = candidate.sha256
    stage.finished_at = now

    contracts = contracts_for_version(job.workflow_version)
    current_index = next(index for index, row in enumerate(contracts) if row.key == stage.stage_key)
    if current_index + 1 < len(contracts):
        next_contract = contracts[current_index + 1]
        next_stage = _latest_stage(db, job.id, next_contract.key)
        if (
            not next_stage
            or next_stage.machine_status != "pending"
            or next_stage.generation != stage.generation
            or next_stage.review_resolution_sha256
            != stage.review_resolution_sha256
        ):
            raise WorkflowV3TransitionError("next stage is not pending")
        next_stage.input_kind = "promoted_artifact"
        next_stage.input_promotion_id = promotion.id
        next_stage.input_artifact_sha256 = candidate.sha256
        next_stage.machine_status = "queued"
        job.current_stage_key = next_contract.key
        job.machine_status = "queued"
        job.spec_status = "in_progress"
        _event(
            db,
            job,
            next_stage,
            "stage_queued_from_promotion",
            "Next stage may consume only the promoted upstream SHA.",
            {
                "upstream_stage_key": stage.stage_key,
                "promotion_id": str(promotion.id),
                "sha256": candidate.sha256,
                "generation": stage.generation,
                "review_resolution_sha256": stage.review_resolution_sha256,
            },
        )
    else:
        job.machine_status = "succeeded"
        job.spec_status = "passed"
        job.readiness_status = "ready"
        job.finished_at = now
        outbox = _enqueue_final_projection(
            db,
            job=job,
            stage=stage,
            candidate=candidate,
            promotion=promotion,
        )
        _event(
            db,
            job,
            stage,
            "run_ready_for_user_acceptance",
            "All machine and specification gates passed; human acceptance remains independent.",
            {
                "promotion_id": str(promotion.id),
                "sha256": candidate.sha256,
                "human_acceptance_status": job.human_acceptance_status,
                "projection_outbox_id": str(outbox.id),
                "projection_status": outbox.status,
            },
        )
    _event(
        db,
        job,
        stage,
        "candidate_promoted",
        "Control plane promoted an independently evaluated candidate.",
        {
            "candidate_id": str(candidate.id),
            "evaluation_id": str(evaluation.id),
            "promotion_id": str(promotion.id),
            "sha256": candidate.sha256,
        },
    )
    if operation_attempt_id is not None:
        finish_operation_attempt(
            db,
            public_id,
            operation_attempt_id=operation_attempt_id,
            operation="promotion",
            target_id=evaluation_id,
            owner_identity=promoted_by,
            owner_token=owner_token,
            status="succeeded",
        )
    db.flush()
    return job, stage, promotion


def fail_execution(
    db: Session,
    public_id: str,
    *,
    execution_id: int,
    error_code: str,
    error_message: str,
) -> tuple[WorkflowV3Job, WorkflowV3StageRun, WorkflowV3Execution]:
    job = _locked_job(db, public_id)
    execution = db.query(WorkflowV3Execution).filter(WorkflowV3Execution.id == execution_id).first()
    if not execution or execution.workflow_job_id != job.id:
        raise WorkflowV3TransitionError("execution does not belong to this job")
    stage = db.query(WorkflowV3StageRun).filter(WorkflowV3StageRun.id == execution.stage_run_id).one()
    if execution.machine_status == "failed":
        return job, stage, execution
    if execution.machine_status != "running" or stage.machine_status != "running":
        raise WorkflowV3TransitionError("only a running execution can fail")
    now = datetime.utcnow()
    execution.machine_status = "failed"
    execution.error_code = error_code
    execution.error_message = error_message
    execution.finished_at = now
    execution.heartbeat_at = now
    stage.machine_status = "failed"
    stage.error_code = error_code
    stage.error_message = error_message
    stage.finished_at = now
    job.machine_status = "failed"
    job.error_code = error_code
    job.error_message = error_message
    job.finished_at = now
    _event(
        db,
        job,
        stage,
        "execution_failed",
        "Producer execution failed; no candidate or promotion was inferred.",
        {"execution_id": str(execution.id), "error_code": error_code},
        level="error",
    )
    db.flush()
    return job, stage, execution


def cancel_job(
    db: Session,
    public_id: str,
    *,
    cancelled_by: str,
    reason: str = "",
) -> tuple[WorkflowV3Job, WorkflowV3StageRun | None, WorkflowV3Execution | None]:
    """Cancel the current attempt without manufacturing a candidate or promotion.

    Cancellation is a terminal machine decision.  It is intentionally separate
    from a failed attempt: a cancelled run cannot be retried implicitly and a
    producer that finishes late cannot submit its result.
    """
    if not cancelled_by:
        raise WorkflowV3TransitionError("cancelling identity is required")
    job = _locked_job(db, public_id)
    stage = _latest_stage(db, job.id, job.current_stage_key)
    if job.machine_status == "cancelled":
        execution = (
            db.query(WorkflowV3Execution)
            .filter(
                WorkflowV3Execution.workflow_job_id == job.id,
                WorkflowV3Execution.stage_run_id == stage.id if stage else False,
            )
            .first()
            if stage
            else None
        )
        return job, stage, execution
    if job.machine_status in {"needs_review", "failed", "succeeded"}:
        raise WorkflowV3TransitionError(f"terminal {job.machine_status} job cannot be cancelled")
    if not stage or stage.machine_status not in {
        "queued",
        "running",
        "awaiting_evaluation",
        "awaiting_promotion",
    }:
        current = stage.machine_status if stage else "missing"
        raise WorkflowV3TransitionError(f"current stage is not cancellable: {current}")

    execution = (
        db.query(WorkflowV3Execution)
        .filter(
            WorkflowV3Execution.workflow_job_id == job.id,
            WorkflowV3Execution.stage_run_id == stage.id,
        )
        .order_by(WorkflowV3Execution.id.desc())
        .first()
    )
    now = datetime.utcnow()
    if execution and execution.machine_status == "running":
        execution.machine_status = "cancelled"
        execution.error_code = "execution_cancelled"
        execution.error_message = reason or "cancelled by control plane"
        execution.heartbeat_at = now
        execution.finished_at = now
    cancelled_operations: list[str] = []
    for operation_attempt in (
        db.query(WorkflowV3OperationAttempt)
        .filter(
            WorkflowV3OperationAttempt.workflow_job_id == job.id,
            WorkflowV3OperationAttempt.stage_run_id == stage.id,
            WorkflowV3OperationAttempt.status == "running",
        )
        .with_for_update()
        .all()
    ):
        _finish_operation_attempt_row(
            db,
            job=job,
            stage=stage,
            attempt=operation_attempt,
            status="cancelled",
            error_code="operation_cancelled",
            error_message=reason or "cancelled by control plane",
            retryable=False,
        )
        cancelled_operations.append(str(operation_attempt.id))
    stage.machine_status = "cancelled"
    stage.error_code = "execution_cancelled"
    stage.error_message = reason or "cancelled by control plane"
    stage.finished_at = now
    job.machine_status = "cancelled"
    job.error_code = "execution_cancelled"
    job.error_message = reason or "cancelled by control plane"
    job.finished_at = now
    _event(
        db,
        job,
        stage,
        "job_cancelled",
        "Control plane cancelled the run; late producer or evaluator results are inadmissible.",
        {
            "cancelled_by": cancelled_by,
            "reason": reason,
            "execution_id": str(execution.id) if execution else "",
            "operation_attempt_ids": cancelled_operations,
        },
        level="warning",
    )
    db.flush()
    return job, stage, execution


def retry_failed_stage(
    db: Session,
    public_id: str,
) -> tuple[WorkflowV3Job, WorkflowV3StageRun]:
    job = _locked_job(db, public_id)
    if job.machine_status == "needs_review":
        raise WorkflowV3TransitionError(
            "needs_review requires an immutable human review resolution"
        )
    if job.machine_status != "failed":
        raise WorkflowV3TransitionError(
            "only a failed or needs_review job can retry; needs_review "
            "requires an immutable human review resolution"
        )
    previous = _latest_stage(db, job.id, job.current_stage_key)
    if not previous or previous.machine_status != "failed":
        raise WorkflowV3TransitionError("current stage has no failed attempt")
    contract = contract_for(job.workflow_version, previous.stage_key)
    if previous.input_kind == "promoted_artifact":
        _require_valid_promotion_input(db, job, previous)
    retry = WorkflowV3StageRun(
        workflow_job_id=job.id,
        stage_key=previous.stage_key,
        stage_version=contract.stage_version,
        attempt=previous.attempt + 1,
        generation=previous.generation,
        review_resolution_id=previous.review_resolution_id,
        review_resolution_sha256=previous.review_resolution_sha256,
        machine_status="queued",
        spec_status="not_evaluated",
        owner=contract.owner,
        input_kind=previous.input_kind,
        input_promotion_id=previous.input_promotion_id,
        input_artifact_sha256=previous.input_artifact_sha256,
    )
    db.add(retry)
    job.machine_status = "queued"
    passed_stage_count = (
        db.query(WorkflowV3StageRun)
        .filter(
            WorkflowV3StageRun.workflow_job_id == job.id,
            WorkflowV3StageRun.machine_status == "succeeded",
            WorkflowV3StageRun.spec_status == "passed",
        )
        .count()
    )
    job.spec_status = "in_progress" if passed_stage_count else "not_evaluated"
    job.error_code = ""
    job.error_message = ""
    job.finished_at = None
    _event(
        db,
        job,
        retry,
        "stage_retry_queued",
        "A new stage attempt preserves the same frozen or promoted input identity.",
        {
            "previous_stage_run_id": str(previous.id),
            "attempt": retry.attempt,
            "input_promotion_id": str(retry.input_promotion_id) if retry.input_promotion_id else "",
            "input_sha256": retry.input_artifact_sha256,
            "generation": retry.generation,
            "review_resolution_sha256": retry.review_resolution_sha256,
        },
    )
    db.flush()
    return job, retry


def apply_review_resolution(
    db: Session,
    public_id: str,
    *,
    idempotency_key: str,
    authorized_by: str,
    manifest_bucket: str,
    manifest_object: str,
    manifest_sha256: str,
    manifest_size_bytes: int,
    manifest: dict,
) -> tuple[
    WorkflowV3Job,
    WorkflowV3ReviewResolution,
    WorkflowV3StageRun,
    WorkflowV3Candidate | None,
]:
    """Apply one immutable human decision by creating a new recovery generation."""

    manifest_sha256 = _require_sha256(
        manifest_sha256,
        "review resolution manifest sha256",
    )
    if (
        not idempotency_key
        or not authorized_by.strip()
        or not manifest_bucket.strip()
        or not manifest_object.strip()
    ):
        raise WorkflowV3TransitionError(
            "review resolution identity, authorizer, and manifest object are required"
        )
    if (
        not isinstance(manifest_size_bytes, int)
        or isinstance(manifest_size_bytes, bool)
        or manifest_size_bytes <= 0
    ):
        raise WorkflowV3TransitionError(
            "review resolution manifest size must be positive"
        )
    try:
        validate_review_resolution_manifest(manifest)
    except ReviewResolutionManifestError as exc:
        raise WorkflowV3TransitionError(str(exc)) from exc

    duplicate = (
        db.query(WorkflowV3ReviewResolution)
        .filter(WorkflowV3ReviewResolution.idempotency_key == idempotency_key)
        .first()
    )
    if duplicate:
        job = db.get(WorkflowV3Job, duplicate.workflow_job_id)
        if (
            job is None
            or job.public_id != public_id
            or duplicate.authorized_by != authorized_by
            or duplicate.manifest_bucket != manifest_bucket
            or duplicate.manifest_object != manifest_object
            or duplicate.manifest_sha256 != manifest_sha256
            or duplicate.manifest_size_bytes != manifest_size_bytes
            or duplicate.load(duplicate.manifest_json, {}) != manifest
        ):
            raise WorkflowV3TransitionError(
                "review resolution idempotency key conflicts with another request"
            )
        stage = (
            db.query(WorkflowV3StageRun)
            .filter(
                WorkflowV3StageRun.workflow_job_id == job.id,
                WorkflowV3StageRun.generation == duplicate.recovery_generation,
                WorkflowV3StageRun.stage_key == duplicate.recovery_stage_key,
            )
            .one()
        )
        candidate = (
            db.query(WorkflowV3Candidate)
            .filter(
                WorkflowV3Candidate.workflow_job_id == job.id,
                WorkflowV3Candidate.stage_run_id == stage.id,
                WorkflowV3Candidate.review_resolution_sha256 == manifest_sha256,
            )
            .one_or_none()
        )
        return job, duplicate, stage, candidate

    job = _locked_job(db, public_id)
    if job.machine_status != "needs_review":
        raise WorkflowV3TransitionError(
            "review resolution requires a needs_review job"
        )
    current_stage = _latest_stage(db, job.id, job.current_stage_key)
    if current_stage is None or current_stage.machine_status != "needs_review":
        raise WorkflowV3TransitionError(
            "current stage has no unresolved needs_review evaluation"
        )
    if current_stage.generation != job.current_generation:
        raise WorkflowV3TransitionError(
            "needs_review stage generation drifted from the job"
        )
    evaluation = (
        db.query(WorkflowV3Evaluation)
        .filter(
            WorkflowV3Evaluation.workflow_job_id == job.id,
            WorkflowV3Evaluation.stage_run_id == current_stage.id,
            WorkflowV3Evaluation.decision == "needs_review",
        )
        .order_by(WorkflowV3Evaluation.id.desc())
        .first()
    )
    if evaluation is None:
        raise WorkflowV3TransitionError(
            "current needs_review stage has no bound evaluation"
        )
    if (
        db.query(WorkflowV3ReviewResolution)
        .filter(WorkflowV3ReviewResolution.evaluation_id == evaluation.id)
        .first()
        is not None
    ):
        raise WorkflowV3TransitionError(
            "needs_review evaluation already has an immutable resolution"
        )
    candidate = db.get(WorkflowV3Candidate, evaluation.candidate_id)
    if candidate is None or candidate.stage_run_id != current_stage.id:
        raise WorkflowV3TransitionError(
            "needs_review evaluation candidate binding is invalid"
        )
    if (
        candidate.generation != current_stage.generation
        or evaluation.generation != current_stage.generation
        or candidate.review_resolution_sha256
        != current_stage.review_resolution_sha256
        or evaluation.review_resolution_sha256
        != current_stage.review_resolution_sha256
    ):
        raise WorkflowV3TransitionError(
            "needs_review evaluation recovery lineage drifted"
        )

    evaluation_binding = manifest["evaluation"]
    findings = evaluation.load(evaluation.findings_json, [])
    blocking_findings = [
        finding
        for finding in findings
        if isinstance(finding, dict) and finding.get("blocking") is True
    ]
    if len(blocking_findings) != len(findings) or not blocking_findings:
        raise WorkflowV3TransitionError(
            "needs_review evaluation blockers are incomplete"
        )
    finding_fingerprints = [
        finding_fingerprint(finding) for finding in blocking_findings
    ]
    bound_evaluation_sha256 = evaluation_fingerprint(evaluation, candidate)
    if (
        manifest["job_id"] != public_id
        or evaluation_binding["id"] != str(evaluation.id)
        or evaluation_binding["sha256"] != bound_evaluation_sha256
        or evaluation_binding["candidate_id"] != str(candidate.id)
        or evaluation_binding["candidate_sha256"] != candidate.sha256
        or evaluation_binding["finding_fingerprints"] != finding_fingerprints
    ):
        raise WorkflowV3TransitionError(
            "review resolution manifest does not match the exact evaluation"
        )
    if manifest["authorization"]["authorized_by"] != authorized_by:
        raise WorkflowV3TransitionError(
            "review resolution authorizer does not match the authenticated admin"
        )
    resolved_fingerprints = [
        row["finding_fingerprint"] for row in manifest["blocker_resolutions"]
    ]
    if set(resolved_fingerprints) != set(finding_fingerprints):
        raise WorkflowV3TransitionError(
            "review resolution must cover every blocking finding exactly once"
        )

    contracts = contracts_for_version(job.workflow_version)
    stage_keys = [contract.key for contract in contracts]
    recovery_stage_key = min(
        (
            str(finding["recovery_stage"])
            for finding in blocking_findings
        ),
        key=stage_keys.index,
    )
    if manifest["recovery_stage"] != recovery_stage_key:
        raise WorkflowV3TransitionError(
            "review resolution must start at the earliest blocking recovery stage"
        )
    recovery_index = stage_keys.index(recovery_stage_key)
    source_generation = int(job.current_generation or current_stage.generation or 1)
    recovery_generation = source_generation + 1
    resolution = WorkflowV3ReviewResolution(
        workflow_job_id=job.id,
        evaluation_id=evaluation.id,
        idempotency_key=idempotency_key,
        evaluation_sha256=bound_evaluation_sha256,
        finding_fingerprints_json=WorkflowV3ReviewResolution.dump(
            finding_fingerprints
        ),
        authorized_by=authorized_by,
        recovery_stage_key=recovery_stage_key,
        source_generation=source_generation,
        recovery_generation=recovery_generation,
        manifest_bucket=manifest_bucket,
        manifest_object=manifest_object,
        manifest_sha256=manifest_sha256,
        manifest_size_bytes=manifest_size_bytes,
        manifest_json=WorkflowV3ReviewResolution.dump(manifest),
    )
    db.add(resolution)
    db.flush()

    input_kind = "frozen_source"
    input_promotion_id: int | None = None
    input_artifact_sha256 = job.source_popo_sha256
    if recovery_index > 0:
        predecessor = _latest_reliable_stage(
            db,
            job=job,
            stage_key=stage_keys[recovery_index - 1],
        )
        input_kind = "promoted_artifact"
        input_promotion_id = predecessor.promotion_id
        input_artifact_sha256 = predecessor.promoted_artifact_sha256

    recovery_stage: WorkflowV3StageRun | None = None
    for offset, contract in enumerate(contracts[recovery_index:]):
        attempts = (
            db.query(WorkflowV3StageRun.attempt)
            .filter(
                WorkflowV3StageRun.workflow_job_id == job.id,
                WorkflowV3StageRun.stage_key == contract.key,
            )
            .all()
        )
        next_attempt = max((row[0] for row in attempts), default=0) + 1
        stage = WorkflowV3StageRun(
            workflow_job_id=job.id,
            stage_key=contract.key,
            stage_version=contract.stage_version,
            attempt=next_attempt,
            generation=recovery_generation,
            review_resolution_id=resolution.id,
            review_resolution_sha256=manifest_sha256,
            machine_status="queued" if offset == 0 else "pending",
            spec_status="not_evaluated",
            owner=contract.owner,
            input_kind=input_kind if offset == 0 else "promoted_artifact",
            input_promotion_id=input_promotion_id if offset == 0 else None,
            input_artifact_sha256=input_artifact_sha256 if offset == 0 else "",
        )
        db.add(stage)
        if offset == 0:
            recovery_stage = stage
    db.flush()
    assert recovery_stage is not None

    job.current_generation = recovery_generation
    job.current_stage_key = recovery_stage_key
    job.machine_status = "queued"
    job.spec_status = "in_progress" if recovery_index > 0 else "not_evaluated"
    job.readiness_status = "not_ready"
    job.human_acceptance_status = "pending"
    job.error_code = ""
    job.error_message = ""
    job.finished_at = None
    _event(
        db,
        job,
        recovery_stage,
        "review_resolution_applied",
        (
            "An immutable human resolution created a new recovery generation; "
            "prior attempts and promotions remain unchanged."
        ),
        {
            "review_resolution_id": str(resolution.id),
            "review_resolution_sha256": manifest_sha256,
            "authorized_by": authorized_by,
            "evaluation_id": str(evaluation.id),
            "evaluation_sha256": bound_evaluation_sha256,
            "finding_fingerprints": finding_fingerprints,
            "source_generation": source_generation,
            "recovery_generation": recovery_generation,
            "recovery_stage": recovery_stage_key,
            "reused_predecessor_promotion_id": (
                str(input_promotion_id) if input_promotion_id else ""
            ),
            "recovery_mode": "human_evidence_then_worker_retry",
        },
        level="warning",
    )
    db.flush()
    return job, resolution, recovery_stage, None


def recover_stale_executions(
    db: Session,
    *,
    stale_after_seconds: int = 60,
    requeue: bool = True,
) -> list[str]:
    cutoff = datetime.utcnow() - timedelta(seconds=stale_after_seconds)
    stale = (
        db.query(WorkflowV3Execution)
        .filter(
            WorkflowV3Execution.machine_status == "running",
            WorkflowV3Execution.heartbeat_at < cutoff,
        )
        .order_by(WorkflowV3Execution.id.asc())
        .all()
    )
    recovered: list[str] = []
    for execution in stale:
        job = db.query(WorkflowV3Job).filter(WorkflowV3Job.id == execution.workflow_job_id).one()
        fail_execution(
            db,
            job.public_id,
            execution_id=execution.id,
            error_code="execution_lease_expired",
            error_message="producer heartbeat expired before candidate submission",
        )
        if requeue:
            retry_failed_stage(db, job.public_id)
        recovered.append(job.public_id)
    db.flush()
    return recovered


def record_human_acceptance(
    db: Session,
    public_id: str,
    *,
    accepted: bool,
    decided_by: str,
    output_id: int,
    manifest_sha256: str,
    reason: str = "",
) -> WorkflowV3Job:
    job = _locked_job(db, public_id)
    desired = "accepted" if accepted else "rejected"
    if job.machine_status != "succeeded" or job.spec_status != "passed" or job.readiness_status != "ready":
        raise WorkflowV3TransitionError("run is not ready for human acceptance")
    if not decided_by:
        raise WorkflowV3TransitionError("human decider identity is required")
    final_ready = (
        db.query(WorkflowV3ProjectionOutbox)
        .filter(
            WorkflowV3ProjectionOutbox.workflow_job_id == job.id,
            WorkflowV3ProjectionOutbox.event_kind == "final_ready",
        )
        .one_or_none()
    )
    manifest_sha256 = _require_sha256(
        manifest_sha256,
        "manifest_sha256",
    )
    if (
        final_ready is None
        or final_ready.status != "applied"
        or not isinstance(output_id, int)
        or isinstance(output_id, bool)
        or output_id <= 0
        or final_ready.projected_output_id != output_id
        or final_ready.projected_manifest_sha256 != manifest_sha256
    ):
        raise WorkflowV3TransitionError(
            "human acceptance requires the exact applied formal output"
        )
    if job.human_acceptance_status == desired:
        return job
    if job.human_acceptance_status != "pending":
        raise WorkflowV3TransitionError("human acceptance decision is immutable")
    job.human_acceptance_status = desired
    acceptance_outbox = _enqueue_human_acceptance_projection(
        db,
        job=job,
        accepted=accepted,
        decided_by=decided_by,
        reason=reason,
    )
    _event(
        db,
        job,
        _latest_stage(db, job.id, job.current_stage_key),
        "human_acceptance_recorded",
        "Human acceptance was recorded independently from machine and specification status.",
        {
            "accepted": accepted,
            "decided_by": decided_by,
            "reason": reason,
            "projection_outbox_id": str(acceptance_outbox.id),
            "projection_status": acceptance_outbox.status,
        },
    )
    db.flush()
    return job


def _operation_target_stage(
    db: Session,
    *,
    job: WorkflowV3Job,
    operation: str,
    target_id: int,
    lock: bool,
) -> WorkflowV3StageRun:
    if operation == "evaluation":
        query = db.query(WorkflowV3Candidate).filter(
            WorkflowV3Candidate.id == target_id
        )
        candidate = (query.with_for_update() if lock else query).first()
        if (
            candidate is None
            or candidate.workflow_job_id != job.id
            or candidate.status != "candidate"
            or not candidate.immutable
        ):
            raise WorkflowV3TransitionError(
                "candidate is not awaiting evaluation for this job"
            )
        return db.query(WorkflowV3StageRun).filter(
            WorkflowV3StageRun.id == candidate.stage_run_id
        ).one()

    query = db.query(WorkflowV3Evaluation).filter(
        WorkflowV3Evaluation.id == target_id
    )
    evaluation = (query.with_for_update() if lock else query).first()
    if (
        evaluation is None
        or evaluation.workflow_job_id != job.id
        or evaluation.decision != "passed"
        or not evaluation.spec_passed
    ):
        raise WorkflowV3TransitionError(
            "evaluation is not awaiting promotion for this job"
        )
    candidate = db.query(WorkflowV3Candidate).filter(
        WorkflowV3Candidate.id == evaluation.candidate_id
    ).one()
    if candidate.status != "evaluated_passed":
        raise WorkflowV3TransitionError("candidate is not promotion-ready")
    existing = db.query(WorkflowV3Promotion).filter(
        WorkflowV3Promotion.evaluation_id == evaluation.id
    ).first()
    if existing:
        raise WorkflowV3TransitionError("evaluation was already promoted")
    return db.query(WorkflowV3StageRun).filter(
        WorkflowV3StageRun.id == evaluation.stage_run_id
    ).one()


def _finish_operation_attempt_row(
    db: Session,
    *,
    job: WorkflowV3Job,
    stage: WorkflowV3StageRun,
    attempt: WorkflowV3OperationAttempt,
    status: str,
    error_code: str,
    error_message: str,
    retryable: bool,
) -> bool:
    now = datetime.utcnow()
    attempt.status = status
    attempt.error_code = error_code
    attempt.error_message = error_message
    attempt.heartbeat_at = now
    attempt.lease_expires_at = now
    attempt.finished_at = now
    expected_stage_status = (
        "awaiting_evaluation"
        if attempt.operation == "evaluation"
        else "awaiting_promotion"
    )
    exhausted = (
        status == "failed"
        and (not retryable or attempt.attempt >= attempt.max_attempts)
        and job.machine_status == "running"
        and stage.stage_key == job.current_stage_key
        and stage.machine_status == expected_stage_status
    )
    if exhausted:
        stage.machine_status = "failed"
        stage.error_code = error_code or f"{attempt.operation}_operation_failed"
        stage.error_message = (
            error_message or f"{attempt.operation} operation retry budget exhausted"
        )
        stage.finished_at = now
        job.machine_status = "failed"
        job.error_code = stage.error_code
        job.error_message = stage.error_message
        job.finished_at = now

    event_type = f"{attempt.operation}_operation_{status}"
    if status == "failed" and retryable and not exhausted:
        event_type = f"{attempt.operation}_operation_retryable_failure"
    _event(
        db,
        job,
        stage,
        event_type,
        (
            f"{attempt.operation.capitalize()} operation completed with {status}; "
            "no specification decision was inferred."
        ),
        {
            "operation_attempt_id": str(attempt.id),
            "operation_attempt": attempt.attempt,
            "target_id": str(attempt.target_id),
            "owner_identity": attempt.owner_identity,
            "error_code": error_code,
            "retryable": retryable,
            "retry_budget_exhausted": exhausted,
        },
        level="error" if status == "failed" else "warning" if status == "cancelled" else "info",
    )
    return exhausted


def _owner_token_sha256(owner_token: str) -> str:
    if not owner_token:
        return ""
    return hashlib.sha256(owner_token.encode("utf-8")).hexdigest()


def _execution_by_idempotency(db: Session, idempotency_key: str) -> WorkflowV3Execution | None:
    if not idempotency_key:
        return None
    return (
        db.query(WorkflowV3Execution)
        .filter(WorkflowV3Execution.idempotency_key == idempotency_key)
        .first()
    )


def _locked_job(db: Session, public_id: str) -> WorkflowV3Job:
    job = (
        db.query(WorkflowV3Job)
        .filter(WorkflowV3Job.public_id == public_id)
        .with_for_update()
        .first()
    )
    if not job:
        raise WorkflowV3TransitionError("workflow job not found")
    contracts_for_version(job.workflow_version)
    return job


def _latest_stage(db: Session, job_id: int, stage_key: str) -> WorkflowV3StageRun | None:
    return (
        db.query(WorkflowV3StageRun)
        .filter(
            WorkflowV3StageRun.workflow_job_id == job_id,
            WorkflowV3StageRun.stage_key == stage_key,
        )
        .order_by(WorkflowV3StageRun.attempt.desc())
        .first()
    )


def _latest_reliable_stage(
    db: Session,
    *,
    job: WorkflowV3Job,
    stage_key: str,
) -> WorkflowV3StageRun:
    stage = (
        db.query(WorkflowV3StageRun)
        .filter(
            WorkflowV3StageRun.workflow_job_id == job.id,
            WorkflowV3StageRun.stage_key == stage_key,
            WorkflowV3StageRun.machine_status == "succeeded",
            WorkflowV3StageRun.spec_status == "passed",
        )
        .order_by(
            WorkflowV3StageRun.generation.desc(),
            WorkflowV3StageRun.attempt.desc(),
        )
        .first()
    )
    if (
        stage is None
        or not stage.promotion_id
        or not stage.promoted_candidate_id
        or not stage.promoted_artifact_sha256
    ):
        raise WorkflowV3TransitionError(
            f"recovery predecessor {stage_key!r} has no reliable promotion"
        )
    promotion = db.get(WorkflowV3Promotion, stage.promotion_id)
    candidate = db.get(WorkflowV3Candidate, stage.promoted_candidate_id)
    evaluation = (
        db.get(WorkflowV3Evaluation, promotion.evaluation_id)
        if promotion is not None
        else None
    )
    if (
        promotion is None
        or candidate is None
        or evaluation is None
        or promotion.workflow_job_id != job.id
        or promotion.stage_run_id != stage.id
        or promotion.candidate_id != candidate.id
        or promotion.artifact_sha256 != candidate.sha256
        or candidate.status != "promoted"
        or candidate.stage_run_id != stage.id
        or candidate.sha256 != stage.promoted_artifact_sha256
        or evaluation.stage_run_id != stage.id
        or evaluation.candidate_id != candidate.id
        or evaluation.decision != "passed"
        or evaluation.spec_passed is not True
    ):
        raise WorkflowV3TransitionError(
            f"recovery predecessor {stage_key!r} promotion lineage drifted"
        )
    return stage


def _validate_needs_review_findings(
    findings: list,
    *,
    workflow_version: str,
    current_stage_key: str,
) -> None:
    """Require a complete, source-bound handoff before pausing for a human.

    A generic validator failure is a failure, not a review state.  Review is
    reserved for genuine ambiguity with evidence and an executable recovery
    point.
    """

    contracts = contracts_for_version(workflow_version)
    stage_keys = [row.key for row in contracts]
    current_index = stage_keys.index(current_stage_key)
    if not findings:
        raise WorkflowV3TransitionError(
            "needs_review requires at least one evidence-bound finding"
        )
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise WorkflowV3TransitionError(
                f"needs_review finding {index} must be an object"
            )
        if not str(finding.get("code") or "").strip() or finding.get("blocking") is not True:
            raise WorkflowV3TransitionError(
                f"needs_review finding {index} requires code and blocking=true"
            )
        responsible = str(finding.get("responsible_stage") or "")
        recovery = str(finding.get("recovery_stage") or "")
        if responsible not in stage_keys or recovery not in stage_keys:
            raise WorkflowV3TransitionError(
                f"needs_review finding {index} has an unknown responsibility or recovery stage"
            )
        if stage_keys.index(responsible) > current_index or stage_keys.index(recovery) > current_index:
            raise WorkflowV3TransitionError(
                f"needs_review finding {index} cannot route to a future stage"
            )
        evidence_refs = finding.get("evidence_refs")
        if not isinstance(evidence_refs, list) or not evidence_refs:
            raise WorkflowV3TransitionError(
                f"needs_review finding {index} requires evidence_refs"
            )
        for evidence_index, reference in enumerate(evidence_refs):
            if not isinstance(reference, dict):
                raise WorkflowV3TransitionError(
                    f"needs_review finding {index} evidence {evidence_index} must be an object"
                )
            sha256 = str(reference.get("sha256") or "")
            has_location = bool(
                str(reference.get("path") or "")
                or (
                    str(reference.get("bucket") or "")
                    and str(reference.get("object") or "")
                )
            )
            if not has_location or len(sha256) != 64 or any(
                char not in "0123456789abcdef" for char in sha256
            ):
                raise WorkflowV3TransitionError(
                    f"needs_review finding {index} evidence {evidence_index} is not hash-bound"
                )
        handoff = finding.get("handoff")
        if not isinstance(handoff, dict):
            raise WorkflowV3TransitionError(
                f"needs_review finding {index} requires a handoff object"
            )
        required_handoff = {
            "summary": str(handoff.get("summary") or "").strip(),
            "required_action": str(handoff.get("required_action") or "").strip(),
            "resume_stage": str(handoff.get("resume_stage") or "").strip(),
        }
        if not all(required_handoff.values()) or required_handoff["resume_stage"] != recovery:
            raise WorkflowV3TransitionError(
                f"needs_review finding {index} has an incomplete or inconsistent handoff"
            )


def _enqueue_final_projection(
    db: Session,
    *,
    job: WorkflowV3Job,
    stage: WorkflowV3StageRun,
    candidate: WorkflowV3Candidate,
    promotion: WorkflowV3Promotion,
) -> WorkflowV3ProjectionOutbox:
    payload = job.load(job.payload_json, {})
    shadow = payload.get("shadow") is True
    identity = hashlib.sha256(
        (
            f"{job.public_id}\n{promotion.id}\n{candidate.bucket}\n"
            f"{candidate.object_name}\n{candidate.sha256}"
        ).encode("utf-8")
    ).hexdigest()
    existing = (
        db.query(WorkflowV3ProjectionOutbox)
        .filter(
            WorkflowV3ProjectionOutbox.workflow_job_id == job.id,
            WorkflowV3ProjectionOutbox.event_kind == "final_ready",
        )
        .first()
    )
    if existing:
        if (
            existing.final_promotion_id != promotion.id
            or existing.idempotency_key != identity
        ):
            raise WorkflowV3TransitionError(
                "final projection outbox conflicts with another promotion"
            )
        return existing
    source_evidence = (
        payload.get("source_evidence")
        if isinstance(payload.get("source_evidence"), dict)
        else {}
    )
    promoted_chain = []
    contract_order = {
        contract.key: contract.order for contract in contracts_for_version(job.workflow_version)
    }
    chain_rows = (
        db.query(
            WorkflowV3Promotion,
            WorkflowV3Candidate,
            WorkflowV3StageRun,
            WorkflowV3Evaluation,
        )
        .join(
            WorkflowV3Candidate,
            WorkflowV3Candidate.id == WorkflowV3Promotion.candidate_id,
        )
        .join(
            WorkflowV3StageRun,
            WorkflowV3StageRun.id == WorkflowV3Promotion.stage_run_id,
        )
        .join(
            WorkflowV3Evaluation,
            WorkflowV3Evaluation.id == WorkflowV3Promotion.evaluation_id,
        )
        .filter(WorkflowV3Promotion.workflow_job_id == job.id)
        .all()
    )
    for chain_promotion, chain_candidate, chain_stage, chain_evaluation in sorted(
        chain_rows,
        key=lambda row: contract_order[row[2].stage_key],
    ):
        promoted_chain.append(
            {
                "stage_key": chain_stage.stage_key,
                "stage_version": chain_stage.stage_version,
                "stage_run_id": str(chain_stage.id),
                "promotion_id": str(chain_promotion.id),
                "promotion_idempotency_key": chain_promotion.idempotency_key,
                "evaluation": {
                    "id": str(chain_evaluation.id),
                    "decision": chain_evaluation.decision,
                    "spec_passed": bool(chain_evaluation.spec_passed),
                    "evaluator_identity": chain_evaluation.evaluator_identity,
                    "evaluator_version": chain_evaluation.evaluator_version,
                    "policy_sha256": chain_evaluation.policy_sha256,
                    "gate_results": chain_evaluation.load(
                        chain_evaluation.gate_results_json,
                        {},
                    ),
                    "findings": chain_evaluation.load(
                        chain_evaluation.findings_json,
                        [],
                    ),
                },
                "candidate": {
                    "id": str(chain_candidate.id),
                    "bucket": chain_candidate.bucket,
                    "object": chain_candidate.object_name,
                    "sha256": chain_candidate.sha256,
                    "size_bytes": chain_candidate.size_bytes,
                    "metadata": chain_candidate.load(
                        chain_candidate.metadata_json,
                        {},
                    ),
                },
            }
        )
    outbox_payload = {
        "schema_version": "luceon.worker-v3-final-projection/v1",
        "shadow": shadow,
        "job_id": job.public_id,
        "workflow_version": job.workflow_version,
        "material_pk": str(job.material_pk),
        "material_id": job.material_id,
        "user_id": job.user_id,
        "stage_key": stage.stage_key,
        "final_promotion_id": str(promotion.id),
        "candidate": {
            "id": str(candidate.id),
            "bucket": candidate.bucket,
            "object": candidate.object_name,
            "sha256": candidate.sha256,
            "size_bytes": candidate.size_bytes,
            "metadata": candidate.load(candidate.metadata_json, {}),
        },
        "promoted_chain": promoted_chain,
        "release": {
            "version": job.skill_release_version,
            "manifest_sha256": job.skill_release_sha256,
        },
        "template_sha256": job.template_sha256,
        "input_set_sha256": str(source_evidence.get("input_set_sha256") or ""),
        "human_acceptance_status": job.human_acceptance_status,
    }
    outbox = WorkflowV3ProjectionOutbox(
        workflow_job_id=job.id,
        final_promotion_id=promotion.id,
        idempotency_key=identity,
        event_kind="final_ready",
        status="suppressed" if shadow else "pending",
        target_kind="material_output",
        payload_json=WorkflowV3ProjectionOutbox.dump(outbox_payload),
    )
    db.add(outbox)
    db.flush()
    return outbox


def _enqueue_human_acceptance_projection(
    db: Session,
    *,
    job: WorkflowV3Job,
    accepted: bool,
    decided_by: str,
    reason: str,
) -> WorkflowV3ProjectionOutbox:
    final_ready = (
        db.query(WorkflowV3ProjectionOutbox)
        .filter(
            WorkflowV3ProjectionOutbox.workflow_job_id == job.id,
            WorkflowV3ProjectionOutbox.event_kind == "final_ready",
        )
        .one_or_none()
    )
    if final_ready is None:
        raise WorkflowV3TransitionError(
            "human acceptance cannot project before final readiness outbox"
        )
    source_payload = final_ready.load(final_ready.payload_json, {})
    identity = hashlib.sha256(
        (
            f"{job.public_id}\nhuman_acceptance\n{accepted}\n"
            f"{final_ready.final_promotion_id}\n{job.skill_release_sha256}"
        ).encode("utf-8")
    ).hexdigest()
    existing = (
        db.query(WorkflowV3ProjectionOutbox)
        .filter(
            WorkflowV3ProjectionOutbox.workflow_job_id == job.id,
            WorkflowV3ProjectionOutbox.event_kind == "human_acceptance",
        )
        .one_or_none()
    )
    if existing:
        if existing.idempotency_key != identity:
            raise WorkflowV3TransitionError(
                "human acceptance projection conflicts with another immutable decision"
            )
        return existing
    payload = {
        "schema_version": "luceon.worker-v3-human-acceptance-projection/v1",
        "shadow": source_payload.get("shadow") is True,
        "job_id": job.public_id,
        "workflow_version": job.workflow_version,
        "material_pk": str(job.material_pk),
        "material_id": job.material_id,
        "user_id": job.user_id,
        "final_promotion_id": str(final_ready.final_promotion_id),
        "final_ready_outbox_id": str(final_ready.id),
        "projected_output_id": str(final_ready.projected_output_id),
        "projected_manifest": {
            "bucket": final_ready.projected_manifest_bucket,
            "object": final_ready.projected_manifest_object,
            "sha256": final_ready.projected_manifest_sha256,
        },
        "release": {
            "version": job.skill_release_version,
            "manifest_sha256": job.skill_release_sha256,
        },
        "accepted": accepted,
        "decided_by": decided_by,
        "reason": reason,
    }
    outbox = WorkflowV3ProjectionOutbox(
        workflow_job_id=job.id,
        final_promotion_id=final_ready.final_promotion_id,
        idempotency_key=identity,
        event_kind="human_acceptance",
        status="suppressed" if payload["shadow"] else "pending",
        target_kind="material_output",
        payload_json=WorkflowV3ProjectionOutbox.dump(payload),
    )
    db.add(outbox)
    db.flush()
    return outbox


def _verify_stage_input_is_promoted(db, job, stage, contracts) -> None:
    index = next((i for i, row in enumerate(contracts) if row.key == stage.stage_key), None)
    if index is None:
        raise WorkflowV3TransitionError("current stage is not in the registered workflow")
    if index == 0:
        if (
            stage.input_kind != "frozen_source"
            or stage.input_promotion_id is not None
            or stage.input_artifact_sha256 != job.source_popo_sha256
        ):
            raise WorkflowV3TransitionError("first stage input is not the frozen source SHA")
        return
    _require_valid_promotion_input(db, job, stage)
    promotion = db.query(WorkflowV3Promotion).filter(WorkflowV3Promotion.id == stage.input_promotion_id).one()
    upstream_stage = db.query(WorkflowV3StageRun).filter(WorkflowV3StageRun.id == promotion.stage_run_id).one()
    if upstream_stage.stage_key != contracts[index - 1].key:
        raise WorkflowV3TransitionError("stage input was not promoted by the immediate upstream stage")


def _require_valid_promotion_input(db: Session, job: WorkflowV3Job, stage: WorkflowV3StageRun) -> None:
    if stage.input_kind != "promoted_artifact" or not stage.input_promotion_id:
        raise WorkflowV3TransitionError("downstream stage has no promoted input")
    promotion = (
        db.query(WorkflowV3Promotion)
        .filter(WorkflowV3Promotion.id == stage.input_promotion_id)
        .first()
    )
    if (
        not promotion
        or promotion.workflow_job_id != job.id
        or promotion.artifact_sha256 != stage.input_artifact_sha256
    ):
        raise WorkflowV3TransitionError("downstream stage input SHA does not match its promotion")


def _require_sha256(value: str, field_name: str) -> str:
    normalized = (value or "").lower()
    if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
        raise WorkflowV3TransitionError(f"{field_name} must be a lowercase SHA-256")
    return normalized


def _event(
    db: Session,
    job: WorkflowV3Job,
    stage: WorkflowV3StageRun | None,
    event_type: str,
    message: str,
    payload: dict,
    *,
    level: str = "info",
) -> None:
    db.add(
        WorkflowV3Event(
            workflow_job_id=job.id,
            stage_run_id=stage.id if stage else None,
            event_type=event_type,
            level=level,
            message=message,
            payload_json=WorkflowV3Event.dump(payload),
        )
    )
