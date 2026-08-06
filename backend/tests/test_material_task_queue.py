import io
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.material import Material, MetadataJob, PipelineRun, PipelineRunItem, PipelineStageAttempt
from app.models.material_metadata import MaterialMetadata
from app.services.material_task_queue import (
    MaterialTaskError,
    add_pipeline_run_items,
    claim_next_pipeline_run,
    enqueue_metadata_job,
    list_pipeline_runs,
    material_snapshot,
    pipeline_run_detail,
    pipeline_idempotency_key,
    project_pipeline_result,
    recover_stale_tasks,
    retry_metadata_job,
)
from app.services import material_inventory
from app.services import material_task_queue


@pytest.fixture(autouse=True)
def isolate_manifest_hashing_from_live_minio(monkeypatch):
    monkeypatch.setattr(
        material_task_queue,
        "_manifest_sha256",
        lambda bucket, object_name: f"{bucket}/{object_name}".encode().hex()[:64].ljust(64, "0"),
    )


def make_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)()


def add_material(db, index: int, *, user_id: str = "u1") -> Material:
    material = Material(
        user_id=user_id,
        material_id=f"pdf-{index:016x}",
        source_hash=f"source-{index}",
        title=f"Book {index}",
        filename=f"book-{index}.pdf",
        source_type="uploaded",
        input_bucket="eduassets-input",
        input_object=f"book-{index}.pdf",
        input_sha256=f"{index:064x}",
        stage_status="input",
        pipeline_status="idle",
    )
    db.add(material)
    db.flush()
    return material


def add_run(db, materials: list[Material], *, mode: str = "apply") -> PipelineRun:
    snapshot = material_snapshot(db, "u1", [row.id for row in materials])
    run = PipelineRun(
        user_id="u1",
        status="running",
        mode=mode,
        idempotency_key=pipeline_idempotency_key("u1", mode, snapshot),
        request_json="{}",
        total=len(snapshot),
        current_stage="pipeline_command",
        created_at=datetime.utcnow(),
    )
    db.add(run)
    db.flush()
    add_pipeline_run_items(db, run, snapshot)
    return run


def freeze(material: Material, stage: str) -> dict:
    run_id = f"{stage}-run-{material.id}"
    bucket = "eduassets-mineru" if stage == "mineru" else "eduassets-minerupopo"
    prefix = "mineru" if stage == "mineru" else "minerupopo"
    return {
        "material_id": material.material_id,
        "run_id": run_id,
        "manifest": {"bucket": bucket, "object": f"{prefix}/{material.material_id}/{run_id}/manifest.json"},
    }


def test_snapshot_is_explicit_ordered_and_rejects_duplicate_content():
    db = make_session()
    first = add_material(db, 1)
    second = add_material(db, 2)

    snapshot = material_snapshot(db, "u1", [second.id, first.id])
    assert [row["material_pk"] for row in snapshot] == [second.id, first.id]

    second.input_sha256 = first.input_sha256
    with pytest.raises(MaterialTaskError, match="重复PDF"):
        material_snapshot(db, "u1", [first.id, second.id])


def test_pipeline_run_list_is_summary_only_and_detail_keeps_items():
    db = make_session()
    run = add_run(db, [add_material(db, 1), add_material(db, 2)])
    db.commit()

    page = list_pipeline_runs(db, "u1")
    detail = pipeline_run_detail(db, run)

    assert page["total"] == 1
    assert page["runs"][0]["id"] == str(run.id)
    assert "items" not in page["runs"][0]
    assert "summary" not in page["runs"][0]
    assert "request" not in page["runs"][0]
    assert len(detail["items"]) == 2


def test_three_item_result_is_partial_and_preserves_two_frozen_assets():
    db = make_session()
    materials = [add_material(db, value) for value in (1, 2, 3)]
    run = add_run(db, materials)
    payload = {
        "status": "PARTIAL",
        "mineru_batch_id": "mineru-batch",
        "mineru_freezes": [freeze(material, "mineru") for material in materials],
        "mineru_errors": [],
        "popo": {
            "batch_id": "popo-batch",
            "freezes": [freeze(materials[0], "popo"), freeze(materials[1], "popo")],
            "errors": [
                {
                    "material_id": materials[2].material_id,
                    "run_id": "popo-run-failed",
                    "stage": "popo",
                    "reason": "popo_stage_failed",
                    "error": {"message": "wrapper failure"},
                }
            ],
        },
    }

    outcome = project_pipeline_result(db, run, payload)
    db.commit()

    assert outcome == "partial"
    assert (run.total, run.success, run.failed) == (3, 2, 1)
    items = db.query(PipelineRunItem).order_by(PipelineRunItem.id).all()
    assert [item.status for item in items] == ["succeeded", "succeeded", "failed"]
    assert materials[0].popo_manifest_object.endswith("/manifest.json")
    assert materials[1].popo_manifest_object.endswith("/manifest.json")
    assert materials[2].popo_manifest_object is None
    assert db.query(MetadataJob).count() == 2
    assert db.query(PipelineStageAttempt).filter(PipelineStageAttempt.stage == "popo").count() == 3


