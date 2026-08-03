import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.models.base import Base
from app.models.material import Material
from app.models.review_asset import ReviewAsset
from app.utils.user_dep import get_user_id
from main import app


class FakeObject:
    def __init__(self, content: bytes):
        self.content = content

    def read(self):
        return self.content

    def stream(self, size: int):
        for offset in range(0, len(self.content), size):
            yield self.content[offset : offset + size]

    def close(self):
        return None

    def release_conn(self):
        return None


class FakeMinio:
    def __init__(self, objects: dict[tuple[str, str], bytes]):
        self.objects = objects

    def stat_object(self, bucket: str, object_name: str):
        try:
            content = self.objects[(bucket, object_name)]
        except KeyError as exc:
            raise FileNotFoundError(object_name) from exc
        return SimpleNamespace(size=len(content), content_type="")

    def get_object(self, bucket: str, object_name: str, offset: int = 0, length: int | None = None):
        try:
            content = self.objects[(bucket, object_name)]
        except KeyError as exc:
            raise FileNotFoundError(object_name) from exc
        if length is not None:
            content = content[offset : offset + length]
        elif offset:
            content = content[offset:]
        return FakeObject(content)

    def list_objects(self, bucket: str, prefix: str = "", recursive: bool = True):
        _ = recursive
        for object_bucket, object_name in sorted(self.objects):
            if object_bucket == bucket and object_name.startswith(prefix):
                yield SimpleNamespace(object_name=object_name)


def make_client(monkeypatch, objects: dict[tuple[str, str], bytes]):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    testing_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    def fake_read_object(bucket: str, object_name: str) -> bytes:
        try:
            return objects[(bucket, object_name)]
        except KeyError as exc:
            raise FileNotFoundError(object_name) from exc

    def fake_object_exists(bucket: str, object_name: str) -> bool:
        return (bucket, object_name) in objects

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_user_id] = lambda: "u1"
    fake_minio = FakeMinio(objects)
    monkeypatch.setattr("app.api.review.read_object", fake_read_object)
    monkeypatch.setattr("app.api.review.object_exists", fake_object_exists)
    monkeypatch.setattr("app.api.review.minio_client", fake_minio)
    monkeypatch.setattr("app.services.codex_elegantbook.read_object", fake_read_object)
    monkeypatch.setattr("app.services.codex_elegantbook.object_exists", fake_object_exists)
    monkeypatch.setattr("app.services.codex_elegantbook.minio_client", fake_minio)
    monkeypatch.setattr("app.services.material_outputs.read_object", fake_read_object)

    db = testing_session()
    asset = ReviewAsset(
        user_id="u1",
        title="Demo",
        input_filename="Demo.pdf",
        review_stage="compare",
        material_id="pdf-demo",
        run_id="popo-run",
        manifest_bucket="eduassets-minerupopo",
        manifest_object="minerupopo/pdf-demo/popo-run/manifest.json",
        manifest_json=json.dumps({"material_id": "pdf-demo"}),
        review_status="pending",
    )
    db.add(asset)
    db.flush()
    material = Material(
        user_id="u1",
        material_id="pdf-demo",
        title="Demo",
        filename="Demo.pdf",
        input_bucket="eduassets-input",
        input_object="Demo.pdf",
        source_type="imported",
        stage_status="latex_done",
        pipeline_status="idle",
        popo_manifest_bucket="eduassets-minerupopo",
        popo_manifest_object="minerupopo/pdf-demo/popo-run/manifest.json",
        popo_run_id="popo-run",
        latex_manifest_bucket="eduassets-latex",
        latex_manifest_object="latex/pdf-demo/popo-run/manifest.json",
        latex_run_id="popo-run",
        review_asset_id=asset.id,
    )
    db.add(material)
    db.commit()
    asset_id = asset.id
    db.close()
    return TestClient(app), asset_id


def teardown_function():
    app.dependency_overrides.clear()


