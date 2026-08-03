from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.workflow_v2.contracts import contract_for, contracts_for_version
from app.workflow_v2.models import RepairAttempt, StageEvent, StageRun, WorkflowJob


ACTIVE_STAGE_STATUSES = {"queued", "running"}
TERMINAL_JOB_STATUSES = {"succeeded", "cancelled", "needs_review", "blocked", "handoff_ready"}


class WorkflowTransitionError(ValueError):
    pass


def claim_current_stage(db: Session, public_id: str, worker_id: str) -> tuple[WorkflowJob, StageRun]:
    job = _locked_job(db, public_id)
    if job.cancellation_requested:
        raise WorkflowTransitionError("job cancellation was requested")
    if job.status in TERMINAL_JOB_STATUSES:
        raise WorkflowTransitionError(f"job is already {job.status}")
    stage = _latest_stage(db, job.id, job.current_stage_key)
    if not stage or stage.status != "queued":
        current = stage.status if stage else "missing"
        raise WorkflowTransitionError(f"current stage is not claimable: {current}")
    now = datetime.utcnow()
    stage.status = "running"
    stage.started_at = stage.started_at or now
    stage.heartbeat_at = now
    stage.metrics_json = StageRun.dump({"worker_id": worker_id})
    job.status = "running"
    job.started_at = job.started_at or now
    _event(db, job.id, stage.id, "stage_started", f"Stage {stage.stage_key} started.", {"worker_id": worker_id, "attempt": stage.attempt})
    db.flush()
    return job, stage


def complete_current_stage(
    db: Session,
    public_id: str,
    *,
    output_bucket: str,
    output_object: str,
    output_sha256: str,
    metrics: dict | None = None,
) -> tuple[WorkflowJob, StageRun]:
    job = _locked_job(db, public_id)
    stage = _latest_stage(db, job.id, job.current_stage_key)
    if not stage or stage.status != "running":
        raise WorkflowTransitionError("current stage is not running")
    if not output_bucket or not output_object or len(output_sha256) != 64:
        raise WorkflowTransitionError("a content-addressed output manifest is required")
    stage.status = "succeeded"
    stage.output_manifest_bucket = output_bucket
    stage.output_manifest_object = output_object
    stage.output_manifest_sha256 = output_sha256
    stage.metrics_json = StageRun.dump(metrics or {})
    stage.finished_at = datetime.utcnow()
    stage.heartbeat_at = stage.finished_at
    _event(
        db,
        job.id,
        stage.id,
        "stage_succeeded",
        f"Stage {stage.stage_key} passed its acceptance gates.",
        {"manifest": {"bucket": output_bucket, "object": output_object, "sha256": output_sha256}},
    )
    next_contract = _next_contract(job.workflow_version, stage.stage_key)
    if next_contract:
        next_stage = _latest_stage(db, job.id, next_contract.key)
        if not next_stage or next_stage.status != "pending":
            raise WorkflowTransitionError("next stage is not pending")
        next_stage.status = "queued"
        next_stage.input_manifest_sha256 = output_sha256
        job.current_stage_key = next_contract.key
        job.status = "queued"
        _event(db, job.id, next_stage.id, "stage_queued", f"Stage {next_contract.key} is ready.", {"upstream_sha256": output_sha256})
    else:
        finished_at = datetime.utcnow()
        queued_repairs = (
            db.query(RepairAttempt)
            .filter(RepairAttempt.workflow_job_id == job.id, RepairAttempt.status == "queued")
            .all()
        )
        for repair in queued_repairs:
            repair.status = "failed"
            repair.finished_at = finished_at
            repair.error_message = "superseded by successful workflow completion"
        job.status = "succeeded"
        job.finished_at = finished_at
        _event(
            db,
            job.id,
            stage.id,
            "job_succeeded",
            "All Worker V2.3 stages passed independent gates.",
            {"superseded_repair_count": len(queued_repairs)},
        )
    db.flush()
    return job, stage


