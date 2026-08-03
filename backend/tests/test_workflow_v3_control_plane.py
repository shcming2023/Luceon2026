from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.workflow_v3.contracts import (
    STAGE_CONTRACTS,
    WORKFLOW_VERSION,
    UnknownWorkflowVersion,
    contract_for,
    contracts_for_version,
)
from app.workflow_v3.database import bootstrap_workflow_v3_database, workflow_v3_schema_status
from app.workflow_v3.models import (
    WorkflowV3Base,
    WorkflowV3Candidate,
    WorkflowV3Evaluation,
    WorkflowV3Execution,
    WorkflowV3Job,
    WorkflowV3ProjectionOutbox,
    WorkflowV3Promotion,
    WorkflowV3ReviewResolution,
    WorkflowV3StageRun,
)
from app.workflow_v3.review_resolution import (
    ReviewResolutionManifestError,
    evaluation_fingerprint,
    finding_fingerprint,
    validate_review_resolution_manifest,
)
from app.workflow_v3.service import (
    create_workflow_job,
    list_skill_releases,
    register_skill_release,
    runtime_identity_for_manifest,
    workflow_job_detail,
)
from app.workflow_v3.state_machine import (
    WorkflowV3TransitionError,
    apply_review_resolution,
    claim_current_stage,
    promote_candidate,
    record_evaluation,
    record_human_acceptance,
    recover_stale_executions,
    retry_failed_stage,
    submit_candidate,
)


SOURCE_SHA = "1" * 64
MANIFEST_SHA = "2" * 64
PACKAGE_SHA = "3" * 64
TEMPLATE_SHA = "4" * 64
POLICY_SHA = "6" * 64
RUNTIME_MANIFEST = {
    "python": "3.12.11",
    "application_dependencies_sha256": "5" * 64,
    "system_tools": {"xelatex": "2025"},
    "fonts_sha256": "7" * 64,
    "tex_sha256": "8" * 64,
    "poppler_sha256": "9" * 64,
    "container_image_digest": f"sha256:{'a' * 64}",
    "sbom_path": "runtime/sbom.json",
    "attestations": ["runtime/attestation.json"],
}
RUNTIME_SHA = runtime_identity_for_manifest({"runtime": RUNTIME_MANIFEST})


def make_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    WorkflowV3Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def release_manifest() -> dict:
    formal_ids = [
        identifier
        for row in STAGE_CONTRACTS
        for identifier in (f"formal-{row.key}-produce", f"formal-{row.key}-evaluate")
    ]
    return {
        "schema_version": "luceon.worker-v3-skill-release/v1",
        "version": "0.1.0-rc1",
        "status": "rc",
        "eligibility": {"rc_eligible": True, "stable_eligible": False},
        "template": {"tree_sha256": TEMPLATE_SHA},
        "runtime": RUNTIME_MANIFEST,
        "entrypoints": {
            "formal": formal_ids,
            "definitions": {
                identifier: {
                    "stage": contract.key,
                    "execution_role": execution_role,
                }
                for contract in STAGE_CONTRACTS
                for identifier, execution_role in (
                    (f"formal-{contract.key}-produce", "producer"),
                    (f"formal-{contract.key}-evaluate", "evaluator"),
                )
            },
        },
    }


def make_release(db):
    release, created = register_skill_release(
        db,
        release_version="0.1.0-rc1",
        manifest_sha256=MANIFEST_SHA,
        package_bucket="worker-v3-releases",
        package_object="skills/2026.07.26-rc1/release.tar.gz",
        package_sha256=PACKAGE_SHA,
        workflow_version=WORKFLOW_VERSION,
        template_sha256=TEMPLATE_SHA,
        runtime_identity_sha256=RUNTIME_SHA,
        manifest=release_manifest(),
        registered_by="release-controller",
    )
    db.commit()
    return release, created


def make_job(db, *, shadow: bool = True):
    make_release(db)
    job, created = create_workflow_job(
        db,
        user_id="u1",
        material_pk=4242,
        material_id="pdf-worker-v3-test",
        source_popo_bucket="eduassets-minerupopo",
        source_popo_object="minerupopo/pdf-worker-v3-test/popo-run/manifest.json",
        source_popo_sha256=SOURCE_SHA,
        skill_release_version="0.1.0-rc1",
        skill_release_sha256=MANIFEST_SHA,
        template_sha256=TEMPLATE_SHA,
        payload={"shadow": shadow},
    )
    db.commit()
    return job, created