def attach_source_pdf(asset_id: int):
    db = next(app.dependency_overrides[get_db]())
    asset = db.query(ReviewAsset).filter(ReviewAsset.id == asset_id).one()
    asset.input_pdf_bucket = "eduassets-input"
    asset.input_pdf_object = "Demo.pdf"
    db.commit()
    db.close()


def test_compare_uses_legacy_selfloop_when_codex_output_missing(monkeypatch):
    legacy_manifest = {
        "schema": "luceon-latex-material/v1",
        "stage": "latex",
        "material_id": "pdf-demo",
        "run_id": "popo-run",
        "origin": "legacy_selfloop",
        "objects": {
            "compiled_pdf": "compiled.pdf",
            "package_zip": "latex-project.zip",
            "compile_report": "compile_report.json",
        },
    }
    objects = {
        ("eduassets-input", "Demo.pdf"): b"%PDF-source\n",
        ("eduassets-latex", "latex/pdf-demo/popo-run/manifest.json"): json.dumps(legacy_manifest).encode(),
        ("eduassets-latex", "latex/pdf-demo/popo-run/compiled.pdf"): b"%PDF-legacy\n",
        ("eduassets-latex", "latex/pdf-demo/popo-run/latex-project.zip"): b"PK-legacy",
        ("eduassets-latex", "latex/pdf-demo/popo-run/compile_report.json"): b'{"success": true}',
    }
    client, asset_id = make_client(monkeypatch, objects)

    response = client.get(f"/api/review/assets/{asset_id}/latex_compare")

    assert response.status_code == 200
    body = response.json()
    assert body["stage"] == "elegantbook"
    assert body["output_origin"] == "legacy_selfloop"
    assert body["manifest"]["bucket"] == "eduassets-latex"
    assert body["source_pdf_url"].endswith(f"/review/assets/{asset_id}/review_pdf")
    assert body["source_pdf_original_url"].endswith(f"/review/assets/{asset_id}/content")
    assert "stage=elegantbook" in body["latex_pdf_url"]

    artifact = client.get(f"/api/review/assets/{asset_id}/artifact", params={"stage": "elegantbook", "path": "compiled.pdf"})
    assert artifact.status_code == 200
    assert artifact.content == b"%PDF-legacy\n"