def fail_current_stage(db: Session, public_id: str, *, error_code: str, error_message: str) -> tuple[WorkflowJob, StageRun]:
    job = _locked_job(db, public_id)
    stage = _latest_stage(db, job.id, job.current_stage_key)
    if not stage or stage.status not in ACTIVE_STAGE_STATUSES:
        raise WorkflowTransitionError("current stage is not active")
    stage.status = "failed"
    stage.error_code = error_code
    stage.error_message = error_message
    stage.finished_at = datetime.utcnow()
    stage.heartbeat_at = stage.finished_at
    job.status = "failed"
    job.error_code = error_code
    job.error_message = error_message
    job.finished_at = datetime.utcnow()
    _event(db, job.id, stage.id, "stage_failed", f"Stage {stage.stage_key} failed.", {"error_code": error_code, "error_message": error_message}, level="error")
    db.flush()
    return job, stage


def block_current_stage_for_review(
    db: Session,
    public_id: str,
    *,
    output_bucket: str,
    output_object: str,
    output_sha256: str,
    error_message: str,
    metrics: dict | None = None,
    handoff_ready: bool = False,
) -> tuple[WorkflowJob, StageRun]:
    """Persist immutable evidence while distinguishing hard gates from a usable handoff."""
    job = _locked_job(db, public_id)
    stage = _latest_stage(db, job.id, job.current_stage_key)
    if not stage or stage.status != "running":
        raise WorkflowTransitionError("current stage is not running")
    if not output_bucket or not output_object or len(output_sha256) != 64:
        raise WorkflowTransitionError("a content-addressed candidate manifest is required")
    now = datetime.utcnow()
    terminal_status = "handoff_ready" if handoff_ready else "blocked"
    stage.status = terminal_status
    stage.output_manifest_bucket = output_bucket
    stage.output_manifest_object = output_object
    stage.output_manifest_sha256 = output_sha256
    stage.metrics_json = StageRun.dump(metrics or {})
    stage.error_code = "quality_blocked"
    stage.error_message = error_message
    stage.finished_at = now
    stage.heartbeat_at = now
    job.status = terminal_status
    job.error_code = "quality_blocked"
    job.error_message = error_message
    job.finished_at = now
    _event(
        db,
        job.id,
        stage.id,
        "stage_handoff_ready" if handoff_ready else "stage_quality_blocked",
        (
            f"Stage {stage.stage_key} produced a complete deliverable ready for manual handoff."
            if handoff_ready
            else f"Stage {stage.stage_key} was stopped by a hard quality gate."
        ),
        {
            "manifest": {"bucket": output_bucket, "object": output_object, "sha256": output_sha256},
            "quality_blockers": (metrics or {}).get("quality_blockers", []),
        },
        level="warning",
    )
    db.flush()
    return job, stage


def retry_failed_stage(db: Session, public_id: str) -> tuple[WorkflowJob, StageRun]:
    job = _locked_job(db, public_id)
    if job.status != "failed":
        raise WorkflowTransitionError("only a failed job can retry")
    previous = _latest_stage(db, job.id, job.current_stage_key)
    if not previous or previous.status != "failed":
        raise WorkflowTransitionError("current stage has no failed attempt")
    contract = contract_for(job.workflow_version, previous.stage_key)
    retry = StageRun(
        workflow_job_id=job.id,
        stage_key=previous.stage_key,
        stage_version=contract.version,
        attempt=previous.attempt + 1,
        status="queued",
        owner=contract.owner,
        input_manifest_sha256=previous.input_manifest_sha256,
    )
    db.add(retry)
    db.flush()
    job.status = "queued"
    job.error_code = ""
    job.error_message = ""
    job.finished_at = None
    _event(db, job.id, retry.id, "stage_retry_queued", f"Retry {retry.attempt} queued for {retry.stage_key}.", {"previous_stage_run_id": str(previous.id)})
    db.flush()
    return job, retry


