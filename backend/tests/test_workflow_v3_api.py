from __future__ import annotations

import hashlib
import io
import json
import tarfile

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import workflow_v3 as workflow_v3_api
from app.api.workflow_v3 import (
    require_workflow_v3_enabled,
    router,
    workflow_v3_db_dependency,
)
from app.database import get_db
from app.models.base import Base
from app.models.material import Material, MaterialOutput
from app.models.review_asset import ReviewAsset
from app.models.user import User
from app.utils.user_dep import get_user_id, require_pipeline_admin
from app.workflow_v3.contracts import WORKFLOW_VERSION
from app.workflow_v3.database import (
    workflow_v3_engine,
    workflow_v3_session_factory,
)
from app.workflow_v3.models import (
    WorkflowV3Base,
    WorkflowV3Job,
    WorkflowV3ProjectionOutbox,
    WorkflowV3SkillRelease,
    WorkflowV3StageRun,
)
from app.workflow_v3.review_resolution import (
    evaluation_fingerprint,
    finding_fingerprint,
)
from app.workflow_v3.state_machine import (
    claim_current_stage,
    record_evaluation,
    submit_candidate,
)
from app.workflow_v3.service import create_workflow_job
from main import app


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


@pytest.fixture
def v3_api(monkeypatch):
    monkeypatch.setenv("WORKFLOW_V3_ENABLED", "true")
    material_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    workflow_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(material_engine)
    WorkflowV3Base.metadata.create_all(workflow_engine)
    material_factory = sessionmaker(bind=material_engine, expire_on_commit=False)
    workflow_factory = sessionmaker(bind=workflow_engine, expire_on_commit=False)

    material_db = material_factory()
    workflow_db = workflow_factory()
    source_sha = "a" * 64
    material = Material(
        user_id="u1",
        material_id="pdf-v3-source",
        source_hash=source_sha,
        title="Worker V3 source",
        filename="source.pdf",
        source_type="uploaded",
        input_bucket="eduassets-input",
        input_object="source.pdf",
        input_sha256=source_sha,
        size_bytes=12345,
        stage_status="popo_done",
        pipeline_status="idle",
        mineru_manifest_bucket="eduassets-mineru",
        mineru_manifest_object="mineru/pdf-v3-source/mineru-run/manifest.json",
        mineru_run_id="mineru-run",
        popo_manifest_bucket="eduassets-minerupopo",
        popo_manifest_object="minerupopo/pdf-v3-source/popo-run/manifest.json",
        popo_run_id="popo-run",
    )
    material_db.add(material)
    material_db.flush()
    review_asset = ReviewAsset(
        user_id=material.user_id,
        title=material.title,
        input_filename=material.filename,
        review_stage="popo",
        material_id=material.material_id,
        run_id=material.popo_run_id,
        manifest_bucket=material.popo_manifest_bucket,
        manifest_object=material.popo_manifest_object,
        manifest_json="{}",
        review_status="completed",
    )
    material_db.add(review_asset)
    material_db.flush()
    material.review_asset_id = review_asset.id
    material_db.commit()
    material_db.refresh(material)

    release = WorkflowV3SkillRelease(
        release_version="3.0.0-rc.1",
        manifest_sha256="b" * 64,
        package_bucket="luceon-releases",
        package_object="worker-v3/3.0.0-rc.1.tar.gz",
        package_sha256="c" * 64,
        workflow_version=WORKFLOW_VERSION,
        template_sha256="d" * 64,
        runtime_identity_sha256="e" * 64,
        manifest_json="{}",
        status="registered",
        registered_by="admin@luceon.local",
    )
    workflow_db.add(release)
    workflow_db.commit()

    mineru_archive = {
        "bucket": "eduassets-parsed",
        "object": "gpu-wrapper/pdf-v3-source/mineru-run/mineru-result.tar.gz",
        "sha256": "1" * 64,
        "size_bytes": 23456,
    }
    popo_archive = {
        "bucket": "eduassets-parsed",
        "object": "gpu-wrapper/pdf-v3-source/popo-run/popo-result.tar.gz",
        "sha256": "2" * 64,
        "size_bytes": 34567,
    }
    mineru_manifest_bytes = json.dumps(
        {
            "schema": "luceon-gpu-wrapper-mineru-only-manifest/v1",
            "status": "mineru_done_frozen",
            "material_id": material.material_id,
            "run_id": material.mineru_run_id,
            "source_pdf": {
                "sha256": source_sha,
                "size_bytes": material.size_bytes,
                "input_bucket": material.input_bucket,
                "input_object": material.input_object,
            },
            "objects": {"archive": mineru_archive},
        },
        sort_keys=True,
    ).encode()
    mineru_manifest_sha = _sha(mineru_manifest_bytes)
    manifest_bytes = json.dumps(
        {
            "schema": "luceon-gpu-wrapper-popo-from-frozen-mineru-manifest/v1",
            "material_id": material.material_id,
            "run_id": material.popo_run_id,
            "source_pdf": {
                "sha256": source_sha,
                "size_bytes": material.size_bytes,
                "input_bucket": material.input_bucket,
                "input_object": material.input_object,
            },
            "upstream_mineru": {
                "run_id": material.mineru_run_id,
                "manifest": {
                    "bucket": material.mineru_manifest_bucket,
                    "object": material.mineru_manifest_object,
                },
            },
            "objects": {"archive": popo_archive},
        },
        sort_keys=True,
    ).encode()
    popo_manifest_sha = _sha(manifest_bytes)
    mineru_marker_bytes = json.dumps(
        {
            "schema": "luceon-input-status-marker/v1",
            "status": "mineru_done_frozen",
            "material_id": material.material_id,
            "run_id": material.mineru_run_id,
            "source_pdf_sha256": source_sha,
            "source_pdf_size_bytes": material.size_bytes,
            "manifest": {
                "bucket": material.mineru_manifest_bucket,
                "object": material.mineru_manifest_object,
                "sha256": mineru_manifest_sha,
                "size_bytes": len(mineru_manifest_bytes),
            },
        },
        sort_keys=True,
    ).encode()
    marker_bytes = json.dumps(
        {
            "schema": "luceon-input-status-marker/v1",
            "status": "popo_done_frozen",
            "material_id": material.material_id,
            "run_id": material.popo_run_id,
            "source_pdf_sha256": source_sha,
            "source_pdf_size_bytes": material.size_bytes,
            "manifest": {
                "bucket": material.popo_manifest_bucket,
                "object": material.popo_manifest_object,
                "sha256": popo_manifest_sha,
                "size_bytes": len(manifest_bytes),
            },
            "mineru_manifest": {
                "bucket": material.mineru_manifest_bucket,
                "object": material.mineru_manifest_object,
            },
            "upstream_mineru_run_id": material.mineru_run_id,
        },
        sort_keys=True,
    ).encode()
    objects = {
        (material.mineru_manifest_bucket, material.mineru_manifest_object): mineru_manifest_bytes,
        (material.popo_manifest_bucket, material.popo_manifest_object): manifest_bytes,
        (
            "eduassets-input",
            f"_status/{material.material_id}/{material.mineru_run_id}.mineru_done_frozen.json",
        ): mineru_marker_bytes,
        (
            "eduassets-input",
            f"_status/{material.material_id}/{material.popo_run_id}.popo_done_frozen.json",
        ): marker_bytes,
    }

    def fake_read_object(bucket: str, object_name: str) -> bytes:
        key = (bucket, object_name)
        if key not in objects:
            raise FileNotFoundError(key)
        return objects[key]

    actor = {"user_id": "u1"}

    def material_dependency():
        db = material_factory()
        try:
            yield db
        finally:
            db.close()

    def workflow_dependency():
        db = workflow_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = material_dependency
    app.dependency_overrides[workflow_v3_db_dependency] = workflow_dependency
    app.dependency_overrides[get_user_id] = lambda: actor["user_id"]
    monkeypatch.setattr(workflow_v3_api, "read_object", fake_read_object)
    client = TestClient(app)
    try:
        yield {
            "client": client,
            "actor": actor,
            "material": material,
            "review_asset": review_asset,
            "material_factory": material_factory,
            "workflow_factory": workflow_factory,
            "objects": objects,
            "manifest_key": (
                material.popo_manifest_bucket,
                material.popo_manifest_object,
            ),
            "manifest_sha256": _sha(manifest_bytes),
            "mineru_manifest_sha256": mineru_manifest_sha,
        }
    finally:
        app.dependency_overrides.clear()
        material_db.close()
        workflow_db.close()


