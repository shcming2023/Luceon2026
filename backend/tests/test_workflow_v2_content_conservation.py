import json

from app.workflow_v2.content_conservation import build_canonical_conservation
from app.workflow_v2.runtime.canonical.scripts.bootstrap_clean_markdown import (
    IMAGE_BACKED_VISUAL_TYPES,
    eligible_block_for_assignment,
    is_noise_block,
)


def _fixture(tmp_path, blocks, assignments, *, included=(1, 10)):
    source = tmp_path / "source"
    canonical = tmp_path / "canonical"
    source.mkdir()
    canonical.mkdir()
    (source / "content_list.json").write_text(json.dumps(blocks), encoding="utf-8")
    (canonical / "manifest.json").write_text(
        json.dumps({"source_root": str(source), "content_file": "content_list.json", "included_page_range": {"start_page": included[0], "end_page": included[1]}}),
        encoding="utf-8",
    )
    (canonical / "raw_block_assignments.jsonl").write_text("".join(json.dumps(row) + "\n" for row in assignments), encoding="utf-8")
    (canonical / "image_closure_report.json").write_text(json.dumps({"markdown_refs_not_copied": []}), encoding="utf-8")
    (canonical / "outline_decision.json").write_text(json.dumps({"final_outline": []}), encoding="utf-8")
    (canonical / "outline_candidates.jsonl").write_text("", encoding="utf-8")
    clean_lines = []
    current_page = None
    for assignment in assignments:
        index = int(str(assignment["block_ref"]).rsplit("-", 1)[-1])
        block = blocks[index]
        page = block.get("page_idx")
        if page != current_page:
            clean_lines.append(f"<!-- page_idx: {page} -->")
            current_page = page
        if block.get("img_path"):
            clean_lines.append(f"![image]({block['img_path']})")
        elif block.get("text"):
            clean_lines.append(block["text"])
    (canonical / "clean.md").write_text("\n\n".join(clean_lines) + "\n", encoding="utf-8")
    return canonical


def test_every_source_block_gets_allowlisted_disposition(tmp_path):
    blocks = [
        {"type": "text", "text": "Cover", "page_idx": 0, "bbox": [0, 0, 100, 20]},
        {"type": "text", "text": "Question 1", "page_idx": 1, "bbox": [0, 150, 100, 200]},
        {"type": "text", "text": "", "page_idx": 1},
    ]
    canonical = _fixture(tmp_path, blocks, [{"block_ref": "content-list-000001", "unit_id": "unit-1"}])

    result = build_canonical_conservation(canonical)

    assert result["status"] == "passed"
    assert result["action_counts"] == {"preserve": 1, "remove_empty": 1, "remove_out_of_scope": 1}
    assert len(json.loads((canonical / "blocks.json").read_text())["blocks"]) == 3


def test_numbered_body_sequence_outside_declared_range_is_a_hard_blocker(tmp_path):
    blocks = [
        {"type": "text", "text": f"{number}. Problem {number}", "page_idx": number - 1}
        for number in range(1, 7)
    ] + [{"type": "text", "text": "Included body", "page_idx": 6}]
    canonical = _fixture(
        tmp_path,
        blocks,
        [{"block_ref": "content-list-000006", "unit_id": "unit-1"}],
        included=(6, 6),
    )

    result = build_canonical_conservation(canonical)

    assert result["status"] == "review"
    assert "body_content_detected_outside_included_page_range" in {
        row["code"] for row in result["blockers"]
    }
    assert result["gates"]["excluded_scope_has_no_numbered_body_sequence"] is False
    assert result["action_counts"] == {"preserve": 1, "remove_out_of_scope": 6}


def test_unexplained_source_block_loss_blocks_stage(tmp_path):
    canonical = _fixture(
        tmp_path,
        [{"type": "text", "text": "A unique body paragraph", "page_idx": 2, "bbox": [0, 200, 100, 250]}],
        [],
    )

    result = build_canonical_conservation(canonical)

    assert result["status"] == "review"
    assert result["unexplained_block_count"] == 1
    assert result["blockers"][0]["code"] == "unexplained_source_block_loss"