def gate_results(stage_key: str) -> dict:
    contract = contract_for(WORKFLOW_VERSION, stage_key)
    return {gate: True for gate in contract.acceptance_gates}


def run_current_stage(db, job, index: int):
    stage_key = job.current_stage_key
    job, stage, execution = claim_current_stage(
        db,
        job.public_id,
        producer_identity=f"producer-{index}",
        idempotency_key=f"lease-{index}",
        runtime_identity_sha256=RUNTIME_SHA,
    )
    candidate_sha = hashlib.sha256(f"{stage_key}-{index}".encode()).hexdigest()
    job, stage, candidate = submit_candidate(
        db,
        job.public_id,
        execution_id=execution.id,
        idempotency_key=f"candidate-{index}",
        artifact_kind=f"{stage_key}-artifact",
        bucket="worker-v3-candidates",
        object_name=f"{job.public_id}/{stage_key}/{candidate_sha}/manifest.json",
        sha256=candidate_sha,
        size_bytes=100 + index,
        metadata={"stage_key": stage_key},
    )
    job, stage, evaluation = record_evaluation(
        db,
        job.public_id,
        candidate_id=candidate.id,
        idempotency_key=f"evaluation-{index}",
        evaluator_identity=f"evaluator-{index}",
        evaluator_version="evaluator-v1",
        policy_sha256=POLICY_SHA,
        decision="passed",
        gate_results=gate_results(stage_key),
        findings=[],
    )
    job, stage, promotion = promote_candidate(
        db,
        job.public_id,
        evaluation_id=evaluation.id,
        idempotency_key=f"promotion-{index}",
        promoted_by="promotion-controller",
    )
    db.flush()
    return job, stage, execution, candidate, evaluation, promotion


def test_v3_contract_has_exact_twelve_ordered_stages_and_unknown_versions_fail_closed():
    assert [row.key for row in STAGE_CONTRACTS] == [
        "intake_snapshot",
        "source_scope_and_order",
        "canonical_block_ledger",
        "outline_reconstruction",
        "semantic_annotation",
        "template_construct_binding",
        "frozen_render_plan",
        "deterministic_elegantbook",
        "readonly_latex_audit",
        "independent_full_page_review",
        "delivery_recompile",
        "ready_for_user_acceptance",
    ]
    assert [row.order for row in STAGE_CONTRACTS] == sorted(row.order for row in STAGE_CONTRACTS)
    with pytest.raises(UnknownWorkflowVersion):
        contracts_for_version("worker-v3-future-unregistered")


def test_v3_schema_is_namespaced_and_health_check_does_not_create_missing_tables():
    engine = create_engine("sqlite://")
    ready, detail = workflow_v3_schema_status(engine)
    assert ready is False
    assert "missing Worker V3 tables" in detail
    assert inspect(engine).get_table_names() == []

    result = bootstrap_workflow_v3_database(engine)
    assert result["ready"] is True
    assert all(name.startswith("workflow_v3_") for name in inspect(engine).get_table_names())


def test_release_registration_is_immutable_idempotent_and_required_by_job():
    db = make_db()
    release, created = make_release(db)
    duplicate, duplicate_created = register_skill_release(
        db,
        release_version=release.release_version,
        manifest_sha256=release.manifest_sha256,
        package_bucket=release.package_bucket,
        package_object=release.package_object,
        package_sha256=release.package_sha256,
        workflow_version=release.workflow_version,
        template_sha256=release.template_sha256,
        runtime_identity_sha256=release.runtime_identity_sha256,
        manifest=release_manifest(),
        registered_by="another-controller",
    )
    assert created is True
    assert duplicate_created is False
    assert duplicate.id == release.id
    assert len(list_skill_releases(db)) == 1

    with pytest.raises(ValueError, match="not registered"):
        create_workflow_job(
            db,
            user_id="u1",
            material_pk=1,
            material_id="pdf-unbound",
            source_popo_bucket="popo",
            source_popo_object="manifest.json",
            source_popo_sha256=SOURCE_SHA,
            skill_release_version="missing",
            skill_release_sha256="9" * 64,
            template_sha256=TEMPLATE_SHA,
        )


