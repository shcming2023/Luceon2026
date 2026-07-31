from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
import zipfile
from pathlib import Path
from typing import Any

import pytest
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

    image_payload = __import__("base64").b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEElEQVR4nGP8"
        "zwACTGCSAQANHQEDgslx/wAAAABJRU5ErkJggg=="
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
            "bbox": [0.1, 0.1, 0.4, 0.4],
            "type": "image",
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


def test_ledger_evaluator_rejects_media_resolved_without_source_ownership(
    tmp_path: Path,
) -> None:
    _, _, spec03, paths = _run_pipeline(tmp_path)
    plan_path = spec03 / "media/media_representation_plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["representations"][0]["source_block_ids"] = []
    plan["payload_hash"] = kernel._contract_payload_hash(plan)  # noqa: SLF001
    _write_json(plan_path, plan)
    media_path = spec03 / "ledgers/media_ledger.json"
    media = json.loads(media_path.read_text(encoding="utf-8"))
    media["media_representation_plan_hash"] = _sha(plan_path)
    _write_json(media_path, media)

    result = _evaluate(
        "canonical_block_ledger",
        spec03,
        paths["release_manifest"].parent,
    )

    assert result.gate_results["media_relations_closed"] is False


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
    assert len(selected) == 1
    assert selected[0].stat().st_size < frozen_bytes // 100
    representation_plan = json.loads(
        (spec03 / "media/media_representation_plan.json").read_text(
            encoding="utf-8"
        )
    )
    assert {
        item["disposition"]
        for item in representation_plan["representations"]
    } == {"source_asset"}
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


def test_spec03_media_contract_is_directly_consumable_by_spec04d(
    tmp_path: Path,
) -> None:
    _, _, spec03, paths = _run_pipeline(tmp_path)
    evidence_path = spec03 / "media/media_evidence_ledger.json"
    plan_path = spec03 / "media/media_representation_plan.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    plan = json.loads(plan_path.read_text(encoding="utf-8"))

    assert evidence["schema_version"] == "media-evidence-ledger/1.1"
    assert evidence["source_pdf"]["sha256"] == _sha(paths["source_pdf"])
    assert evidence["payload_hash"] == kernel._contract_payload_hash(  # noqa: SLF001
        evidence
    )
    assert len(evidence["atoms"]) == 1
    atom = evidence["atoms"][0]
    assert atom["source_block_ids"] == [
        kernel._block_id("pdf-test-atomic", "unit-p1")  # noqa: SLF001
    ]
    assert atom["inclusion_status"] == "included"
    assert atom["review_status"] == "closed"
    assert len(atom["candidates"]) == 1
    candidate = atom["candidates"][0]
    selected_path = spec03 / candidate["resolved_path"]
    assert selected_path.is_file()
    assert candidate["status"] == "usable"
    assert candidate["artifact_sha256"] == _sha(selected_path)

    assert plan["schema_version"] == "media-representation-plan/1.1"
    assert plan["media_evidence_ledger_sha256"] == _sha(evidence_path)
    assert plan["payload_hash"] == kernel._contract_payload_hash(  # noqa: SLF001
        plan
    )
    assert plan["spec_status"] == "passed"
    assert plan["open_reviews"] == 0
    representation = plan["representations"][0]
    assert representation["status"] == "closed"
    assert representation["source_block_ids"] == atom["source_block_ids"]
    assert representation["representation_type"] == "source_asset_image"
    assert representation["selected_candidate_id"] == candidate["candidate_id"]
    assert representation["artifact_sha256"] == candidate["artifact_sha256"]
    assert representation["decision_refs"] == ["stage-decision-run-3"]