def test_popo_resume_records_reused_mineru_without_new_mineru_freeze():
    db = make_session()
    material = add_material(db, 7)
    material.mineru_run_id = "mineru-frozen"
    material.mineru_manifest_bucket = "eduassets-mineru"
    material.mineru_manifest_object = f"mineru/{material.material_id}/mineru-frozen/manifest.json"
    run = add_run(db, [material], mode="resume_popo")

    outcome = project_pipeline_result(
        db,
        run,
        {
            "status": "DONE",
            "existing_mineru_batch_id": "mineru-batch",
            "mineru_freezes": [],
            "mineru_errors": [],
            "popo": {"batch_id": "popo-batch", "freezes": [freeze(material, "popo")], "errors": []},
        },
    )
    db.commit()

    mineru_attempt = db.query(PipelineStageAttempt).filter(PipelineStageAttempt.stage == "mineru").one()
    assert outcome == "succeeded"
    assert mineru_attempt.status == "succeeded"
    assert mineru_attempt.evidence()["reused"] is True
    assert mineru_attempt.external_run_id == "mineru-frozen"


def test_popo_preparation_failure_preserves_frozen_mineru_stage():
    db = make_session()
    material = add_material(db, 9)
    run = add_run(db, [material])
    mineru_freeze = freeze(material, "mineru")

    outcome = project_pipeline_result(
        db,
        run,
        {
            "status": "PARTIAL",
            "mineru_batch_id": "mineru-batch",
            "mineru_freezes": [mineru_freeze],
            "mineru_errors": [],
            "popo": {
                "batch_id": "",
                "freezes": [],
                "errors": [
                    {
                        "material_id": material.material_id,
                        "stage": "popo",
                        "reason": "popo_submit_preparation_failed",
                        "error": {"message": "ssh failed"},
                    }
                ],
            },
        },
    )
    db.commit()

    item = db.query(PipelineRunItem).one()
    mineru_attempt = db.query(PipelineStageAttempt).filter(PipelineStageAttempt.stage == "mineru").one()
    popo_attempt = db.query(PipelineStageAttempt).filter(PipelineStageAttempt.stage == "popo").one()
    assert outcome == "failed"
    assert material.stage_status == "mineru_done"
    assert material.mineru_manifest_object == mineru_freeze["manifest"]["object"]
    assert item.current_stage == "popo_failed"
    assert item.error_code == "popo_submit_preparation_failed"
    assert mineru_attempt.status == "succeeded"
    assert popo_attempt.status == "failed"


def test_expired_pipeline_lease_is_requeued_and_claimed_after_restart():
    db = make_session()
    run = PipelineRun(
        user_id="u1",
        status="running",
        mode="apply",
        current_stage="pipeline_command",
        worker_id="dead-worker",
        lease_expires_at=datetime.utcnow() - timedelta(seconds=1),
        created_at=datetime.utcnow(),
    )
    db.add(run)
    db.commit()

    recovered = recover_stale_tasks(db)
    claimed = claim_next_pipeline_run(db, "new-worker")

    assert recovered["pipeline_runs"] == 1
    assert claimed.id == run.id
    assert claimed.status == "running"
    assert claimed.worker_id == "new-worker"
    assert claimed.attempt_count == 1


def test_running_items_use_honest_opaque_gpu_stage():
    db = make_session()
    material = add_material(db, 8)
    run = add_run(db, [material])

    material_inventory.mark_pipeline_items_running(db, run)

    item = db.query(PipelineRunItem).one()
    attempt = db.query(PipelineStageAttempt).one()
    assert item.current_stage == "pipeline_command"
    assert attempt.stage == "mineru"
    assert attempt.status == "running"