def test_job_creation_is_idempotent_and_keeps_machine_spec_and_acceptance_separate():
    db = make_db()
    first, created = make_job(db)
    second, created_again = create_workflow_job(
        db,
        user_id="u1",
        material_pk=4242,
        material_id="pdf-worker-v3-test",
        source_popo_bucket="eduassets-minerupopo",
        source_popo_object="minerupopo/pdf-worker-v3-test/popo-run/manifest.json",
        source_popo_sha256=SOURCE_SHA,
        skill_release_version="0.1.0-rc1",
        skill_release_sha256=MANIFEST_SHA,
        template_sha256=TEMPLATE_SHA,
        payload={"ignored_on_duplicate": True},
    )
    db.commit()

    assert created is True
    assert created_again is False
    assert first.id == second.id
    assert db.query(WorkflowV3Job).count() == 1
    assert db.query(WorkflowV3StageRun).count() == 12
    assert first.machine_status == "queued"
    assert first.spec_status == "not_evaluated"
    assert first.readiness_status == "not_ready"
    assert first.human_acceptance_status == "pending"
    stages = db.query(WorkflowV3StageRun).order_by(WorkflowV3StageRun.id).all()
    assert stages[0].input_kind == "frozen_source"
    assert stages[0].input_artifact_sha256 == SOURCE_SHA
    assert all(row.input_kind == "promoted_artifact" for row in stages[1:])


def test_producer_can_only_submit_candidate_and_duplicate_messages_are_idempotent():
    db = make_db()
    job, _ = make_job(db)
    first_claim = claim_current_stage(
        db,
        job.public_id,
        producer_identity="producer-a",
        idempotency_key="lease-message-1",
        runtime_identity_sha256=RUNTIME_SHA,
    )
    duplicate_claim = claim_current_stage(
        db,
        job.public_id,
        producer_identity="producer-a",
        idempotency_key="lease-message-1",
        runtime_identity_sha256=RUNTIME_SHA,
    )
    assert first_claim[2].id == duplicate_claim[2].id
    with pytest.raises(WorkflowV3TransitionError, match="idempotency key conflicts"):
        claim_current_stage(
            db,
            job.public_id,
            producer_identity="producer-a",
            idempotency_key="lease-message-1",
            runtime_identity_sha256="b" * 64,
        )

    candidate_args = dict(
        execution_id=first_claim[2].id,
        idempotency_key="candidate-message-1",
        artifact_kind="intake-snapshot",
        bucket="candidates",
        object_name="run/intake/manifest.json",
        sha256="7" * 64,
        size_bytes=10,
    )
    first = submit_candidate(db, job.public_id, **candidate_args)
    duplicate = submit_candidate(db, job.public_id, **candidate_args)
    assert first[2].id == duplicate[2].id
    with pytest.raises(WorkflowV3TransitionError, match="idempotency key conflicts"):
        submit_candidate(db, job.public_id, **{**candidate_args, "size_bytes": 11})
    assert first[0].machine_status == "running"
    assert first[1].machine_status == "awaiting_evaluation"
    assert db.query(WorkflowV3Promotion).count() == 0


def test_evaluator_must_be_independent_and_all_registered_gates_must_pass():
    db = make_db()
    job, _ = make_job(db)
    _job, stage, execution = claim_current_stage(
        db,
        job.public_id,
        producer_identity="same-identity",
        idempotency_key="lease",
        runtime_identity_sha256=RUNTIME_SHA,
    )
    _job, _stage, candidate = submit_candidate(
        db,
        job.public_id,
        execution_id=execution.id,
        idempotency_key="candidate",
        artifact_kind="intake",
        bucket="candidate",
        object_name="manifest.json",
        sha256="8" * 64,
        size_bytes=1,
    )
    with pytest.raises(WorkflowV3TransitionError, match="cannot independently evaluate"):
        record_evaluation(
            db,
            job.public_id,
            candidate_id=candidate.id,
            idempotency_key="evaluation-same",
            evaluator_identity="same-identity",
            evaluator_version="v1",
            policy_sha256=POLICY_SHA,
            decision="passed",
            gate_results=gate_results(stage.stage_key),
        )
    incomplete = gate_results(stage.stage_key)
    incomplete.pop(next(iter(incomplete)))
    with pytest.raises(WorkflowV3TransitionError, match="missing required gates"):
        record_evaluation(
            db,
            job.public_id,
            candidate_id=candidate.id,
            idempotency_key="evaluation-incomplete",
            evaluator_identity="independent",
            evaluator_version="v1",
            policy_sha256=POLICY_SHA,
            decision="passed",
            gate_results=incomplete,
        )
    assert db.query(WorkflowV3Evaluation).count() == 0
    assert db.query(WorkflowV3Promotion).count() == 0


