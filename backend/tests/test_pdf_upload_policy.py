import asyncio
import json

import pytest
import fitz
from fastapi import Depends, FastAPI, UploadFile
from fastapi.testclient import TestClient
import starlette.formparsers

from app.middleware.upload_envelope import UploadEnvelopeMiddleware
from app.services.upload_policy import GIB, MIB, load_pdf_upload_policy
from app.services.material_inventory import apply_pipeline_resource_gate
from app.api.materials import bounded_material_upload_files


POLICY_ENV = [
    "LUCEON_MAX_UPLOAD_PDF_BYTES",
    "LUCEON_MAX_UPLOAD_PDF_PAGES",
    "LUCEON_MAX_UPLOAD_REQUEST_BYTES",
    "LUCEON_MAX_UPLOAD_REQUEST_FILES",
    "LUCEON_MAX_UPLOAD_REQUEST_FIELDS",
    "LUCEON_MAX_GPU_BATCH_INPUT_BYTES",
    "LUCEON_MAX_GPU_BATCH_FILES",
    "LUCEON_LARGE_PDF_THRESHOLD_BYTES",
    "LUCEON_LARGE_PDF_PAGE_THRESHOLD",
    "LUCEON_LARGE_PDF_EXPANSION_FACTOR",
    "LUCEON_MIN_UPLOAD_TEMP_FREE_BYTES",
    "LUCEON_MIN_GPU_HEADROOM_BYTES",
    "LUCEON_PDF_STAGE_TIMEOUT_SECONDS",
    "LUCEON_LARGE_PDF_STAGE_TIMEOUT_SECONDS",
    "LUCEON_MULTIPART_OVERHEAD_BYTES",
]


def clear_policy_env(monkeypatch):
    for name in POLICY_ENV:
        monkeypatch.delenv(name, raising=False)


def test_upload_policy_defaults_are_2_gib_and_2000_pages(monkeypatch):
    clear_policy_env(monkeypatch)
    policy = load_pdf_upload_policy()
    assert policy.max_file_bytes == 2 * GIB
    assert policy.max_file_pages == 2000
    assert policy.max_request_bytes == 3 * GIB
    assert policy.max_request_files == 5
    assert policy.large_pdf_threshold_bytes == 256 * MIB
    assert policy.large_pdf_page_threshold == 1000
    assert policy.as_capabilities()["actual_2gib_transfer_qualified"] is False
    assert policy.as_capabilities()["policy_sha256"] == policy.identity_sha256()
    assert policy.as_capabilities()["internal_2gib_2000_profile_qualified"] is True


def test_upload_policy_accepts_safe_overrides(monkeypatch, tmp_path):
    clear_policy_env(monkeypatch)
    monkeypatch.setenv("LUCEON_MAX_UPLOAD_PDF_BYTES", str(GIB))
    monkeypatch.setenv("LUCEON_MAX_UPLOAD_REQUEST_BYTES", str(2 * GIB))
    monkeypatch.setenv("LUCEON_MAX_GPU_BATCH_INPUT_BYTES", str(2 * GIB))
    monkeypatch.setenv("LUCEON_MAX_UPLOAD_PDF_PAGES", "1200")
    monkeypatch.setenv("LUCEON_LARGE_PDF_PAGE_THRESHOLD", "800")
    monkeypatch.setenv("LUCEON_UPLOAD_TEMP_DIR", str(tmp_path))
    assert load_pdf_upload_policy().max_file_pages == 1200


def test_capabilities_mark_lower_deployment_as_not_internally_qualified(monkeypatch):
    clear_policy_env(monkeypatch)
    monkeypatch.setenv("LUCEON_MAX_UPLOAD_PDF_BYTES", str(GIB))
    monkeypatch.setenv("LUCEON_MAX_UPLOAD_REQUEST_BYTES", str(2 * GIB))
    monkeypatch.setenv("LUCEON_MAX_GPU_BATCH_INPUT_BYTES", str(2 * GIB))
    monkeypatch.setenv("LUCEON_MAX_UPLOAD_PDF_PAGES", "1200")
    monkeypatch.setenv("LUCEON_LARGE_PDF_PAGE_THRESHOLD", "800")
    capabilities = load_pdf_upload_policy().as_capabilities()
    assert capabilities["internal_2gib_2000_profile_qualified"] is False
    assert capabilities["internal_profile_gap"]


