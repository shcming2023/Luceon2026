from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.material import Material, MaterialOutput
from app.api.workflow_v2 import _output_projection
from app.models.material_metadata import MaterialMetadata
from app.workflow_v2.contracts import (
    LEGACY_STAGE_CONTRACTS,
    LEGACY_WORKFLOW_VERSION,
    STRICT_COMPILE4_STAGE_CONTRACTS,
    STRICT_COMPILE4_WORKFLOW_VERSION,
    STRICT_COMPILE5_STAGE_CONTRACTS,
    STRICT_COMPILE5_WORKFLOW_VERSION,
    STRICT_COMPILE6_STAGE_CONTRACTS,
    STRICT_COMPILE6_WORKFLOW_VERSION,
    STRICT_COMPILE7_STAGE_CONTRACTS,
    STRICT_COMPILE7_WORKFLOW_VERSION,
    STRICT_COMPILE8_STAGE_CONTRACTS,
    STRICT_COMPILE8_WORKFLOW_VERSION,
    STRICT_COMPILE9_STAGE_CONTRACTS,
    STRICT_COMPILE9_WORKFLOW_VERSION,
    STRICT_COMPILE10_STAGE_CONTRACTS,
    STRICT_COMPILE10_WORKFLOW_VERSION,
    STRICT_COMPILE11_STAGE_CONTRACTS,
    STRICT_COMPILE11_WORKFLOW_VERSION,
    STRICT_COMPILE12_STAGE_CONTRACTS,
    STRICT_COMPILE12_WORKFLOW_VERSION,
    STRICT_COMPILE13_STAGE_CONTRACTS,
    STRICT_COMPILE13_WORKFLOW_VERSION,
    STRICT_COMPILE15_STAGE_CONTRACTS,
    STRICT_COMPILE15_WORKFLOW_VERSION,
    STAGE_CONTRACTS,
    WORKFLOW_VERSION,
    contracts_for_version,
    stage_contracts,
)
from app.workflow_v2.models import RepairAttempt, StageEvent, StageRun, WorkflowBase, WorkflowJob
from app.workflow_v2.service import create_workflow_job, list_workflow_job_summaries, workflow_job_detail
from app.workflow_v2.state_machine import (
    WorkflowTransitionError,
    block_current_stage_for_review,
    claim_current_stage,
    complete_current_stage,
    fail_current_stage,
    request_cancellation,
    recover_stale_stages,
    retry_failed_stage,
    restart_from_stage,
    touch_stage_heartbeat,
)


def make_sessions():
    old_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    workflow_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(old_engine)
    WorkflowBase.metadata.create_all(workflow_engine)
    old_db = sessionmaker(bind=old_engine)()
    workflow_db = sessionmaker(bind=workflow_engine, expire_on_commit=False)()
    material = Material(
        user_id="u1",
        material_id="pdf-golden-theme",
        title="Theme Reading",
        filename="theme.pdf",
        source_type="uploaded",
        stage_status="popo_done",
        pipeline_status="idle",
        input_bucket="eduassets-input",
        input_object="theme.pdf",
        popo_manifest_bucket="eduassets-minerupopo",
        popo_manifest_object="minerupopo/pdf-golden-theme/popo-run/manifest.json",
        latex_manifest_bucket="eduassets-latex",
        latex_manifest_object="latex/pdf-golden-theme/legacy/manifest.json",
    )
    old_db.add(material)
    old_db.commit()
    old_db.refresh(material)
    return old_db, workflow_db, material


def test_output_projection_keeps_job_output_separate_from_material_current_baseline():
    rows = [
        MaterialOutput(
            id=541,
            user_id="u1",
            material_pk=1,
            material_id="pdf-1",
            manifest_bucket="outputs",
            manifest_object="successful/manifest.json",
            output_run_id="successful-job",
            quality_status="passed",
            status="promoted",
            is_current=True,
        )
    ]

    blocked = _output_projection("needs-review-job", rows)
    succeeded = _output_projection("successful-job", rows)

    assert blocked["job_output_id"] == ""
    assert blocked["outputs"] == []
    assert blocked["current_material_output_id"] == "541"
    assert succeeded["job_output_id"] == "541"
    assert succeeded["current_material_output_id"] == "541"