def test_recovered_first_page_title_keeps_source_lineage(tmp_path):
    blocks = [
        {"type": "header", "text": "A Source-Evidenced Reference List", "page_idx": 0, "bbox": [0, 10, 500, 40]},
        {"type": "table", "table_body": "<table><tr><td>A</td></tr></table>", "page_idx": 0},
    ]
    canonical = _fixture(
        tmp_path,
        blocks,
        [{"block_ref": "content-list-000001", "unit_id": "unit-1"}],
        included=(0, 0),
    )
    (canonical / "clean.md").write_text(
        "<!-- page_idx: 0 -->\n# A Source-Evidenced Reference List\n<table><tr><td>A</td></tr></table>\n",
        encoding="utf-8",
    )
    (canonical / "outline_free_table_recovery_report.json").write_text(
        json.dumps({
            "changed": True,
            "root_source_order": 0,
            "root_title": "A Source-Evidenced Reference List",
        }),
        encoding="utf-8",
    )

    result = build_canonical_conservation(canonical)
    mapping = json.loads((canonical / "clean-block-map.json").read_text())

    assert result["status"] == "passed"
    assert mapping["unmapped_clean_block_ids"] == []
    assert any(row["source_block_id"] == "content-list-000000" for row in mapping["mappings"])


def test_exact_body_representation_wins_over_same_title_outline_reference(tmp_path):
    blocks = [{"type": "text", "text": "1.3", "page_idx": 2}]
    canonical = _fixture(
        tmp_path,
        blocks,
        [{"block_ref": "content-list-000000", "unit_id": "unit-1"}],
    )
    (canonical / "outline_decision.json").write_text(
        json.dumps({
            "final_outline": [{
                "title": "1.3",
                "page": 20,
                "candidate_ids": ["heading-1"],
            }],
        }),
        encoding="utf-8",
    )
    (canonical / "outline_candidates.jsonl").write_text(
        json.dumps({
            "candidate_id": "heading-1",
            "title_text": "1.3",
            "block_ids": ["content-list-000000"],
        }) + "\n",
        encoding="utf-8",
    )

    result = build_canonical_conservation(canonical)

    assert result["status"] == "passed"
    assert result["action_counts"] == {"preserve": 1}


def test_allowlisted_ocr_operations_consume_repeated_source_values_one_to_one(tmp_path):
    blocks = [
        {"type": "equation", "latex": "$1 2 + 3$", "page_idx": 2},
        {"type": "equation", "latex": "$1 2 + 3$", "page_idx": 2},
    ]
    canonical = _fixture(
        tmp_path,
        blocks,
        [
            {"block_ref": "content-list-000000", "unit_id": "unit-1"},
            {"block_ref": "content-list-000001", "unit_id": "unit-1"},
        ],
    )
    (canonical / "clean.md").write_text(
        "<!-- page_idx: 2 -->\n$12 + 3$\n$12 + 3$\n",
        encoding="utf-8",
    )
    (canonical / "math_ocr_repair_report.json").write_text(
        json.dumps({
            "operations": [
                {"line": 2, "before": "$1 2 + 3$", "after": "$12 + 3$"},
                {"line": 3, "before": "$1 2 + 3$", "after": "$12 + 3$"},
            ],
        }),
        encoding="utf-8",
    )

    result = build_canonical_conservation(canonical)
    mapping = json.loads((canonical / "clean-block-map.json").read_text())

    assert result["status"] == "passed"
    assert len(mapping["ocr_correction_operations"]) == 2
    assert {row["source_block_id"] for row in mapping["ocr_correction_operations"]} == {
        "content-list-000000",
        "content-list-000001",
    }


def test_allowlisted_ocr_operation_can_be_embedded_in_prose(tmp_path):
    before = "If a is zero, then $-0 = _0 = _1 0 0$ PQ."
    after = "If a is zero, then $-0 = _0 = _1 00$ PQ."
    canonical = _fixture(
        tmp_path,
        [{"type": "text", "text": before, "page_idx": 2}],
        [{"block_ref": "content-list-000000", "unit_id": "unit-1"}],
    )
    (canonical / "clean.md").write_text(f"<!-- page_idx: 2 -->\n{after}\n", encoding="utf-8")
    (canonical / "math_ocr_repair_report.json").write_text(
        json.dumps({"operations": [{"line": 2, "before": "$-0 = _0 = _1 0 0$", "after": "$-0 = _0 = _1 00$"}]}),
        encoding="utf-8",
    )

    result = build_canonical_conservation(canonical)

    assert result["status"] == "passed"