def restart_from_stage(db: Session, public_id: str, stage_key: str) -> tuple[WorkflowJob, StageRun]:
    job = _locked_job(db, public_id)
    if job.status not in {"failed", "needs_review", "blocked", "handoff_ready", "succeeded"}:
        raise WorkflowTransitionError("only a failed, blocked, handoff-ready, or succeeded job can restart from an earlier stage")
    try:
        contract = contract_for(job.workflow_version, stage_key)
    except KeyError:
        raise WorkflowTransitionError("unknown workflow stage")
    current_contract = contract_for(job.workflow_version, job.current_stage_key)
    if contract.order > current_contract.order:
        raise WorkflowTransitionError("restart stage cannot be later than the failed stage")
    stage_contracts = contracts_for_version(job.workflow_version)
    preceding = [item for item in stage_contracts if item.order < contract.order]
    upstream_sha256 = ""
    if preceding:
        previous = _latest_stage(db, job.id, preceding[-1].key)
        if not previous or previous.status != "succeeded" or not previous.output_manifest_sha256:
            raise WorkflowTransitionError("the preceding stage has no accepted immutable artifact")
        upstream_sha256 = previous.output_manifest_sha256

    new_stages = []
    for item in stage_contracts:
        if item.order < contract.order:
            continue
        latest = _latest_stage(db, job.id, item.key)
        attempt = (latest.attempt if latest else 0) + 1
        stage = StageRun(
            workflow_job_id=job.id,
            stage_key=item.key,
            stage_version=item.version,
            attempt=attempt,
            status="queued" if item.key == stage_key else "pending",
            owner=item.owner,
            input_manifest_sha256=upstream_sha256 if item.key == stage_key else "",
        )
        db.add(stage)
        new_stages.append(stage)
    db.flush()
    target = new_stages[0]
    superseded_repairs = (
        db.query(RepairAttempt)
        .filter(RepairAttempt.workflow_job_id == job.id, RepairAttempt.status == "queued")
        .all()
    )
    for repair in superseded_repairs:
        repair.status = "failed"
        repair.error_message = f"superseded by workflow restart from {stage_key}"
        repair.finished_at = datetime.utcnow()
    job.current_stage_key = stage_key
    job.status = "queued"
    job.error_code = ""
    job.error_message = ""
    job.finished_at = None
    _event(
        db,
        job.id,
        target.id,
        "stage_branch_restarted",
        f"A new immutable branch starts from {stage_key}.",
        {"stage_key": stage_key, "attempt": target.attempt, "upstream_sha256": upstream_sha256, "superseded_repair_count": len(superseded_repairs)},
    )
    db.flush()
    return job, target


def request_cancellation(db: Session, public_id: str) -> WorkflowJob:
    job = _locked_job(db, public_id)
    if job.status in TERMINAL_JOB_STATUSES:
        return job
    job.cancellation_requested = True
    stage = _latest_stage(db, job.id, job.current_stage_key)
    if stage and stage.status in {"queued", "pending"}:
        stage.status = "cancelled"
        stage.finished_at = datetime.utcnow()
        job.status = "cancelled"
        job.finished_at = datetime.utcnow()
    _event(db, job.id, stage.id if stage else None, "cancellation_requested", "Workflow cancellation requested.", {})
    db.flush()
    return job


def record_stage_progress(db: Session, public_id: str, *, step: str, message: str, payload: dict | None = None) -> StageEvent:
    job = _locked_job(db, public_id)
    stage = _latest_stage(db, job.id, job.current_stage_key)
    if not stage or stage.status != "running":
        raise WorkflowTransitionError("current stage is not running")
    event = StageEvent(
        workflow_job_id=job.id,
        stage_run_id=stage.id,
        event_type="stage_progress",
        level="info",
        message=message,
        payload_json=StageEvent.dump({"step": step, **(payload or {})}),
    )
    db.add(event)
    db.flush()
    return event


