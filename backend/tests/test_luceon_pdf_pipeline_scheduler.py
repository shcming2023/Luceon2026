from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "luceon_pdf_pipeline.py"
SPEC = importlib.util.spec_from_file_location("luceon_pdf_pipeline", MODULE_PATH)
pipeline = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = pipeline
SPEC.loader.exec_module(pipeline)


def write_lineage(tmp_path: Path, items: list[dict], active_marker_count: int = 0) -> str:
    path = tmp_path / "lineage.json"
    path.write_text(
        json.dumps(
            {
                "command": "lineage-audit",
                "scope": "test",
                "items": items,
                "scheduler_state_counts": {},
                "state_counts": {},
                "active_marker_count": active_marker_count,
                "active_marker_samples": [],
            }
        ),
        encoding="utf-8",
    )
    return str(path)


def test_plan_next_can_be_limited_to_explicit_input_object(tmp_path):
    lineage_file = write_lineage(
        tmp_path,
        [
            {
                "input_object": "uat-input-only-20260702095009.pdf",
                "input_size_bytes": 560,
                "material_id": "pdf-uat",
                "scheduler_state": "eligible_for_submit",
            },
            {
                "input_object": "cambridge-igcse.pdf",
                "input_size_bytes": 166331327,
                "material_id": "pdf-d38fd83ffd5a7f6a",
                "scheduler_state": "eligible_for_submit",
            },
        ],
    )

    plan = pipeline.build_next_batch_plan(
        None,
        None,
        limit=5,
        sort_by="smallest",
        with_sha=False,
        lineage_file=lineage_file,
        input_objects=("cambridge-igcse.pdf",),
    )

    assert plan["status"] == "READY"
    assert plan["selected_count"] == 1
    assert plan["selected"][0]["input_object"] == "cambridge-igcse.pdf"


def test_existing_recovery_can_select_active_marker_items(tmp_path):
    lineage_file = write_lineage(
        tmp_path,
        [
            {
                "input_object": "cambridge-igcse.pdf",
                "input_size_bytes": 166331327,
                "material_id": "pdf-d38fd83ffd5a7f6a",
                "scheduler_state": "active_or_stale",
            }
        ],
        active_marker_count=1,
    )

    blocked = pipeline.build_next_batch_plan(
        None,
        None,
        limit=5,
        sort_by="smallest",
        with_sha=False,
        lineage_file=lineage_file,
    )
    recovery = pipeline.build_next_batch_plan(
        None,
        None,
        limit=5,
        sort_by="smallest",
        with_sha=False,
        lineage_file=lineage_file,
        allow_active=True,
        candidate_states={"active_or_stale"},
    )

    assert blocked["status"] == "BLOCKED_ACTIVE_MARKERS"
    assert recovery["status"] == "READY"
    assert recovery["selected"][0]["input_object"] == "cambridge-igcse.pdf"


def test_completed_asset_is_selected_only_for_explicit_versioned_reprocess(tmp_path):
    lineage_file = write_lineage(
        tmp_path,
        [
            {
                "input_object": "completed.pdf",
                "input_size_bytes": 1024,
                "material_id": "pdf-completed",
                "scheduler_state": "gpu_done",
            }
        ],
    )

    ordinary = pipeline.build_next_batch_plan(
        None,
        None,
        limit=1,
        sort_by="smallest",
        with_sha=False,
        lineage_file=lineage_file,
        material_ids=("pdf-completed",),
    )
    versioned = pipeline.build_next_batch_plan(
        None,
        None,
        limit=1,
        sort_by="smallest",
        with_sha=False,
        lineage_file=lineage_file,
        material_ids=("pdf-completed",),
        candidate_states={"eligible_for_submit", "gpu_done"},
    )

    assert ordinary["status"] == "TARGET_NOT_ELIGIBLE"
    assert versioned["status"] == "READY"
    assert versioned["selected"][0]["material_id"] == "pdf-completed"


class FakeS3:
    def __init__(self, objects: dict[str, bytes]):
        self.objects = objects

    def list_objects(self, bucket: str, max_keys: int = 1000, prefix: str = "") -> list[dict[str, str]]:
        return [
            {"Key": key, "Size": str(len(data))}
            for key, data in self.objects.items()
            if key.startswith(prefix)
        ]

    def get_bytes(self, bucket: str, obj: str, max_bytes: int | None = None) -> bytes:
        data = self.objects[obj]
        if max_bytes is not None and len(data) > max_bytes:
            raise ValueError("too large")
        return data

    def presign_get(self, public_endpoint: str, bucket: str, obj: str, expires: int = 86400) -> str:
        return f"{public_endpoint}/{bucket}/{obj}?expires={expires}"


def test_reuse_frozen_mineru_requires_matching_manifest_and_marker():
    material_id = "pdf-abc"
    run_id = "mineru-run-1"
    batch_id = "mineru-batch-1"
    manifest_object = f"mineru/{material_id}/{run_id}/manifest.json"
    marker_object = pipeline.status_marker_object(material_id, run_id, "mineru_done_frozen")
    manifest = {
        "status": "mineru_done_frozen",
        "material_id": material_id,
        "run_id": run_id,
        "batch_id": batch_id,
        "source_pdf": {
            "input_object": "first.pdf",
            "sha256": "abc123",
            "size_bytes": 42,
        },
    }

    class FrozenS3:
        def list_objects(self, bucket: str, max_keys: int = 1000, prefix: str = ""):
            return [{"Key": manifest_object, "LastModified": "2026-07-14T00:00:00Z"}]

        def get_json(self, bucket: str, obj: str):
            if obj == manifest_object:
                return manifest
            if obj == marker_object:
                return {"status": "mineru_done_frozen"}
            raise KeyError(obj)

    cfg = SimpleNamespace(mineru_bucket="eduassets-mineru", input_bucket="eduassets-input")
    doc = {
        "material_id": material_id,
        "object": "first.pdf",
        "source_pdf_sha256": "abc123",
        "source_pdf_size_bytes": 42,
    }
    doc_status = {"run_id": run_id, "status": "succeeded"}

    result = pipeline.reuse_frozen_mineru_result(FrozenS3(), cfg, doc, doc_status, batch_id)

    assert result["status"] == "PASS"
    assert result["reused"] is True
    assert result["manifest"] == {"bucket": "eduassets-mineru", "object": manifest_object}


