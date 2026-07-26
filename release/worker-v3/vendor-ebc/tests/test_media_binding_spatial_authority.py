import argparse
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bind_normalized_media_to_source_ledger.py"
SPEC = importlib.util.spec_from_file_location("media_binding", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_spatial_coverage_overrides_reused_asset_identity(tmp_path: Path) -> None:
    records = [
        {
            "record_type": "source_block", "block_id": "src-option-a", "pdf_physical_page": 1,
            "bbox": [0.10, 0.10, 0.20, 0.20], "source_label": "image", "scope_status": "included",
            "asset_ref": "images/reused.jpg",
        },
        {
            "record_type": "source_block", "block_id": "src-option-b", "pdf_physical_page": 1,
            "bbox": [0.20, 0.10, 0.30, 0.20], "source_label": "image", "scope_status": "included",
            "asset_ref": "images/other.jpg",
        },
        {
            "record_type": "source_block", "block_id": "src-question-figure", "pdf_physical_page": 1,
            "bbox": [0.10, 0.40, 0.30, 0.50], "source_label": "image", "scope_status": "included",
            "asset_ref": "images/reused.jpg",
        },
    ]
    header = {
        "record_type": "ledger_header", "ledger_checkpoint": "source_reconciled", "spec_status": "passed",
        "ledger_snapshot_id": "fixture-source-v1", "current_ledger_hash": MODULE.canonical_hash(records),
    }
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("\n".join(json.dumps(row) for row in [header, *records]) + "\n", encoding="utf-8")
    normalized = {
        "schema_version": "normalized-media-candidates/1.0", "adapter": "fixture", "summary": {},
        "atoms": [
            {
                "media_id": "media-options", "source_page": 1, "media_kind": "image",
                "bbox": [0.10, 0.10, 0.30, 0.20],
                "candidates": [{"representation_type": "source_asset_image", "path": "images/other.jpg"}],
            },
            {
                "media_id": "media-question", "source_page": 1, "media_kind": "image",
                "bbox": [0.10, 0.40, 0.30, 0.50],
                "candidates": [{"representation_type": "source_asset_image", "path": "images/reused.jpg"}],
            },
        ],
    }
    source = tmp_path / "normalized.json"
    output = tmp_path / "bound.json"
    report = tmp_path / "report.json"
    write_json(source, normalized)

    MODULE.bind(argparse.Namespace(
        normalized_candidates=source, parent_ledger=ledger, output=output, report=report,
        minimum_iou=0.25, review_iou=0.50, minimum_fragment_coverage=0.50,
    ))

    atoms = {atom["media_id"]: atom for atom in json.loads(output.read_text())["atoms"]}
    assert atoms["media-options"]["source_block_ids"] == ["src-option-a", "src-option-b"]
    assert atoms["media-question"]["source_block_ids"] == ["src-question-figure"]
    assert atoms["media-options"]["source_ledger_binding"]["fragment_assignment_evidence"]["src-option-a"]["asset_identity_match"] is False
    assert json.loads(report.read_text())["summary"]["canonical_media_blocks_unbound"] == 0