def test_table_numeric_grouping_spaces_are_allowlisted_formatting_changes(tmp_path):
    source_table = "<table><tr><td>0</td><td>$42 000.00</td></tr><tr><td>1</td><td>$13 762.56</td></tr></table>"
    clean_table = "<table><tr><td>0</td><td>$42000.00</td></tr><tr><td>1</td><td>$13762.56</td></tr></table>"
    canonical = _fixture(
        tmp_path,
        [{"type": "table", "table_body": source_table, "page_idx": 2}],
        [{"block_ref": "content-list-000000", "unit_id": "unit-1"}],
    )
    (canonical / "clean.md").write_text(f"<!-- page_idx: 2 -->\n{clean_table}\n", encoding="utf-8")

    result = build_canonical_conservation(canonical)
    mapping = json.loads((canonical / "clean-block-map.json").read_text())

    assert result["status"] == "passed"
    operation = mapping["ocr_correction_operations"][0]
    assert operation["operation"] == "numeric_grouping_format_normalization"
    assert operation["reason"] == "remove_thousands_group_spaces_inside_numeric_table_cells"


def test_repeated_source_label_maps_to_heading_and_plain_occurrence(tmp_path):
    blocks = [
        {"type": "text", "text": "Solution: (A).", "page_idx": 2},
        {"type": "text", "text": "Solution: (A).", "page_idx": 2},
    ]
    canonical = _fixture(
        tmp_path,
        blocks,
        [
            {"block_ref": "content-list-000000", "unit_id": "unit-1"},
            {"block_ref": "content-list-000001", "unit_id": "unit-1"},
        ],
    )
    (canonical / "clean.md").write_text(
        "<!-- page_idx: 2 -->\n## Solution: (A).\nSolution: (A).\n",
        encoding="utf-8",
    )
    (canonical / "outline_decision.json").write_text(
        json.dumps({"final_outline": [{"title": "Solution: (A).", "page": 2}]}),
        encoding="utf-8",
    )

    result = build_canonical_conservation(canonical)
    mapping = json.loads((canonical / "clean-block-map.json").read_text())

    assert result["status"] == "passed"
    assert len(mapping["mappings"]) == 2


def test_structural_page_noise_is_removed_before_exact_clean_matching(tmp_path):
    blocks = [
        {"type": "header", "text": "Electricity", "page_idx": 2, "bbox": [20, 12, 300, 42]},
        {"type": "page_number", "text": "6", "page_idx": 2, "bbox": [280, 760, 320, 790]},
        {"type": "text", "text": "Electricity", "page_idx": 2, "bbox": [20, 180, 300, 220]},
    ]
    canonical = _fixture(
        tmp_path,
        blocks,
        [{"block_ref": "content-list-000002", "unit_id": "unit-1"}],
    )

    result = build_canonical_conservation(canonical)
    ledger = json.loads((canonical / "cleaning-ledger.json").read_text())["entries"]

    assert result["status"] == "passed"
    assert [row["action"] for row in ledger] == [
        "remove_structural_page_noise",
        "remove_structural_page_noise",
        "preserve",
    ]


def test_preserved_duplicate_short_text_has_stable_one_to_one_lineage(tmp_path):
    blocks = [
        {"type": "text", "text": "Method", "page_idx": 2, "bbox": [20, 180, 300, 220]},
        {"type": "text", "text": "Method", "page_idx": 2, "bbox": [20, 420, 300, 460]},
    ]
    canonical = _fixture(
        tmp_path,
        blocks,
        [
            {"block_ref": "content-list-000000", "unit_id": "unit-1"},
            {"block_ref": "content-list-000001", "unit_id": "unit-1"},
        ],
    )

    result = build_canonical_conservation(canonical)
    mapping = json.loads((canonical / "clean-block-map.json").read_text())
    ledger = json.loads((canonical / "cleaning-ledger.json").read_text())["entries"]

    assert result["status"] == "passed"
    assert len(mapping["mappings"]) == 2
    assert len({row["clean_block_id"] for row in mapping["mappings"]}) == 2
    assert all(row["source_locator"]["bbox"] for row in mapping["mappings"])
    assert len({row["source_locator"]["locator_sha256"] for row in mapping["mappings"]}) == 2
    assert all(any(ref.startswith("clean:") for ref in row["output_refs"]) for row in ledger)


def test_unassigned_source_list_is_preserved_when_clean_split_is_exact(tmp_path):
    canonical = _fixture(
        tmp_path,
        [{"type": "list", "list_items": ["A. One", "B. Two", "C. Three"], "page_idx": 2}],
        [],
    )
    (canonical / "clean.md").write_text("<!-- page_idx: 2 -->\nA. One\nB. Two\nC. Three\n", encoding="utf-8")

    result = build_canonical_conservation(canonical)

    assert result["status"] == "passed"
    assert result["action_counts"] == {"preserve": 1}