def touch_stage_heartbeat(db: Session, public_id: str, worker_id: str) -> bool:
    job = db.query(WorkflowJob).filter(WorkflowJob.public_id == public_id).first()
    if not job or job.status != "running":
        return False
    stage = _latest_stage(db, job.id, job.current_stage_key)
    if not stage or stage.status != "running":
        return False
    metrics = stage.load(stage.metrics_json, {})
    if metrics.get("worker_id") != worker_id:
        return False
    stage.heartbeat_at = datetime.utcnow()
    db.flush()
    return True


def recover_stale_stages(db: Session, *, stale_after_seconds: int = 60, active_lease_checker=None) -> list[str]:
    cutoff = datetime.utcnow() - timedelta(seconds=stale_after_seconds)
    stale = (
        db.query(StageRun)
        .filter(StageRun.status == "running", StageRun.heartbeat_at.isnot(None), StageRun.heartbeat_at < cutoff)
        .order_by(StageRun.id)
        .all()
    )
    recovered = []
    for stage in stale:
        job = db.query(WorkflowJob).filter(WorkflowJob.id == stage.workflow_job_id).with_for_update().one()
        if active_lease_checker and active_lease_checker(job.public_id):
            continue
        if job.status != "running" or job.current_stage_key != stage.stage_key:
            continue
        stage.status = "failed"
        stage.error_code = "worker_lease_expired"
        stage.error_message = "Worker heartbeat expired; the same stage was queued for recovery."
        stage.finished_at = datetime.utcnow()
        job.status = "failed"
        job.error_code = stage.error_code
        job.error_message = stage.error_message
        job.finished_at = stage.finished_at
        _event(db, job.id, stage.id, "worker_lease_expired", "Worker heartbeat expired; preserving the interrupted attempt.", {"attempt": stage.attempt}, level="error")
        retry_failed_stage(db, job.public_id)
        recovered.append(job.public_id)
    db.flush()
    return recovered


def authorize_execution(db: Session, public_id: str, *, requested_by: str) -> WorkflowJob:
    job = _locked_job(db, public_id)
    if job.status != "queued":
        raise WorkflowTransitionError("only a queued job can be authorized")
    payload = job.load(job.payload_json, {})
    payload["execution_authorized"] = True
    payload["execution_requested_by"] = requested_by
    job.payload_json = WorkflowJob.dump(payload)
    stage = _latest_stage(db, job.id, job.current_stage_key)
    _event(db, job.id, stage.id if stage else None, "execution_requested", "Worker V2.3 execution requested.", {"requested_by": requested_by})
    db.flush()
    return job


def _locked_job(db: Session, public_id: str) -> WorkflowJob:
    job = db.query(WorkflowJob).filter(WorkflowJob.public_id == public_id).with_for_update().first()
    if not job:
        raise WorkflowTransitionError("workflow job not found")
    return job


def _latest_stage(db: Session, job_id: int, stage_key: str) -> StageRun | None:
    return (
        db.query(StageRun)
        .filter(StageRun.workflow_job_id == job_id, StageRun.stage_key == stage_key)
        .order_by(StageRun.attempt.desc())
        .first()
    )


def _next_contract(workflow_version: str, stage_key: str):
    contracts = contracts_for_version(workflow_version)
    for index, contract in enumerate(contracts):
        if contract.key == stage_key:
            return contracts[index + 1] if index + 1 < len(contracts) else None
    raise WorkflowTransitionError(f"unknown stage: {stage_key}")


def _event(db: Session, job_id: int, stage_run_id: int | None, event_type: str, message: str, payload: dict, *, level: str = "info") -> None:
    db.add(StageEvent(workflow_job_id=job_id, stage_run_id=stage_run_id, event_type=event_type, level=level, message=message, payload_json=StageEvent.dump(payload)))