@pytest.mark.parametrize(
    ("name", "value", "gap_field"),
    [
        ("LUCEON_MAX_UPLOAD_REQUEST_BYTES", str(2 * GIB), "max_request_bytes"),
        ("LUCEON_MAX_UPLOAD_REQUEST_FILES", "4", "max_request_files"),
        ("LUCEON_MAX_GPU_BATCH_INPUT_BYTES", str(2 * GIB), "max_gpu_batch_input_bytes"),
        ("LUCEON_MAX_GPU_BATCH_FILES", "4", "max_gpu_batch_files"),
    ],
)
def test_capabilities_mark_any_reduced_internal_envelope_as_unqualified(monkeypatch, name, value, gap_field):
    clear_policy_env(monkeypatch)
    monkeypatch.setenv(name, value)
    capabilities = load_pdf_upload_policy().as_capabilities()
    assert capabilities["internal_2gib_2000_profile_qualified"] is False
    assert any(item.startswith(f"{gap_field}=") for item in capabilities["internal_profile_gap"])


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("LUCEON_MAX_UPLOAD_PDF_BYTES", str(MIB - 1)),
        ("LUCEON_MAX_UPLOAD_PDF_PAGES", "9"),
        ("LUCEON_LARGE_PDF_EXPANSION_FACTOR", "1"),
        ("LUCEON_MIN_GPU_HEADROOM_BYTES", str(4 * GIB - 1)),
    ],
)
def test_upload_policy_rejects_unsafe_values(monkeypatch, name, value):
    clear_policy_env(monkeypatch)
    monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError):
        load_pdf_upload_policy()


def test_large_threshold_must_be_inside_upload_envelope(monkeypatch):
    clear_policy_env(monkeypatch)
    monkeypatch.setenv("LUCEON_MAX_UPLOAD_PDF_PAGES", "900")
    monkeypatch.setenv("LUCEON_LARGE_PDF_PAGE_THRESHOLD", "1000")
    with pytest.raises(RuntimeError, match="inside"):
        load_pdf_upload_policy()


def test_upload_middleware_rejects_content_length_before_app(monkeypatch):
    clear_policy_env(monkeypatch)
    monkeypatch.setenv("LUCEON_MAX_UPLOAD_PDF_BYTES", str(MIB))
    monkeypatch.setenv("LUCEON_MAX_UPLOAD_REQUEST_BYTES", str(MIB))
    monkeypatch.setenv("LUCEON_MAX_GPU_BATCH_INPUT_BYTES", str(MIB))
    monkeypatch.setenv("LUCEON_LARGE_PDF_THRESHOLD_BYTES", str(MIB))
    called = False

    async def app(scope, receive, send):
        nonlocal called
        called = True

    sent = []

    async def send(message):
        sent.append(message)

    middleware = UploadEnvelopeMiddleware(app)
    asyncio.run(
        middleware(
            {"type": "http", "path": "/api/materials/upload", "headers": [(b"content-length", str(18 * MIB).encode())]},
            lambda: None,
            send,
        )
    )
    assert called is False
    assert sent[0]["status"] == 413
    assert "configured envelope" in json.loads(sent[1]["body"])["detail"]