def test_downstream_stage_receives_only_promoted_sha_and_cannot_claim_without_promotion():
    db = make_db()
    job, _ = make_job(db)
    job, completed_stage, _execution, candidate, _evaluation, promotion = run_current_stage(db, job, 0)
    next_stage = (
        db.query(WorkflowV3StageRun)
        .filter_by(workflow_job_id=job.id, stage_key=job.current_stage_key)
        .one()
    )
    assert completed_stage.machine_status == "succeeded"
    assert next_stage.input_promotion_id == promotion.id
    assert next_stage.input_artifact_sha256 == candidate.sha256

    next_stage.input_promotion_id = None
    with pytest.raises(WorkflowV3TransitionError, match="no promoted input"):
        claim_current_stage(
            db,
            job.public_id,
            producer_identity="producer-next",
            idempotency_key="lease-next",
            runtime_identity_sha256=RUNTIME_SHA,
        )


def test_rejected_evaluation_fails_closed_and_cannot_be_promoted():
    db = make_db()
    job, _ = make_job(db)
    _job, stage, execution = claim_current_stage(
        db,
        job.public_id,
        producer_identity="producer",
        idempotency_key="lease",
        runtime_identity_sha256=RUNTIME_SHA,
    )
    _job, _stage, candidate = submit_candidate(
        db,
        job.public_id,
        execution_id=execution.id,
        idempotency_key="candidate",
        artifact_kind="intake",
        bucket="candidate",
        object_name="manifest.json",
        sha256="a" * 64,
        size_bytes=1,
    )
    job, stage, evaluation = record_evaluation(
        db,
        job.public_id,
        candidate_id=candidate.id,
        idempotency_key="evaluation",
        evaluator_identity="evaluator",
        evaluator_version="v1",
        policy_sha256=POLICY_SHA,
        decision="failed",
        gate_results={},
        findings=[{"code": "source_identity_mismatch"}],
    )
    assert job.machine_status == "failed"
    assert job.spec_status == "failed"
    assert stage.machine_status == "failed"
    assert candidate.status == "rejected"
    with pytest.raises(WorkflowV3TransitionError, match="only a passed"):
        promote_candidate(
            db,
            job.public_id,
            evaluation_id=evaluation.id,
            idempotency_key="promotion",
            promoted_by="controller",
        )


def test_needs_review_is_not_failure_or_success_and_requires_complete_handoff():
    db = make_db()
    job, _ = make_job(db)
    _job, stage, execution = claim_current_stage(
        db,
        job.public_id,
        producer_identity="producer",
        idempotency_key="lease-review",
        runtime_identity_sha256=RUNTIME_SHA,
    )
    _job, _stage, candidate = submit_candidate(
        db,
        job.public_id,
        execution_id=execution.id,
        idempotency_key="candidate-review",
        artifact_kind="intake",
        bucket="worker-v3-candidates",
        object_name="review/candidate.tar",
        sha256="b" * 64,
        size_bytes=10,
    )
    finding = {
        "code": "source_scope_ambiguous",
        "blocking": True,
        "responsible_stage": stage.stage_key,
        "recovery_stage": stage.stage_key,
        "evidence_refs": [
            {
                "path": "evidence/source-page-1.png",
                "sha256": "c" * 64,
            }
        ],
        "handoff": {
            "summary": "The source does not distinguish body from answer key.",
            "required_action": "Confirm whether page 1 is in body scope.",
            "resume_stage": stage.stage_key,
        },
    }
    job, stage, evaluation = record_evaluation(
        db,
        job.public_id,
        candidate_id=candidate.id,
        idempotency_key="evaluation-review",
        evaluator_identity="evaluator",
        evaluator_version="v1",
        policy_sha256=POLICY_SHA,
        decision="needs_review",
        gate_results={},
        findings=[finding],
    )
    assert evaluation.decision == "needs_review"
    assert job.machine_status == "needs_review"
    assert job.spec_status == "needs_review"
    assert job.readiness_status == "not_ready"
    assert stage.machine_status == "needs_review"
    assert stage.spec_status == "needs_review"
    assert candidate.status == "needs_review"
    assert db.query(WorkflowV3Promotion).count() == 0

    with pytest.raises(
        WorkflowV3TransitionError,
        match="immutable human review resolution",
    ):
        retry_failed_stage(db, job.public_id)


