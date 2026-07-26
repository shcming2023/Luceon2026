import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_source_lineage_integrity.py"
SPEC = importlib.util.spec_from_file_location("source_lineage", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def fixture(tmp_path: Path, schema: str) -> tuple[Path, Path, dict, list[dict]]:
    evidence = tmp_path / "page-001.png"
    evidence.write_bytes(b"review evidence")
    audit = {
        "schema_version": schema,
        "risk_pages": [1],
        "risk_events": [{
            "event_id": "ORDER-STRATEGY-P001", "physical_page": 1,
            "trigger_kind": "reviewed_page_flow_strategy", "trigger_code": "SINGLE_COLUMN_SPATIAL_SWEEP",
            "action_kind": "reanchor_reading_order", "signal_only": False,
            "requires_human_review": True, "review_status": "closed",
            "affected_source_refs": ["src-a", "src-b"],
            "before_order": ["src-a", "src-b"], "after_order": ["src-b", "src-a"],
            "evidence_refs": [str(evidence)],
        }],
        "pages": [{"physical_page": 1, "risk_event_ids": ["ORDER-STRATEGY-P001"],
                   "ordered_block_ids": ["src-b", "src-a"]}],
    }
    review = {
        "status": "closed", "reviewed_pages": [1], "risk_pages": [1],
        "closed_risk_event_ids": ["ORDER-STRATEGY-P001"],
    }
    audit_path, review_path = tmp_path / "audit.json", tmp_path / "review.json"
    write_json(audit_path, audit)
    write_json(review_path, review)
    records = [{"block_id": "src-a", "pdf_physical_page": 1}, {"block_id": "src-b", "pdf_physical_page": 1}]
    return audit_path, review_path, {"material_identity": {"page_count": 1}}, records


def test_source_order_audit_21_page_flow_is_accepted(tmp_path: Path) -> None:
    audit, review, header, records = fixture(tmp_path, "source-order-audit/2.1")
    result = MODULE.validate_review_precision(audit, review, header, records, "formal_full_source")
    assert result["source_order_audit_schema"] == "source-order-audit/2.1"
    assert result["risk_events"] == 1


def test_unknown_source_order_audit_version_is_rejected(tmp_path: Path) -> None:
    audit, review, header, records = fixture(tmp_path, "source-order-audit/2.2")
    with pytest.raises(ValueError, match="must use one of"):
        MODULE.validate_review_precision(audit, review, header, records, "formal_full_source")