def test_compare_prefers_codex_refined_elegantbook_output(monkeypatch):
    legacy_manifest = {
        "schema": "luceon-latex-material/v1",
        "stage": "latex",
        "material_id": "pdf-demo",
        "run_id": "popo-run",
        "origin": "legacy_selfloop",
        "objects": {"compiled_pdf": "compiled.pdf", "package_zip": "latex-project.zip"},
    }
    codex_manifest = {
        "schema": "luceon-codex-elegantbook/v1",
        "material_id": "pdf-demo",
        "popo_run_id": "popo-run",
        "codex_run_id": "codex-001",
        "created_at": "2026-07-07T08:00:00Z",
        "stages": [{"skill": "refine-elegantbook-latex", "status": "passed"}],
        "objects": {
            "compiled_pdf": "compiled.pdf",
            "refined_overleaf_zip": "refined-overleaf.zip",
            "compile_report": "compile_report.json",
            "latex_polish_report": "latex_polish_report.md",
        },
    }
    objects = {
        ("eduassets-input", "Demo.pdf"): b"%PDF-source\n",
        ("eduassets-latex", "latex/pdf-demo/popo-run/manifest.json"): json.dumps(legacy_manifest).encode(),
        ("eduassets-latex", "latex/pdf-demo/popo-run/compiled.pdf"): b"%PDF-legacy\n",
        ("eduassets-elegantbook", "elegantbook/pdf-demo/popo-run/codex-001/manifest.json"): json.dumps(codex_manifest).encode(),
        ("eduassets-elegantbook", "elegantbook/pdf-demo/popo-run/codex-001/compiled.pdf"): b"%PDF-codex\n",
        ("eduassets-elegantbook", "elegantbook/pdf-demo/popo-run/codex-001/refined-overleaf.zip"): b"PK-codex",
        ("eduassets-elegantbook", "elegantbook/pdf-demo/popo-run/codex-001/compile_report.json"): b'{"success": true}',
        ("eduassets-elegantbook", "elegantbook/pdf-demo/popo-run/codex-001/latex_polish_report.md"): b"# Polish\n",
    }
    client, asset_id = make_client(monkeypatch, objects)

    response = client.get(f"/api/review/assets/{asset_id}/latex_compare")

    assert response.status_code == 200
    body = response.json()
    assert body["output_origin"] == "codex_refined"
    assert body["output_run_id"] == "codex-001"
    assert body["output_id"]
    assert len(body["available_outputs"]) == 2
    assert body["manifest"]["bucket"] == "eduassets-elegantbook"
    assert "stage=elegantbook&path=refined-overleaf.zip" in body["download_urls"]["package_zip"]
    assert "output_id=" in body["download_urls"]["package_zip"]

    artifact = client.get(f"/api/review/assets/{asset_id}/artifact", params={"stage": "elegantbook", "path": "compiled.pdf"})
    assert artifact.status_code == 200
    assert artifact.content == b"%PDF-codex\n"

    legacy_output_id = next(row["id"] for row in body["available_outputs"] if row["origin"] == "legacy_selfloop")
    legacy_response = client.get(f"/api/review/assets/{asset_id}/latex_compare", params={"output_id": legacy_output_id})
    assert legacy_response.status_code == 200
    legacy_body = legacy_response.json()
    assert legacy_body["output_origin"] == "legacy_selfloop"
    assert f"output_id={legacy_output_id}" in legacy_body["latex_pdf_url"]
    legacy_artifact = client.get(
        f"/api/review/assets/{asset_id}/artifact",
        params={"stage": "elegantbook", "path": "compiled.pdf", "output_id": legacy_output_id},
    )
    assert legacy_artifact.status_code == 200
    assert legacy_artifact.content == b"%PDF-legacy\n"


def test_compare_reads_worker_v2_immutable_artifact_manifest(monkeypatch):
    prefix = "worker-v2/pdf-demo/workflow-001/bounded_deepseek_polish_qa/attempt-1/digest"
    workflow_manifest = {
        "schema": "luceon.workflow.artifact-manifest/v1",
        "workflow_job_id": "workflow-001",
        "material_id": "pdf-demo",
        "stage": {"key": "bounded_deepseek_polish_qa", "version": "v1", "attempt": 1},
        "files": [
            {"path": "main.pdf", "sha256": "a" * 64},
            {"path": "latex-project.zip", "sha256": "b" * 64},
            {"path": "compile-report.json", "sha256": "c" * 64},
            {"path": "core-acceptance.json", "sha256": "d" * 64},
        ],
    }
    objects = {
        ("eduassets-input", "Demo.pdf"): b"%PDF-source\n",
        ("eduassets-elegantbook", f"{prefix}/manifest.json"): json.dumps(workflow_manifest).encode(),
        ("eduassets-elegantbook", f"{prefix}/files/main.pdf"): b"%PDF-worker-v2\n",
        ("eduassets-elegantbook", f"{prefix}/files/latex-project.zip"): b"PK-worker-v2",
        ("eduassets-elegantbook", f"{prefix}/files/compile-report.json"): b'{"byte_identical_final_passes":true}',
        ("eduassets-elegantbook", f"{prefix}/files/core-acceptance.json"): b'{"status":"passed"}',
    }
    client, asset_id = make_client(monkeypatch, objects)

    from app.models.material import MaterialOutput

    db = next(app.dependency_overrides[get_db]())
    db.add(MaterialOutput(
        user_id="u1", material_pk=1, material_id="pdf-demo", output_type="elegantbook",
        origin="worker_v2", status="promoted", quality_status="passed", is_current=True,
        manifest_bucket="eduassets-elegantbook", manifest_object=f"{prefix}/manifest.json",
        output_run_id="workflow-001", version_label="Worker V2",
    ))
    material = db.query(Material).filter(Material.material_id == "pdf-demo").one()
    material.latex_manifest_bucket = "eduassets-elegantbook"
    material.latex_manifest_object = f"{prefix}/manifest.json"
    material.latex_run_id = "workflow-001"
    db.commit()
    db.close()

    response = client.get(f"/api/review/assets/{asset_id}/latex_compare")

    assert response.status_code == 200
    body = response.json()
    assert body["output_origin"] == "worker_v2"
    assert body["output_run_id"] == "workflow-001"
    assert "path=files/main.pdf" in body["latex_pdf_url"]
    assert "path=files/latex-project.zip" in body["download_urls"]["package_zip"]
    artifact = client.get(body["latex_pdf_url"])
    assert artifact.status_code == 200
    assert artifact.content == b"%PDF-worker-v2\n"