def test_stage_contracts_are_explicit_and_ordered():
    rows = stage_contracts()
    assert [row["key"] for row in rows] == [
        "canonical_clean_material",
        "outline_reconstruction",
        "semantic_annotation",
        "deterministic_elegantbook",
        "bounded_deepseek_polish_qa",
    ]
    assert [row["order"] for row in rows] == sorted(row["order"] for row in rows)
    assert all(row["input_schema"] and row["output_schema"] for row in rows)
    assert all(row["acceptance_gates"] for row in rows)
    assert all(row["version"] for row in rows)


def test_failed_ai_metadata_does_not_block_worker_for_frozen_popo():
    old_db, workflow_db, material = make_sessions()
    old_db.add(
        MaterialMetadata(
            user_id="u1",
            material_pk=material.id,
            status="failed",
            source="ai",
            extraction_error="temporary metadata failure",
        )
    )
    old_db.commit()

    job, created = create_workflow_job(workflow_db, user_id="u1", material=material)

    assert created is True
    assert job.status == "queued"
    assert job.source_popo_object == material.popo_manifest_object


def test_legacy_jobs_keep_their_original_five_stage_contract():
    assert contracts_for_version(LEGACY_WORKFLOW_VERSION) == LEGACY_STAGE_CONTRACTS
    assert [row.key for row in LEGACY_STAGE_CONTRACTS] == [
        "canonical_clean_material",
        "semantic_annotation",
        "deterministic_elegantbook",
        "bounded_llm_polish",
        "independent_final_review",
    ]


def test_strict_compile_history_is_preserved_after_worker_v23_release():
    assert WORKFLOW_VERSION == "worker-v2.3"
    assert contracts_for_version(STRICT_COMPILE4_WORKFLOW_VERSION) == STRICT_COMPILE4_STAGE_CONTRACTS
    assert contracts_for_version(STRICT_COMPILE5_WORKFLOW_VERSION) == STRICT_COMPILE5_STAGE_CONTRACTS
    assert contracts_for_version(STRICT_COMPILE6_WORKFLOW_VERSION) == STRICT_COMPILE6_STAGE_CONTRACTS
    assert contracts_for_version(STRICT_COMPILE7_WORKFLOW_VERSION) == STRICT_COMPILE7_STAGE_CONTRACTS
    assert contracts_for_version(STRICT_COMPILE8_WORKFLOW_VERSION) == STRICT_COMPILE8_STAGE_CONTRACTS
    assert contracts_for_version(STRICT_COMPILE9_WORKFLOW_VERSION) == STRICT_COMPILE9_STAGE_CONTRACTS
    assert contracts_for_version(STRICT_COMPILE10_WORKFLOW_VERSION) == STRICT_COMPILE10_STAGE_CONTRACTS
    assert contracts_for_version(STRICT_COMPILE11_WORKFLOW_VERSION) == STRICT_COMPILE11_STAGE_CONTRACTS
    assert contracts_for_version(STRICT_COMPILE12_WORKFLOW_VERSION) == STRICT_COMPILE12_STAGE_CONTRACTS
    assert contracts_for_version(STRICT_COMPILE13_WORKFLOW_VERSION) == STRICT_COMPILE13_STAGE_CONTRACTS
    assert contracts_for_version(STRICT_COMPILE15_WORKFLOW_VERSION) == STRICT_COMPILE15_STAGE_CONTRACTS
    assert all("strict4" in row.version for row in STRICT_COMPILE4_STAGE_CONTRACTS)
    assert all("strict5" in row.version for row in STRICT_COMPILE5_STAGE_CONTRACTS)
    assert all("strict6" in row.version for row in STRICT_COMPILE6_STAGE_CONTRACTS)
    assert all(row.version.endswith(".v2.2.6-strict7") for row in STRICT_COMPILE7_STAGE_CONTRACTS)
    assert all(row.version.endswith(".v2.2.7-strict8") for row in STRICT_COMPILE8_STAGE_CONTRACTS)
    assert all(row.version.endswith(".v2.2.8-strict9") for row in STRICT_COMPILE9_STAGE_CONTRACTS)
    assert all(row.version.endswith(".v2.2.9-strict10") for row in STRICT_COMPILE10_STAGE_CONTRACTS)
    assert all(row.version.endswith(".v2.2.10-strict11") for row in STRICT_COMPILE11_STAGE_CONTRACTS)
    assert all(row.version.endswith(".v2.2.11-strict12") for row in STRICT_COMPILE12_STAGE_CONTRACTS)
    assert all(row.version.endswith(".v2.2.12-strict13") for row in STRICT_COMPILE13_STAGE_CONTRACTS)
    assert all(row.version.endswith(".v2.2.13-strict14") for row in STRICT_COMPILE15_STAGE_CONTRACTS)
    assert all(row.version.endswith(".v2.3") for row in STAGE_CONTRACTS)
    assert all(len(row.version) <= 64 for row in STAGE_CONTRACTS)


