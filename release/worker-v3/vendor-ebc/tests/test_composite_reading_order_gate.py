from __future__ import annotations

import sys
import types

import pytest

sys.modules.setdefault("pypdf", types.SimpleNamespace(PdfReader=object))

from scripts.build_native_spec01_spec02 import (
    canonical_block_id,
    detect_composite_media_order_risks,
    expand_page_reading_strategies,
    normalize_composite_relationships,
    single_column_spatial_sweep,
)


def row(raw_id: int, source_id: str, kind: str, bbox: list[float]) -> dict:
    return {
        "id": raw_id,
        "source_id": source_id,
        "type": kind,
        "page": 1,
        "bbox": bbox,
        "content": source_id,
    }


def fixture_rows() -> list[dict]:
    stem = row(1, "neutral:stem", "text", [0.05, 0.05, 0.90, 0.15])
    media = row(2, "neutral:media", "image", [0.25, 0.18, 0.75, 0.45])
    option_a = row(3, "neutral:option-a", "text", [0.10, 0.50, 0.30, 0.55])
    option_b = row(4, "neutral:option-b", "text", [0.10, 0.58, 0.30, 0.63])
    return [stem, option_a, option_b, media]


def closed_config() -> dict:
    return {
        "composite_reading_relationships": [
            {
                "relationship_id": "REL-001",
                "relationship_type": "stem_media_options",
                "physical_page": 1,
                "review_status": "closed",
                "segments": [
                    {"role": "stem", "source_ids": ["neutral:stem"]},
                    {"role": "media", "source_ids": ["neutral:media"]},
                    {"role": "options", "source_ids": ["neutral:option-a", "neutral:option-b"]},
                ],
                "evidence_refs": ["evidence/page-001.png"],
                "rationale": "The source geometry places media between the upper stem and lower options.",
            }
        ]
    }


def test_detector_is_content_and_language_agnostic() -> None:
    findings = detect_composite_media_order_risks(fixture_rows())
    assert len(findings) == 1
    assert findings[0]["media_source_id"] == "neutral:media"
    assert findings[0]["displaced_below_refs"] == [
        canonical_block_id(fixture_rows()[1]),
        canonical_block_id(fixture_rows()[2]),
    ]


def test_closed_relation_mechanically_freezes_stem_media_options() -> None:
    rows = fixture_rows()
    ordered = {1: rows.copy()}
    relationships, events = normalize_composite_relationships(closed_config(), rows, ordered)
    assert [item["source_id"] for item in ordered[1]] == [
        "neutral:stem",
        "neutral:media",
        "neutral:option-a",
        "neutral:option-b",
    ]
    assert relationships[0]["after_order"] == [
        canonical_block_id(rows[0]),
        canonical_block_id(rows[3]),
        canonical_block_id(rows[1]),
        canonical_block_id(rows[2]),
    ]
    assert events[0]["trigger_code"] == "COMPOSITE_STEM_MEDIA_OPTIONS_ORDER"
    assert events[0]["review_status"] == "closed"


def test_unbound_inversion_is_an_actionable_open_gate() -> None:
    rows = fixture_rows()
    relationships, events = normalize_composite_relationships({}, rows, {1: rows.copy()})
    assert relationships == []
    assert len(events) == 1
    assert events[0]["trigger_code"] == "COMPOSITE_MEDIA_ORDER_INVERSION_UNRESOLVED"
    assert events[0]["review_status"] == "open"
    assert events[0]["affected_source_refs"]
    assert events[0]["evidence_refs"]


def test_closed_explicit_page_order_closes_page_local_media_ambiguity() -> None:
    rows = fixture_rows()
    config = {
        "page_reading_order_strategies": [
            {
                "strategy_id": "FLOW-EXPLICIT",
                "strategy_type": "explicit_source_order",
                "start_page": 1,
                "end_page": 1,
                "review_status": "closed",
                "ordered_source_ids": [item["source_id"] for item in rows],
                "evidence_refs": ["evidence/page-001.png"],
                "rationale": "The complete page order was visually reviewed against source evidence.",
            }
        ]
    }
    relationships, events = normalize_composite_relationships(config, rows, {1: rows.copy()})
    assert relationships == []
    assert events == []


def test_spatial_page_strategy_does_not_suppress_composite_review() -> None:
    rows = fixture_rows()
    config = {
        "page_reading_order_strategies": [
            {
                "strategy_id": "FLOW-SPATIAL",
                "strategy_type": "single_column_spatial_sweep",
                "start_page": 1,
                "end_page": 1,
                "review_status": "closed",
                "evidence_refs": ["evidence/page-001.png"],
                "rationale": "The page was reviewed as a single-column flow.",
            }
        ]
    }
    relationships, events = normalize_composite_relationships(config, rows, {1: rows.copy()})
    assert relationships == []
    assert len(events) == 1
    assert events[0]["review_status"] == "open"


def test_relation_rejects_unsupported_role_geometry() -> None:
    rows = fixture_rows()
    config = closed_config()
    rows[3]["bbox"] = [0.25, 0.70, 0.75, 0.85]
    with pytest.raises(ValueError, match="source vertical geometry"):
        normalize_composite_relationships(config, rows, {1: rows.copy()})


def test_relation_rejects_cross_relationship_overlap() -> None:
    rows = fixture_rows()
    config = closed_config()
    duplicate = {**config["composite_reading_relationships"][0], "relationship_id": "REL-002"}
    config["composite_reading_relationships"].append(duplicate)
    with pytest.raises(ValueError, match="overlaps another composite relationship"):
        normalize_composite_relationships(config, rows, {1: rows.copy()})


def test_reviewed_spatial_sweep_reanchors_media_without_text_or_language_rules() -> None:
    rows = fixture_rows()
    ordered, quantum = single_column_spatial_sweep(rows, {row["id"]: index for index, row in enumerate(rows)})
    assert quantum > 0
    assert [item["source_id"] for item in ordered] == [
        "neutral:stem",
        "neutral:media",
        "neutral:option-a",
        "neutral:option-b",
    ]
    assert detect_composite_media_order_risks(ordered) == []


def test_page_strategy_rejects_overlap() -> None:
    strategy = {
        "strategy_id": "FLOW-1",
        "strategy_type": "single_column_spatial_sweep",
        "start_page": 1,
        "end_page": 2,
        "review_status": "closed",
        "evidence_refs": ["page.png"],
        "rationale": "Reviewed single-column source geometry.",
    }
    with pytest.raises(ValueError, match="multiple page reading strategies"):
        expand_page_reading_strategies(
            {"page_reading_order_strategies": [strategy, {**strategy, "strategy_id": "FLOW-2"}]},
            2,
        )


def test_explicit_page_strategy_requires_one_page_and_unique_full_candidate_order() -> None:
    strategy = {
        "strategy_id": "FLOW-EXPLICIT",
        "strategy_type": "explicit_source_order",
        "start_page": 1,
        "end_page": 2,
        "review_status": "closed",
        "ordered_source_ids": ["neutral:a"],
        "evidence_refs": ["page.png"],
        "rationale": "The source layout needs an explicit reviewed order.",
    }
    with pytest.raises(ValueError, match="exactly one page"):
        expand_page_reading_strategies({"page_reading_order_strategies": [strategy]}, 2)
