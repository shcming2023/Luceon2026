from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.workflow_v3.contracts import WORKFLOW_VERSION
from app.workflow_v3.models import (
    WorkflowV3Base,
    WorkflowV3Candidate,
    WorkflowV3Evaluation,
    WorkflowV3Execution,
    WorkflowV3Job,
    WorkflowV3OperationAttempt,
    WorkflowV3SkillRelease,
    WorkflowV3StageRun,
)
from app.workflow_v3.queue import (
    claim_next_evaluation_item,
    claim_next_promotion_item,
)
from app.workflow_v3.state_machine import (
    cancel_job,
    finish_operation_attempt,
    recover_stale_operation_attempts,
    touch_operation_heartbeat,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'operations.sqlite'}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    WorkflowV3Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory()
    release = WorkflowV3SkillRelease(
        release_version="v3-operation-fixture",
        manifest_sha256="1" * 64,
        package_bucket="releases",
        package_object="worker-v3.tar.gz",
        package_sha256="2" * 64,
        workflow_version=WORKFLOW_VERSION,
        template_sha256="3" * 64,
        runtime_identity_sha256="4" * 64,
        manifest_json="{}",
        status="registered",
        registered_by="test",
    )
    db.add(release)
    db.flush()
    job = WorkflowV3Job(
        public_id="operation-job",
        idempotency_key="operation-job-key",
        user_id="u1",
        material_pk=4242,
        material_id="pdf-operation",
        source_popo_bucket="popo",
        source_popo_object="run/manifest.json",
        source_popo_sha256="5" * 64,
        workflow_version=WORKFLOW_VERSION,
        skill_release_id=release.id,
        skill_release_version=release.release_version,
        skill_release_sha256=release.manifest_sha256,
        template_sha256=release.template_sha256,
        machine_status="running",
        spec_status="in_progress",
        current_stage_key="intake_snapshot",
    )
    db.add(job)
    db.flush()
    stage = WorkflowV3StageRun(
        workflow_job_id=job.id,
        stage_key="intake_snapshot",
        stage_version="1",
        attempt=1,
        machine_status="awaiting_evaluation",
        spec_status="not_evaluated",
        owner="deterministic",
        input_kind="frozen_source",
        input_artifact_sha256=job.source_popo_sha256,
    )
    db.add(stage)
    db.flush()
    execution = WorkflowV3Execution(
        workflow_job_id=job.id,
        stage_run_id=stage.id,
        producer_identity="producer",
        idempotency_key="producer-execution",
        machine_status="succeeded",
        skill_release_sha256=release.manifest_sha256,
        runtime_identity_sha256=release.runtime_identity_sha256,
        finished_at=datetime.utcnow(),
    )
    db.add(execution)
    db.flush()
    candidate = WorkflowV3Candidate(
        workflow_job_id=job.id,
        stage_run_id=stage.id,
        execution_id=execution.id,
        idempotency_key="candidate",
        artifact_kind="fixture",
        bucket="candidates",
        object_name="operation-job/intake/candidate.tar.gz",
        object_identity_hash=_sha("candidate-identity"),
        sha256="6" * 64,
        size_bytes=100,
        immutable=True,
        status="candidate",
    )
    db.add(candidate)
    db.commit()
    db.close()
    return factory


def _passed_evaluation(factory) -> int:
    db = factory()
    job = db.query(WorkflowV3Job).one()
    stage = db.query(WorkflowV3StageRun).one()
    candidate = db.query(WorkflowV3Candidate).one()
    candidate.status = "evaluated_passed"
    stage.machine_status = "awaiting_promotion"
    stage.spec_status = "passed"
    evaluation = WorkflowV3Evaluation(
        workflow_job_id=job.id,
        stage_run_id=stage.id,
        candidate_id=candidate.id,
        idempotency_key="evaluation",
        evaluator_identity="evaluator",
        evaluator_version="v1",
        policy_sha256="7" * 64,
        decision="passed",
        spec_passed=True,
        gate_results_json='{"frozen_inputs_hash_bound":true}',
        findings_json="[]",
    )
    db.add(evaluation)
    db.commit()
    evaluation_id = evaluation.id
    db.close()
    return evaluation_id


