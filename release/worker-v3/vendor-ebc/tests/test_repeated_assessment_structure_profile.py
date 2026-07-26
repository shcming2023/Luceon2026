from __future__ import annotations

import importlib.util
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_repeated_assessment_structure_profile.py"
SPEC = importlib.util.spec_from_file_location("assessment_structure_profile", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def node(order: int, text: str, block_id: str, construct: str = "paragraph") -> dict:
    payload_key = "title" if construct == "subsubsection*" else "raw_content"
    return {
        "render_order": order,
        "target_construct": construct,
        "payload": {payload_key: text},
        "source_block_ids": [block_id],
        "pdf_physical_pages": [1],
    }


def test_question_ranges_close_exact_expected_set() -> None:
    nodes = [
        node(1, "Questions 1-14", "a"),
        node(2, "Questions 15~28", "b"),
        node(3, "Questions29–42", "c"),
    ]
    evidence = MODULE.question_evidence(
        nodes,
        re.compile(r"^\s*(\d{1,2})(?:\s*[.)．、,]|\s+)"),
        re.compile(r"Questions?\s*(\d{1,2})\s*[-–—~～]\s*(\d{1,2})", re.I),
        [],
        {},
        42,
    )
    assert set(evidence) == set(range(1, 43))


def test_out_of_contract_numbers_do_not_mask_missing_question() -> None:
    nodes = [node(number, f"{number}. Question", f"q{number}") for number in range(1, 42)]
    nodes.append(node(50, "50 line-number text", "line-50"))
    evidence = MODULE.question_evidence(
        nodes,
        re.compile(r"^\s*(\d{1,2})(?:\s*[.)．、,]|\s+)"),
        re.compile(r"Questions?\s*(\d{1,2})\s*[-–—~～]\s*(\d{1,2})", re.I),
        [],
        {},
        42,
    )
    assert 42 not in evidence
    assert 50 not in evidence


def test_composite_explanation_heading_emits_one_anchor_for_two_slots() -> None:
    profile = {
        "optional_slot_ids": [
            "answers",
            "listening_script",
            "listening_explanations",
            "language_explanations",
            "reading_explanations",
        ],
        "optional_detection": {
            "context_patterns": {"listening": [], "language": [], "reading": [r"^Reading$"]},
            "slot_marker_patterns": {
                "answers": [],
                "listening_script": [],
                "listening_explanations": [],
                "language_explanations": [],
                "reading_explanations": [],
            },
            "answer_content_patterns": [],
            "transcript_content_patterns": [],
            "explanation_content_patterns": [r"^Answer Explanation$"],
            "implicit_explanation_patterns": [],
            "structure_heading_patterns": [r"^Answer Explanation$"],
            "heading_max_characters": 120,
        },
    }
    slots, anchors = MODULE.optional_features(
        [
            node(1, "Reading", "context", "subsubsection*"),
            node(2, "Answer Explanation", "shared", "subsubsection*"),
        ],
        profile,
    )
    assert slots["answers"]["status"] == "composite_present"
    assert slots["reading_explanations"]["status"] == "composite_present"
    assert len(anchors) == 1
    assert anchors[0]["source_block_id"] == "shared"
    assert anchors[0]["slot_ids"] == ["answers", "reading_explanations"]


def test_shared_producer_contains_no_sample_identity_switches() -> None:
    source = SCRIPT.read_text(encoding="utf-8").lower()
    for forbidden in (
        "toefl",
        "src-5d874c198b4ffec71772535c",
        "sample-005",
        "小托福",
        "page-344",
    ):
        assert forbidden not in source