@pytest.mark.parametrize(
    "finding",
    [
        {},
        {
            "code": "ambiguous",
            "blocking": True,
            "responsible_stage": "intake_snapshot",
            "recovery_stage": "intake_snapshot",
            "evidence_refs": [],
            "handoff": {
                "summary": "x",
                "required_action": "y",
                "resume_stage": "intake_snapshot",
            },
        },
    ],
)
def test_needs_review_without_hash_bound_handoff_fails_closed(finding):
    db = make_db()
    job, _ = make_job(db)
    _job, _stage, execution = claim_current_stage(
        db,
        job.public_id,
        producer_identity="producer",
        idempotency_key=f"lease-{len(finding)}",
        runtime_identity_sha256=RUNTIME_SHA,
    )
    _job, _stage, candidate = submit_candidate(
        db,
        job.public_id,
        execution_id=execution.id,
        idempotency_key=f"candidate-{len(finding)}",
        artifact_kind="intake",
        bucket="worker-v3-candidates",
        object_name=f"review/{len(finding)}.tar",
        sha256="d" * 64,
        size_bytes=10,
    )
    with pytest.raises(WorkflowV3TransitionError, match="needs_review"):
        record_evaluation(
            db,
            job.public_id,
            candidate_id=candidate.id,
            idempotency_key=f"evaluation-{len(finding)}",
            evaluator_identity="evaluator",
            evaluator_version="v1",
            policy_sha256=POLICY_SHA,
            decision="needs_review",
            gate_results={},
            findings=[finding] if finding else [],
        )


def _pause_first_stage_for_review(db):
    job, _ = make_job(db)
    _job, stage, execution = claim_current_stage(
        db,
        job.public_id,
        producer_identity="producer-review-resolution",
        idempotency_key="lease-review-resolution",
        runtime_identity_sha256=RUNTIME_SHA,
    )
    _job, _stage, candidate = submit_candidate(
        db,
        job.public_id,
        execution_id=execution.id,
        idempotency_key="candidate-review-resolution",
        artifact_kind="intake",
        bucket="worker-v3-candidates",
        object_name="review-resolution/original.tar",
        sha256="1" * 64,
        size_bytes=10,
    )
    finding = {
        "code": "source_scope_ambiguous",
        "blocking": True,
        "responsible_stage": stage.stage_key,
        "recovery_stage": stage.stage_key,
        "evidence_refs": [
            {
                "path": "evidence/source-page-1.png",
                "sha256": "2" * 64,
            }
        ],
        "handoff": {
            "summary": "The source scope is ambiguous.",
            "required_action": "Authorize the exact scope for a new revision.",
            "resume_stage": stage.stage_key,
        },
    }
    job, stage, evaluation = record_evaluation(
        db,
        job.public_id,
        candidate_id=candidate.id,
        idempotency_key="evaluation-review-resolution",
        evaluator_identity="evaluator-review-resolution",
        evaluator_version="v1",
        policy_sha256=POLICY_SHA,
        decision="needs_review",
        gate_results={},
        findings=[finding],
    )
    return job, stage, candidate, evaluation, finding


def _resolution_manifest(
    job,
    candidate,
    evaluation,
    finding,
    *,
    expert_candidate=None,
    stage_payload=None,
):
    fingerprint = finding_fingerprint(finding)
    manifest = {
        "schema_version": "luceon.worker-v3.review-resolution/v1",
        "job_id": job.public_id,
        "evaluation": {
            "id": str(evaluation.id),
            "sha256": evaluation_fingerprint(evaluation, candidate),
            "candidate_id": str(candidate.id),
            "candidate_sha256": candidate.sha256,
            "finding_fingerprints": [fingerprint],
        },
        "authorization": {
            "authorized_by": "admin@luceon.local",
            "decision": "revise",
        },
        "blocker_resolutions": [
            {
                "finding_fingerprint": fingerprint,
                "disposition": "resolved_for_revision",
                "rationale": "The administrator selected the source-bound scope.",
            }
        ],
        "recovery_stage": finding["recovery_stage"],
        "created_at": "2026-07-26T12:00:00Z",
    }
    if expert_candidate is not None:
        manifest["expert_candidate"] = expert_candidate
    if stage_payload is not None:
        manifest["stage_payload"] = stage_payload
    return manifest