def test_outline_review_task_compacts_full_ledger_without_weakening_binding(
    tmp_path: Path,
) -> None:
    _, _, spec03, paths = _run_pipeline(tmp_path)
    ledger_path = spec03 / "ledgers/canonical_block_ledger.jsonl"
    rows = [
        json.loads(line)
        for line in ledger_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    header, records = rows[0], rows[1:]
    records[0].update(
        {
            "source_type": "title",
            "source_label": "title",
            "raw_content": "Chapter 1",
            "raw_content_sha256": hashlib.sha256(b"Chapter 1").hexdigest(),
        }
    )
    records[1].update(
        {
            "source_type": "title",
            "source_label": "title",
            "raw_content": "1.1 Source-supported topic",
            "raw_content_sha256": hashlib.sha256(
                b"1.1 Source-supported topic"
            ).hexdigest(),
        }
    )
    header["current_ledger_hash"] = kernel._canonical_hash(records)
    ledger_path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in [header, *records]
        ),
        encoding="utf-8",
    )
    promotion = tmp_path / "spec03-promotion.json"
    _write_json(
        promotion,
        {
            "schema_version": "stage-promotion-manifest/1.0",
            "promotion_id": "promotion-spec03-1",
            "disposition": "promoted",
        },
    )
    output = tmp_path / "outline-review-task.json"

    result = kernel.prepare_outline_review_task(
        argparse.Namespace(
            parent=spec03,
            source_pdf=paths["source_pdf"],
            source_pdf_ref="inputs/source_pdf/artifact",
            parent_promotion=promotion,
            output=output,
        )
    )

    task = json.loads(output.read_text(encoding="utf-8"))
    assert result["title_candidates"] == 2
    assert task["schema_version"] == kernel.OUTLINE_REVIEW_TASK_SCHEMA
    assert task["title_candidate_inventory_payload_hash"] == (
        kernel._outline_title_inventory(records)["payload_hash"]
    )
    assert task["parent_binding"] == {
        "ledger_snapshot_id": header["ledger_snapshot_id"],
        "ledger_payload_hash": header["current_ledger_hash"],
        "source_pdf_sha256": _sha(paths["source_pdf"]),
        "promotion_id": "promotion-spec03-1",
        "promotion_manifest_sha256": _sha(promotion),
    }
    assert {
        item["block_id"] for item in task["title_candidates"]
    } == {
        records[0]["block_id"],
        records[1]["block_id"],
    }
    assert [
        item["candidate_index"] for item in task["title_candidates"]
    ] == [0, 1]
    assert {
        item["pdf_physical_page"]
        for item in task["allowed_source_outline_evidence"]
    } == {1, 2}
    assert all(
        item["path"] == "inputs/source_pdf/artifact"
        for item in task["allowed_source_outline_evidence"]
    )
    alternate_root = tmp_path / "alternate-run"
    alternate_root.mkdir()
    alternate_source = alternate_root / "source.pdf"
    alternate_source.write_bytes(paths["source_pdf"].read_bytes())
    alternate_output = alternate_root / "outline-review-task.json"
    kernel.prepare_outline_review_task(
        argparse.Namespace(
            parent=spec03,
            source_pdf=alternate_source,
            source_pdf_ref="inputs/source_pdf/artifact",
            parent_promotion=promotion,
            output=alternate_output,
        )
    )
    assert alternate_output.read_bytes() == output.read_bytes()
    alternate_promotion = alternate_root / "spec03-promotion.json"
    _write_json(
        alternate_promotion,
        {
            "schema_version": "stage-promotion-manifest/1.0",
            "promotion_id": "promotion-spec03-2",
            "disposition": "promoted",
            "evaluated_at": "2030-01-01T00:00:00Z",
        },
    )
    alternate_promotion_output = alternate_root / "outline-review-task-2.json"
    kernel.prepare_outline_review_task(
        argparse.Namespace(
            parent=spec03,
            source_pdf=alternate_source,
            source_pdf_ref="inputs/source_pdf/artifact",
            parent_promotion=alternate_promotion,
            output=alternate_promotion_output,
        )
    )
    alternate_task = json.loads(
        alternate_promotion_output.read_text(encoding="utf-8")
    )
    assert alternate_task["parent_binding"] != task["parent_binding"]
    assert alternate_task["task_id"] == task["task_id"]
    assert kernel.outline_model_evidence(alternate_task) == (
        kernel.outline_model_evidence(task)
    )
    assert len(kernel._canonical_bytes(task)) < 1_000_000
    assert (
        0
        < task["capacity"]["minimum_response_bytes"]
        <= task["capacity"]["maximum_response_bytes"]
        < 256_000
    )
    compact_review = tmp_path / "outline-compact-review.json"
    _write_json(
        compact_review,
        {
            "schema_version": kernel.OUTLINE_COMPACT_REVIEW_SCHEMA,
            "task_id": task["task_id"],
            "review_status": "closed",
            "selected_nodes": [
                {
                    "candidate_index": 0,
                    "level": 0,
                    "include_in_toc": True,
                },
                {
                    "candidate_index": 1,
                    "level": 1,
                    "include_in_toc": True,
                },
            ],
            "open_reviews": [],
        },
    )
    projected = tmp_path / "outline-review-bundle.json"
    projection = kernel.project_outline_review(
        argparse.Namespace(
            task=output,
            compact_review=compact_review,
            output=projected,
        )
    )
    bundle = json.loads(projected.read_text(encoding="utf-8"))
    assert projection["selected_nodes"] == 2
    assert bundle["schema_version"] == "spec04a-outline-review-bundle/1.0"
    assert bundle["parent_binding"] == task["parent_binding"]
    assert [node["title"] for node in bundle["nodes"]] == [
        "Chapter 1",
        "1.1 Source-supported topic",
    ]
    assert bundle["nodes"][0]["parent_node_id"] is None
    assert (
        bundle["nodes"][1]["parent_node_id"]
        == bundle["nodes"][0]["node_id"]
    )
    assert {
        item["pdf_physical_page"]
        for item in bundle["source_outline_evidence"]
    } == {1, 2}
    assert (
        bundle["title_candidate_disposition"][
            "candidate_inventory_payload_hash"
        ]
        == task["title_candidate_inventory_payload_hash"]
    )