def test_compare_preserves_worker_v3_identity_and_exposes_both_delivery_volumes(
    monkeypatch,
):
    prefix = "elegantbook/pdf-demo/popo-run/worker-v3-job"
    manifest = {
        "schema": "luceon.workflow.artifact-manifest/v1",
        "origin": "worker_v3",
        "workflow_version": "worker-v3.0.0-rc1",
        "workflow_job_id": "worker-v3-job",
        "material_id": "pdf-demo",
        "popo_run_id": "popo-run",
        "objects": {
            "compiled_pdf": "files/volume-1/main.pdf",
            "package_zip": "files/volume-1/latex-project.zip",
            "compile_report": "files/volume-1/compile-report.json",
        },
        "volumes": [
            {
                "volume_id": "volume-1",
                "label": "第 1 卷",
                "objects": {
                    "compiled_pdf": "files/volume-1/main.pdf",
                    "package_zip": "files/volume-1/latex-project.zip",
                    "compile_report": "files/volume-1/compile-report.json",
                },
            },
            {
                "volume_id": "volume-2",
                "label": "第 2 卷",
                "objects": {
                    "compiled_pdf": "files/volume-2/main.pdf",
                    "package_zip": "files/volume-2/latex-project.zip",
                    "compile_report": "files/volume-2/compile-report.json",
                },
            },
        ],
        "files": [
            {"path": "files/volume-1/main.pdf", "sha256": "1" * 64},
            {"path": "files/volume-1/latex-project.zip", "sha256": "2" * 64},
            {"path": "files/volume-1/compile-report.json", "sha256": "3" * 64},
            {"path": "files/volume-2/main.pdf", "sha256": "4" * 64},
            {"path": "files/volume-2/latex-project.zip", "sha256": "5" * 64},
            {"path": "files/volume-2/compile-report.json", "sha256": "6" * 64},
        ],
    }
    objects = {
        ("eduassets-input", "Demo.pdf"): b"%PDF-source\n",
        ("eduassets-elegantbook", f"{prefix}/manifest.json"): json.dumps(manifest).encode(),
        ("eduassets-elegantbook", f"{prefix}/files/volume-1/main.pdf"): b"%PDF-v3-1\n",
        ("eduassets-elegantbook", f"{prefix}/files/volume-1/latex-project.zip"): b"PK-v3-1",
        ("eduassets-elegantbook", f"{prefix}/files/volume-1/compile-report.json"): b'{"status":"succeeded"}',
        ("eduassets-elegantbook", f"{prefix}/files/volume-2/main.pdf"): b"%PDF-v3-2\n",
        ("eduassets-elegantbook", f"{prefix}/files/volume-2/latex-project.zip"): b"PK-v3-2",
        ("eduassets-elegantbook", f"{prefix}/files/volume-2/compile-report.json"): b'{"status":"succeeded"}',
    }
    client, asset_id = make_client(monkeypatch, objects)

    response = client.get(f"/api/review/assets/{asset_id}/latex_compare")

    assert response.status_code == 200
    body = response.json()
    assert body["output_origin"] == "worker_v3"
    assert body["output_run_id"] == "worker-v3-job"
    assert [row["volume_id"] for row in body["volumes"]] == [
        "volume-1",
        "volume-2",
    ]
    second = body["volumes"][1]
    assert "path=files/volume-2/main.pdf" in second["latex_pdf_url"]
    assert "path=files/volume-2/latex-project.zip" in second["download_urls"]["package_zip"]
    rendered = client.get(second["latex_pdf_url"], headers={"Range": "bytes=0-3"})
    assert rendered.status_code == 206
    assert rendered.content == b"%PDF"
    archive = client.get(second["download_urls"]["package_zip"])
    assert archive.status_code == 200
    assert archive.content == b"PK-v3-2"


