from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

from app.workflow_v3.evaluator import _build_control_plane_chain_snapshot
from app.workflow_v3.executor import _StageRequestBuilder
from app.workflow_v3.models import WorkflowV3SkillRelease, WorkflowV3StageRun
from test_workflow_v3_control_plane import (
    MANIFEST_SHA,
    make_db,
    make_job,
    run_current_stage,
)


def _expected_candidate_rows(snapshot: dict) -> list[dict]:
    return [
        {
            "stage_key": row["stage_key"],
            "stage_version": row["stage_version"],
            "stage_run_id": row["stage_run_id"],
            "candidate_id": row["artifact_version"]["candidate_id"],
            "evaluation_id": row["evaluation"]["evaluation_id"],
            "promotion_id": row["promotion"]["promotion_id"],
            "artifact_sha256": row["promotion"]["artifact_sha256"],
            "evaluation_record_sha256": row["evaluation"]["record_sha256"],
            "promotion_record_sha256": row["promotion"]["record_sha256"],
            "evaluation_decision": "passed",
            "promotion_status": "promoted",
        }
        for row in snapshot["promotions"]
    ]


def _builder(db, job, final_stage, tmp_path: Path) -> _StageRequestBuilder:
    release = (
        db.query(WorkflowV3SkillRelease)
        .filter(WorkflowV3SkillRelease.manifest_sha256 == MANIFEST_SHA)
        .one()
    )
    return _StageRequestBuilder(
        db=db,
        session_factory=lambda: db,
        artifact_store=object(),
        job=job,
        stage=final_stage,
        release=release,
        release_root=tmp_path / "release",
        bound=SimpleNamespace(manifest_sha256=MANIFEST_SHA),
        workdir=tmp_path / "stage12",
        heartbeat=lambda: None,
    )


def test_stage12_producer_chain_exactly_projects_evaluator_owned_v2_contract(
    tmp_path: Path,
):
    db = make_db()
    job, _created = make_job(db)
    for index in range(11):
        run_current_stage(db, job, index)
    final_stage = (
        db.query(WorkflowV3StageRun)
        .filter(
            WorkflowV3StageRun.workflow_job_id == job.id,
            WorkflowV3StageRun.stage_key == "ready_for_user_acceptance",
        )
        .one()
    )
    builder = _builder(db, job, final_stage, tmp_path)

    candidate_chain = builder._promotion_chain()
    final_stage.machine_status = "awaiting_evaluation"
    db.flush()
    evaluator_snapshot = _build_control_plane_chain_snapshot(
        db,
        job=job,
        stage=final_stage,
    )

    assert candidate_chain == {
        "schema_version": "luceon.worker-v3-promotion-chain/v2",
        "job_id": job.public_id,
        "workflow_version": job.workflow_version,
        "release_manifest_sha256": job.skill_release_sha256,
        "source_popo_manifest_sha256": job.source_popo_sha256,
        "promotions": _expected_candidate_rows(evaluator_snapshot),
    }
    assert len(candidate_chain["promotions"]) == 11
    assert all(
        set(row)
        == {
            "stage_key",
            "stage_version",
            "stage_run_id",
            "candidate_id",
            "evaluation_id",
            "promotion_id",
            "artifact_sha256",
            "evaluation_record_sha256",
            "promotion_record_sha256",
            "evaluation_decision",
            "promotion_status",
        }
        for row in candidate_chain["promotions"]
    )


def test_stage12_lineage_binds_candidate_chain_file_not_future_snapshot(
    tmp_path: Path,
):
    db = make_db()
    job, _created = make_job(db)
    for index in range(11):
        run_current_stage(db, job, index)
    final_stage = (
        db.query(WorkflowV3StageRun)
        .filter(
            WorkflowV3StageRun.workflow_job_id == job.id,
            WorkflowV3StageRun.stage_key == "ready_for_user_acceptance",
        )
        .one()
    )
    builder = _builder(db, job, final_stage, tmp_path)
    observed: list[str] = []

    def lineage(candidate_chain_sha256: str) -> dict:
        observed.append(candidate_chain_sha256)
        return {
            "schema_version": "luceon.worker-v3-page-db-minio-lineage/v1",
            "promotion_chain_sha256": candidate_chain_sha256,
        }

    builder._lineage_attestation = lineage
    builder._prepare_readiness_inputs()
    chain_path = (
        tmp_path / "stage12/generated-inputs/promotion_chain.json"
    )
    actual_sha256 = hashlib.sha256(chain_path.read_bytes()).hexdigest()

    assert observed == [actual_sha256]
    assert (
        builder.artifacts[1].role == "lineage_attestation"
        and builder.artifacts[1].ref.sha256
        != actual_sha256
    )
