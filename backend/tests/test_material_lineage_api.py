import hashlib
import io
import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.datastructures import UploadFile

from app.api import materials as materials_api
from app.models.base import Base
from app.models.material import CodexSkillJob, Material, MaterialOutput, MetadataJob, PipelineRun, PipelineRunItem, PipelineStageAttempt
from app.models.user import User
from app.services.material_inventory import material_id_from_sha256, upload_input_pdfs


def make_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def add_material(db, *, user_id="u1"):
    row = Material(
        user_id=user_id,
        material_id="pdf-lineage",
        source_hash="source",
        title="Lineage Book",
        filename="lineage.pdf",
        source_type="uploaded",
        input_bucket="eduassets-input",
        input_object="lineage.pdf",
        input_sha256="a" * 64,
        stage_status="popo_done",
        pipeline_status="idle",
    )
    db.add(row)
    db.flush()
    return row


def test_lineage_joins_pipeline_metadata_worker_output_and_review(monkeypatch):
    db = make_session()
    material = add_material(db)
    material.review_asset_id = 42
    run = PipelineRun(user_id="u1", status="partial", mode="apply", total=1, failed=1)
    db.add(run)
    db.flush()
    item = PipelineRunItem(
        run_id=run.id,
        user_id="u1",
        material_pk=material.id,
        material_id=material.material_id,
        filename=material.filename,
        input_bucket=material.input_bucket,
        input_object=material.input_object,
        status="failed",
        current_stage="popo",
    )
    db.add(item)
    db.flush()
    db.add(PipelineStageAttempt(run_item_id=item.id, user_id="u1", stage="popo", attempt=1, status="failed"))
    db.add(MetadataJob(user_id="u1", material_pk=material.id, material_id=material.material_id, status="queued", idempotency_key="metadata-1"))
    db.add(MaterialOutput(user_id="u1", material_pk=material.id, material_id=material.material_id, review_asset_id=42, manifest_bucket="outputs", manifest_object="manifest.json"))
    db.commit()

    class WorkflowSession:
        def close(self):
            pass

    monkeypatch.setattr(materials_api, "workflow_session_factory", lambda: lambda: WorkflowSession())
    monkeypatch.setattr(materials_api, "list_workflow_jobs", lambda *_args, **_kwargs: [{"id": "worker-1", "status": "needs_review"}])

    result = materials_api.get_material_lineage(material.id, user_id="u1", db=db)

    assert result["pipeline_items"][0]["attempts"][0]["stage"] == "popo"
    assert result["metadata_jobs"][0]["status"] == "queued"
    assert result["workflow_jobs"][0]["id"] == "worker-1"
    assert result["outputs"][0]["review_asset_id"] == "42"
    assert result["review"] == {"asset_id": "42", "available": True}


def test_upload_deduplicates_by_sha_before_minio_write_and_records_pages(monkeypatch):
    data = (Path(__file__).parent / "test.pdf").read_bytes()
    sha256 = hashlib.sha256(data).hexdigest()
    db = make_session()
    material = add_material(db)
    material.material_id = material_id_from_sha256(sha256)
    material.input_sha256 = sha256
    material.page_count = None
    db.commit()

    class ForbiddenMinio:
        def put_object(self, *_args, **_kwargs):
            raise AssertionError("duplicate upload must not write MinIO")

    monkeypatch.setattr("app.services.material_inventory.minio_client", ForbiddenMinio())
    upload = UploadFile(filename="same-content-renamed.pdf", file=io.BytesIO(data))
    result = asyncio.run(upload_input_pdfs([upload], "u1", db))

    assert result["duplicates"] == 1
    assert result["success"] == 1
    assert result["files"][0]["status"] == "duplicate"
    assert result["files"][0]["filename"] == "same-content-renamed.pdf"
    assert result["files"][0]["material"]["filename"] == material.filename
    assert result["files"][0]["material"]["material_id"] == material.material_id
    assert material.page_count and material.page_count > 0
    assert db.query(Material).count() == 1