def test_evaluation_claim_has_one_owner_token_and_renewable_lease(tmp_path):
    factory = _factory(tmp_path)
    first_db = factory()
    first = claim_next_evaluation_item(
        first_db,
        owner_identity="evaluator-a",
        lease_seconds=60,
        max_attempts=2,
    )
    first_db.commit()
    first_db.close()

    second_db = factory()
    assert (
        claim_next_evaluation_item(
            second_db,
            owner_identity="evaluator-b",
            lease_seconds=60,
            max_attempts=2,
        )
        is None
    )
    second_db.rollback()
    second_db.close()

    heartbeat_db = factory()
    attempt = heartbeat_db.get(
        WorkflowV3OperationAttempt,
        first.operation_attempt_id,
    )
    old_expiry = attempt.lease_expires_at
    assert touch_operation_heartbeat(
        heartbeat_db,
        first.public_id,
        operation_attempt_id=attempt.id,
        operation="evaluation",
        target_id=first.candidate_id,
        owner_identity="evaluator-a",
        owner_token="wrong-token",
    ) is False
    assert touch_operation_heartbeat(
        heartbeat_db,
        first.public_id,
        operation_attempt_id=attempt.id,
        operation="evaluation",
        target_id=first.candidate_id,
        owner_identity="evaluator-a",
        owner_token=first.owner_token,
    ) is True
    assert attempt.lease_expires_at >= old_expiry
    heartbeat_db.commit()
    heartbeat_db.close()


def test_operation_claims_skip_release_bound_to_another_runtime(tmp_path):
    factory = _factory(tmp_path)
    db = factory()
    assert (
        claim_next_evaluation_item(
            db,
            owner_identity="evaluator-wrong-runtime",
            runtime_identity_sha256="9" * 64,
        )
        is None
    )
    assert db.query(WorkflowV3OperationAttempt).count() == 0
    matched = claim_next_evaluation_item(
        db,
        owner_identity="evaluator-matching-runtime",
        runtime_identity_sha256="4" * 64,
    )
    assert matched is not None
    db.rollback()
    db.close()

    evaluation_id = _passed_evaluation(factory)
    db = factory()
    assert (
        claim_next_promotion_item(
            db,
            owner_identity="promoter-wrong-runtime",
            runtime_identity_sha256="9" * 64,
        )
        is None
    )
    assert (
        claim_next_promotion_item(
            db,
            owner_identity="promoter-matching-runtime",
            runtime_identity_sha256="4" * 64,
        )
        is not None
    )
    db.rollback()
    db.close()