def test_upload_middleware_counts_chunked_body(monkeypatch):
    clear_policy_env(monkeypatch)
    monkeypatch.setenv("LUCEON_MAX_UPLOAD_PDF_BYTES", str(MIB))
    monkeypatch.setenv("LUCEON_MAX_UPLOAD_REQUEST_BYTES", str(MIB))
    monkeypatch.setenv("LUCEON_MAX_GPU_BATCH_INPUT_BYTES", str(MIB))
    monkeypatch.setenv("LUCEON_LARGE_PDF_THRESHOLD_BYTES", str(MIB))
    monkeypatch.setenv("LUCEON_MULTIPART_OVERHEAD_BYTES", str(MIB))
    messages = iter(
        [
            {"type": "http.request", "body": b"x" * MIB, "more_body": True},
            {"type": "http.request", "body": b"x" * (MIB + 1), "more_body": False},
        ]
    )

    async def receive():
        return next(messages)

    async def app(scope, receive, send):
        await receive()
        await receive()

    sent = []

    async def send(message):
        sent.append(message)

    asyncio.run(UploadEnvelopeMiddleware(app)({"type": "http", "path": "/api/materials/upload", "headers": []}, receive, send))
    assert sent[0]["status"] == 413


@pytest.mark.parametrize("known_length", [True, False])
def test_upload_disk_preflight_rejects_before_receiving_body(monkeypatch, known_length):
    clear_policy_env(monkeypatch)
    monkeypatch.setattr(
        "app.middleware.upload_envelope.shutil.disk_usage",
        lambda _path: type("Usage", (), {"free": 0})(),
    )
    receive_count = 0
    app_called = False

    async def receive():
        nonlocal receive_count
        receive_count += 1
        return {"type": "http.request", "body": b"payload", "more_body": False}

    async def app(scope, receive, send):
        nonlocal app_called
        app_called = True

    sent = []

    async def send(message):
        sent.append(message)

    headers = [(b"content-length", b"1024")] if known_length else []
    asyncio.run(UploadEnvelopeMiddleware(app)({"type": "http", "path": "/api/materials/upload", "headers": headers}, receive, send))
    assert receive_count == 0
    assert app_called is False
    assert sent[0]["status"] == 507


def _bounded_test_app(entered: dict[str, int]) -> FastAPI:
    app = FastAPI()
    app.add_middleware(UploadEnvelopeMiddleware)

    @app.post("/api/materials/upload")
    async def upload(files: list[UploadFile] = Depends(bounded_material_upload_files)):
        entered["count"] += 1
        return {"count": len(files)}

    return app


def test_sixth_file_is_rejected_before_spool_creation_and_all_prior_spools_close(monkeypatch, tmp_path):
    clear_policy_env(monkeypatch)
    monkeypatch.setenv("LUCEON_UPLOAD_TEMP_DIR", str(tmp_path))
    original = starlette.formparsers.SpooledTemporaryFile
    created = []

    def tracking_spool(*args, **kwargs):
        handle = original(*args, **kwargs)
        created.append(handle)
        return handle

    monkeypatch.setattr(starlette.formparsers, "SpooledTemporaryFile", tracking_spool)
    entered = {"count": 0}
    with TestClient(_bounded_test_app(entered)) as client:
        files = [("files", (f"{index}.pdf", b"%PDF-1.7\n", "application/pdf")) for index in range(6)]
        response = client.post("/api/materials/upload", files=files)
    assert response.status_code == 413
    assert entered["count"] == 0
    assert len(created) == 5
    assert all(handle.closed for handle in created)
    assert list(tmp_path.iterdir()) == []


def test_five_files_and_bounded_plain_fields_reach_handler(monkeypatch, tmp_path):
    clear_policy_env(monkeypatch)
    monkeypatch.setenv("LUCEON_UPLOAD_TEMP_DIR", str(tmp_path))
    entered = {"count": 0}
    with TestClient(_bounded_test_app(entered)) as client:
        files = [("files", (f"{index}.pdf", b"%PDF-1.7\n", "application/pdf")) for index in range(5)]
        response = client.post("/api/materials/upload", files=files, data={"note": "bounded"})
    assert response.status_code == 200
    assert response.json() == {"count": 5}
    assert entered["count"] == 1