def test_repeated_page_edge_text_is_allowlisted_noise(tmp_path):
    blocks = [
        {"type": "text", "text": "Workbook title", "page_idx": page, "bbox": [0, 20, 100, 40]}
        for page in (1, 2, 3)
    ]
    canonical = _fixture(tmp_path, blocks, [])

    result = build_canonical_conservation(canonical)

    assert result["status"] == "passed"
    assert result["action_counts"] == {"remove_repeated_header_footer": 3}


def test_assigned_repeated_edge_label_is_preserved(tmp_path):
    blocks = [
        {"type": "text", "text": "Word power", "page_idx": page, "bbox": [0, 20, 100, 40]}
        for page in (1, 2, 3)
    ]
    assignments = [
        {"block_ref": f"content-list-{index:06d}", "unit_id": f"unit-{index}"}
        for index in range(3)
    ]
    canonical = _fixture(tmp_path, blocks, assignments)

    result = build_canonical_conservation(canonical)

    assert result["status"] == "passed"
    assert result["action_counts"] == {"preserve": 3}


def test_image_caption_and_image_are_separate_authoritative_components(tmp_path):
    blocks = [{
        "type": "image",
        "image_caption": ["Source caption"],
        "img_path": "images/a.jpg",
        "page_idx": 2,
    }]
    canonical = _fixture(tmp_path, blocks, [{"block_ref": "content-list-000000", "unit_id": "unit-1"}])
    (canonical / "clean.md").write_text("<!-- page_idx: 2 -->\n![Source caption](images/a.jpg)\n*Source caption*\n", encoding="utf-8")

    result = build_canonical_conservation(canonical)
    mapping = json.loads((canonical / "clean-block-map.json").read_text())

    assert result["status"] == "passed"
    assert {row["source_component"] for row in mapping["mappings"]} == {"caption", "image"}

    source_root = tmp_path / "source"
    for path in source_root.iterdir():
        path.unlink()
    source_root.rmdir()
    assert build_canonical_conservation(canonical)["status"] == "passed"


def test_no_copy_image_report_does_not_create_false_lineage_blocker(tmp_path):
    canonical = _fixture(
        tmp_path,
        [{"type": "image", "img_path": "images/a.jpg", "page_idx": 2}],
        [{"block_ref": "content-list-000000", "unit_id": "unit-1"}],
    )
    (canonical / "image_closure_report.json").write_text(
        json.dumps({
            "markdown_refs_not_copied": ["images/a.jpg"],
            "markdown_refs_missing_from_source": [],
        }),
        encoding="utf-8",
    )

    result = build_canonical_conservation(canonical)

    assert result["status"] == "passed"
    assert result["gates"]["image_lineage_is_resolved"] is True


def test_repeated_semantic_label_at_varying_heights_is_not_page_noise(tmp_path):
    blocks = [
        {"type": "text", "text": "Solution:", "page_idx": 1, "bbox": [0, 720, 100, 745]},
        {"type": "text", "text": "Solution:", "page_idx": 2, "bbox": [0, 780, 100, 805]},
        {"type": "text", "text": "Solution:", "page_idx": 3, "bbox": [0, 850, 100, 875]},
    ]
    canonical = _fixture(tmp_path, blocks, [])
    (canonical / "clean.md").write_text(
        "<!-- page_idx: 1 -->\nSolution:\n<!-- page_idx: 2 -->\nSolution:\n<!-- page_idx: 3 -->\nSolution:\n",
        encoding="utf-8",
    )

    result = build_canonical_conservation(canonical)

    assert result["status"] == "passed"
    assert result["action_counts"] == {"preserve": 3}


def test_source_table_prefers_semantic_table_over_duplicate_crop(tmp_path):
    blocks = [{
        "type": "table",
        "table_body": "<table><tr><td>A</td></tr></table>",
        "img_path": "images/table.jpg",
        "page_idx": 2,
    }]
    canonical = _fixture(tmp_path, blocks, [{"block_ref": "content-list-000000", "unit_id": "unit-1"}])
    (canonical / "clean.md").write_text(
        "<!-- page_idx: 2 -->\n<table><tr><td>A</td></tr></table>\n",
        encoding="utf-8",
    )

    result = build_canonical_conservation(canonical)
    mapping = json.loads((canonical / "clean-block-map.json").read_text())

    assert result["status"] == "passed"
    assert {row["source_component"] for row in mapping["mappings"]} == {"content"}
    assert mapping["media_counts"]["source_images"] == 1