def test_existing_stage_status_maps_sanitized_filename_by_sha():
    data = b"%PDF-1.7 actual target bytes"
    source_sha = hashlib.sha256(data).hexdigest()
    s3 = FakeS3({"original/cambridge-igcse-long-name.pdf": data})
    cfg = SimpleNamespace(
        input_bucket="eduassets-input",
        minio_public_endpoint="http://minio.local",
        presign_expires=3600,
        max_file_bytes=1024,
    )
    doc_status = {
        "doc_id": "remote-doc-003",
        "source": {
            "filename": "sanitized.pdf",
            "sha256": source_sha,
            "size_bytes": len(data),
        },
    }

    doc = pipeline.existing_stage_status_to_submit_doc(s3, cfg, doc_status, 2)

    assert doc["object"] == "original/cambridge-igcse-long-name.pdf"
    assert doc["doc_id"] == "remote-doc-003"
    assert doc["source_pdf_sha256"] == source_sha
    assert doc["material_id"] == "pdf-" + source_sha[:16]


def test_staged_completion_requires_every_selected_document_to_freeze_both_stages():
    result = {
        "selected_count": 3,
        "mineru_freezes": [{}, {}],
        "mineru_errors": [{"reason": "freeze failed"}],
        "popo": {"freezes": [{}, {}], "errors": []},
    }

    assert pipeline.staged_completion_status(result) == "PARTIAL"


def test_staged_completion_is_done_only_without_stage_or_freeze_errors():
    result = {
        "selected_count": 2,
        "mineru_freezes": [{}, {}],
        "mineru_errors": [],
        "popo": {"freezes": [{}, {}], "errors": []},
    }

    assert pipeline.staged_completion_status(result) == "DONE"


def test_cli_exit_code_rejects_partial_and_incomplete_results():
    assert pipeline.cli_result_exit_code({"status": "DONE"}) == 0
    assert pipeline.cli_result_exit_code({"status": "PARTIAL"}) != 0
    assert pipeline.cli_result_exit_code({"status": "MINERU_WAIT_INCOMPLETE"}) != 0
    assert pipeline.cli_result_exit_code({"status": "ERROR"}) != 0


def test_freeze_error_marker_is_stage_specific_and_cleans_active_stage_markers():
    class MarkerS3:
        def __init__(self):
            self.puts = []
            self.deletes = []

        def put_json(self, bucket, obj, value):
            self.puts.append((bucket, obj, value))
            return {"bucket": bucket, "object": obj}

        def delete(self, bucket, obj):
            self.deletes.append((bucket, obj))
            return {"bucket": bucket, "object": obj}

    s3 = MarkerS3()
    cfg = SimpleNamespace(input_bucket="eduassets-input")
    doc = {
        "bucket": "eduassets-input",
        "object": "book.pdf",
        "material_id": "pdf-book",
        "source_hash": "source-book",
    }

    result = pipeline.write_freeze_error(
        s3,
        cfg,
        doc,
        "mineru-run-1",
        "mineru",
        "mineru-batch-1",
        {"status": "succeeded"},
        RuntimeError("copy failed"),
    )

    assert result["reason"] == "mineru_freeze_failed"
    assert all("mineru_freeze_error" in obj for _, obj, _ in s3.puts)
    assert {obj.rsplit(".", 2)[-2] for _, obj in s3.deletes} == {"mineru_submitted", "mineru_running"}


def _tar_with_member(name: str, data: bytes = b"ok", *, mode: int = 0o644) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as tf:
        info = tarfile.TarInfo(name)
        info.size = len(data)
        info.mode = mode
        tf.addfile(info, io.BytesIO(data))
    return output.getvalue()


@pytest.mark.parametrize("name", ["../escape", "/absolute", "folder\\windows", "~/.ssh/key"])
def test_result_archive_rejects_path_traversal(name):
    archive = _tar_with_member(name)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tf:
        with pytest.raises(ValueError, match="unsafe tar member path"):
            pipeline.validate_result_tar(tf, len(archive))


def test_result_archive_rejects_expansion_bomb(monkeypatch):
    archive = _tar_with_member("safe/result.bin", b"x" * 4096)
    monkeypatch.setattr(pipeline, "MAX_RESULT_EXPANSION_RATIO", 1)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tf:
        with pytest.raises(ValueError, match="expansion ratio"):
            pipeline.validate_result_tar(tf, len(archive))


def test_result_archive_accepts_bounded_regular_members():
    archive = _tar_with_member("mineru/content_list_v2.json", b"[]")
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tf:
        inventory = pipeline.validate_result_tar(tf, len(archive))
    assert inventory == {"members_total": 1, "file_members": 1, "uncompressed_bytes": 2}