def test_plain_field_denominator_is_bounded(monkeypatch, tmp_path):
    clear_policy_env(monkeypatch)
    monkeypatch.setenv("LUCEON_UPLOAD_TEMP_DIR", str(tmp_path))
    monkeypatch.setenv("LUCEON_MAX_UPLOAD_REQUEST_FIELDS", "2")
    entered = {"count": 0}
    with TestClient(_bounded_test_app(entered)) as client:
        response = client.post(
            "/api/materials/upload",
            files=[
                ("files", ("one.pdf", b"%PDF-1.7\n", "application/pdf")),
                ("one", (None, "1")),
                ("two", (None, "2")),
                ("three", (None, "3")),
            ],
        )
    assert response.status_code == 413
    assert entered["count"] == 0


def test_multipart_client_disconnect_closes_created_spool_before_handler(monkeypatch, tmp_path):
    clear_policy_env(monkeypatch)
    monkeypatch.setenv("LUCEON_UPLOAD_TEMP_DIR", str(tmp_path))
    original = starlette.formparsers.SpooledTemporaryFile
    created = []

    def tracking_spool(*args, **kwargs):
        handle = original(*args, **kwargs)
        created.append(handle)
        return handle

    monkeypatch.setattr(starlette.formparsers, "SpooledTemporaryFile", tracking_spool)
    entered = {"count": 0}
    app = _bounded_test_app(entered)
    boundary = "task35-boundary"
    first_chunk = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="files"; filename="one.pdf"\r\n'
        "Content-Type: application/pdf\r\n\r\n%PDF-1.7\n"
    ).encode()
    incoming = iter(
        [
            {"type": "http.request", "body": first_chunk, "more_body": True},
            {"type": "http.disconnect"},
        ]
    )
    sent = []

    async def receive():
        return next(incoming)

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/materials/upload",
        "raw_path": b"/api/materials/upload",
        "query_string": b"",
        "headers": [(b"content-type", f"multipart/form-data; boundary={boundary}".encode())],
        "client": ("test", 123),
        "server": ("testserver", 80),
        "root_path": "",
        "state": {},
        "app": app,
    }
    asyncio.run(app(scope, receive, send))
    assert entered["count"] == 0
    assert created and all(handle.closed for handle in created)
    assert sent[0]["status"] == 400
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(("pages", "allowed"), [(1999, True), (2000, True), (2001, False)])
def test_page_limit_boundary_is_exact(monkeypatch, pages, allowed):
    clear_policy_env(monkeypatch)
    policy = load_pdf_upload_policy()
    document = fitz.open()
    for _ in range(pages):
        document.new_page(width=10, height=10)
    assert (document.page_count <= policy.max_file_pages) is allowed
    document.close()


@pytest.mark.parametrize(("offset", "allowed"), [(-1, True), (0, True), (1, False)])
def test_byte_limit_boundary_is_exact(monkeypatch, offset, allowed):
    clear_policy_env(monkeypatch)
    policy = load_pdf_upload_policy()
    assert (policy.max_file_bytes + offset <= policy.max_file_bytes) is allowed


def test_gpu_batch_aggregate_envelope_is_distinct_from_per_file(monkeypatch):
    clear_policy_env(monkeypatch)
    rows = [{"size_bytes": 2 * GIB, "page_count": 100}, {"size_bytes": 2 * GIB, "page_count": 100}]
    result = apply_pipeline_resource_gate({"selected": rows}, rows)
    assert result["resource_gate"]["ok"] is False
    assert result["resource_gate"]["status"] == "rejected_by_config"
    assert result["resource_gate"]["max_gpu_batch_input_bytes"] == 3 * GIB


def test_large_pdf_remote_headroom_fails_closed(monkeypatch):
    clear_policy_env(monkeypatch)
    rows = [{"size_bytes": 2 * GIB, "page_count": 1500}]
    payload = {
        "selected": rows,
        "health": {"health": {"artifact_limit_bytes": 20 * GIB, "artifact_used_bytes": 0, "disk_free_bytes": 15 * GIB}},
    }
    result = apply_pipeline_resource_gate(payload, rows)
    assert result["resource_gate"]["ok"] is False
    assert result["resource_gate"]["required_headroom_bytes"] == 50 * GIB