@pytest.mark.parametrize(
    "source_pdf_ref",
    (
        "/tmp/source.pdf",
        "../source.pdf",
        "inputs\\source.pdf",
    ),
)
def test_outline_review_task_rejects_unsafe_source_pdf_reference(
    tmp_path: Path,
    source_pdf_ref: str,
) -> None:
    _, _, spec03, paths = _run_pipeline(tmp_path)
    promotion = tmp_path / "spec03-promotion.json"
    _write_json(
        promotion,
        {
            "schema_version": "stage-promotion-manifest/1.0",
            "promotion_id": "promotion-spec03-1",
            "disposition": "promoted",
        },
    )

    with pytest.raises(kernel.KernelContractError) as exc_info:
        kernel._outline_review_task(
            spec03,
            source_pdf=paths["source_pdf"],
            source_pdf_ref=source_pdf_ref,
            parent_promotion=promotion,
        )

    assert exc_info.value.code == "path_invalid"


def test_outline_compact_review_rejects_unordered_or_open_decisions(
    tmp_path: Path,
) -> None:
    task = {
        "schema_version": kernel.OUTLINE_REVIEW_TASK_SCHEMA,
        "required_output_schema": kernel.OUTLINE_COMPACT_REVIEW_SCHEMA,
        "task_id": "outline-review-test",
        "parent_binding": {"ledger_snapshot_id": "ledger"},
        "title_candidate_inventory_payload_hash": "a" * 64,
        "title_candidates": [
            {
                "candidate_index": index,
                "block_id": f"block-{index}",
                "raw_content": f"Title {index}",
                "pdf_physical_page": 1,
            }
            for index in range(2)
        ],
        "allowed_source_outline_evidence": [
            {
                "evidence_id": "source-pdf-page-000001",
                "kind": "source_pdf_page",
                "pdf_physical_page": 1,
                "path": "/frozen/source.pdf",
                "sha256": "b" * 64,
            }
        ],
    }
    for selected_nodes, open_reviews in (
        (
            [
                {
                    "candidate_index": 1,
                    "level": 0,
                    "include_in_toc": True,
                },
                {
                    "candidate_index": 0,
                    "level": 0,
                    "include_in_toc": True,
                },
            ],
            [],
        ),
        (
            [
                {
                    "candidate_index": 0,
                    "level": 0,
                    "include_in_toc": True,
                }
            ],
            ["unresolved"],
        ),
    ):
        compact = {
            "schema_version": kernel.OUTLINE_COMPACT_REVIEW_SCHEMA,
            "task_id": task["task_id"],
            "review_status": "closed",
            "selected_nodes": selected_nodes,
            "open_reviews": open_reviews,
        }
        try:
            kernel._project_outline_review(task, compact)  # noqa: SLF001
        except kernel.KernelContractError as exc:
            assert exc.code == "outline_compact_review_invalid"
        else:
            raise AssertionError("invalid compact outline review must fail")


