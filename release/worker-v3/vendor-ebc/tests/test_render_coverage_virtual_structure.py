from scripts.build_render_coverage import (
    included_source_pages_emitted,
    source_supported_virtual_structure,
)


def test_accepts_source_title_with_separately_emitted_body_anchor():
    node = {
        "node_kind": "book_structure",
        "source_block_ids": [],
        "virtual_source_supported": True,
        "source_evidence_ids": ["outline-title-1"],
        "payload": {
            "structure_node_id": "assessment-1",
            "title": "2026 AMC 8 Solutions",
            "source_evidence_block_ids": ["title", "question-1"],
            "separate_body_block_ids": ["question-1"],
            "media_evidence_block_ids": [],
            "title_source_fragments": [],
        },
    }
    by_block = {
        "title": {"raw_content": "2026 AMC 8 Solutions"},
        "question-1": {"raw_content": "1. What is the value?"},
    }

    assert source_supported_virtual_structure(
        node,
        by_block,
        {"question-1"},
        {"question-1": ["render-question-1"]},
    )


def test_rejects_unemitted_or_invented_virtual_structure():
    node = {
        "node_kind": "book_structure",
        "source_block_ids": [],
        "virtual_source_supported": False,
        "source_evidence_ids": ["outline-title-1"],
        "payload": {
            "structure_node_id": "assessment-1",
            "title": "Invented title",
            "source_evidence_block_ids": ["title", "question-1"],
            "separate_body_block_ids": ["question-1"],
            "media_evidence_block_ids": [],
            "title_source_fragments": [],
        },
    }
    by_block = {
        "title": {"raw_content": "2026 AMC 8 Solutions"},
        "question-1": {"raw_content": "1. What is the value?"},
    }

    assert not source_supported_virtual_structure(node, by_block, {"question-1"}, {})
    assert not source_supported_virtual_structure(
        node,
        by_block,
        {"question-1"},
        {"question-1": ["render-question-1"]},
    )


def test_accepts_excluded_title_evidence_with_external_included_anchor():
    node = {
        "node_kind": "book_structure",
        "source_block_ids": [],
        "virtual_source_supported": True,
        "source_evidence_ids": ["outline-title-1"],
        "payload": {
            "structure_node_id": "assessment-1",
            "title": "Practice Test 01",
            "source_evidence_block_ids": ["excluded-title"],
            "separate_body_block_ids": ["first-body"],
            "media_evidence_block_ids": [],
            "title_source_fragments": [],
        },
    }
    by_block = {
        "excluded-title": {"raw_content": "TOEFL Junior Practice Test 01 Answer Key"},
        "first-body": {"raw_content": "Directions"},
    }

    assert source_supported_virtual_structure(
        node,
        by_block,
        {"first-body"},
        {"first-body": ["render-first-body"]},
    )
    assert not source_supported_virtual_structure(
        node,
        by_block,
        {"first-body"},
        {"first-body": []},
    )


def test_included_page_coverage_allows_excluded_numeric_gaps():
    rows = [
        {"block_id": "a", "pdf_physical_page": 1},
        {"block_id": "b", "pdf_physical_page": 3},
    ]
    assert included_source_pages_emitted(
        rows,
        {"a": ["render-a"], "b": ["render-b"]},
    )
    assert not included_source_pages_emitted(
        rows,
        {"a": ["render-a"], "b": []},
    )
