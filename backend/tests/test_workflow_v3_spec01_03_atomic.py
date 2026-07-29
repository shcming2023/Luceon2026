from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
import zipfile
from pathlib import Path
from typing import Any

from pypdf import PdfWriter

from app.workflow_v3 import spec01_03_atomic_kernel as kernel, stage_evaluators
from app.workflow_v3.stage_evaluation_entrypoint import (
    EvaluationInput,
    StageEvaluationRequest,
)
from app.workflow_v3.stage_evaluators import STAGE_GATES


SCHEMA_SHA = "ca3d163bab055381827226140568f3bef7eaac187cebd76878e0b63e9e442356"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )


def _tar(path: Path, members: dict[str, bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w") as archive:
        for name, payload in sorted(members.items()):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mtime = 0
            info.mode = 0o644
            archive.addfile(info, __import__("io").BytesIO(payload))


def _tree_hash(root: Path) -> str:
    rows = [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": _sha(path),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _fixture(root: Path, *, padding_bytes: int = 0) -> dict[str, Path]:
    inputs = root / "inputs"
    inputs.mkdir(parents=True)
    source_pdf = inputs / "source.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.add_blank_page(width=612, height=792)
    with source_pdf.open("wb") as handle:
        writer.write(handle)

    image_payload = (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
        b"\x90wS\xde"
        b"\x00\x00\x00\x0cIDAT\x08\xd7c\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00"
        b"\x18\xdd\x8d\xb4"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    content = [
        [
            {
                "type": "image",
                "bbox": [100, 100, 400, 400],
                "content": {"image_source": {"path": "images/diagram.png"}},
            }
        ],
        [],
    ]
    content_payload = json.dumps(
        content,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    mineru_members = {
        "mineru/content_list_v2.json": content_payload,
        "mineru/images/diagram.png": image_payload,
    }
    if padding_bytes:
        mineru_members["metadata/padding.bin"] = b"M" * padding_bytes
    mineru_archive = inputs / "mineru.tar"
    _tar(mineru_archive, mineru_members)

    popo_raw = [
        {
            "id": "p1",
            "source_id": "unit-p1",
            "page": 1,
            "bbox": [0.1, 0.1, 0.9, 0.3],
            "type": "paragraph",
            "content": "First source unit.",
        },
        {
            "id": "p2",
            "source_id": "unit-p2",
            "page": 2,
            "bbox": [0.1, 0.1, 0.9, 0.3],
            "type": "paragraph",
            "content": "Second source unit.",
        },
    ]
    popo_tree = {
        "type": "root",
        "title": "Document",
        "level": 0,
        "block_ids": ["p1", "p2"],
        "children": [],
    }
    popo_raw_payload = json.dumps(
        popo_raw,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    popo_tree_payload = json.dumps(
        popo_tree,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    popo_members = {
        "enhanced/popo_raw.json": popo_raw_payload,
        "enhanced/document_tree.json": popo_tree_payload,
    }
    if padding_bytes:
        popo_members["metadata/padding.bin"] = b"P" * padding_bytes
    popo_archive = inputs / "popo.tar"
    _tar(popo_archive, popo_members)

    source_identity = {
        "sha256": _sha(source_pdf),
        "size_bytes": source_pdf.stat().st_size,
    }
    mineru_manifest = {
        "schema": "luceon-gpu-wrapper-mineru-only-manifest/v1",
        "status": "succeeded",
        "material_id": "pdf-test-atomic",
        "run_id": "mineru-run-1",
        "source_pdf": source_identity,
        "full_tree_counts": {
            "mineru": 2,
            "minerupopo": 0,
            "metadata": 1 if padding_bytes else 0,
            "logs": 0,
            "other": 0,
        },
        "objects": {
            "archive": {
                "bucket": "eduassets-mineru",
                "object": "mineru/pdf-test-atomic/mineru.tar",
                "sha256": _sha(mineru_archive),
                "size_bytes": mineru_archive.stat().st_size,
            },
            "content_list_v2": {
                "bucket": "eduassets-mineru",
                "object": "mineru/pdf-test-atomic/content_list_v2.json",
                "sha256": hashlib.sha256(content_payload).hexdigest(),
                "size_bytes": len(content_payload),
            },
            "images": [
                {
                    "source_member": "mineru/images/diagram.png",
                    "sha256": hashlib.sha256(image_payload).hexdigest(),
                    "size_bytes": len(image_payload),
                }
            ],
        },
    }
    popo_manifest = {
        "schema": "luceon-gpu-wrapper-popo-from-frozen-mineru-manifest/v1",
        "status": "succeeded",
        "material_id": "pdf-test-atomic",
        "run_id": "popo-run-1",
        "source_pdf": source_identity,
        "upstream_mineru": {
            "run_id": "mineru-run-1",
            "manifest": {
                "bucket": "eduassets-mineru",
                "object": "mineru/pdf-test-atomic/manifest.json",
            },
        },
        "full_tree_counts": {
            "minerupopo": 0,
            "enhanced": 2,
            "metadata": 1 if padding_bytes else 0,
            "logs": 0,
            "other": 0,
            "skipped_mineru": 0,
        },
        "objects": {
            "archive": {
                "bucket": "eduassets-minerupopo",
                "object": "minerupopo/pdf-test-atomic/popo.tar",
                "sha256": _sha(popo_archive),
                "size_bytes": popo_archive.stat().st_size,
            },
            "popo_raw": {
                "bucket": "eduassets-minerupopo",
                "object": "minerupopo/pdf-test-atomic/popo_raw.json",
                "sha256": hashlib.sha256(popo_raw_payload).hexdigest(),
                "size_bytes": len(popo_raw_payload),
            },
            "document_tree": {
                "bucket": "eduassets-minerupopo",
                "object": "minerupopo/pdf-test-atomic/document_tree.json",
                "sha256": hashlib.sha256(popo_tree_payload).hexdigest(),
                "size_bytes": len(popo_tree_payload),
            },
            "images": [],
        },
    }
    mineru_manifest_path = inputs / "mineru-manifest.json"
    popo_manifest_path = inputs / "popo-manifest.json"
    _write_json(mineru_manifest_path, mineru_manifest)
    _write_json(popo_manifest_path, popo_manifest)
    mineru_manifest_identity = {
        "bucket": "eduassets-mineru",
        "object": "mineru/pdf-test-atomic/manifest.json",
        "sha256": _sha(mineru_manifest_path),
        "size_bytes": mineru_manifest_path.stat().st_size,
    }
    popo_manifest_identity = {
        "bucket": "eduassets-minerupopo",
        "object": "minerupopo/pdf-test-atomic/manifest.json",
        "sha256": _sha(popo_manifest_path),
        "size_bytes": popo_manifest_path.stat().st_size,
    }
    marker_common = {
        "schema": "luceon-input-status-marker/v1",
        "material_id": "pdf-test-atomic",
        "source_pdf_sha256": source_identity["sha256"],
        "source_pdf_size_bytes": source_identity["size_bytes"],
    }
    mineru_marker = inputs / "mineru-frozen.json"
    _write_json(
        mineru_marker,
        {
            **marker_common,
            "status": "mineru_done_frozen",
            "run_id": "mineru-run-1",
            "manifest": mineru_manifest_identity,
        },
    )
    popo_marker = inputs / "popo-frozen.json"
    _write_json(
        popo_marker,
        {
            **marker_common,
            "status": "popo_done_frozen",
            "run_id": "popo-run-1",
            "manifest": popo_manifest_identity,
            "mineru_manifest": {
                "bucket": mineru_manifest_identity["bucket"],
                "object": mineru_manifest_identity["object"],
            },
        },
    )

    template = inputs / "template.zip"
    with zipfile.ZipFile(template, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, payload in (
            ("main.tex", "\\documentclass{elegantbook}\n"),
            ("elegantbook.cls", "\\NeedsTeXFormat{LaTeX2e}\n"),
            ("figure/fixed.txt", "fixed\n"),
        ):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            archive.writestr(info, payload)
    release_manifest = inputs / "release-manifest.json"
    schema_path = inputs / "schemas/test.schema.json"
    _write_json(schema_path, {})
    _write_json(
        release_manifest,
        {
            "schemas": [
                {
                    "id": "worker-v3.test-atomic",
                    "version": "1.0.0",
                    "path": "schemas/test.schema.json",
                    "sha256": SCHEMA_SHA,
                }
            ],
            "template": {
                "archive_sha256": _sha(template),
                "main_member": "main.tex",
                "class_member": "elegantbook.cls",
                "fixed_asset_members": ["figure/fixed.txt"],
            }
        },
    )
    promotion = inputs / "promotion.json"
    _write_json(promotion, {"status": "promoted"})
    return {
        "source_pdf": source_pdf,
        "mineru_manifest": mineru_manifest_path,
        "mineru_marker": mineru_marker,
        "mineru_archive": mineru_archive,
        "popo_manifest": popo_manifest_path,
        "popo_marker": popo_marker,
        "popo_archive": popo_archive,
        "template": template,
        "release_manifest": release_manifest,
        "promotion": promotion,
    }


def _common(output: Path, *, run_id: str) -> dict[str, Any]:
    return {
        "job_id": "job-atomic",
        "run_id": run_id,
        "decision_snapshot_id": f"decision-{run_id}",
        "stage_decision_id": f"stage-decision-{run_id}",
        "contract_schema_path": "schemas/test.schema.json",
        "contract_schema_sha256": SCHEMA_SHA,
        "output_dir": output,
    }


def _run_pipeline(root: Path, *, padding_bytes: int = 0) -> tuple[Path, Path, Path, dict[str, Path]]:
    paths = _fixture(root, padding_bytes=padding_bytes)
    spec01 = root / "spec01"
    intake = argparse.Namespace(
        **_common(spec01, run_id="run-1"),
        decision_index_id="decision-index-1",
        source_pdf=paths["source_pdf"],
        mineru_manifest=paths["mineru_manifest"],
        mineru_marker=paths["mineru_marker"],
        mineru_archive=paths["mineru_archive"],
        popo_manifest=paths["popo_manifest"],
        popo_marker=paths["popo_marker"],
        popo_archive=paths["popo_archive"],
        template_archive=paths["template"],
        release_manifest=paths["release_manifest"],
    )
    kernel.produce_intake(intake)

    scope_task = root / "scope-task.json"
    kernel.prepare_scope_review_task(
        argparse.Namespace(parent=spec01, output=scope_task)
    )
    task = json.loads(scope_task.read_text(encoding="utf-8"))
    review_pages = []
    for page in task["pages"]:
        review_pages.append(
            {
                "physical_page": page["physical_page"],
                "baseline_disposition": "accepted",
            }
        )
    scope_review = root / "scope-review.json"
    _write_json(
        scope_review,
        {
            "schema_version": kernel.SCOPE_REVIEW_SCHEMA,
            "review_id": "scope-review-1",
            "material_id": task["material_id"],
            "source_pdf_sha256": task["source_pdf_sha256"],
            "baseline_sha256": task["baseline_sha256"],
            "review_status": "closed",
            "pages": review_pages,
            "page_overrides": [],
            "unit_scope_overrides": [],
            "reading_order_overrides": [],
            "relationships": [],
            "open_reviews": [],
        },
    )
    spec02 = root / "spec02"
    scope_args = argparse.Namespace(
        **_common(spec02, run_id="run-2"),
        parent=spec01,
        parent_promotion=paths["promotion"],
        source_pdf=paths["source_pdf"],
        mineru_archive=paths["mineru_archive"],
        popo_archive=paths["popo_archive"],
        template_archive=paths["template"],
        review_task=scope_task,
        review=scope_review,
    )
    kernel.produce_scope(scope_args)

    media_task = root / "media-task.json"
    kernel.prepare_media_review_task(
        argparse.Namespace(parent=spec02, output=media_task)
    )
    task = json.loads(media_task.read_text(encoding="utf-8"))
    media_review = root / "media-review.json"
    media = [
        {
            "media_index": atom["media_index"],
            "baseline_disposition": "accepted",
        }
        for atom in task["media_atoms"]
    ]
    _write_json(
        media_review,
        {
            "schema_version": kernel.MEDIA_REVIEW_SCHEMA,
            "review_id": "media-review-1",
            "material_id": task["material_id"],
            "source_pdf_sha256": task["source_pdf_sha256"],
            "baseline_sha256": task["baseline_sha256"],
            "review_status": "closed",
            "media": media,
            "media_overrides": [],
            "open_reviews": [],
        },
    )
    spec03 = root / "spec03"
    ledger_args = argparse.Namespace(
        **_common(spec03, run_id="run-3"),
        parent=spec02,
        parent_promotion=paths["promotion"],
        source_pdf=paths["source_pdf"],
        mineru_archive=paths["mineru_archive"],
        popo_archive=paths["popo_archive"],
        template_archive=paths["template"],
        review_task=media_task,
        review=media_review,
        ledger_id="ledger-1",
        ledger_snapshot_id="ledger-snapshot-1",
        ledger_version=1,
    )
    kernel.produce_ledger(ledger_args)
    return spec01, spec02, spec03, paths


def _evaluate(stage: str, root: Path, release_root: Path):
    request = StageEvaluationRequest(
        job_id="job-atomic",
        stage_key=stage,
        stage_version="test",
        attempt=1,
        candidate=None,  # type: ignore[arg-type]
        release_manifest_sha256=_sha(release_root / "release-manifest.json"),
        policy_sha256="2" * 64,
        required_gates=STAGE_GATES[stage],
        output_manifest="evaluation-manifest.json",
        workdir=root.parent / f"evaluate-{stage}",
    )
    return stage_evaluators.evaluate_stage(
        request,
        EvaluationInput(root, {}),
        release_root,
    )


def test_atomic_stage_evaluators_recompute_all_gates(tmp_path: Path) -> None:
    spec01, spec02, spec03, paths = _run_pipeline(tmp_path)
    for stage, root in (
        ("intake_snapshot", spec01),
        ("source_scope_and_order", spec02),
        ("canonical_block_ledger", spec03),
    ):
        result = _evaluate(stage, root, paths["release_manifest"].parent)
        assert result.gate_results == {
            gate: True for gate in STAGE_GATES[stage]
        }


def test_scope_evaluator_rejects_noncontiguous_order(tmp_path: Path) -> None:
    _, spec02, _, paths = _run_pipeline(tmp_path)
    order_path = spec02 / "ledgers/reading_order_ledger.json"
    order = json.loads(order_path.read_text(encoding="utf-8"))
    order["ordered_source_units"].reverse()
    _write_json(order_path, order)
    result = _evaluate(
        "source_scope_and_order",
        spec02,
        paths["release_manifest"].parent,
    )
    assert result.gate_results["reading_order_closed"] is False


def test_ledger_evaluator_rejects_duplicate_source_coverage(tmp_path: Path) -> None:
    _, _, spec03, paths = _run_pipeline(tmp_path)
    coverage_path = spec03 / "ledgers/block_coverage_ledger.json"
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    coverage["source_units"][0]["coverage_count"] = 2
    _write_json(coverage_path, coverage)
    result = _evaluate(
        "canonical_block_ledger",
        spec03,
        paths["release_manifest"].parent,
    )
    assert result.gate_results["content_conservation_passed"] is False


def test_large_frozen_inputs_are_referenced_not_recursively_materialized(
    tmp_path: Path,
) -> None:
    spec01, spec02, spec03, paths = _run_pipeline(
        tmp_path,
        padding_bytes=4 * 1024 * 1024,
    )
    frozen_bytes = (
        paths["source_pdf"].stat().st_size
        + paths["mineru_archive"].stat().st_size
        + paths["popo_archive"].stat().st_size
        + paths["template"].stat().st_size
    )
    assert frozen_bytes > 8 * 1024 * 1024
    for bundle in (spec01, spec02, spec03):
        files = [path for path in bundle.rglob("*") if path.is_file()]
        assert not any(
            path.suffix.lower() in {".pdf", ".zip", ".tar", ".tgz", ".gz"}
            for path in files
        )
        assert sum(path.stat().st_size for path in files) < frozen_bytes // 4
    assert not list(spec01.rglob("*.png"))
    risk_thumbnails = list(
        spec02.glob("evidence/risk-page-thumbnails/page-*.png")
    )
    assert 0 < len(risk_thumbnails) <= 12
    assert not [
        path
        for path in spec02.rglob("*.png")
        if path not in risk_thumbnails
    ]
    selected = list(spec03.glob("media/selected/*.png"))
    assert selected == []
    representation_plan = json.loads(
        (spec03 / "media/media_representation_plan.json").read_text(
            encoding="utf-8"
        )
    )
    assert {
        item["disposition"]
        for item in representation_plan["representations"]
    } == {"source_region"}
    contract = json.loads(
        (spec01 / "contracts/input_contract.json").read_text(encoding="utf-8")
    )
    assert contract["inputs"]["source_pdf"]["storage"] == "external_frozen_input"
    assert contract["inputs"]["mineru_archive"]["sha256"] == _sha(
        paths["mineru_archive"]
    )
    units = kernel._read_jsonl(  # noqa: SLF001
        spec01 / "source/popo_source_units.jsonl",
        "test units",
    )
    assert units[0]["archive_entry_evidence"]["popo_raw"]["member"].endswith(
        "popo_raw.json"
    )
    media = kernel._read_jsonl(  # noqa: SLF001
        spec01 / "source/mineru_media_atoms.jsonl",
        "test media",
    )
    asset = next(
        item
        for item in media[0]["candidates"]
        if item["representation_type"] == "source_asset_image"
    )
    assert asset["archive_member"] == "mineru/images/diagram.png"
    assert "path" not in asset


def test_candidate_hashes_are_stable_across_workdirs(tmp_path: Path) -> None:
    first = _run_pipeline(tmp_path / "one")
    second = _run_pipeline(tmp_path / "two")
    assert [_tree_hash(path) for path in first[:3]] == [
        _tree_hash(path) for path in second[:3]
    ]
    for bundle in (*first[:3], *second[:3]):
        serialized = "\n".join(
            path.read_text(encoding="utf-8", errors="ignore")
            for path in sorted(bundle.rglob("*"))
            if path.is_file()
        )
        assert str(tmp_path) not in serialized


def test_scope_rejects_drifted_external_source(tmp_path: Path) -> None:
    spec01, _, _, paths = _run_pipeline(tmp_path / "baseline")
    scope_task = tmp_path / "drift-task.json"
    kernel.prepare_scope_review_task(
        argparse.Namespace(parent=spec01, output=scope_task)
    )
    drifted = tmp_path / "drifted.pdf"
    drifted.write_bytes(paths["source_pdf"].read_bytes() + b"\n")
    args = argparse.Namespace(
        **_common(tmp_path / "failed", run_id="run-drift"),
        parent=spec01,
        parent_promotion=paths["promotion"],
        source_pdf=drifted,
        mineru_archive=paths["mineru_archive"],
        popo_archive=paths["popo_archive"],
        template_archive=paths["template"],
        review_task=scope_task,
        review=tmp_path / "unused-review.json",
    )
    try:
        kernel.produce_scope(args)
    except kernel.KernelContractError as exc:
        assert exc.code == "input_reference_drift"
    else:
        raise AssertionError("drifted source must fail before review")


def test_intake_rejects_frozen_marker_source_mismatch(tmp_path: Path) -> None:
    paths = _fixture(tmp_path)
    marker = json.loads(paths["popo_marker"].read_text(encoding="utf-8"))
    marker["source_pdf_sha256"] = "f" * 64
    _write_json(paths["popo_marker"], marker)
    args = argparse.Namespace(
        **_common(tmp_path / "failed", run_id="run-marker-drift"),
        decision_index_id="decision-index-marker-drift",
        source_pdf=paths["source_pdf"],
        mineru_manifest=paths["mineru_manifest"],
        mineru_marker=paths["mineru_marker"],
        mineru_archive=paths["mineru_archive"],
        popo_manifest=paths["popo_manifest"],
        popo_marker=paths["popo_marker"],
        popo_archive=paths["popo_archive"],
        template_archive=paths["template"],
        release_manifest=paths["release_manifest"],
    )
    try:
        kernel.produce_intake(args)
    except kernel.KernelContractError as exc:
        assert exc.code == "frozen_marker_source_mismatch"
    else:
        raise AssertionError("drifted frozen marker must fail")


def test_mineru_directory_placeholder_is_not_treated_as_media_asset() -> None:
    rows = kernel._normalize_mineru_media(  # noqa: SLF001
        [
            [
                {
                    "type": "table",
                    "bbox": [0, 0, 1000, 1000],
                    "content": {
                        "image_source": {"path": "images/"},
                        "html": "",
                        "table_type": "simple_table",
                    },
                }
            ]
        ],
        page_count=1,
        mineru_run_id="mineru-directory-placeholder",
        assets_by_basename={},
        content_list_entry={
            "provider": "mineru",
            "member": "mineru/input/vlm/input_content_list_v2.json",
        },
    )

    assert len(rows) == 1
    assert [candidate["representation_type"] for candidate in rows[0]["candidates"]] == [
        "source_region_image",
        "structured_table",
    ]


def test_safe_tar_rejects_parent_traversal(tmp_path: Path) -> None:
    archive_path = tmp_path / "unsafe.tar"
    with tarfile.open(archive_path, "w") as archive:
        payload = b"escape"
        info = tarfile.TarInfo("../escape.txt")
        info.size = len(payload)
        archive.addfile(info, __import__("io").BytesIO(payload))
    try:
        kernel._safe_extract_tar(  # noqa: SLF001
            archive_path,
            tmp_path / "extract",
            "unsafe test archive",
        )
    except kernel.KernelContractError as exc:
        assert exc.code == "path_invalid"
    else:
        raise AssertionError("archive traversal must fail")
    assert not (tmp_path / "escape.txt").exists()


def test_safe_tar_accepts_standard_dot_prefixed_root_directory(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "standard.tar"
    with tarfile.open(archive_path, "w") as archive:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        root.size = 0
        archive.addfile(root)
        directory = tarfile.TarInfo("./safe/")
        directory.type = tarfile.DIRTYPE
        directory.size = 0
        archive.addfile(directory)
        payload = b"safe"
        member = tarfile.TarInfo("./safe/file.txt")
        member.size = len(payload)
        archive.addfile(member, __import__("io").BytesIO(payload))

    files = kernel._safe_extract_tar(  # noqa: SLF001
        archive_path,
        tmp_path / "extract",
        "standard test archive",
    )

    assert set(files) == {"safe/file.txt"}
    assert files["safe/file.txt"].read_bytes() == b"safe"


def test_scope_review_rejects_unknown_unit_override(tmp_path: Path) -> None:
    spec01, _, _, _ = _run_pipeline(tmp_path)
    task = kernel._scope_review_task(spec01)  # noqa: SLF001
    source_units = kernel._read_jsonl(  # noqa: SLF001
        spec01 / "source/popo_source_units.jsonl",
        "test source units",
    )
    baseline = kernel._scope_baseline(  # noqa: SLF001
        page_count=task["page_count"],
        source_units=source_units,
    )
    review = {
        "schema_version": kernel.SCOPE_REVIEW_SCHEMA,
        "review_id": "unknown-unit-override",
        "material_id": task["material_id"],
        "source_pdf_sha256": task["source_pdf_sha256"],
        "baseline_sha256": task["baseline_sha256"],
        "review_status": "closed",
        "pages": [
            {
                "physical_page": page["physical_page"],
                "baseline_disposition": "accepted",
            }
            for page in task["pages"]
        ],
        "page_overrides": [],
        "unit_scope_overrides": [
            {
                "source_id": "not-enumerated",
                "scope_status": "excluded",
                "reason": "invalid test override",
                "evidence_refs": ["test:evidence"],
                "review_status": "closed",
            }
        ],
        "reading_order_overrides": [],
        "relationships": [],
        "open_reviews": [],
    }
    try:
        kernel._validate_scope_review(  # noqa: SLF001
            review,
            material_id=task["material_id"],
            source_sha256=task["source_pdf_sha256"],
            review_task=task,
            baseline=baseline,
            source_units=source_units,
        )
    except kernel.KernelContractError as exc:
        assert exc.code == "scope_unit_override_invalid"
    else:
        raise AssertionError("unknown source-unit override must fail")


def test_compact_scope_review_expands_large_exhaustive_baseline() -> None:
    page_count = 200
    source_units = [
        {
            "source_id": f"unit-{page:04d}-{index:02d}",
            "physical_page": page,
            "source_label": "text",
            "source_type": "text",
            "bbox": [0.1, index / 20, 0.9, (index + 1) / 20],
            "popo_tree_rank": (page - 1) * 15 + index,
        }
        for page in range(1, page_count + 1)
        for index in range(15)
    ]
    baseline = kernel._scope_baseline(  # noqa: SLF001
        page_count=page_count,
        source_units=source_units,
    )
    task = {
        "page_count": page_count,
        "pages": [
            {"physical_page": page, "complexity_flags": []}
            for page in range(1, page_count + 1)
        ],
    }
    review = {
        "schema_version": kernel.SCOPE_REVIEW_SCHEMA,
        "review_id": "large-compact-review",
        "material_id": "pdf-large-generic",
        "source_pdf_sha256": "a" * 64,
        "baseline_sha256": baseline["sha256"],
        "review_status": "closed",
        "pages": [
            {
                "physical_page": page,
                "baseline_disposition": "accepted",
            }
            for page in range(1, page_count + 1)
        ],
        "page_overrides": [],
        "unit_scope_overrides": [],
        "reading_order_overrides": [],
        "relationships": [],
        "open_reviews": [],
    }
    pages, units, relationships = kernel._validate_scope_review(  # noqa: SLF001
        review,
        material_id="pdf-large-generic",
        source_sha256="a" * 64,
        review_task=task,
        baseline=baseline,
        source_units=source_units,
    )
    assert len(pages) == page_count
    assert len(units) == 3000
    assert relationships == []
    assert [unit["candidate_final_order"] for unit in units] == list(
        range(1, 3001)
    )
    assert (
        kernel._minimum_scope_review_bytes(  # noqa: SLF001
            material_id="pdf-large-generic",
            source_pdf_sha256="a" * 64,
            baseline_sha256=baseline["sha256"],
            page_count=page_count,
        )
        < 16_000
    )


def test_compact_scope_review_projects_only_declared_page_override() -> None:
    source_units = [
        {
            "source_id": "unit-1",
            "physical_page": 1,
            "source_label": "text",
            "source_type": "text",
            "bbox": [0.1, 0.1, 0.9, 0.2],
            "popo_tree_rank": 1,
        }
    ]
    baseline = kernel._scope_baseline(  # noqa: SLF001
        page_count=1,
        source_units=source_units,
    )
    review = {
        "schema_version": kernel.SCOPE_REVIEW_SCHEMA,
        "review_id": "page-override",
        "material_id": "pdf-generic",
        "source_pdf_sha256": "a" * 64,
        "baseline_sha256": baseline["sha256"],
        "review_status": "closed",
        "pages": [
            {"physical_page": 1, "baseline_disposition": "overridden"}
        ],
        "page_overrides": [
            {
                "physical_page": 1,
                "scope_status": "excluded",
                "page_category": "non_body",
                "reason": "reviewed source evidence excludes this page",
                "evidence_refs": ["physical-page:1"],
                "review_status": "closed",
            }
        ],
        "unit_scope_overrides": [],
        "reading_order_overrides": [],
        "relationships": [],
        "open_reviews": [],
    }
    pages, units, relationships = kernel._validate_scope_review(  # noqa: SLF001
        review,
        material_id="pdf-generic",
        source_sha256="a" * 64,
        review_task={"page_count": 1, "pages": []},
        baseline=baseline,
        source_units=source_units,
    )
    assert pages[0]["scope_status"] == "excluded"
    assert pages[0]["reason"] == "reviewed source evidence excludes this page"
    assert units[0]["scope_status"] == "excluded"
    assert relationships == []


def test_compact_scope_review_rejects_missing_declared_page_override() -> None:
    source_units = [
        {
            "source_id": "unit-1",
            "physical_page": 1,
            "source_label": "text",
            "source_type": "text",
            "bbox": [0.1, 0.1, 0.9, 0.2],
            "popo_tree_rank": 1,
        }
    ]
    baseline = kernel._scope_baseline(  # noqa: SLF001
        page_count=1,
        source_units=source_units,
    )
    review = {
        "schema_version": kernel.SCOPE_REVIEW_SCHEMA,
        "review_id": "missing-page-override",
        "material_id": "pdf-generic",
        "source_pdf_sha256": "a" * 64,
        "baseline_sha256": baseline["sha256"],
        "review_status": "closed",
        "pages": [
            {"physical_page": 1, "baseline_disposition": "overridden"}
        ],
        "page_overrides": [],
        "unit_scope_overrides": [],
        "reading_order_overrides": [],
        "relationships": [],
        "open_reviews": [],
    }
    try:
        kernel._validate_scope_review(  # noqa: SLF001
            review,
            material_id="pdf-generic",
            source_sha256="a" * 64,
            review_task={"page_count": 1, "pages": []},
            baseline=baseline,
            source_units=source_units,
        )
    except kernel.KernelContractError as exc:
        assert exc.code == "scope_page_override_invalid"
    else:
        raise AssertionError("declared page override must have one exact payload")


def test_scope_review_rejects_nonempty_roles_for_semantic_group() -> None:
    source_units = [
        {
            "source_id": "unit-1",
            "physical_page": 1,
            "source_label": "text",
            "source_type": "text",
            "bbox": [0.1, 0.1, 0.9, 0.2],
            "popo_tree_rank": 1,
        }
    ]
    baseline = kernel._scope_baseline(  # noqa: SLF001
        page_count=1,
        source_units=source_units,
    )
    review = {
        "schema_version": kernel.SCOPE_REVIEW_SCHEMA,
        "review_id": "invalid-semantic-group-roles",
        "material_id": "pdf-generic",
        "source_pdf_sha256": "a" * 64,
        "baseline_sha256": baseline["sha256"],
        "review_status": "closed",
        "pages": [
            {
                "physical_page": 1,
                "baseline_disposition": "accepted",
            }
        ],
        "page_overrides": [],
        "unit_scope_overrides": [],
        "reading_order_overrides": [],
        "relationships": [
            {
                "relationship_id": "semantic-1",
                "relationship_type": "semantic_group",
                "member_source_ids": ["unit-1"],
                "roles": {"stem": ["unit-1"], "media": [], "options": []},
                "physical_pages": [1],
                "evidence_refs": ["source:unit-1"],
                "review_status": "closed",
            }
        ],
        "open_reviews": [],
    }
    try:
        kernel._validate_scope_review(  # noqa: SLF001
            review,
            material_id="pdf-generic",
            source_sha256="a" * 64,
            review_task={
                "page_count": 1,
                "pages": [{"physical_page": 1, "complexity_flags": []}],
            },
            baseline=baseline,
            source_units=source_units,
        )
    except kernel.KernelContractError as exc:
        assert exc.code == "scope_relationship_invalid"
    else:
        raise AssertionError("semantic group roles must be empty")


def test_media_review_rejects_unknown_candidate(tmp_path: Path) -> None:
    spec01, spec02, _, _ = _run_pipeline(tmp_path)
    contract = json.loads(
        (spec01 / "contracts/input_contract.json").read_text(encoding="utf-8")
    )
    media_atoms = kernel._read_jsonl(  # noqa: SLF001
        spec01 / "source/mineru_media_atoms.jsonl",
        "test media atoms",
    )
    review_task = kernel._media_review_task(spec02)  # noqa: SLF001
    review = {
        "schema_version": kernel.MEDIA_REVIEW_SCHEMA,
        "review_id": "unknown-candidate",
        "material_id": contract["material_identity"]["material_id"],
        "source_pdf_sha256": contract["material_identity"]["source_pdf_sha256"],
        "baseline_sha256": review_task["baseline_sha256"],
        "review_status": "closed",
        "media": [
            {
                "media_index": atom["media_index"],
                "baseline_disposition": (
                    "overridden" if atom["media_index"] == 1 else "accepted"
                ),
            }
            for atom in review_task["media_atoms"]
        ],
        "media_overrides": [
            {
                "media_index": 1,
                "disposition": "source_asset",
                "selected_candidate_index": 999,
                "source_unit_indexes": [],
                "reason": "exercise unknown candidate rejection",
                "review_status": "closed",
            }
        ],
        "open_reviews": [],
    }
    try:
        kernel._validate_media_review(  # noqa: SLF001
            review,
            material_id=contract["material_identity"]["material_id"],
            source_sha256=contract["material_identity"]["source_pdf_sha256"],
            media_atoms=media_atoms,
            source_ids={"unit-p1", "unit-p2"},
            review_task=review_task,
        )
    except kernel.KernelContractError as exc:
        assert exc.code == "media_candidate_invalid"
    else:
        raise AssertionError("unknown media candidate must fail")


def test_media_review_rejects_baseline_drift(tmp_path: Path) -> None:
    spec01, spec02, _, _ = _run_pipeline(tmp_path)
    contract = json.loads(
        (spec01 / "contracts/input_contract.json").read_text(encoding="utf-8")
    )
    media_atoms = kernel._read_jsonl(  # noqa: SLF001
        spec01 / "source/mineru_media_atoms.jsonl",
        "test media atoms",
    )
    review_task = kernel._media_review_task(spec02)  # noqa: SLF001
    review = {
        "schema_version": kernel.MEDIA_REVIEW_SCHEMA,
        "review_id": "drifted-baseline",
        "material_id": contract["material_identity"]["material_id"],
        "source_pdf_sha256": contract["material_identity"]["source_pdf_sha256"],
        "baseline_sha256": "0" * 64,
        "review_status": "closed",
        "media": [
            {
                "media_index": atom["media_index"],
                "baseline_disposition": "accepted",
            }
            for atom in review_task["media_atoms"]
        ],
        "media_overrides": [],
        "open_reviews": [],
    }
    try:
        kernel._validate_media_review(  # noqa: SLF001
            review,
            material_id=contract["material_identity"]["material_id"],
            source_sha256=contract["material_identity"]["source_pdf_sha256"],
            media_atoms=media_atoms,
            source_ids={"unit-p1", "unit-p2"},
            review_task=review_task,
        )
    except kernel.KernelContractError as exc:
        assert exc.code == "media_review_baseline_mismatch"
    else:
        raise AssertionError("media baseline drift must fail")


def test_media_review_expands_evidence_backed_exclusion(tmp_path: Path) -> None:
    spec01, spec02, _, _ = _run_pipeline(tmp_path)
    contract = json.loads(
        (spec01 / "contracts/input_contract.json").read_text(encoding="utf-8")
    )
    media_atoms = kernel._read_jsonl(  # noqa: SLF001
        spec01 / "source/mineru_media_atoms.jsonl",
        "test media atoms",
    )
    review_task = kernel._media_review_task(spec02)  # noqa: SLF001
    first = review_task["media_atoms"][0]
    page_group = next(
        group
        for group in review_task["page_source_units"]
        if group["physical_page"] == first["physical_page"]
    )
    source_unit = page_group["source_units"][0]
    review = {
        "schema_version": kernel.MEDIA_REVIEW_SCHEMA,
        "review_id": "evidence-backed-exclusion",
        "material_id": contract["material_identity"]["material_id"],
        "source_pdf_sha256": contract["material_identity"]["source_pdf_sha256"],
        "baseline_sha256": review_task["baseline_sha256"],
        "review_status": "closed",
        "media": [
            {
                "media_index": atom["media_index"],
                "baseline_disposition": (
                    "overridden"
                    if atom["media_index"] == first["media_index"]
                    else "accepted"
                ),
            }
            for atom in review_task["media_atoms"]
        ],
        "media_overrides": [
            {
                "media_index": first["media_index"],
                "disposition": "excluded_noninstructional",
                "selected_candidate_index": 0,
                "source_unit_indexes": [source_unit["source_unit_index"]],
                "reason": "page decoration confirmed by the enumerated source unit",
                "review_status": "closed",
            }
        ],
        "open_reviews": [],
    }
    normalized = kernel._validate_media_review(  # noqa: SLF001
        review,
        material_id=contract["material_identity"]["material_id"],
        source_sha256=contract["material_identity"]["source_pdf_sha256"],
        media_atoms=media_atoms,
        source_ids={"unit-p1", "unit-p2"},
        review_task=review_task,
    )
    excluded = next(
        item for item in normalized if item["media_id"] == first["media_id"]
    )
    assert excluded["selected_candidate"] is None
    assert excluded["source_ids"] == [source_unit["source_id"]]
    assert excluded["evidence_refs"] == [
        f"media:{first['media_id']}",
        f"source:{source_unit['source_id']}",
    ]


def test_compact_media_review_capacity_scales_to_large_inventory() -> None:
    assert (
        kernel._minimum_media_review_bytes(  # noqa: SLF001
            material_id="pdf-" + "a" * 16,
            source_pdf_sha256="b" * 64,
            baseline_sha256="c" * 64,
            media_count=255,
        )
        < 16_000
    )
    assert (
        kernel._minimum_media_review_bytes(  # noqa: SLF001
            material_id="pdf-" + "a" * 16,
            source_pdf_sha256="b" * 64,
            baseline_sha256="c" * 64,
            media_count=1_000,
        )
        < 60_000
    )