def test_create_workflow_job_is_idempotent_and_preserves_legacy_reference():
    old_db, workflow_db, material = make_sessions()
    legacy_ref = (material.latex_manifest_bucket, material.latex_manifest_object)

    first, created = create_workflow_job(
        workflow_db,
        user_id="u1",
        material=material,
        payload={"golden_set": True},
    )
    workflow_db.commit()
    second, created_again = create_workflow_job(
        workflow_db,
        user_id="u1",
        material=material,
        payload={"golden_set": True},
    )
    workflow_db.commit()

    assert created is True
    assert created_again is False
    assert first.id == second.id
    assert workflow_db.query(WorkflowJob).count() == 1
    assert workflow_db.query(StageRun).count() == len(STAGE_CONTRACTS)
    assert workflow_db.query(StageEvent).count() == 1
    stages = workflow_db.query(StageRun).order_by(StageRun.id.asc()).all()
    assert stages[0].status == "queued"
    assert all(stage.status == "pending" for stage in stages[1:])
    old_db.refresh(material)
    assert (material.latex_manifest_bucket, material.latex_manifest_object) == legacy_ref


def test_workflow_job_detail_exposes_stages_and_events():
    _old_db, workflow_db, material = make_sessions()
    job, _created = create_workflow_job(workflow_db, user_id="u1", material=material)
    workflow_db.commit()

    detail = workflow_job_detail(workflow_db, job.public_id)

    assert detail is not None
    assert detail["status"] == "queued"
    assert len(detail["stages"]) == 5
    assert detail["events"][0]["event_type"] == "job_created"
    assert detail["events"][0]["payload"]["legacy_outputs_preserved"] is True
    assert detail["artifacts"] == []
    assert detail["model_calls"] == []
    assert detail["qa_findings"] == []
    assert detail["repair_attempts"] == []


def test_stage_success_advances_only_to_next_stage():
    _old_db, db, material = make_sessions()
    job, _ = create_workflow_job(db, user_id="u1", material=material)
    db.commit()
    claim_current_stage(db, job.public_id, "worker-a")
    complete_current_stage(db, job.public_id, output_bucket="v2", output_object="immutable/manifest.json", output_sha256="a" * 64)
    db.commit()

    detail = workflow_job_detail(db, job.public_id)
    assert detail["status"] == "queued"
    assert detail["current_stage_key"] == "outline_reconstruction"
    assert [row["status"] for row in detail["stages"]] == ["succeeded", "queued", "pending", "pending", "pending"]


def test_failed_stage_retry_keeps_failed_attempt_and_does_not_rewind():
    _old_db, db, material = make_sessions()
    job, _ = create_workflow_job(db, user_id="u1", material=material)
    db.commit()
    claim_current_stage(db, job.public_id, "worker-a")
    fail_current_stage(db, job.public_id, error_code="gate_failed", error_message="outline incomplete")
    retry_failed_stage(db, job.public_id)
    db.commit()

    attempts = db.query(StageRun).filter(StageRun.stage_key == "canonical_clean_material").order_by(StageRun.attempt).all()
    assert [(row.attempt, row.status) for row in attempts] == [(1, "failed"), (2, "queued")]
    assert db.query(StageRun).filter(StageRun.stage_key == "semantic_annotation").one().status == "pending"


def test_quality_block_preserves_candidate_and_requires_review_branch():
    _old_db, db, material = make_sessions()
    job, _ = create_workflow_job(db, user_id="u1", material=material)
    db.commit()
    claim_current_stage(db, job.public_id, "worker-a")
    block_current_stage_for_review(
        db,
        job.public_id,
        output_bucket="v2",
        output_object="immutable/candidate/manifest.json",
        output_sha256="a" * 64,
        error_message="latex_missing_glyphs",
        metrics={"quality_blockers": ["latex_missing_glyphs"]},
    )
    db.commit()

    stage = db.query(StageRun).filter_by(workflow_job_id=job.id, stage_key="canonical_clean_material").one()
    assert job.status == "blocked"
    assert job.error_code == "quality_blocked"
    assert stage.status == "blocked"
    assert stage.output_manifest_sha256 == "a" * 64
    assert db.query(StageEvent).filter_by(event_type="stage_quality_blocked").one().level == "warning"
    try:
        retry_failed_stage(db, job.public_id)
    except WorkflowTransitionError as exc:
        assert "only a failed job" in str(exc)
    else:
        raise AssertionError("a quality-blocked job must not use blind technical retry")

    restarted, target = restart_from_stage(db, job.public_id, "canonical_clean_material")
    assert restarted.status == "queued"
    assert target.attempt == 2