def test_review_resolution_accepts_exact_spec05_warning_closures():
    db = make_db()
    job, _stage, candidate, evaluation, finding = _pause_first_stage_for_review(db)
    manifest = _resolution_manifest(
        job,
        candidate,
        evaluation,
        finding,
        stage_payload={
            "stage_key": "deterministic_elegantbook",
            "kind": "spec05_warning_review",
            "payload": {
                "schema_version": "spec05-warning-review/1.0",
                "status": "approved",
                "closures": [
                    {
                        "fingerprint": "a" * 64,
                        "classification": "C2_REVIEW_REQUIRED_CLOSED",
                        "rationale": "Rendered pages were visually inspected.",
                        "visual_pages": [1, 3],
                    }
                ],
            },
        },
    )
    manifest["recovery_stage"] = "deterministic_elegantbook"

    assert validate_review_resolution_manifest(manifest) is manifest

    manifest["stage_payload"]["payload"]["closures"].append(
        dict(manifest["stage_payload"]["payload"]["closures"][0])
    )
    with pytest.raises(
        ReviewResolutionManifestError,
        match="duplicate warning fingerprints",
    ):
        validate_review_resolution_manifest(manifest)


def test_review_resolution_is_immutable_idempotent_and_carries_recovery_lineage():
    db = make_db()
    job, old_stage, old_candidate, evaluation, finding = (
        _pause_first_stage_for_review(db)
    )
    manifest = _resolution_manifest(
        job,
        old_candidate,
        evaluation,
        finding,
    )
    result = apply_review_resolution(
        db,
        job.public_id,
        idempotency_key="resolution-request-1",
        authorized_by="admin@luceon.local",
        manifest_bucket="worker-v3-resolutions",
        manifest_object=f"{job.public_id}/resolution.json",
        manifest_sha256="3" * 64,
        manifest_size_bytes=999,
        manifest=manifest,
    )
    resolved_job, resolution, recovery_stage, expert_candidate = result

    assert expert_candidate is None
    assert resolved_job.current_generation == 2
    assert resolved_job.machine_status == "queued"
    assert recovery_stage.attempt == 2
    assert recovery_stage.generation == 2
    assert recovery_stage.review_resolution_id == resolution.id
    assert recovery_stage.review_resolution_sha256 == "3" * 64
    assert old_stage.machine_status == "needs_review"
    assert old_candidate.status == "needs_review"
    assert db.query(WorkflowV3Promotion).count() == 0
    assert db.query(WorkflowV3ReviewResolution).count() == 1

    replay = apply_review_resolution(
        db,
        job.public_id,
        idempotency_key="resolution-request-1",
        authorized_by="admin@luceon.local",
        manifest_bucket="worker-v3-resolutions",
        manifest_object=f"{job.public_id}/resolution.json",
        manifest_sha256="3" * 64,
        manifest_size_bytes=999,
        manifest=manifest,
    )
    assert replay[1].id == resolution.id
    assert replay[2].id == recovery_stage.id
    assert db.query(WorkflowV3ReviewResolution).count() == 1

    _job, _stage, execution = claim_current_stage(
        db,
        job.public_id,
        producer_identity="producer-recovery",
        idempotency_key="lease-recovery",
        runtime_identity_sha256=RUNTIME_SHA,
    )
    assert execution.generation == 2
    assert execution.review_resolution_sha256 == "3" * 64
    _job, _stage, candidate = submit_candidate(
        db,
        job.public_id,
        execution_id=execution.id,
        idempotency_key="candidate-recovery",
        artifact_kind="intake",
        bucket="worker-v3-candidates",
        object_name="review-resolution/recovery.tar",
        sha256="4" * 64,
        size_bytes=11,
    )
    assert candidate.load(candidate.metadata_json, {})["recovery_lineage"] == {
        "generation": 2,
        "review_resolution_sha256": "3" * 64,
    }
    _job, _stage, recovered_evaluation = record_evaluation(
        db,
        job.public_id,
        candidate_id=candidate.id,
        idempotency_key="evaluation-recovery",
        evaluator_identity="evaluator-recovery",
        evaluator_version="v1",
        policy_sha256=POLICY_SHA,
        decision="passed",
        gate_results=gate_results(recovery_stage.stage_key),
    )
    assert recovered_evaluation.generation == 2
    assert recovered_evaluation.review_resolution_sha256 == "3" * 64
    db.commit()
    resolution.authorized_by = "tampered-admin"
    with pytest.raises(ValueError, match="immutable"):
        db.flush()
    db.rollback()