def test_table_caption_maps_as_text_while_body_maps_as_table(tmp_path):
    blocks = [{
        "type": "table",
        "table_caption": ["Table caption"],
        "table_body": "<table><tr><td>A</td></tr></table>",
        "page_idx": 2,
    }]
    canonical = _fixture(tmp_path, blocks, [{"block_ref": "content-list-000000", "unit_id": "unit-1"}])
    (canonical / "clean.md").write_text(
        "<!-- page_idx: 2 -->\n**Table caption**\n<table><tr><td>A</td></tr></table>\n",
        encoding="utf-8",
    )

    result = build_canonical_conservation(canonical)
    mapping = json.loads((canonical / "clean-block-map.json").read_text())

    assert result["status"] == "passed"
    assert {row["source_component"] for row in mapping["mappings"]} == {"caption", "content"}


def test_source_chart_prefers_original_image_over_derived_description(tmp_path):
    blocks = [{
        "type": "chart",
        "text": "| Category | Value |\n| A | 1 |",
        "img_path": "images/chart.jpg",
        "page_idx": 2,
    }]
    canonical = _fixture(tmp_path, blocks, [{"block_ref": "content-list-000000", "unit_id": "unit-1"}])
    (canonical / "clean.md").write_text("<!-- page_idx: 2 -->\n![image](images/chart.jpg)\n", encoding="utf-8")

    result = build_canonical_conservation(canonical)
    mapping = json.loads((canonical / "clean-block-map.json").read_text())

    assert result["status"] == "passed"
    assert {row["source_component"] for row in mapping["mappings"]} == {"image"}


def test_selected_heading_block_is_transformed_not_lost(tmp_path):
    canonical = _fixture(
        tmp_path,
        [{"type": "text", "text": "Chapter 1", "page_idx": 2, "bbox": [0, 200, 100, 250]}],
        [],
    )
    (canonical / "outline_decision.json").write_text(
        json.dumps({"final_outline": [{"title": "Chapter 1", "candidate_ids": ["heading-1"]}]}),
        encoding="utf-8",
    )
    (canonical / "outline_candidates.jsonl").write_text(
        json.dumps({"candidate_id": "heading-1", "title_text": "Chapter 1", "block_ids": ["content-list-000000"]}) + "\n",
        encoding="utf-8",
    )
    (canonical / "clean.md").write_text("<!-- page_idx: 2 -->\n\n# Chapter 1\n", encoding="utf-8")

    result = build_canonical_conservation(canonical)

    assert result["status"] == "passed"
    assert result["action_counts"] == {"transform_to_outline_heading": 1}


def test_numbered_outline_prefix_is_a_source_evidenced_heading_transform(tmp_path):
    canonical = _fixture(
        tmp_path,
        [{"type": "text", "text": "Functions", "page_idx": 4, "bbox": [0, 30, 100, 60]}],
        [],
    )
    (canonical / "outline_decision.json").write_text(json.dumps({
        "final_outline": [{"title": "CHAPTER 1 Functions", "page": 5, "candidate_ids": []}],
    }), encoding="utf-8")
    (canonical / "clean.md").write_text("<!-- page_idx: 4 -->\n# CHAPTER 1 Functions\n", encoding="utf-8")

    result = build_canonical_conservation(canonical)
    mapping = json.loads((canonical / "clean-block-map.json").read_text())

    assert result["status"] == "passed"
    assert result["action_counts"] == {"transform_to_outline_heading": 1}
    assert mapping["mappings"][0]["match"] == "source_evidenced_outline_heading_transform"


def test_outline_heading_with_toc_evidence_needs_no_body_block(tmp_path):
    canonical = _fixture(tmp_path, [], [])
    (canonical / "outline_decision.json").write_text(json.dumps({
        "final_outline": [{"title": "CHAPTER 7 Logarithmic functions", "page": 38, "candidate_ids": ["toc-7"]}],
    }), encoding="utf-8")
    (canonical / "clean.md").write_text("<!-- page_idx: 37 -->\n# CHAPTER 7 Logarithmic functions\n", encoding="utf-8")

    result = build_canonical_conservation(canonical)

    assert result["status"] == "passed"


def test_outline_heading_punctuation_normalization_is_source_evidenced(tmp_path):
    canonical = _fixture(
        tmp_path,
        [{"type": "text", "text": "The Five-Paragraph Essay", "page_idx": 7}],
        [],
    )
    (canonical / "outline_decision.json").write_text(json.dumps({
        "final_outline": [{"title": "The Five Paragraph Essay", "page": 8, "candidate_ids": ["toc"]}],
    }), encoding="utf-8")
    (canonical / "clean.md").write_text("<!-- page_idx: 7 -->\n## The Five Paragraph Essay\n", encoding="utf-8")

    assert build_canonical_conservation(canonical)["status"] == "passed"


