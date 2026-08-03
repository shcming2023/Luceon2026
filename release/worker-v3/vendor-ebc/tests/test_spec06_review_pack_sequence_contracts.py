from scripts.build_spec06_review_pack import (
    locate_source_pdf,
    longest_nondecreasing_anchor_ids,
    normalized_last_body_line_without_footer,
    normalized_page_body_without_footer,
    ordered_toc_outline_pairs,
    ordered_toc_text_line_pairs,
)


def test_anchor_filter_keeps_equal_pages_and_drops_backward_outlier():
    pairs = [
        (1, 4, "a"),
        (2, 4, "b"),
        (3, 7, "c"),
        (4, 2, "false-repeat-hit"),
        (5, 8, "d"),
    ]
    selected = longest_nondecreasing_anchor_ids(pairs)
    assert selected == {"a", "b", "c", "d"}


def test_repeated_toc_titles_bind_by_order_when_sequences_match():
    nodes = [
        {"render_node_id": "n1", "payload": {"title": "Practice Test 1"}},
        {"render_node_id": "n2", "payload": {"title": "Practice Test 1"}},
    ]
    outline = [
        {"title": "PRACTICE TEST 1", "physical_page": 10},
        {"title": "Practice Test 1", "physical_page": 30},
    ]
    result = ordered_toc_outline_pairs(nodes, outline)
    assert result["n1"]["physical_page"] == 10
    assert result["n2"]["physical_page"] == 30


def test_toc_order_mismatch_remains_unbound():
    nodes = [
        {"render_node_id": "n1", "payload": {"title": "A"}},
        {"render_node_id": "n2", "payload": {"title": "B"}},
    ]
    outline = [
        {"title": "B", "physical_page": 10},
        {"title": "A", "physical_page": 30},
    ]
    assert ordered_toc_outline_pairs(nodes, outline) == {}


def test_repeated_toc_text_lines_bind_by_visible_order():
    nodes = [
        {"render_node_id": "n1", "payload": {"title": "Practice Test 1"}},
        {"render_node_id": "n2", "payload": {"title": "Practice Test 1"}},
    ]
    lines = ["Contents", "Practice Test 1 153", "Practice Test 1 278"]
    assert ordered_toc_text_line_pairs(nodes, lines) == {
        "n1": "Practice Test 1 153",
        "n2": "Practice Test 1 278",
    }


def test_toc_text_line_missing_duplicate_remains_unbound():
    nodes = [
        {"render_node_id": "n1", "payload": {"title": "Practice Test 1"}},
        {"render_node_id": "n2", "payload": {"title": "Practice Test 1"}},
    ]
    assert ordered_toc_text_line_pairs(nodes, ["Practice Test 1 153"]) == {}


def test_numeric_heading_does_not_match_trailing_page_footer():
    assert normalized_page_body_without_footer("Explanation\n1\nText\n232\n", "232") == "explanation1text"
    assert normalized_last_body_line_without_footer("Explanation\n19\n233\n", "233") == "19"


def test_locate_source_pdf_supports_legacy_intake_layout(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    expected = source / "original.pdf"
    expected.write_bytes(b"%PDF-test")
    assert locate_source_pdf(tmp_path) == expected