def test_review_resolution_contract_rejects_automated_candidate_injection():
    db = make_db()
    job, _old_stage, old_candidate, evaluation, finding = (
        _pause_first_stage_for_review(db)
    )
    expert = {
        "attempt_id": "expert-resolution-1",
        "artifact_kind": "worker-v3-candidate-bundle",
        "artifact": {
            "bucket": "worker-v3-candidates",
            "object": "expert-resolution-1/artifact",
            "sha256": "5" * 64,
            "size_bytes": 12,
        },
        "manifest": {
            "bucket": "worker-v3-candidates",
            "object": "expert-resolution-1/manifest.json",
            "sha256": "6" * 64,
            "size_bytes": 13,
        },
    }
    manifest = _resolution_manifest(
        job,
        old_candidate,
        evaluation,
        finding,
        expert_candidate=expert,
    )
    with pytest.raises(
        ReviewResolutionManifestError,
        match="missing or unknown fields",
    ):
        validate_review_resolution_manifest(manifest)
    with pytest.raises(
        WorkflowV3TransitionError,
        match="missing or unknown fields",
    ):
        apply_review_resolution(
            db,
            job.public_id,
            idempotency_key="resolution-request-expert",
            authorized_by="admin@luceon.local",
            manifest_bucket="worker-v3-resolutions",
            manifest_object=f"{job.public_id}/expert-resolution.json",
            manifest_sha256="7" * 64,
            manifest_size_bytes=1000,
            manifest=manifest,
        )
    assert db.query(WorkflowV3ReviewResolution).count() == 0
    assert db.query(WorkflowV3Promotion).count() == 0


def test_review_resolution_restarts_earliest_stage_and_reuses_only_reliable_predecessor():
    db = make_db()
    job, _ = make_job(db)
    (
        job,
        intake_stage,
        _execution_0,
        intake_candidate,
        _evaluation_0,
        intake_promotion,
    ) = run_current_stage(db, job, 0)
    (
        job,
        scope_stage,
        _execution_1,
        _scope_candidate,
        _evaluation_1,
        scope_promotion,
    ) = run_current_stage(db, job, 1)
    _job, current_stage, execution = claim_current_stage(
        db,
        job.public_id,
        producer_identity="producer-earliest-recovery",
        idempotency_key="lease-earliest-recovery",
        runtime_identity_sha256=RUNTIME_SHA,
    )
    _job, _stage, candidate = submit_candidate(
        db,
        job.public_id,
        execution_id=execution.id,
        idempotency_key="candidate-earliest-recovery",
        artifact_kind="canonical-ledger",
        bucket="worker-v3-candidates",
        object_name="earliest-recovery/original.tar",
        sha256="a" * 64,
        size_bytes=10,
    )
    finding = {
        "code": "scope_requires_revision",
        "blocking": True,
        "responsible_stage": current_stage.stage_key,
        "recovery_stage": scope_stage.stage_key,
        "evidence_refs": [
            {
                "path": "evidence/scope.json",
                "sha256": "b" * 64,
            }
        ],
        "handoff": {
            "summary": "The canonical ledger exposed an earlier scope ambiguity.",
            "required_action": "Revise source scope before rebuilding the ledger.",
            "resume_stage": scope_stage.stage_key,
        },
    }
    job, _stage, evaluation = record_evaluation(
        db,
        job.public_id,
        candidate_id=candidate.id,
        idempotency_key="evaluation-earliest-recovery",
        evaluator_identity="evaluator-earliest-recovery",
        evaluator_version="v1",
        policy_sha256=POLICY_SHA,
        decision="needs_review",
        gate_results={},
        findings=[finding],
    )
    manifest = _resolution_manifest(
        job,
        candidate,
        evaluation,
        finding,
    )
    _job, _resolution, recovery_stage, _expert = apply_review_resolution(
        db,
        job.public_id,
        idempotency_key="resolution-earliest-recovery",
        authorized_by="admin@luceon.local",
        manifest_bucket="worker-v3-resolutions",
        manifest_object=f"{job.public_id}/earliest.json",
        manifest_sha256="c" * 64,
        manifest_size_bytes=1000,
        manifest=manifest,
    )

    assert recovery_stage.stage_key == scope_stage.stage_key
    assert recovery_stage.generation == 2
    assert recovery_stage.input_promotion_id == intake_promotion.id
    assert recovery_stage.input_artifact_sha256 == intake_candidate.sha256
    assert intake_stage.promotion_id == intake_promotion.id
    assert scope_stage.promotion_id == scope_promotion.id
    assert scope_stage.machine_status == "succeeded"
    assert current_stage.machine_status == "needs_review"
    assert db.query(WorkflowV3Promotion).count() == 2
    intake_rows = (
        db.query(WorkflowV3StageRun)
        .filter_by(workflow_job_id=job.id, stage_key=intake_stage.stage_key)
        .all()
    )
    assert len(intake_rows) == 1