def test_typed_footer_at_page_edge_is_allowlisted_noise(tmp_path):
    canonical = _fixture(
        tmp_path,
        [{"type": "footer", "text": "Photocopying prohibited", "page_idx": 2, "bbox": [0, 950, 100, 970]}],
        [],
    )

    result = build_canonical_conservation(canonical)

    assert result["status"] == "passed"
    assert result["action_counts"] == {"remove_structural_page_noise": 1}


def test_exact_split_is_recorded_without_content_loss(tmp_path):
    canonical = _fixture(
        tmp_path,
        [{"type": "text", "text": "First sentence. Second sentence.", "page_idx": 2}],
        [{"block_ref": "content-list-000000", "unit_id": "unit-1"}],
    )
    (canonical / "clean.md").write_text(
        "<!-- page_idx: 2 -->\n\nFirst sentence.\n\nSecond sentence.\n",
        encoding="utf-8",
    )

    result = build_canonical_conservation(canonical)
    mapping = json.loads((canonical / "clean-block-map.json").read_text())

    assert result["status"] == "passed"
    assert mapping["missing_source_block_ids"] == []
    assert mapping["unmapped_clean_block_ids"] == []
    assert mapping["split_merge_operations"][0]["operation"] == "split"


def test_resegmentation_requires_exact_content(tmp_path):
    canonical = _fixture(
        tmp_path,
        [{"type": "text", "text": "Original teaching content", "page_idx": 2}],
        [{"block_ref": "content-list-000000", "unit_id": "unit-1"}],
    )
    (canonical / "clean.md").write_text("<!-- page_idx: 2 -->\n\nRewritten teaching content\n", encoding="utf-8")

    result = build_canonical_conservation(canonical)

    codes = {row["code"] for row in result["blockers"]}
    assert "preserved_source_blocks_missing_from_clean" in codes
    assert "clean_blocks_without_source_lineage" in codes


def test_mineru_list_items_map_to_multiple_clean_lines(tmp_path):
    canonical = _fixture(
        tmp_path,
        [{"type": "list", "list_items": ["A. First", "B. Second"], "page_idx": 2}],
        [{"block_ref": "content-list-000000", "unit_id": "unit-1"}],
    )
    (canonical / "clean.md").write_text("<!-- page_idx: 2 -->\n\nA. First\n\nB. Second\n", encoding="utf-8")

    result = build_canonical_conservation(canonical)
    mapping = json.loads((canonical / "clean-block-map.json").read_text())

    assert result["status"] == "passed"
    assert {row["source_component"] for row in mapping["mappings"]} == {"item:0", "item:1"}


def test_multiline_list_items_resegment_independently_within_one_source_block(tmp_path):
    canonical = _fixture(
        tmp_path,
        [{
            "type": "list",
            "list_items": [
                "1 First question\nFirst detail.",
                "2 Second question\nSecond detail.",
            ],
            "page_idx": 2,
        }],
        [{"block_ref": "content-list-000000", "unit_id": "unit-1"}],
    )
    (canonical / "clean.md").write_text(
        "<!-- page_idx: 2 -->\n1 First question\nFirst detail.\n2 Second question\nSecond detail.\n",
        encoding="utf-8",
    )

    result = build_canonical_conservation(canonical)
    mapping = json.loads((canonical / "clean-block-map.json").read_text())

    assert result["status"] == "passed"
    assert {row["source_component"] for row in mapping["mappings"]} == {
        "item:0:line:0",
        "item:0:line:1",
        "item:1:line:0",
        "item:1:line:1",
    }


def test_each_multiline_list_component_uses_exact_line_evidence(tmp_path):
    canonical = _fixture(
        tmp_path,
        [{
            "type": "list",
            "list_items": [
                "Domain is\n{x : x > 2}\nRange is\n{y : y in R}",
                "VA is x = 2\nx-intercept 27\nno y-intercept",
                "x = 7",
            ],
            "page_idx": 2,
        }],
        [{"block_ref": "content-list-000000", "unit_id": "unit-1"}],
    )
    (canonical / "clean.md").write_text(
        "<!-- page_idx: 2 -->\nDomain is\n{x : x > 2}\nRange is\n{y : y in R}\n"
        "VA is x = 2\nx-intercept 27\nno y-intercept\nx = 7\n",
        encoding="utf-8",
    )

    result = build_canonical_conservation(canonical)
    mapping = json.loads((canonical / "clean-block-map.json").read_text())

    assert result["status"] == "passed"
    assert mapping["missing_source_block_ids"] == []
    assert mapping["unmapped_clean_block_ids"] == []
    assert {row["source_component"] for row in mapping["mappings"]} == {
        "item:0:line:0", "item:0:line:1", "item:0:line:2", "item:0:line:3",
        "item:1:line:0", "item:1:line:1", "item:1:line:2", "item:2",
    }