def test_parent_progress_aggregates_durable_child_stages_without_fake_freeze():
    db = make_session()
    materials = [add_material(db, value) for value in (31, 32, 33, 34, 35)]
    run = add_run(db, materials)
    items = db.query(PipelineRunItem).order_by(PipelineRunItem.id).all()
    for item, stage in zip(
        items,
        [
            "mineru_frozen",
            "mineru_frozen",
            "mineru_remote_running",
            "pipeline_command",
            "pipeline_command",
        ],
    ):
        item.current_stage = stage
    run.status = "running"
    run.current_stage = "mineru_remote_running"
    db.commit()

    detail = pipeline_run_detail(db, run)

    assert detail["progress_basis"] == "per_item_stage_aggregate"
    assert detail["progress_percent"] == 25
    assert detail["processed"] == 0


def test_model_switch_has_visible_progress_without_claiming_popo_freeze():
    db = make_session()
    materials = [add_material(db, value) for value in (41, 42)]
    run = add_run(db, materials)
    for item in db.query(PipelineRunItem).all():
        item.current_stage = "mineru_frozen"
    run.status = "running"
    run.current_stage = "popo_model_loading"
    db.commit()

    detail = pipeline_run_detail(db, run)

    assert detail["progress_percent"] == 50
    assert detail["processed"] == 0


