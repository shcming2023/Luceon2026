from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SPEC06 = load_script("build_delivery_set_spec06_review_pack.py")


def test_single_volume_has_no_synthetic_cross_volume_boundary() -> None:
    partition = {
        "volumes": [{
            "volume_id": "volume-01", "render_order_start": 1, "render_order_end": 8,
            "render_node_ids": ["render-1", "render-8"],
        }],
        "cross_volume_contract": {"cross_volume_parent_dependencies": 0},
    }
    audit = SPEC06.build_boundary_audit(partition)
    assert audit == {
        "mode": "single_volume", "volume_id": "volume-01", "render_order_start": 1,
        "render_order_end": 8, "contiguous": True, "dependency_groups_split": [],
    }


def test_two_volume_boundary_requires_contiguous_orders_and_no_dependency_split() -> None:
    partition = {
        "volumes": [
            {"volume_id": "volume-01", "render_order_start": 1, "render_order_end": 8, "render_node_ids": ["r1", "r8"]},
            {"volume_id": "volume-02", "render_order_start": 9, "render_order_end": 12, "render_node_ids": ["r9", "r12"]},
        ],
        "cross_volume_contract": {"cross_volume_parent_dependencies": 0},
    }
    audit = SPEC06.build_boundary_audit(partition)
    assert audit["mode"] == "two_volume"
    assert audit["contiguous"] is True
    assert audit["dependency_groups_split"] == []
    partition["cross_volume_contract"]["cross_volume_parent_dependencies"] = 1
    assert SPEC06.build_boundary_audit(partition)["dependency_groups_split"]


def test_failed_delivery_set_disposition_is_schema_valid_and_fail_closed() -> None:
    run = ROOT / "regression_samples/blind-qualification/sample-006-igcse-0580-oxford-2023/runs/spec06-delivery-set-v1e-two-volume"
    if not run.is_dir():
        return
    schema = json.loads((ROOT / "specs/schemas/spec06-delivery-set-disposition.schema.json").read_text())
    disposition = json.loads((run / "final_acceptance.json").read_text())
    assert schema["properties"]["schema_version"]["const"] == disposition["schema_version"]
    assert all(key in disposition for key in schema["required"])
    assert 1 <= len(disposition["volumes"]) <= 2
    assert disposition["manifest_kind"] == "spec06_failure_commit"
    assert disposition["spec_status"] == "failed"
    assert disposition["acceptance_status"] == "not_ready"
    assert disposition["final_verified_ledger_L"] is None
    assert len(disposition["volumes"]) == 2


def test_contracts_keep_limits_per_volume_and_cap_cardinality_at_two() -> None:
    agents = (ROOT / "AGENTS.md").read_text()
    spec05 = (ROOT / "specs/05-template-freeze-and-compile-spec.md").read_text()
    spec06 = (ROOT / "specs/06-final-page-review-and-acceptance-spec.md").read_text()
    assert "严格小于 `50,000,000`" in agents
    assert "严格小于 `2,000`" in agents
    assert "一卷或两卷" in agents
    assert "CP-H24" in spec05
    assert "FR-H22" in spec06 and "FR-H23" in spec06 and "FR-H24" in spec06