def test_multiline_text_splits_only_when_each_clean_line_has_exact_evidence(tmp_path):
    canonical = _fixture(
        tmp_path,
        [{"type": "text", "text": "First instruction.\nSecond instruction.", "page_idx": 2}],
        [{"block_ref": "content-list-000000", "unit_id": "unit-1"}],
    )
    (canonical / "clean.md").write_text(
        "<!-- page_idx: 2 -->\nFirst instruction.\nSecond instruction.\n",
        encoding="utf-8",
    )

    result = build_canonical_conservation(canonical)
    mapping = json.loads((canonical / "clean-block-map.json").read_text())

    assert result["status"] == "passed"
    assert [row["source_component"] for row in mapping["mappings"]] == ["content:line:0", "content:line:1"]


def test_repeated_list_items_preserve_source_and_clean_multiplicity(tmp_path):
    repeated = "Give your answer in kilometres/hour."
    canonical = _fixture(
        tmp_path,
        [{"type": "list", "list_items": [repeated, repeated, repeated], "page_idx": 2}],
        [{"block_ref": "content-list-000000", "unit_id": "unit-1"}],
    )
    (canonical / "clean.md").write_text(
        f"<!-- page_idx: 2 -->\n{repeated}\n{repeated}\n{repeated}\n",
        encoding="utf-8",
    )

    result = build_canonical_conservation(canonical)
    mapping = json.loads((canonical / "clean-block-map.json").read_text())

    assert result["status"] == "passed"
    assert [row["source_component"] for row in mapping["mappings"]] == [
        "item:0",
        "item:1",
        "item:2",
    ]


def test_multiline_math_is_one_clean_block(tmp_path):
    canonical = _fixture(
        tmp_path,
        [{"type": "equation", "text": "$$\nx + 1\n$$", "page_idx": 2}],
        [{"block_ref": "content-list-000000", "unit_id": "unit-1"}],
    )
    (canonical / "clean.md").write_text("<!-- page_idx: 2 -->\n\n$$\nx + 1\n$$\n", encoding="utf-8")

    result = build_canonical_conservation(canonical)

    assert result["status"] == "passed"


def test_reported_math_ocr_correction_is_audited_and_mapped(tmp_path):
    canonical = _fixture(
        tmp_path,
        [{"type": "equation", "text": "$$\n1 0 + 1 1\n$$", "page_idx": 2}],
        [{"block_ref": "content-list-000000", "unit_id": "unit-1"}],
    )
    (canonical / "clean.md").write_text("<!-- page_idx: 2 -->\n\n$$\n10 + 11\n$$\n", encoding="utf-8")
    (canonical / "math_ocr_repair_report.json").write_text(json.dumps({
        "examples": [{"line": 3, "substitutions": 2, "before": "$$\n1 0 + 1 1\n$$", "after": "$$\n10 + 11\n$$"}],
    }), encoding="utf-8")

    result = build_canonical_conservation(canonical)
    mapping = json.loads((canonical / "clean-block-map.json").read_text())

    assert result["status"] == "passed"
    assert mapping["ocr_correction_operations"] == [{
        "operation": "ocr_correction",
        "source_block_id": "content-list-000000",
        "source_component": "content",
        "clean_block_id": "clean-line-000003",
        "page_idx": 2,
        "before": "$$\n1 0 + 1 1\n$$",
        "after": "$$\n10 + 11\n$$",
        "substitution_count": 2,
        "reason": "deterministic_math_digit_spacing_repair",
        "evidence": "math_ocr_repair_report.json",
    }]


def test_multiline_image_alt_preserves_image_reference(tmp_path):
    canonical = _fixture(
        tmp_path,
        [{"type": "image", "img_path": "images/a.jpg", "page_idx": 2}],
        [{"block_ref": "content-list-000000", "unit_id": "unit-1"}],
    )
    (canonical / "clean.md").write_text("<!-- page_idx: 2 -->\n\n![line one\nline two](images/a.jpg)\n", encoding="utf-8")

    result = build_canonical_conservation(canonical)

    assert result["status"] == "passed"