def test_two_evaluators_cannot_claim_the_same_candidate(tmp_path):
    factory = _factory(tmp_path)
    barrier = threading.Barrier(2)
    results = []
    errors = []

    def claim(identity: str) -> None:
        db = factory()
        try:
            barrier.wait()
            item = claim_next_evaluation_item(
                db,
                owner_identity=identity,
                lease_seconds=60,
                max_attempts=2,
            )
            db.commit()
            results.append(item)
        except Exception as exc:  # pragma: no cover - asserted below
            db.rollback()
            errors.append(exc)
        finally:
            db.close()

    threads = [
        threading.Thread(target=claim, args=("evaluator-a",)),
        threading.Thread(target=claim, args=("evaluator-b",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    claimed = [item for item in results if item is not None]
    assert len(claimed) == 1
    db = factory()
    attempts = db.query(WorkflowV3OperationAttempt).all()
    assert len(attempts) == 1
    assert attempts[0].owner_identity in {"evaluator-a", "evaluator-b"}
    assert attempts[0].owner_token_sha256 == _sha(claimed[0].owner_token)
    db.close()


def test_stale_evaluation_reclaims_new_attempt_and_exhausts_bounded_retry(tmp_path):
    factory = _factory(tmp_path)
    db = factory()
    first = claim_next_evaluation_item(
        db,
        owner_identity="evaluator",
        lease_seconds=60,
        max_attempts=2,
    )
    attempt = db.get(WorkflowV3OperationAttempt, first.operation_attempt_id)
    attempt.lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
    db.commit()

    assert recover_stale_operation_attempts(db, operation="evaluation") == [
        first.operation_attempt_id
    ]
    db.commit()
    assert attempt.status == "failed"
    assert db.query(WorkflowV3Job).one().machine_status == "running"
    assert db.query(WorkflowV3StageRun).one().machine_status == "awaiting_evaluation"

    second = claim_next_evaluation_item(
        db,
        owner_identity="evaluator",
        lease_seconds=60,
        max_attempts=2,
    )
    assert second.operation_attempt == 2
    assert second.operation_attempt_id != first.operation_attempt_id
    second_attempt = db.get(
        WorkflowV3OperationAttempt,
        second.operation_attempt_id,
    )
    _attempt, exhausted = finish_operation_attempt(
        db,
        second.public_id,
        operation_attempt_id=second.operation_attempt_id,
        operation="evaluation",
        target_id=second.candidate_id,
        owner_identity="evaluator",
        owner_token=second.owner_token,
        status="failed",
        error_code="entrypoint_timeout",
        error_message="formal evaluator timed out",
        retryable=True,
    )
    db.commit()
    assert exhausted is True
    assert second_attempt.status == "failed"
    job = db.query(WorkflowV3Job).one()
    stage = db.query(WorkflowV3StageRun).one()
    assert job.machine_status == "failed"
    assert stage.machine_status == "failed"
    assert job.spec_status == "in_progress"
    assert stage.spec_status == "not_evaluated"
    assert db.query(WorkflowV3Evaluation).count() == 0
    db.close()


def test_promotion_operation_retries_without_fabricating_promotion(tmp_path):
    factory = _factory(tmp_path)
    evaluation_id = _passed_evaluation(factory)
    db = factory()
    first = claim_next_promotion_item(
        db,
        owner_identity="promoter",
        lease_seconds=60,
        max_attempts=2,
    )
    _attempt, exhausted = finish_operation_attempt(
        db,
        first.public_id,
        operation_attempt_id=first.operation_attempt_id,
        operation="promotion",
        target_id=evaluation_id,
        owner_identity="promoter",
        owner_token=first.owner_token,
        status="failed",
        error_code="artifact_stat_failed",
        error_message="temporary object-store error",
        retryable=True,
    )
    db.commit()
    assert exhausted is False
    assert db.query(WorkflowV3Job).one().machine_status == "running"
    assert db.query(WorkflowV3StageRun).one().machine_status == "awaiting_promotion"

    second = claim_next_promotion_item(
        db,
        owner_identity="promoter",
        lease_seconds=60,
        max_attempts=2,
    )
    assert second.operation_attempt == 2
    _attempt, exhausted = finish_operation_attempt(
        db,
        second.public_id,
        operation_attempt_id=second.operation_attempt_id,
        operation="promotion",
        target_id=evaluation_id,
        owner_identity="promoter",
        owner_token=second.owner_token,
        status="failed",
        error_code="artifact_stat_failed",
        error_message="object-store error persisted",
        retryable=True,
    )
    db.commit()
    assert exhausted is True
    assert db.query(WorkflowV3Job).one().machine_status == "failed"
    assert db.query(WorkflowV3StageRun).one().spec_status == "passed"
    db.close()


def test_cancel_marks_active_evaluation_operation_cancelled(tmp_path):
    factory = _factory(tmp_path)
    db = factory()
    claimed = claim_next_evaluation_item(
        db,
        owner_identity="evaluator",
        lease_seconds=60,
        max_attempts=2,
    )
    db.commit()
    cancel_job(
        db,
        claimed.public_id,
        cancelled_by="user",
        reason="stop evaluation",
    )
    db.commit()
    attempt = db.get(WorkflowV3OperationAttempt, claimed.operation_attempt_id)
    assert attempt.status == "cancelled"
    assert db.query(WorkflowV3Job).one().machine_status == "cancelled"
    assert touch_operation_heartbeat(
        db,
        claimed.public_id,
        operation_attempt_id=claimed.operation_attempt_id,
        operation="evaluation",
        target_id=claimed.candidate_id,
        owner_identity="evaluator",
        owner_token=claimed.owner_token,
    ) is False
    db.close()