def test_refinement_projection_separates_available_output_from_latest_needs_review_job():
    material = Material(
        user_id="u1",
        title="Book",
        filename="book.pdf",
        latex_manifest_bucket="eduassets-elegantbook",
        latex_manifest_object="worker-v2/pdf-1/run/manifest.json",
    )
    output = MaterialOutput(
        id=541,
        user_id="u1",
        material_pk=1,
        material_id="pdf-1",
        manifest_bucket="eduassets-elegantbook",
        manifest_object="worker-v2/pdf-1/succeeded/manifest.json",
        output_run_id="succeeded-job",
        quality_status="passed",
        status="promoted",
        is_current=True,
    )
    projection = materials_api._refinement_projection(
        material,
        None,
        {"id": "latest-job", "status": "needs_review", "current_stage_key": "deterministic_elegantbook"},
        output,
    )

    assert projection["refinement_output_status"] == "succeeded"
    assert projection["current_refinement_output"]["id"] == "541"
    assert projection["latest_refinement_status"] == "needs_review"
    assert projection["refinement_status"] == "needs_review"


def test_refinement_status_surfaces_active_new_attempt_over_frozen_output():
    material = Material(
        user_id="u1",
        title="Book",
        filename="book.pdf",
        latex_manifest_bucket="eduassets-elegantbook",
        latex_manifest_object="worker-v2/pdf-1/run/manifest.json",
    )
    job = CodexSkillJob(user_id="u1", status="running", mode="new_pdf", requested_skill="test")

    assert materials_api._refinement_status(material, job) == "running"


def test_refinement_projection_does_not_claim_latest_task_status_when_workflow_db_is_unavailable():
    material = Material(
        user_id="u1",
        title="Book",
        filename="book.pdf",
        latex_manifest_bucket="eduassets-elegantbook",
        latex_manifest_object="worker-v2/pdf-1/run/manifest.json",
    )

    projection = materials_api._refinement_projection(material, None, None, None, workflow_available=False)

    assert projection["refinement_output_status"] == "succeeded"
    assert projection["latest_refinement_status"] == "unavailable"
    assert projection["refinement_status"] == "unavailable"


def test_completed_reprocess_preflight_requires_pipeline_admin(monkeypatch):
    db = make_session()
    user = User(email="reader@example.com", password_hash="test")
    db.add(user)
    db.flush()
    material = add_material(db, user_id=str(user.id))
    db.commit()
    monkeypatch.setenv("LUCEON_AUTH_DISABLED", "false")
    monkeypatch.setenv("LUCEON_PIPELINE_ADMIN_EMAILS", "admin@example.com")

    with pytest.raises(HTTPException) as exc:
        materials_api.pipeline_preflight(
            materials_api.PipelinePreflightRequest(material_pks=[material.id], reprocess_completed=True),
            user_id=str(user.id),
            db=db,
        )

    assert exc.value.status_code == 403


def test_pipeline_admin_can_preflight_new_immutable_parse_version(monkeypatch):
    db = make_session()
    user = User(email="admin@example.com", password_hash="test")
    db.add(user)
    db.flush()
    material = add_material(db, user_id=str(user.id))
    db.commit()
    monkeypatch.setenv("LUCEON_AUTH_DISABLED", "false")
    monkeypatch.setenv("LUCEON_PIPELINE_ADMIN_EMAILS", "admin@example.com")
    monkeypatch.setattr(
        materials_api,
        "run_pipeline_preflight",
        lambda *_args, **kwargs: {"ready": kwargs.get("reprocess_completed") is True, "status": "READY"},
    )

    result = materials_api.pipeline_preflight(
        materials_api.PipelinePreflightRequest(material_pks=[material.id], reprocess_completed=True),
        user_id=str(user.id),
        db=db,
    )

    assert result["ready"] is True
    assert result["snapshot"][0]["material_pk"] == material.id