def test_stale_recovery_preserves_promoted_input_and_creates_a_new_attempt():
    db = make_db()
    job, _ = make_job(db)
    job, _stage, _execution, _candidate, _evaluation, promotion = run_current_stage(db, job, 0)
    _job, running_stage, execution = claim_current_stage(
        db,
        job.public_id,
        producer_identity="producer-stale",
        idempotency_key="lease-stale",
        runtime_identity_sha256=RUNTIME_SHA,
    )
    original_input_sha = running_stage.input_artifact_sha256
    execution.heartbeat_at = datetime.utcnow() - timedelta(minutes=5)
    db.flush()

    recovered = recover_stale_executions(db, stale_after_seconds=60, requeue=True)

    assert recovered == [job.public_id]
    attempts = (
        db.query(WorkflowV3StageRun)
        .filter_by(workflow_job_id=job.id, stage_key=running_stage.stage_key)
        .order_by(WorkflowV3StageRun.attempt)
        .all()
    )
    assert [(row.attempt, row.machine_status) for row in attempts] == [(1, "failed"), (2, "queued")]
    assert attempts[1].input_promotion_id == promotion.id
    assert attempts[1].input_artifact_sha256 == original_input_sha


def test_full_twelve_stage_chain_ends_ready_but_not_human_accepted():
    db = make_db()
    job, _ = make_job(db, shadow=False)
    for index in range(len(STAGE_CONTRACTS)):
        job, _stage, _execution, _candidate, _evaluation, _promotion = run_current_stage(db, job, index)
    db.commit()

    detail = workflow_job_detail(db, job.public_id)
    assert detail["machine_status"] == "succeeded"
    assert detail["spec_status"] == "passed"
    assert detail["readiness_status"] == "ready"
    assert detail["human_acceptance_status"] == "pending"
    assert detail["spec_passed"] is True
    assert detail["spec_ready_for_projection"] is True
    assert detail["ready_for_user_acceptance"] is False
    assert detail["human_accepted"] is False
    assert len(detail["executions"]) == 12
    assert len(detail["candidates"]) == 12
    assert len(detail["evaluations"]) == 12
    assert len(detail["promotions"]) == 12
    assert detail["projection_outbox"][0]["event_kind"] == "final_ready"
    assert detail["projection_outbox"][0]["status"] == "pending"
    assert detail["projection_outbox"][0]["payload"]["shadow"] is False
    assert db.query(WorkflowV3ProjectionOutbox).count() == 1

    final_ready = db.query(WorkflowV3ProjectionOutbox).one()
    final_ready.status = "applied"
    final_ready.applied_identity = "d" * 64
    final_ready.projected_output_id = 73
    final_ready.projected_manifest_bucket = "eduassets-elegantbook"
    final_ready.projected_manifest_object = (
        f"elegantbook/{job.material_id}/popo-run/{job.public_id}/manifest.json"
    )
    final_ready.projected_manifest_sha256 = "e" * 64
    db.flush()
    detail = workflow_job_detail(db, job.public_id)
    assert detail["ready_for_user_acceptance"] is True
    record_human_acceptance(
        db,
        job.public_id,
        accepted=True,
        decided_by="human-publisher",
        output_id=73,
        manifest_sha256="e" * 64,
        reason="page review accepted",
    )
    db.commit()
    assert job.machine_status == "succeeded"
    assert job.spec_status == "passed"
    assert job.human_acceptance_status == "accepted"
    projected = (
        db.query(WorkflowV3ProjectionOutbox)
        .order_by(WorkflowV3ProjectionOutbox.id.asc())
        .all()
    )
    assert [row.event_kind for row in projected] == [
        "final_ready",
        "human_acceptance",
    ]
    assert [row.status for row in projected] == ["applied", "pending"]
    with pytest.raises(
        WorkflowV3TransitionError,
        match="exact applied formal output",
    ):
        record_human_acceptance(
            db,
            job.public_id,
            accepted=True,
            decided_by="human-publisher",
            output_id=74,
            manifest_sha256="e" * 64,
            reason="mismatched replay",
        )


def test_persisted_unknown_workflow_version_cannot_be_claimed():
    db = make_db()
    job, _ = make_job(db)
    job.workflow_version = "worker-v3-unregistered"
    db.flush()
    with pytest.raises(UnknownWorkflowVersion):
        claim_current_stage(
            db,
            job.public_id,
            producer_identity="producer",
            idempotency_key="lease",
            runtime_identity_sha256=RUNTIME_SHA,
        )