def _batch_body(ctx: dict, *, manifest_sha256: str | None = None, release_sha256: str = "b" * 64):
    return {
        "sources": [
            {
                "material_pk": ctx["material"].id,
                "popo_manifest_sha256": manifest_sha256 or ctx["manifest_sha256"],
            }
        ],
        "skill_release_version": "3.0.0-rc.1",
        "skill_release_sha256": release_sha256,
        "payload": {"shadow": True},
    }


def _create_job(ctx: dict) -> dict:
    response = ctx["client"].post("/api/workflow-v3/jobs/batch", json=_batch_body(ctx))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created"] == 1
    return body["results"][0]["job"]


def test_feature_flag_is_disabled_by_default_and_contracts_remain_readable(monkeypatch):
    monkeypatch.delenv("WORKFLOW_V3_ENABLED", raising=False)
    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")
    test_app.dependency_overrides[get_user_id] = lambda: "u1"
    test_app.dependency_overrides[workflow_v3_db_dependency] = lambda: None
    client = TestClient(test_app)

    health = client.get("/api/workflow-v3/health")
    contracts = client.get("/api/workflow-v3/contracts")
    jobs = client.get("/api/workflow-v3/jobs")

    assert health.status_code == 200
    assert health.json()["enabled"] is False
    assert health.json()["execution_enabled"] is False
    assert contracts.status_code == 200
    assert contracts.json()["enabled"] is False
    assert jobs.status_code == 503
    assert "尚未启用" in jobs.json()["detail"]