def test_semantic_outline_contract_blocker_resumes_from_outline():
    _old_db, db, material = make_sessions()
    job, _ = create_workflow_job(db, user_id="u1", material=material)
    db.commit()
    for index in range(2):
        claim_current_stage(db, job.public_id, "worker-a")
        complete_current_stage(
            db,
            job.public_id,
            output_bucket="v2",
            output_object=f"immutable/{index}/manifest.json",
            output_sha256=str(index + 1) * 64,
        )
        db.commit()
    claim_current_stage(db, job.public_id, "worker-a")
    block_current_stage_for_review(
        db,
        job.public_id,
        output_bucket="v2",
        output_object="immutable/semantic-candidate/manifest.json",
        output_sha256="c" * 64,
        error_message=(
            "semantic_section_order_differs_from_accepted_outline; "
            "semantic_section_parent_binding_mismatch"
        ),
        metrics={
            "quality_blockers": [
                "semantic_section_order_differs_from_accepted_outline",
                "semantic_section_parent_binding_mismatch",
            ]
        },
    )
    db.commit()

    detail = workflow_job_detail(db, job.public_id)

    assert detail["current_stage_key"] == "semantic_annotation"
    assert detail["minimal_resume_stage_key"] == "outline_reconstruction"


def test_failed_job_can_start_new_immutable_branch_from_earlier_stage():
    _old_db, db, material = make_sessions()
    job, _ = create_workflow_job(db, user_id="u1", material=material)
    db.commit()
    for digest in ("a", "b", "c"):
        claim_current_stage(db, job.public_id, "worker-a")
        complete_current_stage(
            db,
            job.public_id,
            output_bucket="v2",
            output_object=f"immutable/{digest}/manifest.json",
            output_sha256=digest * 64,
        )
        db.commit()
    claim_current_stage(db, job.public_id, "worker-a")
    fail_current_stage(db, job.public_id, error_code="qa_failed", error_message="rebuild stage 3")
    repair = RepairAttempt(
        workflow_job_id=job.id,
        repair_kind="codex_sidecar_patch",
        status="queued",
        allowed_scope_json="{}",
        invariants_json="{}",
        result_json="{}",
        error_message="",
    )
    db.add(repair)
    db.commit()

    restarted, target = restart_from_stage(db, job.public_id, "deterministic_elegantbook")
    db.commit()

    assert restarted.status == "queued"
    assert restarted.current_stage_key == "deterministic_elegantbook"
    assert target.attempt == 2
    assert target.input_manifest_sha256 == "c" * 64
    assert db.query(StageRun).filter_by(workflow_job_id=job.id, stage_key="bounded_deepseek_polish_qa", attempt=2).one().status == "pending"
    assert repair.status == "failed"
    assert repair.finished_at is not None
    assert repair.error_message == "superseded by workflow restart from deterministic_elegantbook"


def test_succeeded_job_can_be_manually_rebuilt_from_an_explicit_stage():
    _old_db, db, material = make_sessions()
    job, _ = create_workflow_job(db, user_id="u1", material=material)
    db.commit()
    for index, _contract in enumerate(STAGE_CONTRACTS, start=1):
        claim_current_stage(db, job.public_id, "worker-a")
        complete_current_stage(
            db,
            job.public_id,
            output_bucket="v2",
            output_object=f"immutable/{index}/manifest.json",
            output_sha256=str(index) * 64,
        )
        db.commit()
    assert job.status == "succeeded"

    restarted, target = restart_from_stage(db, job.public_id, "deterministic_elegantbook")

    assert restarted.status == "queued"
    assert restarted.current_stage_key == "deterministic_elegantbook"
    assert target.attempt == 2
    assert target.input_manifest_sha256 == "3" * 64