def test_terminal_freeze_is_committed_before_inventory_sync(monkeypatch, tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'pipeline.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    setup = factory()
    material = add_material(setup, 10)
    run = add_run(setup, [material])
    payload = {
        "status": "DONE",
        "mineru_batch_id": "mineru-batch",
        "mineru_freezes": [freeze(material, "mineru")],
        "mineru_errors": [],
        "popo": {
            "batch_id": "popo-batch",
            "freezes": [freeze(material, "popo")],
            "errors": [],
        },
    }
    run_id = run.id
    setup.commit()
    setup.close()
    observed = {}

    def inspect_committed_terminal_state(_db, _user_id, _run_id):
        independent = factory()
        try:
            stored = independent.query(PipelineRun).filter_by(id=run_id).one()
            observed.update(status=stored.status, success=stored.success, worker_id=stored.worker_id)
        finally:
            independent.close()
        raise RuntimeError("inventory scan failed")

    monkeypatch.setattr(material_inventory, "SessionLocal", factory)
    monkeypatch.setattr(material_inventory.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(material_inventory, "sync_pipeline_run_inventory", inspect_committed_terminal_state)
    monkeypatch.setattr(
        material_inventory.subprocess,
        "Popen",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=io.StringIO(json.dumps(payload)), wait=lambda: 0),
    )

    material_inventory.run_pipeline_subprocess(
        run_id,
        apply=True,
        limit=1,
        command_override=["pipeline"],
    )

    verify = factory()
    try:
        stored = verify.query(PipelineRun).filter_by(id=run_id).one()
        assert observed == {"status": "succeeded", "success": 1, "worker_id": None}
        assert stored.status == "succeeded"
        assert stored.success == 1
        assert stored.current_stage == "finished"
    finally:
        verify.close()


def test_terminal_inventory_sync_retries_transient_failure(monkeypatch, tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'pipeline.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    setup = factory()
    material = add_material(setup, 13)
    run = add_run(setup, [material])
    payload = {
        "status": "DONE",
        "mineru_batch_id": "mineru-batch",
        "mineru_freezes": [freeze(material, "mineru")],
        "mineru_errors": [],
        "popo": {"batch_id": "popo-batch", "freezes": [freeze(material, "popo")], "errors": []},
    }
    run_id = run.id
    setup.commit()
    setup.close()
    attempts = []

    def flaky_sync(_db, _user_id, _run_id):
        attempts.append(_run_id)
        if len(attempts) == 1:
            raise OSError("temporary disk I/O error")

    monkeypatch.setattr(material_inventory, "SessionLocal", factory)
    monkeypatch.setattr(material_inventory.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(material_inventory, "sync_pipeline_run_inventory", flaky_sync)
    monkeypatch.setattr(
        material_inventory.subprocess,
        "Popen",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=io.StringIO(json.dumps(payload)), wait=lambda: 0),
    )

    material_inventory.run_pipeline_subprocess(
        run_id,
        apply=True,
        limit=1,
        command_override=["pipeline"],
    )

    verify = factory()
    try:
        stored = verify.query(PipelineRun).filter_by(id=run_id).one()
        warnings = verify.query(material_inventory.PipelineEvent).filter_by(run_id=run_id, level="warning").all()
        assert attempts == [run_id, run_id]
        assert stored.status == "succeeded"
        assert warnings == []
    finally:
        verify.close()


def test_pipeline_inventory_sync_only_touches_current_run_materials(monkeypatch):
    db = make_session()
    selected = add_material(db, 11)
    unselected = add_material(db, 12)
    run = add_run(db, [selected])
    item = db.query(PipelineRunItem).one()
    item.popo_manifest_bucket = "eduassets-minerupopo"
    item.popo_manifest_object = f"minerupopo/{selected.material_id}/popo-run/manifest.json"
    touched = []

    monkeypatch.setattr(material_inventory, "resolve_manifest", lambda *_args, **_kwargs: SimpleNamespace())
    monkeypatch.setattr(
        material_inventory,
        "ensure_resolved_review_asset",
        lambda *_args, **_kwargs: SimpleNamespace(id=321),
    )
    monkeypatch.setattr(
        material_inventory,
        "sync_material_outputs_for_material",
        lambda _db, _user_id, material: touched.append(material.id),
    )

    result = material_inventory.sync_pipeline_run_inventory(db, "u1", run.id)

    assert result == {"materials": 1}
    assert touched == [selected.id]
    assert selected.review_asset_id == 321
    assert unselected.review_asset_id is None


def test_automatic_metadata_job_never_overwrites_manual_override():
    db = make_session()
    material = add_material(db, 9)
    db.add(
        MaterialMetadata(
            user_id="u1",
            material_pk=material.id,
            status="manual",
            source="manual",
            manual_override=True,
        )
    )
    db.commit()

    assert enqueue_metadata_job(db, "u1", material, automatic=True) is None
    with pytest.raises(MaterialTaskError, match="不会覆盖"):
        enqueue_metadata_job(db, "u1", material)
    forced = enqueue_metadata_job(db, "u1", material, force=True)
    duplicate = enqueue_metadata_job(db, "u1", material, force=True)
    assert forced.id == duplicate.id


def test_duplicate_submit_reuses_active_run_and_global_gpu_slot(monkeypatch):
    db = make_session()
    first = add_material(db, 21)
    second = add_material(db, 22)
    monkeypatch.setattr(
        material_inventory,
        "run_pipeline_preflight",
        lambda *_args, **_kwargs: {"ready": True, "status": "DRY_RUN", "selected_count": 1},
    )

    run = material_inventory.start_pipeline_run(db, "u1", apply=True, material_pks=[first.id])
    duplicate = material_inventory.start_pipeline_run(db, "u1", apply=True, material_pks=[first.id])

    assert duplicate.id == run.id
    assert run.queue_slot == "gpu"
    assert db.query(PipelineRun).count() == 1
    with pytest.raises(MaterialTaskError, match="串行GPU队列"):
        material_inventory.start_pipeline_run(db, "u1", apply=True, material_pks=[second.id])


def test_large_pdf_resource_gate_blocks_insufficient_wrapper_headroom():
    payload = {
        "ready": True,
        "status": "READY",
        "health": {
            "health": {
                "artifact_limit_bytes": 16 * 1024**3,
                "artifact_used_bytes": 10 * 1024**3,
                "disk_available_bytes": 12 * 1024**3,
            }
        },
    }
    snapshot = [{"size_bytes": 512 * 1024**2, "page_count": 2415}]

    result = material_inventory.apply_pipeline_resource_gate(payload, snapshot)

    assert result["ready"] is False
    assert result["status"] == "GPU_RESOURCE_HEADROOM_BLOCKED"
    assert result["resource_gate"]["status"] == "insufficient_headroom"
    assert result["resource_gate"]["available_headroom_bytes"] == 6 * 1024**3
    assert result["resource_gate"]["required_headroom_bytes"] == 50 * 1024**3


def test_large_pdf_resource_gate_passes_with_required_wrapper_headroom():
    payload = {
        "ready": True,
        "status": "READY",
        "health": {
            "health": {
                "artifact_limit_bytes": 80 * 1024**3,
                "artifact_used_bytes": 10 * 1024**3,
                "disk_available_bytes": 60 * 1024**3,
            }
        },
    }
    snapshot = [{"size_bytes": 512 * 1024**2, "page_count": 2415}]

    result = material_inventory.apply_pipeline_resource_gate(payload, snapshot)

    assert result["ready"] is True
    assert result["status"] == "READY"
    assert result["resource_gate"]["status"] == "ready"
    assert result["resource_gate"]["available_headroom_bytes"] == 60 * 1024**3


def test_large_pdf_resource_gate_requires_real_disk_headroom() -> None:
    payload = {
        "ready": True,
        "status": "READY",
        "health": {
            "health": {
                "artifact_limit_bytes": 16 * 1024**3,
                "artifact_used_bytes": 6 * 1024**3,
            }
        },
    }

    result = material_inventory.apply_pipeline_resource_gate(
        payload,
        [{"size_bytes": 512 * 1024**2, "page_count": 2415}],
    )

    assert result["ready"] is False
    assert result["status"] == "GPU_RESOURCE_HEADROOM_BLOCKED"
    assert result["resource_gate"]["status"] == "disk_headroom_unknown"
    assert result["resource_gate"]["reason"] == "GPU Wrapper 未报告真实磁盘余量"


def test_large_pdf_resource_gate_uses_historical_expansion_factor() -> None:
    payload = {
        "ready": True,
        "status": "READY",
        "health": {
            "health": {
                "artifact_limit_bytes": 80 * 1024**3,
                "artifact_used_bytes": 2 * 1024**3,
                "disk_available_bytes": 60 * 1024**3,
                "historical_artifact_expansion_factor": 20,
            }
        },
    }

    result = material_inventory.apply_pipeline_resource_gate(
        payload,
        [{"size_bytes": 512 * 1024**2, "page_count": 2415}],
    )

    assert result["ready"] is True
    assert result["resource_gate"]["expansion_factor"] == 20
    assert result["resource_gate"]["required_headroom_bytes"] == 50 * 1024**3


def test_small_pdf_resource_gate_does_not_require_wrapper_quota_fields():
    payload = {"ready": True, "status": "READY", "health": {}}

    result = material_inventory.apply_pipeline_resource_gate(
        payload,
        [{"size_bytes": 20 * 1024**2, "page_count": 120}],
    )

    assert result["ready"] is True
    assert result["resource_gate"] == {
        "applies": False,
        "ok": True,
        "status": "not_applicable",
        "large_pdf_count": 0,
        "selected_input_bytes": 20 * 1024**2,
        "max_page_count": 120,
        "large_pdf_threshold_bytes": 256 * 1024**2,
        "large_pdf_page_threshold": 1000,
    }


def test_versioned_reprocess_is_recorded_without_mutating_existing_frozen_refs(monkeypatch):
    db = make_session()
    material = add_material(db, 24)
    material.mineru_run_id = "mineru-old"
    material.mineru_manifest_bucket = "eduassets-mineru"
    material.mineru_manifest_object = f"mineru/{material.material_id}/mineru-old/manifest.json"
    material.popo_run_id = "popo-old"
    material.popo_manifest_bucket = "eduassets-minerupopo"
    material.popo_manifest_object = f"minerupopo/{material.material_id}/popo-old/manifest.json"
    monkeypatch.setattr(
        material_inventory,
        "run_pipeline_preflight",
        lambda *_args, **kwargs: {
            "ready": kwargs.get("reprocess_completed") is True,
            "status": "READY",
            "selected_count": 1,
        },
    )

    run = material_inventory.start_pipeline_run(
        db,
        "u1",
        apply=True,
        material_pks=[material.id],
        reprocess_completed=True,
    )

    assert run.mode == "reprocess"
    assert '"reprocess_completed": true' in run.request_json
    assert "--reprocess-completed" in run.command
    assert material.mineru_run_id == "mineru-old"
    assert material.popo_run_id == "popo-old"


def test_failed_metadata_job_retries_but_manual_override_remains_protected():
    db = make_session()
    material = add_material(db, 23)
    job = enqueue_metadata_job(db, "u1", material)
    job.status = "failed"
    job.error_message = "temporary model error"
    db.commit()

    retried = retry_metadata_job(db, "u1", material.id, job.id)
    assert retried.status == "queued"
    assert retried.error_message is None

    retried.status = "failed"
    db.add(MaterialMetadata(user_id="u1", material_pk=material.id, status="manual", source="manual", manual_override=True))
    db.commit()
    with pytest.raises(MaterialTaskError, match="人工元数据"):
        retry_metadata_job(db, "u1", material.id, job.id)