def test_source_map_and_page_conservation_are_emitted(tmp_path):
    canonical = _fixture(
        tmp_path,
        [{"type": "text", "text": "Faithful content", "page_idx": 2}],
        [{"block_ref": "content-list-000000", "unit_id": "unit-1"}],
    )

    build_canonical_conservation(canonical)

    source_map = json.loads((canonical / "source-map.json").read_text())
    page_report = json.loads((canonical / "page-content-conservation.json").read_text())
    assert source_map["records"][0]["clean_block_ids"]
    assert page_report["exact_page_count"] == 1
    assert page_report["changed_page_count"] == 0


def test_conservation_can_revalidate_from_canonical_block_snapshot(tmp_path):
    canonical = _fixture(
        tmp_path,
        [{"type": "text", "text": "Durable content", "page_idx": 2}],
        [{"block_ref": "content-list-000000", "unit_id": "unit-1"}],
    )
    first = build_canonical_conservation(canonical)
    source_root = tmp_path / "source"
    for path in source_root.iterdir():
        path.unlink()
    source_root.rmdir()

    second = build_canonical_conservation(canonical)

    assert first["status"] == second["status"] == "passed"
    assert second["source_block_count"] == 1


def test_multiline_html_table_is_parsed_as_one_clean_block(tmp_path):
    table = "<table>\n<tr><td>A</td></tr>\n</table>"
    canonical = _fixture(
        tmp_path,
        [{"type": "table", "table_body": table, "page_idx": 2}],
        [{"block_ref": "content-list-000000", "unit_id": "unit-1"}],
    )
    (canonical / "clean.md").write_text(f"<!-- page_idx: 2 -->\n{table}\n", encoding="utf-8")

    assert build_canonical_conservation(canonical)["status"] == "passed"


def test_html_entities_and_rendered_characters_are_equivalent(tmp_path):
    source_table = "<table><tr><td>Ben&#x27;s book</td></tr></table>"
    clean_table = "<table><tr><td>Ben's book</td></tr></table>"
    canonical = _fixture(
        tmp_path,
        [{"type": "table", "table_body": source_table, "page_idx": 2}],
        [{"block_ref": "content-list-000000", "unit_id": "unit-1"}],
    )
    (canonical / "clean.md").write_text(f"<!-- page_idx: 2 -->\n{clean_table}\n", encoding="utf-8")

    assert build_canonical_conservation(canonical)["status"] == "passed"


def test_markdown_emphasis_is_format_only(tmp_path):
    canonical = _fixture(
        tmp_path,
        [{"type": "text", "text": "Caption text", "page_idx": 2}],
        [{"block_ref": "content-list-000000", "unit_id": "unit-1"}],
    )
    (canonical / "clean.md").write_text("<!-- page_idx: 2 -->\n*Caption text*\n", encoding="utf-8")

    assert build_canonical_conservation(canonical)["status"] == "passed"


def test_internal_clean_marker_is_not_teaching_content(tmp_path):
    canonical = _fixture(
        tmp_path,
        [{"type": "text", "text": "Body", "page_idx": 2}],
        [{"block_ref": "content-list-000000", "unit_id": "unit-1"}],
    )
    (canonical / "clean.md").write_text("<!-- page_idx: 2 -->\n<!-- source_empty_chunk: review -->\nBody\n", encoding="utf-8")

    assert build_canonical_conservation(canonical)["status"] == "passed"


def test_image_backed_visual_types_share_assignment_policy() -> None:
    assert {"image", "chart", "figure", "diagram"} <= IMAGE_BACKED_VISUAL_TYPES
    for block_type in IMAGE_BACKED_VISUAL_TYPES:
        eligible, reason = eligible_block_for_assignment({"type": block_type, "page_idx": 2}, 1, 3)
        assert eligible is True
        assert reason == ""


def test_page_number_type_with_question_content_is_not_noise() -> None:
    assert is_noise_block({"type": "page_number", "text": "12", "bbox": [0, 850, 20, 870]}) is True
    assert is_noise_block({"type": "page_number", "text": "12"}) is False
    assert is_noise_block({"type": "page_number", "text": "3. ____"}) is False
    assert is_noise_block({"type": "header", "text": "Caption", "bbox": [0, 180, 100, 220]}) is False
    assert is_noise_block({"type": "header", "text": "Running", "bbox": [0, 20, 100, 40]}) is True