def test_semantic_review_task_compacts_full_ledger_and_projects_exact_partition(
    tmp_path: Path,
) -> None:
    _, _, spec03, paths = _run_pipeline(tmp_path)
    ledger_path = spec03 / "ledgers/canonical_block_ledger.jsonl"
    rows = [
        json.loads(line)
        for line in ledger_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    header, records = rows[0], rows[1:]
    assert len(records) >= 2
    records.append(json.loads(json.dumps(records[1])))
    records[2]["block_id"] = "src-" + hashlib.sha256(
        b"second-contiguous-source-step"
    ).hexdigest()[:24]
    records.append(json.loads(json.dumps(records[2])))
    records[3]["block_id"] = "src-" + hashlib.sha256(
        b"text-compatible-chart-media"
    ).hexdigest()[:24]
    for index, record in enumerate(records):
        record["source_type"] = "text"
        record["source_label"] = "text"
        record["heading_disposition"] = None
        record["structure_memberships"] = []
        record["asset_ref"] = None
        record["media_contracts"] = []
        record["tree_context"] = {"node_path": [1]}
        record["pdf_physical_page"] = 1 if index < 4 else 2
        record["candidate_final_order"] = index + 1
    records[0].update(
        {
            "source_type": "title",
            "source_label": "title",
            "heading_disposition": "local_heading",
            "raw_content": "Worked example",
            "raw_content_sha256": hashlib.sha256(
                b"Worked example"
            ).hexdigest(),
        }
    )
    records[1].update(
        {
            "raw_content": "A source-supported worked solution.",
            "raw_content_sha256": hashlib.sha256(
                b"A source-supported worked solution."
            ).hexdigest(),
        }
    )
    records[2].update(
        {
            "raw_content": "A second contiguous source-supported step.",
            "raw_content_sha256": hashlib.sha256(
                b"A second contiguous source-supported step."
            ).hexdigest(),
        }
    )
    records[3].update(
        {
            "source_type": "text",
            "source_label": "chart",
            "media_contracts": [{"media_kind": "chart"}],
            "raw_content": "A hash-verified source chart.",
            "raw_content_sha256": hashlib.sha256(
                b"A hash-verified source chart."
            ).hexdigest(),
        }
    )
    header["current_ledger_hash"] = kernel._canonical_hash(records)
    header["spec04a_structure"] = {
        "status": "passed",
        "full_spec04_status": "not_evaluated",
        "open_reviews": 0,
    }
    ledger_path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in [header, *records]
        ),
        encoding="utf-8",
    )
    outline = spec03 / "structure/source_outline_ledger.json"
    toc = spec03 / "structure/final_toc_plan.json"
    _write_json(outline, {"schema_version": "test-outline/1"})
    _write_json(toc, {"schema_version": "test-toc/1"})
    promotion = tmp_path / "spec04a-promotion.json"
    _write_json(
        promotion,
        {
            "schema_version": "stage-promotion-manifest/1.0",
            "promotion_id": "promotion-spec04a-1",
            "disposition": "promoted",
            "stage_kind": "spec04a_structure_contract",
            "promoted_artifacts": {
                "ledger_L": {"sha256": _sha(ledger_path)},
                "source_outline_ledger": {"sha256": _sha(outline)},
                "final_toc_plan": {"sha256": _sha(toc)},
            },
        },
    )
    task_path = tmp_path / "semantic-review-task.json"
    result = kernel.prepare_semantic_review_task(
        argparse.Namespace(
            parent=spec03,
            source_pdf=paths["source_pdf"],
            source_pdf_ref="inputs/source_pdf/artifact",
            parent_promotion=promotion,
            output=task_path,
        )
    )
    task = json.loads(task_path.read_text(encoding="utf-8"))
    assert result["candidates"] == 1
    assert task["schema_version"] == kernel.SEMANTIC_REVIEW_TASK_SCHEMA
    assert task["option_protocol"]["schema_version"] == (
        "luceon.worker-v3-spec04b-total-option-index/v1"
    )
    assert task["candidates"][0]["marker"]["block_id"] == records[0]["block_id"]
    assert [
        row["block_id"] for row in task["candidates"][0]["body_options"]
    ] == [records[1]["block_id"], records[2]["block_id"]]
    assert records[3]["block_id"] not in {
        row["block_id"] for row in task["candidates"][0]["body_options"]
    }
    assert len(kernel._canonical_bytes(kernel.semantic_model_evidence(task))) < 1_000_000
    assert (
        0
        < task["capacity"]["minimum_response_bytes"]
        <= task["capacity"]["maximum_response_bytes"]
        < 256_000
    )

    compact_path = tmp_path / "semantic-compact-review.json"
    _write_json(
        compact_path,
        {
            "schema_version": kernel.SEMANTIC_COMPACT_REVIEW_SCHEMA,
            "task_id": task["task_id"],
            "review_status": "closed",
            "decisions": [
                {
                    "candidate_index": 0,
                    "option_index": (
                        task["option_protocol"]["teaching_group_role_offset"]
                        + task["semantic_role_choices"].index("worked_example")
                    ),
                }
            ],
            "open_reviews": [],
        },
    )
    projected_path = tmp_path / "semantic-review-bundle.json"
    projection = kernel.project_semantic_review(
        argparse.Namespace(
            task=task_path,
            compact_review=compact_path,
            output=projected_path,
        )
    )
    bundle = json.loads(projected_path.read_text(encoding="utf-8"))
    assert projection["teaching_groups"] == 1
    assert projection["standalone_labels"] == 0
    assert bundle["schema_version"] == "spec04b-semantic-review-bundle/1.0"
    assert bundle["parent_binding"] == task["parent_binding"]
    assert bundle["teaching_groups"][0]["marker_block_id"] == records[0]["block_id"]
    assert bundle["teaching_groups"][0]["body_block_ids"] == [
        records[1]["block_id"],
        records[2]["block_id"],
    ]
    assert bundle["teaching_groups"][0]["semantic_role"] == "worked_example"
    assert bundle["source_evidence"] == [
        {
            "evidence_id": "source-pdf-page-000001",
            "path": "inputs/source_pdf/artifact",
            "sha256": _sha(paths["source_pdf"]),
            "pdf_physical_page": 1,
        }
    ]

    invalid = json.loads(compact_path.read_text(encoding="utf-8"))
    invalid["decisions"][0]["body_count"] = 0
    with pytest.raises(kernel.KernelContractError) as exc_info:
        kernel._project_semantic_review(task, invalid)
    assert exc_info.value.code == "semantic_compact_review_invalid"


