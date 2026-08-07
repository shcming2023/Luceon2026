import hashlib
import io
import asyncio
from pathlib import Path

import pytest
import fitz
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.datastructures import UploadFile

from app.api import materials as materials_api
from app.models.base import Base
from app.models.material import CodexSkillJob, Material, MaterialOutput, MetadataJob, PipelineRun, PipelineRunItem, PipelineStageAttempt
from app.models.user import User
from app.services.material_inventory import MaterialTaskError, material_id_from_sha256, start_pipeline_run, upload_input_pdfs


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

    class Response:
        def stream(self, size):
            for offset in range(0, len(data), size):
                yield data[offset : offset + size]

        def close(self):
            pass

        def release_conn(self):
            pass

    class VerifiedDuplicateMinio:
        def put_object(self, *_args, **_kwargs):
            raise AssertionError("duplicate upload must not write MinIO")

        def stat_object(self, *_args, **_kwargs):
            return type("Stat", (), {"size": len(data)})()

        def get_object(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr("app.services.material_inventory.minio_client", VerifiedDuplicateMinio())
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


def test_duplicate_upload_fails_when_existing_minio_object_is_missing(monkeypatch):
    data = (Path(__file__).parent / "test.pdf").read_bytes()
    sha256 = hashlib.sha256(data).hexdigest()
    db = make_session()
    material = add_material(db)
    material.material_id = material_id_from_sha256(sha256)
    material.input_sha256 = sha256
    db.commit()

    class MissingMinio:
        def stat_object(self, *_args, **_kwargs):
            raise FileNotFoundError("missing")

    monkeypatch.setattr("app.services.material_inventory.minio_client", MissingMinio())
    result = asyncio.run(upload_input_pdfs([UploadFile(filename="same.pdf", file=io.BytesIO(data))], "u1", db))
    assert result["failed"] == 1
    assert "冻结对象缺失" in result["files"][0]["error_message"]


def test_duplicate_upload_fails_on_existing_minio_sha_drift(monkeypatch):
    data = (Path(__file__).parent / "test.pdf").read_bytes()
    sha256 = hashlib.sha256(data).hexdigest()
    db = make_session()
    material = add_material(db)
    material.material_id = material_id_from_sha256(sha256)
    material.input_sha256 = sha256
    db.commit()

    class Response:
        def stream(self, _size):
            yield b"different"

        def close(self):
            pass

        def release_conn(self):
            pass

    class DriftMinio:
        def stat_object(self, *_args, **_kwargs):
            return type("Stat", (), {"size": 9})()

        def get_object(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr("app.services.material_inventory.minio_client", DriftMinio())
    result = asyncio.run(upload_input_pdfs([UploadFile(filename="same.pdf", file=io.BytesIO(data))], "u1", db))
    assert result["failed"] == 1
    assert "SHA/size 漂移" in result["files"][0]["error_message"]


def test_completed_popo_material_is_not_resubmitted_without_explicit_reprocess():
    db = make_session()
    material = add_material(db)
    material.mineru_manifest_bucket = "eduassets-mineru"
    material.mineru_manifest_object = "mineru/manifest.json"
    material.popo_manifest_bucket = "eduassets-minerupopo"
    material.popo_manifest_object = "popo/manifest.json"
    db.commit()

    with pytest.raises(MaterialTaskError, match="不会重复提交 GPU"):
        start_pipeline_run(db, "u1", apply=True, material_pks=[material.id])

    assert db.query(PipelineRun).count() == 0


def test_upload_streams_content_addressed_pdf_and_independently_verifies_minio(monkeypatch):
    data = (Path(__file__).parent / "test.pdf").read_bytes()
    sha256 = hashlib.sha256(data).hexdigest()
    material_id = material_id_from_sha256(sha256)
    db = make_session()

    class TrackingFile(io.BytesIO):
        def __init__(self, payload):
            super().__init__(payload)
            self.read_sizes = []

        def read(self, size=-1):
            self.read_sizes.append(size)
            return super().read(size)

    class Response:
        def stream(self, size):
            for offset in range(0, len(data), size):
                yield data[offset : offset + size]

        def close(self):
            pass

        def release_conn(self):
            pass

    class FakeMinio:
        def __init__(self):
            self.writes = []
            self.exists = False

        def stat_object(self, bucket, object_name):
            if not self.exists:
                raise FileNotFoundError(object_name)
            return type("Stat", (), {"size": len(data), "content_type": "application/pdf"})()

        def put_object(self, bucket, object_name, stream, *, length, content_type, metadata):
            payload = stream.read()
            self.writes.append((bucket, object_name, payload, length, content_type, metadata))
            self.exists = True

        def get_object(self, bucket, object_name):
            return Response()

    fake = FakeMinio()
    monkeypatch.setattr("app.services.material_inventory.minio_client", fake)
    monkeypatch.setattr("app.services.material_inventory.UPLOAD_CHUNK_BYTES", 64)
    source = TrackingFile(data)
    upload = UploadFile(filename="renamed.pdf", file=source)

    result = asyncio.run(upload_input_pdfs([upload], "u1", db))

    assert result["success"] == 1
    assert result["files"][0]["status"] == "success"
    assert all(size != -1 for size in source.read_sizes)
    assert len(source.read_sizes) > 2
    assert fake.writes[0][1] == f"pdf/{material_id}/{sha256}.pdf"
    assert fake.writes[0][2] == data
    assert fake.writes[0][5]["sha256"] == sha256
    material = db.query(Material).one()
    assert material.pipeline_status == "input_frozen"
    assert material.input_sha256 == sha256
    assert material.size_bytes == len(data)


def test_upload_stops_at_server_size_limit_before_minio(monkeypatch):
    data = (Path(__file__).parent / "test.pdf").read_bytes()
    monkeypatch.setattr("app.services.material_inventory.MAX_UPLOAD_PDF_BYTES", len(data) - 1)
    monkeypatch.setattr("app.services.material_inventory.UPLOAD_CHUNK_BYTES", 64)

    class ForbiddenMinio:
        def __getattr__(self, name):
            raise AssertionError(f"MinIO must not be called after upload limit: {name}")

    monkeypatch.setattr("app.services.material_inventory.minio_client", ForbiddenMinio())
    result = asyncio.run(upload_input_pdfs([UploadFile(filename="large.pdf", file=io.BytesIO(data))], "u1", make_session()))
    assert result["failed"] == 1
    assert "超过服务器限制" in result["files"][0]["error_message"]


def test_upload_rejects_when_temp_disk_headroom_is_insufficient(monkeypatch):
    data = (Path(__file__).parent / "test.pdf").read_bytes()
    monkeypatch.setattr(
        "app.services.material_inventory.shutil.disk_usage",
        lambda _path: type("Usage", (), {"free": 0})(),
    )
    with pytest.raises(ValueError, match="临时目录空间不足"):
        asyncio.run(upload_input_pdfs([UploadFile(filename="temp-full.pdf", file=io.BytesIO(data), size=len(data))], "u1", make_session()))


@pytest.mark.parametrize(
    "payload",
    [b"not-a-pdf", b"%PDF-1.7\ntruncated-and-invalid"],
)
def test_upload_rejects_invalid_pdf_structure(monkeypatch, payload):
    class ForbiddenMinio:
        def __getattr__(self, name):
            raise AssertionError(f"MinIO must not be called for invalid PDF: {name}")

    monkeypatch.setattr("app.services.material_inventory.minio_client", ForbiddenMinio())
    result = asyncio.run(upload_input_pdfs([UploadFile(filename="broken.pdf", file=io.BytesIO(payload))], "u1", make_session()))
    assert result["failed"] == 1


def test_upload_rejects_encrypted_pdf_before_minio(monkeypatch):
    document = fitz.open()
    document.new_page().insert_text((72, 72), "encrypted")
    payload = document.tobytes(
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw="owner",
        user_pw="reader",
    )
    document.close()

    class ForbiddenMinio:
        def __getattr__(self, name):
            raise AssertionError(f"MinIO must not be called for encrypted PDF: {name}")

    monkeypatch.setattr("app.services.material_inventory.minio_client", ForbiddenMinio())
    result = asyncio.run(upload_input_pdfs([UploadFile(filename="encrypted.pdf", file=io.BytesIO(payload))], "u1", make_session()))
    assert result["failed"] == 1
    assert "已加密" in result["files"][0]["error_message"]


def test_upload_client_disconnect_cleans_partial_file_without_minio(monkeypatch, tmp_path):
    class DisconnectingFile(io.BytesIO):
        def __init__(self):
            super().__init__(b"%PDF-1.7\npartial")
            self.calls = 0

        def read(self, size=-1):
            self.calls += 1
            if self.calls > 1:
                raise ConnectionError("client disconnected")
            return super().read(8 if size < 0 else min(size, 8))

    class ForbiddenMinio:
        def __getattr__(self, name):
            raise AssertionError(f"MinIO must not be called after disconnect: {name}")

    monkeypatch.setattr("app.services.material_inventory.minio_client", ForbiddenMinio())
    monkeypatch.setattr("app.services.material_inventory.UPLOAD_CHUNK_BYTES", 8)
    result = asyncio.run(upload_input_pdfs([UploadFile(filename="disconnect.pdf", file=DisconnectingFile())], "u1", make_session()))
    assert result["failed"] == 1
    assert "client disconnected" in result["files"][0]["error_message"]


def test_upload_validates_and_records_multiple_pages(monkeypatch):
    document = fitz.open()
    for index in range(4):
        page = document.new_page()
        page.insert_text((72, 72), f"Education page {index + 1}")
        page.draw_rect((72, 100, 180, 180))
    payload = document.tobytes()
    document.close()

    class Response:
        def stream(self, size):
            for offset in range(0, len(payload), size):
                yield payload[offset : offset + size]

        def close(self):
            pass

        def release_conn(self):
            pass

    class FakeMinio:
        def stat_object(self, *_args, **_kwargs):
            raise FileNotFoundError

        def put_object(self, *_args, **_kwargs):
            return None

        def get_object(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr("app.services.material_inventory.minio_client", FakeMinio())
    db = make_session()
    result = asyncio.run(upload_input_pdfs([UploadFile(filename="education.pdf", file=io.BytesIO(payload))], "u1", db))
    assert result["success"] == 1
    assert db.query(Material).one().page_count == 4


def test_upload_minio_write_failure_does_not_create_material(monkeypatch):
    data = (Path(__file__).parent / "test.pdf").read_bytes()

    class WriteFailureMinio:
        def stat_object(self, *_args, **_kwargs):
            raise FileNotFoundError

        def put_object(self, *_args, **_kwargs):
            raise OSError("isolated MinIO write failed")

    monkeypatch.setattr("app.services.material_inventory.minio_client", WriteFailureMinio())
    db = make_session()
    result = asyncio.run(upload_input_pdfs([UploadFile(filename="write-fail.pdf", file=io.BytesIO(data))], "u1", db))
    assert result["failed"] == 1
    assert "MinIO write failed" in result["files"][0]["error_message"]
    assert db.query(Material).count() == 0


def test_new_upload_readback_drift_does_not_create_material(monkeypatch):
    data = (Path(__file__).parent / "test.pdf").read_bytes()

    class Response:
        def stream(self, _size):
            yield b"drifted-after-write"

        def close(self):
            pass

        def release_conn(self):
            pass

    class DriftAfterWriteMinio:
        def stat_object(self, *_args, **_kwargs):
            raise FileNotFoundError

        def put_object(self, *_args, **_kwargs):
            return None

        def get_object(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr("app.services.material_inventory.minio_client", DriftAfterWriteMinio())
    db = make_session()
    result = asyncio.run(upload_input_pdfs([UploadFile(filename="drift.pdf", file=io.BytesIO(data))], "u1", db))
    assert result["failed"] == 1
    assert "独立回读 SHA/size 不一致" in result["files"][0]["error_message"]
    assert db.query(Material).count() == 0


def test_upload_db_failure_removes_new_orphan_object(monkeypatch):
    data = (Path(__file__).parent / "test.pdf").read_bytes()

    class Response:
        def stream(self, size):
            for offset in range(0, len(data), size):
                yield data[offset : offset + size]

        def close(self):
            pass

        def release_conn(self):
            pass

    class TrackingMinio:
        def __init__(self):
            self.exists = False
            self.removed = []

        def stat_object(self, *_args, **_kwargs):
            if not self.exists:
                raise FileNotFoundError
            return type("Stat", (), {"size": len(data)})()

        def put_object(self, *_args, **_kwargs):
            self.exists = True

        def get_object(self, *_args, **_kwargs):
            return Response()

        def remove_object(self, bucket, object_name):
            self.removed.append((bucket, object_name))
            self.exists = False

    fake = TrackingMinio()
    monkeypatch.setattr("app.services.material_inventory.minio_client", fake)
    db = make_session()
    monkeypatch.setattr(db, "commit", lambda: (_ for _ in ()).throw(RuntimeError("isolated DB commit failed")))
    result = asyncio.run(upload_input_pdfs([UploadFile(filename="db-fail.pdf", file=io.BytesIO(data))], "u1", db))
    assert result["failed"] == 1
    assert fake.removed
    assert db.query(Material).count() == 0


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
        lambda *_args, **kwargs: {
            "ready": kwargs.get("reprocess_completed") is True,
            "status": "READY",
            "health": {"health": {"artifact_limit_bytes": 20 * 1024**3, "artifact_used_bytes": 0, "disk_available_bytes": 15 * 1024**3}},
        },
    )

    result = materials_api.pipeline_preflight(
        materials_api.PipelinePreflightRequest(material_pks=[material.id], reprocess_completed=True),
        user_id=str(user.id),
        db=db,
    )

    assert result["ready"] is True
    assert result["snapshot"][0]["material_pk"] == material.id