def test_pdf_content_supports_range_and_conditional_cache(monkeypatch):
    objects = {("eduassets-input", "Demo.pdf"): b"%PDF-source-cache-test\n"}
    client, asset_id = make_client(monkeypatch, objects)
    attach_source_pdf(asset_id)

    response = client.get(f"/api/review/assets/{asset_id}/content", headers={"Range": "bytes=0-3"})

    assert response.status_code == 206
    assert response.content == b"%PDF"
    assert response.headers["content-range"] == f"bytes 0-3/{len(objects[('eduassets-input', 'Demo.pdf')])}"
    assert response.headers["etag"]
    cached = client.get(
        f"/api/review/assets/{asset_id}/content",
        headers={"If-None-Match": response.headers["etag"]},
    )
    assert cached.status_code == 304


def test_review_pdf_endpoint_serves_cached_compressed_pdf(monkeypatch, tmp_path):
    source = b"%PDF-large-source\n"
    preview_path = tmp_path / "review.pdf"
    preview_path.write_bytes(b"%PDF-compressed-review\n")
    objects = {("eduassets-input", "Demo.pdf"): source}
    client, asset_id = make_client(monkeypatch, objects)
    attach_source_pdf(asset_id)
    monkeypatch.setattr("app.api.review.REVIEW_PDF_MIN_SOURCE_BYTES", 1)
    monkeypatch.setattr(
        "app.api.review.ensure_review_pdf_preview_isolated",
        lambda *args, **kwargs: SimpleNamespace(
            path=preview_path,
            page_count=1,
            size=preview_path.stat().st_size,
            linearized=True,
        ),
    )

    response = client.get(f"/api/review/assets/{asset_id}/review_pdf", headers={"Range": "bytes=0-3"})

    assert response.status_code == 206
    assert response.content == b"%PDF"
    assert response.headers["x-review-pdf"] == "compressed"
    assert response.headers["x-review-pdf-linearized"] == "1"
    assert response.headers["x-review-pdf-original-length"] == str(len(source))
    cached = client.get(
        f"/api/review/assets/{asset_id}/review_pdf",
        headers={"If-None-Match": response.headers["etag"]},
    )
    assert cached.status_code == 304


def test_internal_latex_execution_and_workspace_routes_are_gone(monkeypatch):
    objects = {
        ("eduassets-input", "Demo.pdf"): b"%PDF-source\n",
        ("eduassets-latex", "latex/pdf-demo/popo-run/manifest.json"): b'{"objects":{"compiled_pdf":"compiled.pdf"}}',
    }
    client, asset_id = make_client(monkeypatch, objects)

    assert client.post("/api/materials/1/popo2latex/preflight").status_code == 410
    assert client.post("/api/materials/1/popo2latex/start", json={"force": True}).status_code == 410
    assert client.get(f"/api/review/assets/{asset_id}/latex_workspace").status_code == 410
    assert client.get(f"/api/review/assets/{asset_id}/overleaf").status_code == 410