def test_semantic_option_protocol_totalizes_unavailable_group_to_standalone() -> None:
    roles = list(kernel.SEMANTIC_ROLE_CHOICES)
    candidate = {
        "candidate_index": 0,
        "allowed_dispositions": ["plain_body", "standalone_label"],
        "body_options": [],
    }
    option_index = 1 + len(roles) + roles.index("source_label")

    disposition, semantic_role, totalized = kernel._resolve_semantic_option(
        candidate,
        option_index,
        roles,
    )

    assert disposition == "standalone_label"
    assert semantic_role == "source_label"
    assert totalized is True


def test_semantic_option_protocol_is_total_for_every_frozen_index() -> None:
    roles = list(kernel.SEMANTIC_ROLE_CHOICES)
    candidates = [
        {
            "candidate_index": 0,
            "allowed_dispositions": ["plain_body", "standalone_label"],
            "body_options": [],
        },
        {
            "candidate_index": 1,
            "allowed_dispositions": [
                "plain_body",
                "standalone_label",
                "teaching_group",
            ],
            "body_options": [{"block_id": "body-1"}],
        },
    ]
    option_count = 1 + (2 * len(roles))

    for candidate in candidates:
        for option_index in range(option_count):
            disposition, semantic_role, _totalized = (
                kernel._resolve_semantic_option(
                    candidate,
                    option_index,
                    roles,
                )
            )
            assert disposition in candidate["allowed_dispositions"]
            assert semantic_role == "plain_body" or semantic_role in roles


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