def test_missing_schema_returns_503_without_auto_creating_tables(tmp_path, monkeypatch):
    database_path = tmp_path / "unprovisioned.db"
    monkeypatch.setenv("WORKFLOW_V3_ENABLED", "true")
    monkeypatch.setenv("WORKFLOW_V3_DATABASE_URL", f"sqlite:///{database_path}")
    workflow_v3_session_factory.cache_clear()
    workflow_v3_engine.cache_clear()
    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")
    test_app.dependency_overrides[get_user_id] = lambda: "u1"
    try:
        response = TestClient(test_app).get("/api/workflow-v3/jobs")
        assert response.status_code == 503
        assert "missing Worker V3 tables" in response.json()["detail"]
        engine = create_engine(f"sqlite:///{database_path}")
        assert inspect(engine).get_table_names() == []
    finally:
        workflow_v3_session_factory.cache_clear()
        workflow_v3_engine.cache_clear()


def test_batch_requires_registered_release_and_exact_frozen_manifest_hash(v3_api):
    unknown = v3_api["client"].post(
        "/api/workflow-v3/jobs/batch",
        json=_batch_body(v3_api, release_sha256="f" * 64),
    )
    assert unknown.status_code == 404

    drift = v3_api["client"].post(
        "/api/workflow-v3/jobs/batch",
        json=_batch_body(v3_api, manifest_sha256="0" * 64),
    )
    assert drift.status_code == 200
    assert drift.json()["failed"] == 1
    assert drift.json()["results"][0]["error_code"] == "popo_source_not_frozen_or_drifted"
    assert "已漂移" in drift.json()["results"][0]["error"]

    created = v3_api["client"].post(
        "/api/workflow-v3/jobs/batch",
        json=_batch_body(v3_api),
    )
    assert created.status_code == 200
    result = created.json()
    assert result["created"] == 1
    job = result["results"][0]["job"]
    assert job["source_popo_manifest"]["sha256"] == v3_api["manifest_sha256"]
    assert job["payload"]["source_evidence"]["popo_frozen_marker"]["sha256"]
    assert job["payload"]["source_evidence"]["source_pdf"] == {
        "bucket": "eduassets-input",
        "object": "source.pdf",
        "sha256": "a" * 64,
        "size_bytes": 12345,
    }
    assert (
        job["payload"]["source_evidence"]["mineru_manifest"]["sha256"]
        == v3_api["mineru_manifest_sha256"]
    )
    assert len(job["payload"]["source_evidence"]["artifacts"]) == 7
    assert job["payload"]["source_evidence"]["input_set_sha256"]
    assert job["payload"]["source_evidence"]["review_asset"] == {
        "id": str(v3_api["review_asset"].id),
        "bucket": v3_api["material"].popo_manifest_bucket,
        "object": v3_api["material"].popo_manifest_object,
        "sha256": v3_api["manifest_sha256"],
    }
    assert job["payload"]["source_evidence"]["filename"] == "source.pdf"
    assert job["payload"]["source_evidence"]["material_pk"] == str(
        v3_api["material"].id
    )
    assert job["filename"] == "source.pdf"
    assert job["material_id"] == "pdf-v3-source"
    assert job["source_pdf_sha256"] == "a" * 64
    assert job["review_asset_id"] == str(v3_api["review_asset"].id)
    assert job["source_identity"]["verified"] is True
    assert job["payload"]["submission_path"] == "public_ui"


def test_batch_fails_when_material_review_asset_is_not_exact(v3_api):
    with v3_api["material_factory"]() as db:
        review = db.get(ReviewAsset, v3_api["review_asset"].id)
        review.manifest_object = "wrong/run/manifest.json"
        db.commit()
    response = v3_api["client"].post(
        "/api/workflow-v3/jobs/batch",
        json=_batch_body(v3_api),
    )
    assert response.status_code == 200
    assert response.json()["failed"] == 1
    assert (
        response.json()["results"][0]["error_code"]
        == "popo_source_not_frozen_or_drifted"
    )
    assert "ReviewAsset" in response.json()["results"][0]["error"]


def test_eligible_sources_exposes_exact_frozen_identities(v3_api):
    response = v3_api["client"].get("/api/workflow-v3/sources/eligible")
    assert response.status_code == 200
    assert response.json()["items"] == [
        {
            "material_pk": str(v3_api["material"].id),
            "material_id": "pdf-v3-source",
            "filename": "source.pdf",
            "size_bytes": 12345,
            "page_count": 0,
            "mineru_run_id": "mineru-run",
            "mineru_manifest_sha256": _sha(
                v3_api["objects"][
                    (
                        v3_api["material"].mineru_manifest_bucket,
                        v3_api["material"].mineru_manifest_object,
                    )
                ]
            ),
            "mineru_frozen_marker_sha256": response.json()["items"][0][
                "mineru_frozen_marker_sha256"
            ],
            "popo_run_id": "popo-run",
            "popo_manifest_sha256": v3_api["manifest_sha256"],
            "popo_frozen_marker_sha256": response.json()["items"][0][
                "popo_frozen_marker_sha256"
            ],
            "source_pdf_sha256": "a" * 64,
            "input_set_sha256": response.json()["items"][0]["input_set_sha256"],
            "eligible": True,
            "error": "",
        }
    ]