def test_successful_workflow_supersedes_queued_repairs():
    _old_db, db, material = make_sessions()
    job, _ = create_workflow_job(db, user_id="u1", material=material)
    repair = RepairAttempt(
        workflow_job_id=job.id,
        repair_kind="codex_sidecar_patch",
        status="queued",
        allowed_scope_json="{}",
        invariants_json="{}",
        result_json="{}",
        error_message="",
    )
    db.add(repair)
    db.commit()

    for index, _contract in enumerate(STAGE_CONTRACTS, start=1):
        claim_current_stage(db, job.public_id, "worker-a")
        complete_current_stage(
            db,
            job.public_id,
            output_bucket="v2",
            output_object=f"immutable/{index}/manifest.json",
            output_sha256=str(index) * 64,
        )
        db.commit()

    assert job.status == "succeeded"
    assert repair.status == "failed"
    assert repair.finished_at == job.finished_at
    assert repair.error_message == "superseded by successful workflow completion"
    event = db.query(StageEvent).filter_by(workflow_job_id=job.id, event_type="job_succeeded").one()
    assert event.load(event.payload_json, {})["superseded_repair_count"] == 1


def test_queued_job_can_be_cancelled_without_execution():
    _old_db, db, material = make_sessions()
    job, _ = create_workflow_job(db, user_id="u1", material=material)
    request_cancellation(db, job.public_id)
    db.commit()
    assert job.status == "cancelled"
    assert db.query(StageRun).filter(StageRun.stage_key == "canonical_clean_material").one().status == "cancelled"


def test_job_summary_exposes_current_attempt_and_observability_counts():
    _old_db, db, material = make_sessions()
    job, _ = create_workflow_job(db, user_id="u1", material=material)
    db.commit()
    claim_current_stage(db, job.public_id, "worker-a")
    fail_current_stage(db, job.public_id, error_code="gate", error_message="blocked")
    retry_failed_stage(db, job.public_id)
    db.commit()

    summary = list_workflow_job_summaries(db, user_id="u1")[0]
    assert summary["current_stage_key"] == "canonical_clean_material"
    assert summary["current_attempt"] == 2
    assert summary["current_stage_status"] == "queued"
    assert summary["is_current_workflow"] is True
    assert summary["event_count"] == 4


def test_stale_running_stage_recovers_as_a_new_attempt_only():
    _old_db, db, material = make_sessions()
    job, _ = create_workflow_job(db, user_id="u1", material=material)
    db.commit()
    _job, stage = claim_current_stage(db, job.public_id, "worker-a")
    stage.heartbeat_at = datetime.utcnow() - timedelta(minutes=5)
    db.commit()

    recovered = recover_stale_stages(db, stale_after_seconds=60)
    db.commit()

    attempts = db.query(StageRun).filter(StageRun.stage_key == "canonical_clean_material").order_by(StageRun.attempt).all()
    assert recovered == [job.public_id]
    assert [(row.attempt, row.status, row.error_code) for row in attempts] == [
        (1, "failed", "worker_lease_expired"),
        (2, "queued", ""),
    ]
    assert db.query(StageRun).filter(StageRun.stage_key == "semantic_annotation").one().status == "pending"


def test_stale_stage_is_not_recovered_while_execution_lease_is_active():
    _old_db, db, material = make_sessions()
    job, _ = create_workflow_job(db, user_id="u1", material=material)
    db.commit()
    _job, stage = claim_current_stage(db, job.public_id, "worker-a")
    stage.heartbeat_at = datetime.utcnow() - timedelta(minutes=5)
    db.commit()

    recovered = recover_stale_stages(
        db,
        stale_after_seconds=60,
        active_lease_checker=lambda public_id: public_id == job.public_id,
    )
    db.commit()

    assert recovered == []
    assert stage.status == "running"
    assert db.query(StageRun).filter(StageRun.stage_key == "canonical_clean_material").count() == 1


def test_stage_heartbeat_is_owned_by_claiming_worker():
    _old_db, db, material = make_sessions()
    job, _ = create_workflow_job(db, user_id="u1", material=material)
    db.commit()
    _job, stage = claim_current_stage(db, job.public_id, "worker-a")
    db.commit()
    original = stage.heartbeat_at

    assert touch_stage_heartbeat(db, job.public_id, "worker-b") is False
    assert touch_stage_heartbeat(db, job.public_id, "worker-a") is True
    db.commit()
    assert stage.heartbeat_at >= original