def test_media_ownership_prefers_balanced_geometry_and_is_total() -> None:
    source_units = [
        {
            "source_id": "large-image",
            "physical_page": 1,
            "bbox": [0.0, 0.48, 1.0, 1.0],
            "source_type": "image",
            "source_label": "image",
            "scope_status": "included",
            "candidate_final_order": 1,
        },
        {
            "source_id": "small-image",
            "physical_page": 1,
            "bbox": [0.75, 0.90, 0.79, 0.94],
            "source_type": "image",
            "source_label": "image",
            "scope_status": "included",
            "candidate_final_order": 2,
        },
        {
            "source_id": "chart-text",
            "physical_page": 1,
            "bbox": [0.1, 0.1, 0.3, 0.2],
            "source_type": "text",
            "source_label": "text",
            "scope_status": "included",
            "candidate_final_order": 3,
        },
    ]
    media_atoms = [
        {
            "media_id": "large-media",
            "physical_page": 1,
            "bbox": [0.0, 0.48, 0.998, 0.998],
        },
        {
            "media_id": "small-media",
            "physical_page": 1,
            "bbox": [0.752, 0.902, 0.788, 0.938],
        },
        {
            "media_id": "chart-media",
            "physical_page": 1,
            "bbox": [0.1, 0.1, 0.3, 0.2],
        },
    ]

    ownership = kernel._deterministic_media_source_ownership(  # noqa: SLF001
        source_units,
        media_atoms,
    )

    assert ownership == {
        "chart-media": ["chart-text"],
        "large-media": ["large-image"],
        "small-media": ["small-image"],
    }
    assert len(
        {
            source_id
            for source_ids in ownership.values()
            for source_id in source_ids
        }
    ) == 3


def test_media_ownership_fails_closed_when_fragile_source_is_unmatched() -> None:
    with pytest.raises(kernel.KernelContractError) as exc_info:
        kernel._deterministic_media_source_ownership(  # noqa: SLF001
            [
                {
                    "source_id": "unmatched-image",
                    "physical_page": 1,
                    "bbox": [0.0, 0.0, 0.1, 0.1],
                    "source_type": "image",
                    "source_label": "image",
                    "scope_status": "included",
                    "candidate_final_order": 1,
                }
            ],
            [
                {
                    "media_id": "distant-media",
                    "physical_page": 1,
                    "bbox": [0.8, 0.8, 0.9, 0.9],
                }
            ],
        )

    assert exc_info.value.code == "media_source_ownership_unresolved"


def test_media_review_rejects_excluding_instructional_fragile_source(
    tmp_path: Path,
) -> None:
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
    with pytest.raises(kernel.KernelContractError) as exc_info:
        kernel._validate_media_review(  # noqa: SLF001
            review,
            material_id=contract["material_identity"]["material_id"],
            source_sha256=contract["material_identity"]["source_pdf_sha256"],
            media_atoms=media_atoms,
            source_ids={"unit-p1", "unit-p2"},
            review_task=review_task,
        )
    assert exc_info.value.code == "media_source_ownership_unresolved"


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


def test_media_model_evidence_uses_indexes_without_audit_identity_duplication(
    tmp_path: Path,
) -> None:
    _, spec02, _, _ = _run_pipeline(tmp_path)
    task = kernel._media_review_task(spec02)  # noqa: SLF001
    frozen = json.loads(kernel._canonical_bytes(task))  # noqa: SLF001

    projected = kernel.media_model_evidence(task)

    assert task == frozen
    assert projected["task_id"] == task["task_id"]
    assert projected["baseline_sha256"] == task["baseline_sha256"]
    assert [row["media_index"] for row in projected["media_atoms"]] == [
        row["media_index"] for row in task["media_atoms"]
    ]
    assert all("media_id" not in row for row in projected["media_atoms"])
    assert all(
        "candidate_id" not in candidate
        for row in projected["media_atoms"]
        for candidate in row["candidates"]
    )
    assert all(
        "source_id" not in unit and "raw_content_sha256" not in unit
        for group in projected["page_source_units"]
        for unit in group["source_units"]
    )
    assert len(kernel._canonical_bytes(projected)) < len(  # noqa: SLF001
        kernel._canonical_bytes(task)  # noqa: SLF001
    )