def test_jobs_are_owner_scoped_for_list_detail_retry_and_acceptance(v3_api):
    job = _create_job(v3_api)
    public_id = job["id"]
    v3_api["actor"]["user_id"] = "u2"

    assert v3_api["client"].get("/api/workflow-v3/jobs").json()["items"] == []
    assert v3_api["client"].get(f"/api/workflow-v3/jobs/{public_id}").status_code == 404
    assert v3_api["client"].post(f"/api/workflow-v3/jobs/{public_id}/retry").status_code == 404
    assert (
        v3_api["client"]
        .post(
            f"/api/workflow-v3/jobs/{public_id}/cancel",
            json={"reason": "无权取消其他用户任务"},
        )
        .status_code
        == 404
    )
    assert (
        v3_api["client"]
        .post(
            f"/api/workflow-v3/jobs/{public_id}/human-acceptance",
            json={
                "accepted": True,
                "output_id": 1,
                "manifest_sha256": "f" * 64,
            },
        )
        .status_code
        == 404
    )


def test_job_list_paginates_and_filters_machine_status(v3_api):
    _create_job(v3_api)
    listed = v3_api["client"].get(
        "/api/workflow-v3/jobs",
        params={"page": 1, "page_size": 20, "machine_status": "queued"},
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert len(listed.json()["items"]) == 1
    assert listed.json()["items"][0]["machine_status"] == "queued"
    empty = v3_api["client"].get(
        "/api/workflow-v3/jobs",
        params={"machine_status": "succeeded"},
    )
    assert empty.status_code == 200
    assert empty.json()["total"] == 0


def test_job_identity_is_frozen_across_duplicate_names_and_material_rename(
    v3_api,
):
    first = _create_job(v3_api)
    with v3_api["workflow_factory"]() as db:
        second, created = create_workflow_job(
            db,
            user_id="u1",
            material_pk=v3_api["material"].id + 100,
            material_id="pdf-v3-second",
            source_popo_bucket="eduassets-minerupopo",
            source_popo_object="minerupopo/pdf-v3-second/popo-run/manifest.json",
            source_popo_sha256="9" * 64,
            skill_release_version="3.0.0-rc.1",
            skill_release_sha256="b" * 64,
            template_sha256="d" * 64,
            payload={
                "source_evidence": {
                    "filename": "source.pdf",
                    "material_pk": str(v3_api["material"].id + 100),
                    "material_id": "pdf-v3-second",
                    "source_pdf": {
                        "bucket": "eduassets-input",
                        "object": "second/source.pdf",
                        "sha256": "8" * 64,
                        "size_bytes": 999,
                    },
                    "popo_manifest": {
                        "bucket": "eduassets-minerupopo",
                        "object": "minerupopo/pdf-v3-second/popo-run/manifest.json",
                        "sha256": "9" * 64,
                    },
                    "review_asset": {
                        "id": "999",
                        "bucket": "eduassets-minerupopo",
                        "object": "minerupopo/pdf-v3-second/popo-run/manifest.json",
                        "sha256": "9" * 64,
                    },
                }
            },
        )
        assert created is True
        db.commit()
        second_id = second.public_id
    with v3_api["material_factory"]() as db:
        material = db.get(Material, v3_api["material"].id)
        material.filename = "renamed-after-v3-job.pdf"
        db.commit()

    listed = v3_api["client"].get(
        "/api/workflow-v3/jobs",
        params={"page": 1, "page_size": 20},
    )
    assert listed.status_code == 200
    by_id = {row["id"]: row for row in listed.json()["items"]}
    assert by_id[first["id"]]["filename"] == "source.pdf"
    assert by_id[first["id"]]["material_id"] == "pdf-v3-source"
    assert by_id[first["id"]]["source_identity"]["verified"] is True
    assert by_id[second_id]["filename"] == "source.pdf"
    assert by_id[second_id]["material_id"] == "pdf-v3-second"
    assert by_id[second_id]["source_pdf_sha256"] == "8" * 64
    assert by_id[second_id]["review_asset_id"] == "999"
    detail = v3_api["client"].get(
        f"/api/workflow-v3/jobs/{first['id']}"
    )
    assert detail.status_code == 200
    assert detail.json()["filename"] == "source.pdf"
    assert detail.json()["filename"] != "renamed-after-v3-job.pdf"


def test_detail_returns_exact_output_review_binding_and_download_links(
    v3_api,
    monkeypatch,
):
    job = _create_job(v3_api)
    manifest = {
        "schema": "luceon.workflow.artifact-manifest/v1",
        "material_id": "pdf-v3-source",
        "popo_run_id": "popo-run",
        "output_run_id": job["id"],
        "origin": "worker_v3",
        "objects": {
            "compiled_pdf": "files/main.pdf",
            "package_zip": "files/latex-project.zip",
            "compile_report": "files/compile-report.json",
        },
        "volumes": [
            {
                "volume_id": "volume-1",
                "label": "第 1 卷",
                "objects": {
                    "compiled_pdf": "files/main.pdf",
                    "package_zip": "files/latex-project.zip",
                    "compile_report": "files/compile-report.json",
                },
            }
        ],
    }
    manifest_bytes = json.dumps(manifest).encode()
    manifest_sha = _sha(manifest_bytes)
    manifest_bucket = "eduassets-elegantbook"
    manifest_object = f"elegantbook/v3/{job['id']}/manifest.json"
    with v3_api["material_factory"]() as db:
        output = MaterialOutput(
            user_id="u1",
            material_pk=v3_api["material"].id,
            material_id="pdf-v3-source",
            review_asset_id=v3_api["review_asset"].id,
            output_type="elegantbook",
            origin="worker_v3",
            status="candidate",
            quality_status="ready_for_user_acceptance",
            is_current=False,
            manifest_bucket=manifest_bucket,
            manifest_object=manifest_object,
            output_run_id=job["id"],
            popo_run_id="popo-run",
            metadata_json=json.dumps({"manifest_sha256": manifest_sha}),
        )
        db.add(output)
        db.commit()
        output_id = output.id
    with v3_api["workflow_factory"]() as db:
        persisted = (
            db.query(WorkflowV3Job)
            .filter(WorkflowV3Job.public_id == job["id"])
            .one()
        )
        db.add(
            WorkflowV3ProjectionOutbox(
                workflow_job_id=persisted.id,
                final_promotion_id=999,
                idempotency_key="exact-output-review-binding",
                event_kind="final_ready",
                status="applied",
                target_kind="material_output",
                payload_json="{}",
                attempt_count=1,
                applied_identity="7" * 64,
                projected_output_id=output_id,
                projected_manifest_bucket=manifest_bucket,
                projected_manifest_object=manifest_object,
                projected_manifest_sha256=manifest_sha,
            )
        )
        db.commit()

    from app.services import material_outputs as material_outputs_service

    monkeypatch.setattr(
        material_outputs_service,
        "read_object",
        lambda bucket, object_name: manifest_bytes
        if (bucket, object_name) == (manifest_bucket, manifest_object)
        else (_ for _ in ()).throw(FileNotFoundError((bucket, object_name))),
    )
    response = v3_api["client"].get(
        f"/api/workflow-v3/jobs/{job['id']}"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["final_output_id"] == str(output_id)
    assert body["review_entry"] == {
        "available": True,
        "review_asset_id": str(v3_api["review_asset"].id),
        "final_output_id": str(output_id),
        "compare_url": (
            f"/review/compare?asset_id={v3_api['review_asset'].id}"
            f"&output_id={output_id}"
        ),
        "compare_api_url": (
            f"/api/review/assets/{v3_api['review_asset'].id}/latex_compare"
            f"?output_id={output_id}"
        ),
    }
    volumes = body["delivery_assets"]["projected_candidate"]["volumes"]
    assert len(volumes) == 1
    assert f"output_id={output_id}" in volumes[0]["zip_url"]
    assert f"output_id={output_id}" in volumes[0]["pdf_url"]
    assert "files%2Flatex-project.zip" in volumes[0]["zip_url"]
    assert "files%2Fmain.pdf" in volumes[0]["pdf_url"]
    assert body["delivery_assets"]["formal"]["volumes"] == []


def test_candidate_download_links_extract_only_hash_bound_zip_and_pdf(
    monkeypatch,
):
    zip_bytes = b"candidate-zip"
    pdf_bytes = b"%PDF-1.4\ncandidate-pdf\n%%EOF\n"
    recompile = {
        "volumes": [
            {
                "volume_id": "volume-a",
                "delivery_zip": {
                    "path": "delivery/volume-a.zip",
                    "sha256": _sha(zip_bytes),
                    "size_bytes": len(zip_bytes),
                },
                "reviewed_pdf": {
                    "path": "delivery/volume-a.pdf",
                    "sha256": _sha(pdf_bytes),
                    "size_bytes": len(pdf_bytes),
                },
            }
        ]
    }
    recompile_bytes = json.dumps(recompile).encode()
    content = {
        "job_id": "job-candidate-download",
        "stage_key": "ready_for_user_acceptance",
        "artifact_kind": "worker-v3-ready-for-user-acceptance-candidate",
        "files": [
            {
                "path": "delivery/volume-a.zip",
                "sha256": _sha(zip_bytes),
                "size_bytes": len(zip_bytes),
            },
            {
                "path": "delivery/volume-a.pdf",
                "sha256": _sha(pdf_bytes),
                "size_bytes": len(pdf_bytes),
            },
            {
                "path": "manifests/delivery_recompile.json",
                "sha256": _sha(recompile_bytes),
                "size_bytes": len(recompile_bytes),
            },
        ],
    }
    archive_io = io.BytesIO()
    with tarfile.open(fileobj=archive_io, mode="w:gz") as archive:
        for name, payload in (
            ("candidate-content-manifest.json", json.dumps(content).encode()),
            ("manifests/delivery_recompile.json", recompile_bytes),
            ("delivery/volume-a.zip", zip_bytes),
            ("delivery/volume-a.pdf", pdf_bytes),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    bundle = archive_io.getvalue()
    detail = {
        "id": "job-candidate-download",
        "user_id": "u1",
        "filename": "same-name.pdf",
        "material_id": "pdf-candidate",
        "stages": [
            {
                "id": "12",
                "stage_key": "ready_for_user_acceptance",
                "attempt": 1,
                "generation": 2,
                "machine_status": "succeeded",
                "spec_status": "passed",
                "promotion": {"candidate_id": "42"},
                "candidates": [
                    {
                        "id": "42",
                        "artifact_kind":
                            "worker-v3-ready-for-user-acceptance-candidate",
                        "status": "promoted",
                        "bucket": "worker-v3-candidates",
                        "object": "v3/job/sha/artifact",
                        "sha256": _sha(bundle),
                        "size_bytes": len(bundle),
                    }
                ],
            }
        ],
    }
    monkeypatch.setattr(
        workflow_v3_api,
        "read_object",
        lambda bucket, object_name: bundle,
    )
    rows = workflow_v3_api._candidate_delivery_rows(detail)
    assert rows == [
        {
            "volume_id": "volume-a",
            "label": "第 1 卷",
            "zip_url": (
                "/api/workflow-v3/jobs/job-candidate-download/"
                "candidate-delivery?kind=zip&volume_id=volume-a"
            ),
            "pdf_url": (
                "/api/workflow-v3/jobs/job-candidate-download/"
                "candidate-delivery?kind=pdf&volume_id=volume-a"
            ),
        }
    ]
    monkeypatch.setattr(
        workflow_v3_api,
        "_owned_job",
        lambda workflow_db, public_id, user_id: detail,
    )
    response = workflow_v3_api.candidate_delivery(
        public_id=detail["id"],
        kind="zip",
        volume_id="volume-a",
        _enabled=None,
        user_id="u1",
        workflow_db=None,
    )
    assert response.body == zip_bytes
    assert response.headers["x-content-sha256"] == _sha(zip_bytes)


def test_retry_is_current_failed_stage_only_and_fails_closed_on_source_drift(v3_api):
    first = _create_job(v3_api)
    public_id = first["id"]
    workflow_db = v3_api["workflow_factory"]()
    try:
        job = workflow_db.query(WorkflowV3Job).filter(WorkflowV3Job.public_id == public_id).one()
        stage = (
            workflow_db.query(WorkflowV3StageRun)
            .filter(
                WorkflowV3StageRun.workflow_job_id == job.id,
                WorkflowV3StageRun.stage_key == job.current_stage_key,
            )
            .one()
        )
        job.machine_status = "failed"
        job.error_code = "fixture_failure"
        stage.machine_status = "failed"
        stage.error_code = "fixture_failure"
        workflow_db.commit()
    finally:
        workflow_db.close()

    retry = v3_api["client"].post(f"/api/workflow-v3/jobs/{public_id}/retry")
    assert retry.status_code == 200
    assert retry.json()["retried_stage"]["stage_key"] == "intake_snapshot"
    assert retry.json()["retried_stage"]["attempt"] == 2

    workflow_db = v3_api["workflow_factory"]()
    try:
        job = workflow_db.query(WorkflowV3Job).filter(WorkflowV3Job.public_id == public_id).one()
        stage = (
            workflow_db.query(WorkflowV3StageRun)
            .filter(
                WorkflowV3StageRun.workflow_job_id == job.id,
                WorkflowV3StageRun.stage_key == job.current_stage_key,
            )
            .order_by(WorkflowV3StageRun.attempt.desc())
            .first()
        )
        job.machine_status = "failed"
        stage.machine_status = "failed"
        workflow_db.commit()
    finally:
        workflow_db.close()
    v3_api["objects"][v3_api["manifest_key"]] = b'{"material_id":"pdf-v3-source","run_id":"popo-run","drift":true}'

    blocked = v3_api["client"].post(f"/api/workflow-v3/jobs/{public_id}/retry")
    assert blocked.status_code == 409
    assert "已漂移" in blocked.json()["detail"]


def test_admin_resolution_api_requires_bound_manifest_and_replays_idempotently(
    v3_api,
):
    created = _create_job(v3_api)
    public_id = created["id"]
    with v3_api["workflow_factory"]() as db:
        job, stage, execution = claim_current_stage(
            db,
            public_id,
            producer_identity="producer-review-api",
            idempotency_key="lease-review-api",
            runtime_identity_sha256="e" * 64,
        )
        _job, _stage, candidate = submit_candidate(
            db,
            public_id,
            execution_id=execution.id,
            idempotency_key="candidate-review-api",
            artifact_kind="intake",
            bucket="worker-v3-candidates",
            object_name="review-api/original.tar",
            sha256="7" * 64,
            size_bytes=10,
        )
        finding = {
            "code": "source_scope_ambiguous",
            "blocking": True,
            "responsible_stage": stage.stage_key,
            "recovery_stage": stage.stage_key,
            "evidence_refs": [
                {
                    "path": "evidence/page-1.png",
                    "sha256": "8" * 64,
                }
            ],
            "handoff": {
                "summary": "Source scope is ambiguous.",
                "required_action": "Authorize the exact source scope.",
                "resume_stage": stage.stage_key,
            },
        }
        job, _stage, evaluation = record_evaluation(
            db,
            public_id,
            candidate_id=candidate.id,
            idempotency_key="evaluation-review-api",
            evaluator_identity="evaluator-review-api",
            evaluator_version="v1",
            policy_sha256="9" * 64,
            decision="needs_review",
            gate_results={},
            findings=[finding],
        )
        manifest = {
            "schema_version": "luceon.worker-v3.review-resolution/v1",
            "job_id": public_id,
            "evaluation": {
                "id": str(evaluation.id),
                "sha256": evaluation_fingerprint(evaluation, candidate),
                "candidate_id": str(candidate.id),
                "candidate_sha256": candidate.sha256,
                "finding_fingerprints": [finding_fingerprint(finding)],
            },
            "authorization": {
                "authorized_by": "admin@luceon.local",
                "decision": "revise",
            },
            "blocker_resolutions": [
                {
                    "finding_fingerprint": finding_fingerprint(finding),
                    "disposition": "resolved_for_revision",
                    "rationale": "The source-bound scope was explicitly selected.",
                }
            ],
            "recovery_stage": stage.stage_key,
            "created_at": "2026-07-26T12:00:00Z",
        }
        db.commit()

    assert (
        v3_api["client"].post(f"/api/workflow-v3/jobs/{public_id}/retry").status_code
        == 409
    )
    app.dependency_overrides[require_pipeline_admin] = lambda: User(
        id=999,
        email="admin@luceon.local",
        password_hash="not-used",
    )
    manifest_bytes = json.dumps(manifest, sort_keys=True).encode()
    manifest_ref = {
        "bucket": "worker-v3-resolutions",
        "object": f"{public_id}/resolution.json",
        "sha256": _sha(manifest_bytes),
        "size_bytes": len(manifest_bytes),
    }
    v3_api["objects"][
        (manifest_ref["bucket"], manifest_ref["object"])
    ] = manifest_bytes
    request = {
        "idempotency_key": "resolution-request-api",
        "resolution_manifest": manifest_ref,
    }

    response = v3_api["client"].post(
        f"/api/workflow-v3/jobs/{public_id}/review-resolution",
        json=request,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["job"]["current_generation"] == 2
    assert body["recovery_stage"]["generation"] == 2
    assert (
        body["recovery_stage"]["review_resolution"]["sha256"]
        == manifest_ref["sha256"]
    )
    assert body["candidate"] is None

    replay = v3_api["client"].post(
        f"/api/workflow-v3/jobs/{public_id}/review-resolution",
        json=request,
    )
    assert replay.status_code == 200
    assert (
        replay.json()["review_resolution"]["id"]
        == body["review_resolution"]["id"]
    )


def test_owner_can_cancel_active_job_and_late_retry_is_rejected(v3_api):
    job = _create_job(v3_api)
    public_id = job["id"]
    cancelled = v3_api["client"].post(
        f"/api/workflow-v3/jobs/{public_id}/cancel",
        json={"reason": "用户主动停止当前精修任务"},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["job"]["machine_status"] == "cancelled"
    assert cancelled.json()["cancelled_stage"]["machine_status"] == "cancelled"

    repeated = v3_api["client"].post(
        f"/api/workflow-v3/jobs/{public_id}/cancel",
        json={"reason": "确认幂等取消当前精修任务"},
    )
    assert repeated.status_code == 200
    assert repeated.json()["job"]["machine_status"] == "cancelled"
    assert v3_api["client"].post(f"/api/workflow-v3/jobs/{public_id}/retry").status_code == 409


def test_human_acceptance_cannot_be_fabricated_by_status_flags(v3_api):
    job = _create_job(v3_api)
    public_id = job["id"]

    not_ready = v3_api["client"].post(
        f"/api/workflow-v3/jobs/{public_id}/human-acceptance",
        json={
            "accepted": True,
            "output_id": 1,
            "manifest_sha256": "f" * 64,
        },
    )
    assert not_ready.status_code == 409

    workflow_db = v3_api["workflow_factory"]()
    try:
        row = workflow_db.query(WorkflowV3Job).filter(WorkflowV3Job.public_id == public_id).one()
        row.machine_status = "succeeded"
        row.spec_status = "passed"
        row.readiness_status = "ready"
        row.human_acceptance_status = "pending"
        workflow_db.commit()
    finally:
        workflow_db.close()

    before = v3_api["client"].get(f"/api/workflow-v3/jobs/{public_id}").json()
    assert before["machine_status"] == "succeeded"
    assert before["spec_status"] == "passed"
    assert before["readiness_status"] == "ready"
    assert before["human_acceptance_status"] == "pending"
    assert before["spec_passed"] is True
    assert before["spec_ready_for_projection"] is True
    assert before["ready_for_user_acceptance"] is False
    assert before["delivery_status"] == "projecting"
    assert before["projection_errors"] == []
    assert before["human_acceptance_decision_recorded"] is False
    assert before["human_acceptance_effective"] is False
    assert before["human_accepted"] is False

    accepted = v3_api["client"].post(
        f"/api/workflow-v3/jobs/{public_id}/human-acceptance",
        json={
            "accepted": True,
            "output_id": 1,
            "manifest_sha256": "f" * 64,
            "reason": "全页人工验收通过",
        },
    )
    assert accepted.status_code == 409
    assert "exact applied formal output" in accepted.json()["detail"]


def test_installed_release_registration_is_admin_only(monkeypatch):
    monkeypatch.setenv("WORKFLOW_V3_ENABLED", "true")
    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")

    def deny_admin():
        raise HTTPException(status_code=403, detail="仅管线管理员可执行异常恢复")

    test_app.dependency_overrides[require_pipeline_admin] = deny_admin
    test_app.dependency_overrides[workflow_v3_db_dependency] = lambda: None
    response = TestClient(test_app).post(
        "/api/workflow-v3/admin/releases/register-installed",
        json={"release_id": "worker-v3-test-rc1"},
    )

    assert response.status_code == 403


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        (
            "post",
            "/api/workflow-v3/jobs/job-1/review-resolution",
            {
                "idempotency_key": "resolution-request-1",
                "resolution_manifest": {
                    "bucket": "worker-v3-resolutions",
                    "object": "job-1/resolution.json",
                    "sha256": "a" * 64,
                    "size_bytes": 1,
                },
            },
        ),
        (
            "post",
            "/api/workflow-v3/admin/jobs/job-1/projection-outbox/1/retry",
            None,
        ),
    ],
)
def test_admin_recovery_control_plane_is_admin_only(monkeypatch, method, path, payload):
    monkeypatch.setenv("WORKFLOW_V3_ENABLED", "true")
    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")

    def deny_admin():
        raise HTTPException(status_code=403, detail="仅管线管理员可执行异常恢复")

    test_app.dependency_overrides[require_pipeline_admin] = deny_admin
    test_app.dependency_overrides[get_db] = lambda: None
    test_app.dependency_overrides[workflow_v3_db_dependency] = lambda: None
    client = TestClient(test_app)

    response = getattr(client, method)(path, json=payload) if payload else getattr(
        client, method
    )(path)

    assert response.status_code == 403


def test_admin_projection_retry_is_single_job_single_outbox_and_maps_conflicts(
    v3_api,
):
    job = _create_job(v3_api)
    with v3_api["workflow_factory"]() as db:
        persisted = (
            db.query(WorkflowV3Job)
            .filter(WorkflowV3Job.public_id == job["id"])
            .one()
        )
        outbox = WorkflowV3ProjectionOutbox(
            workflow_job_id=persisted.id,
            final_promotion_id=999,
            idempotency_key="projection-retry-api",
            event_kind="final_ready",
            status="failed",
            target_kind="material_output",
            payload_json="{}",
            attempt_count=1,
            last_error="transient projection failure",
        )
        db.add(outbox)
        db.commit()
        outbox_id = outbox.id
    app.dependency_overrides[require_pipeline_admin] = lambda: User(
        id=999,
        email="pipeline-admin@example.com",
        password_hash="unused",
    )

    retried = v3_api["client"].post(
        f"/api/workflow-v3/admin/jobs/{job['id']}"
        f"/projection-outbox/{outbox_id}/retry"
    )
    assert retried.status_code == 200
    assert retried.json()["outbox"]["id"] == str(outbox_id)
    assert retried.json()["outbox"]["status"] == "pending"
    assert "pipeline-admin@example.com" in retried.json()["outbox"]["last_error"]

    conflict = v3_api["client"].post(
        f"/api/workflow-v3/admin/jobs/{job['id']}"
        f"/projection-outbox/{outbox_id}/retry"
    )
    assert conflict.status_code == 409
    assert "only a failed projection" in conflict.json()["detail"]
    missing_outbox = v3_api["client"].post(
        f"/api/workflow-v3/admin/jobs/{job['id']}"
        "/projection-outbox/99999/retry"
    )
    assert missing_outbox.status_code == 404
    missing_job = v3_api["client"].post(
        f"/api/workflow-v3/admin/jobs/not-a-job"
        f"/projection-outbox/{outbox_id}/retry"
    )
    assert missing_job.status_code == 404


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/workflow-v3/admin/expert-policy"),
        ("post", "/api/workflow-v3/jobs/job-1/expert-runs"),
        ("post", "/api/workflow-v3/admin/expert-runs/expert-1/cancel"),
        ("post", "/api/workflow-v3/admin/expert-runs/expert-1/resume"),
    ],
)
def test_codex_expert_production_routes_do_not_exist(v3_api, method, path):
    response = getattr(v3_api["client"], method)(path)
    assert response.status_code == 404
