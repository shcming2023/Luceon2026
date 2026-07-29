from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.workflow_v3 import stage_evaluators
from app.workflow_v3.stage_evaluation_entrypoint import (
    EvaluationInput,
    StageEvaluationRequest,
)
from app.workflow_v3.stage_evaluators import STAGE_GATES


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _bundle(root: Path, *, incorrect: int) -> Path:
    bundle = root / "bundle"
    nodes = []
    source_entries = []
    final_entries = []
    for index in range(100):
        entry_id = f"entry-{index:03d}"
        node_id = f"node-{index:03d}"
        nodes.append(
            {
                "node_id": node_id,
                "title": f"Title {index}",
                "level": 0,
                "source_outline_evidence_ids": ["source-toc-page"],
                "heading_evidence_block_ids": [f"block-{index:03d}"],
                "source_toc_entry_ids": [] if index < incorrect else [entry_id],
                "final_toc": {
                    "include": True,
                    "title": f"Title {index}",
                    "level": 0,
                },
            }
        )
        source_entries.append(
            {
                "entry_id": entry_id,
                "scope_status": "included",
                "target_node_id": node_id,
                "match_status": "exact",
                "source_outline_evidence_ids": ["source-toc-page"],
            }
        )
        final_entries.append(
            {
                "node_id": node_id,
                "title": f"Title {index}",
                "level": 0,
            }
        )
    _write_json(
        bundle / "structure/source_outline_ledger.json",
        {
            "source_outline_evidence": [
                {
                    "evidence_id": "source-toc-page",
                    "pdf_physical_page": 1,
                }
            ],
            "source_toc_entries": source_entries,
            "body_hierarchy": nodes,
        },
    )
    _write_json(
        bundle / "structure/final_toc_plan.json",
        {"entries": final_entries},
    )
    return bundle


def _request(root: Path) -> StageEvaluationRequest:
    return StageEvaluationRequest(
        job_id="job-outline",
        stage_key="outline_reconstruction",
        stage_version="spec04a.v1",
        attempt=1,
        candidate=None,  # type: ignore[arg-type]
        release_manifest_sha256="1" * 64,
        policy_sha256="2" * 64,
        required_gates=STAGE_GATES["outline_reconstruction"],
        output_manifest="evaluation-manifest.json",
        workdir=root,
    )


@pytest.mark.parametrize(
    ("incorrect", "expected_pass", "expected_basis_points"),
    ((1, True, 9_900), (2, False, 9_800)),
)
def test_outline_accuracy_is_recomputed_at_exact_99_percent_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    incorrect: int,
    expected_pass: bool,
    expected_basis_points: int,
) -> None:
    bundle = _bundle(tmp_path, incorrect=incorrect)
    monkeypatch.setattr(
        stage_evaluators,
        "run_release_python_kernel",
        lambda **_: SimpleNamespace(returncode=0, stderr=""),
    )

    result = stage_evaluators.evaluate_stage(
        _request(tmp_path),
        EvaluationInput(bundle, {}),
        tmp_path,
    )

    assert (
        result.gate_results["outline_accuracy_at_least_99_percent"]
        is expected_pass
    )
    measurement = next(
        item
        for item in result.findings
        if item["code"] == "outline_accuracy_measurement"
    )
    assert measurement["accuracy_basis_points"] == expected_basis_points
    assert measurement["total_units"] == 100


def test_outline_accuracy_accepts_source_evidenced_depth_beyond_subsection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _bundle(tmp_path, incorrect=0)
    outline_path = bundle / "structure/source_outline_ledger.json"
    outline = json.loads(outline_path.read_text(encoding="utf-8"))
    final_path = bundle / "structure/final_toc_plan.json"
    final = json.loads(final_path.read_text(encoding="utf-8"))
    for index in range(3, 100):
        outline["body_hierarchy"][index]["level"] = 3
        outline["body_hierarchy"][index]["final_toc"]["level"] = 3
        final["entries"][index]["level"] = 3
    _write_json(outline_path, outline)
    _write_json(final_path, final)
    monkeypatch.setattr(
        stage_evaluators,
        "run_release_python_kernel",
        lambda **_: SimpleNamespace(returncode=0, stderr=""),
    )

    result = stage_evaluators.evaluate_stage(
        _request(tmp_path),
        EvaluationInput(bundle, {}),
        tmp_path,
    )

    assert result.gate_results["outline_accuracy_at_least_99_percent"] is True
    measurement = next(
        item
        for item in result.findings
        if item["code"] == "outline_accuracy_measurement"
    )
    assert measurement["accuracy_basis_points"] == 10_000
