import json

import pytest

from app.workflow_v2.outline_reconstruction import (
    _collapse_self_nested_roots,
    _validate_hierarchy,
    build_outline_artifact,
)


@pytest.fixture(autouse=True)
def disable_live_outline_arbitration(monkeypatch):
    monkeypatch.setenv("WORKFLOW_V2_OUTLINE_ARBITRATION_ENABLED", "0")


def _write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def _write_jsonl(path, values):
    path.write_text("".join(json.dumps(value) + "\n" for value in values), encoding="utf-8")


def _canonical(tmp_path, outline):
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    candidates = []
    units = []
    assignments = []
    for index, node in enumerate(outline, 1):
        candidate_id = f"candidate-{index}"
        node["candidate_ids"] = [candidate_id]
        candidates.append({"candidate_id": candidate_id, "title_text": node["title"], "page": node["page"], "source": "contents"})
        units.append({"title": node["title"], "level": node["level"], "block_ids": [f"block-{index}"]})
        assignments.append({"block_ref": f"block-{index}", "unit_title": node["title"]})
    _write_json(canonical / "outline_decision.json", {"final_outline": outline})
    _write_json(canonical / "popo_outline.json", {"outline": outline})
    _write_jsonl(canonical / "outline_candidates.jsonl", candidates)
    _write_jsonl(canonical / "raw_units.jsonl", units)
    _write_jsonl(canonical / "raw_block_assignments.jsonl", assignments)
    _write_jsonl(canonical / "unassigned_blocks.jsonl", [])
    (canonical / "clean.md").write_text(
        "\n".join(f"<!-- page_idx: {row['page']} -->\n{'#' * row['level']} {row['title']}" for row in outline) + "\n",
        encoding="utf-8",
    )
    return canonical


def test_two_level_source_evidenced_outline_passes(tmp_path):
    outline = [
        {"title": "Chapter 1", "level": 1, "parent_title": "", "page": 4, "source": "contents"},
        {"title": "1.1 Functions", "level": 2, "parent_title": "Chapter 1", "page": 5, "source": "body_heading"},
    ]
    canonical = _canonical(tmp_path, outline)

    result = build_outline_artifact(canonical, tmp_path / "output")

    assert result["status"] == "passed"
    assert all(result["gates"].values())


def test_explicit_numbered_textbook_headings_replace_noisy_question_outline(tmp_path):
    canonical = _canonical(
        tmp_path,
        [
            {"title": "Science", "level": 1, "parent_title": "", "page": 1, "source": "fallback"},
            {
                "title": "2.8 a Give the symbol for each element.",
                "level": 3,
                "parent_title": "",
                "page": 76,
                "source": "fallback",
            },
        ],
    )
    lines = []
    for chapter in range(1, 4):
        lines.extend([
            f"<!-- page_idx: {chapter * 10} -->",
            f"## {chapter} Chapter {chapter}",
        ])
        for section in range(1, 4):
            title = f"Section {chapter}.{section}"
            if chapter == 2 and section == 2:
                title = "What is a species?"
            lines.extend([
                f"<!-- page_idx: {chapter * 10 + section} -->",
                f"## > {chapter}.{section} {title}",
                "Source body.",
            ])
    (canonical / "clean.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = build_outline_artifact(canonical, tmp_path / "output")
    document = json.loads((tmp_path / "output" / "outline.json").read_text())

    assert result["status"] == "passed"
    assert document["numbered_textbook_section_reconstruction"]["applied"] is True
    assert document["node_count"] == 12
    assert all("Give the symbol" not in row["title"] for row in document["nodes"])
    assert any(row["title"] == "2.2 What is a species?" for row in document["nodes"])
    assert all(row["parent_id"] for row in document["nodes"] if row["level"] == 2)


def test_partial_textbook_uses_two_evidenced_chapters_without_toc_only_roots(tmp_path):
    canonical = _canonical(
        tmp_path,
        [{"title": "Default Title", "level": 1, "parent_title": "", "page": 1, "source": "fallback"}],
    )
    (canonical / "clean.md").write_text(
        "\n".join(
            [
                "<!-- page_idx: 1 -->",
                "## 1 Respiration",
                "## > 1.1 Lungs",
                "Body.",
                "## > 1.2 Blood",
                "Body.",
                "<!-- page_idx: 20 -->",
                "## 2 Properties of materials",
                "## > 2.1 Dissolving",
                "Body.",
                "## > 2.2 Solutions",
                "Body.",
                "## > 2.3 Chromatography",
                "Body.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = build_outline_artifact(canonical, tmp_path / "output")
    document = json.loads((tmp_path / "output" / "outline.json").read_text())

    assert result["status"] == "passed"
    assert document["numbered_textbook_section_reconstruction"]["chapter_count"] == 2
    assert [row["title"] for row in document["nodes"] if row["level"] == 1] == [
        "1 Respiration",
        "2 Properties of materials",
    ]


def test_split_and_unnumbered_chapter_headings_are_joined_before_sections(tmp_path):
    canonical = _canonical(
        tmp_path,
        [{"title": "Default Title", "level": 1, "parent_title": "", "page": 1, "source": "fallback"}],
    )
    (canonical / "clean.md").write_text(
        "\n".join(
            [
                "<!-- page_idx: 1 -->",
                "## 1 Changes to",
                "## materials",
                "## > 1.1 Reactions",
                "Body.",
                "## > 1.2 Neutralisation",
                "Body.",
                "<!-- page_idx: 20 -->",
                "## Electricity",
                "## > 2.1 Current",
                "Body.",
                "## > 2.2 Circuits",
                "Body.",
                "## > 2.3 Conductors",
                "Body.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = build_outline_artifact(canonical, tmp_path / "output")
    document = json.loads((tmp_path / "output" / "outline.json").read_text())

    assert result["status"] == "passed"
    assert [row["title"] for row in document["nodes"] if row["level"] == 1] == [
        "1 Changes to materials",
        "2 Electricity",
    ]
    assert all(row["parent_id"] for row in document["nodes"] if row["level"] == 2)


def test_shared_candidate_anchor_is_replaced_by_unique_exact_clean_heading_evidence(tmp_path):
    outline = [
        {"title": "1 Cells", "level": 1, "parent_title": "", "page": 8, "source": "contents"},
        {"title": "1.1 Plant cells", "level": 2, "parent_title": "1 Cells", "page": 8, "source": "contents"},
    ]
    canonical = _canonical(tmp_path, outline)
    for row in outline:
        row["candidate_ids"] = ["shared-candidate"]
    _write_json(canonical / "outline_decision.json", {"final_outline": outline})
    _write_jsonl(
        canonical / "outline_candidates.jsonl",
        [{"candidate_id": "shared-candidate", "title_text": "1 Cells / 1.1 Plant cells", "page": 8, "source": "contents"}],
    )

    result = build_outline_artifact(canonical, tmp_path / "output")
    document = json.loads((tmp_path / "output" / "outline.json").read_text())

    assert result["status"] == "passed"
    assert document["heading_evidence_alignment"]["aligned_count"] == 2
    coverage = json.loads((tmp_path / "output" / "outline-body-coverage.json").read_text())
    assert coverage["duplicate_anchor_count"] == 0


def test_omitted_split_article_title_is_added_from_metadata_evidence(tmp_path):
    outline = [
        {"title": "Article B", "level": 1, "parent_title": "", "page": 2, "source": "body"},
        {"title": "Word power", "level": 2, "parent_title": "Article B", "page": 2, "source": "body"},
    ]
    canonical = _canonical(tmp_path, outline)
    (canonical / "clean.md").write_text(
        "<!-- page_idx: 0 -->\n\nLooking Back on This Past\n\nSchool Year\n\n"
        "语篇类型: 应用文 词数: 368 难度: 3级\n\n"
        "<!-- page_idx: 1 -->\n# Article B\n## Word power\n",
        encoding="utf-8",
    )

    result = build_outline_artifact(canonical, tmp_path / "output")
    document = json.loads((tmp_path / "output" / "outline.json").read_text())

    assert result["status"] == "passed"
    assert document["metadata_title_augmentation"]["added_count"] == 1
    added = document["nodes"][0]
    assert added["title"] == "Looking Back on This Past School Year"
    assert added["source_page"] == 1
    assert added["evidence"][0]["method"] == "article_metadata_title_fragments"


def test_repeated_chapter_labels_and_numbered_topic_pairs_replace_noisy_outline(tmp_path):
    canonical = _canonical(
        tmp_path,
        [{"title": "Malformed merged heading", "level": 1, "parent_title": "", "page": 2, "source": "contents"}],
    )
    blocks = []
    order = 0
    for chapter in (1, 2):
        chapter_title = f"Chapter {chapter}: Skills {chapter}"
        for page in range(chapter * 10, chapter * 10 + 3):
            blocks.append({
                "block_id": f"content-list-{order:06d}",
                "source_order": order,
                "type": "footer",
                "content": chapter_title,
                "page_idx": page,
                "bbox": [0, 900, 300, 930],
            })
            order += 1
        for topic in (1, 2, 3):
            page = chapter * 10 + topic
            blocks.append({
                "block_id": f"content-list-{order:06d}",
                "source_order": order,
                "type": "text",
                "content": f"Chapter {chapter}. Topic {topic}",
                "page_idx": page,
                "bbox": [10, 100, 200, 120],
            })
            order += 1
            blocks.append({
                "block_id": f"content-list-{order:06d}",
                "source_order": order,
                "type": "text",
                "content": f"Topic title {chapter}-{topic}",
                "page_idx": page,
                "bbox": [10, 130, 400, 170],
            })
            order += 1
    _write_json(canonical / "blocks.json", {"blocks": blocks})

    result = build_outline_artifact(canonical, tmp_path / "output")
    document = json.loads((tmp_path / "output" / "outline.json").read_text())

    assert result["status"] == "passed"
    assert document["numbered_chapter_topic_reconstruction"]["applied"] is True
    assert document["numbered_chapter_topic_reconstruction"]["chapter_count"] == 2
    assert document["numbered_chapter_topic_reconstruction"]["topic_count"] == 6
    assert [row["title"] for row in document["nodes"][:4]] == [
        "Chapter 1: Skills 1",
        "1.1 Topic title 1-1",
        "1.2 Topic title 1-2",
        "1.3 Topic title 1-3",
    ]


def test_one_level_outline_is_preserved_but_blocked(tmp_path):
    outline = [{"title": "Chapter 1", "level": 1, "parent_title": "", "page": 4, "source": "contents"}]
    canonical = _canonical(tmp_path, outline)

    result = build_outline_artifact(canonical, tmp_path / "output")

    assert result["status"] == "review"
    assert result["gates"]["outline_depth_is_two_or_three"] is False
    assert json.loads((tmp_path / "output" / "outline.json").read_text())["nodes"][0]["title"] == "Chapter 1"


def test_placeholder_title_and_child_before_parent_are_hard_blockers():
    nodes = [
        {"id": "root", "title": "Default Title", "normalized_title": "default title", "level": 1, "parent_title": "", "parent_id": None, "source_page": 10},
        {"id": "child", "title": "Method", "normalized_title": "method", "level": 2, "parent_title": "Default Title", "parent_id": "root", "source_page": 4},
    ]

    result = _validate_hierarchy(nodes)

    assert {"outline_placeholder_title", "outline_child_precedes_parent_source_page"} <= {
        row["code"] for row in result["blockers"]
    }


def test_large_candidate_pool_cannot_finish_with_single_selected_node(tmp_path, monkeypatch):
    outline = [{"title": "Chapter 1", "level": 1, "parent_title": "", "page": 4, "source": "contents"}]
    canonical = _canonical(tmp_path, outline)
    candidates = [
        {"candidate_id": f"candidate-{index}", "title_text": f"Candidate {index}", "page": index, "source": "body"}
        for index in range(1, 26)
    ]
    _write_jsonl(canonical / "outline_candidates.jsonl", candidates)
    monkeypatch.setenv("WORKFLOW_V2_OUTLINE_ARBITRATION_ENABLED", "1")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    result = build_outline_artifact(canonical, tmp_path / "output")

    blocker = next(row for row in result["blockers"] if row["code"] == "outline_arbitration_required")
    assert blocker["candidate_count"] == 25
    assert blocker["arbitration"] == "bounded_outline_arbitration_key_unavailable"


def test_source_visible_chinese_toc_replaces_invalid_outline_with_printed_page_mapping(tmp_path):
    canonical = _canonical(
        tmp_path,
        [{"title": "Noisy heading", "level": 1, "parent_title": "", "page": 3, "source": "fallback"}],
    )
    _write_json(canonical / "popo_outline.json", {"last_front_page": 1, "outline": []})
    _write_json(
        canonical / "blocks.json",
        {
            "blocks": [
                {"block_id": "toc-marker", "source_order": 1, "type": "text", "content": "目录", "page_idx": 0},
                {"block_id": "toc-root-1", "source_order": 2, "type": "text", "content": "一、数与运算", "page_idx": 0},
                {"block_id": "toc-list-1", "source_order": 3, "type": "list", "content": "1 整数 1\n2 小数 …… 4", "page_idx": 0},
                {"block_id": "toc-root-2", "source_order": 4, "type": "text", "content": "二、方程", "page_idx": 0},
                {"block_id": "toc-list-2", "source_order": 5, "type": "list", "content": "1 方程 10\n第 1 课时 等量关系 10", "page_idx": 1},
                {"block_id": "body-1", "source_order": 6, "type": "text", "content": "1 整数", "page_idx": 2},
                {"block_id": "body-2", "source_order": 7, "type": "text", "content": "2 小数", "page_idx": 5},
                {"block_id": "body-3", "source_order": 8, "type": "text", "content": "1 方程", "page_idx": 11},
            ]
        },
    )
    (canonical / "clean.md").write_text("<!-- page_idx: 2 -->\n# 1 整数\n", encoding="utf-8")

    result = build_outline_artifact(canonical, tmp_path / "output")
    document = json.loads((tmp_path / "output" / "outline.json").read_text())

    assert result["status"] == "passed"
    assert document["source_visible_toc_reconstruction"]["applied"] is True
    assert document["source_visible_toc_reconstruction"]["printed_page_offset"] == 2
    assert [(row["level"], row["title"], row.get("printed_page")) for row in document["nodes"]] == [
        (1, "一、数与运算", None),
        (2, "1 整数", 1),
        (2, "2 小数", 4),
        (1, "二、方程", None),
        (2, "1 方程", 10),
        (3, "第 1 课时 等量关系", 10),
    ]
    assert document["nodes"][-1]["source_page"] == 12


def test_body_contents_heading_after_front_matter_is_not_treated_as_source_toc(tmp_path):
    outline = [
        {"title": "Workbook", "level": 1, "parent_title": "", "page": 1, "source": "body"},
        {"title": "Practice", "level": 2, "parent_title": "Workbook", "page": 2, "source": "body"},
    ]
    canonical = _canonical(tmp_path, outline)
    _write_json(canonical / "popo_outline.json", {"last_front_page": 0, "outline": outline})
    _write_json(
        canonical / "blocks.json",
        {"blocks": [
            {"block_id": "body-contents", "source_order": 1, "type": "text", "content": "目录", "page_idx": 3},
            {"block_id": "body-root", "source_order": 2, "type": "text", "content": "一、练习", "page_idx": 3},
            {"block_id": "body-child", "source_order": 3, "type": "list", "content": "1 题目 1\n2 答案 2", "page_idx": 3},
        ]},
    )

    result = build_outline_artifact(canonical, tmp_path / "output")
    document = json.loads((tmp_path / "output" / "outline.json").read_text())

    assert result["status"] == "passed"
    assert document["source_visible_toc_reconstruction"] == {
        "applied": False,
        "reason": "source_visible_toc_marker_not_found",
    }
    assert [row["title"] for row in document["nodes"]] == ["Workbook", "Practice"]


def test_invalid_decision_hierarchy_uses_traceable_canonical_headings(tmp_path):
    outline = [
        {"title": "Default Title", "level": 1, "parent_title": "", "page": 1, "source": "fallback"},
    ]
    canonical = _canonical(tmp_path, outline)
    (canonical / "clean.md").write_text(
        "<!-- page_idx: 0 -->\n# Annual Review\n## City Report\nBody\n",
        encoding="utf-8",
    )

    result = build_outline_artifact(canonical, tmp_path / "output")
    document = json.loads((tmp_path / "output" / "outline.json").read_text())

    assert result["status"] == "passed"
    assert document["canonical_heading_reconstruction"]["applied"] is True
    assert [row["title"] for row in document["nodes"]] == ["Annual Review", "City Report"]


def test_orphan_h3_decision_uses_valid_h1_h2_and_filters_chinese_question(tmp_path):
    outline = [
        {"title": "Workbook", "level": 1, "parent_title": "", "page": 1, "source": "fallback"},
        {"title": "一、单选题", "level": 3, "parent_title": "", "page": 2, "source": "fallback"},
    ]
    canonical = _canonical(tmp_path, outline)
    (canonical / "clean.md").write_text(
        "<!-- page_idx: 0 -->\n# Workbook\n## Practice Set\n## 21. 完成推理填空\nBody\n",
        encoding="utf-8",
    )

    result = build_outline_artifact(canonical, tmp_path / "output")
    document = json.loads((tmp_path / "output" / "outline.json").read_text())

    assert result["status"] == "passed"
    assert [row["title"] for row in document["nodes"]] == ["Workbook", "Practice Set"]
    assert document["canonical_heading_reconstruction"]["discarded_question_heading_count"] == 1


def test_repeated_local_exercise_labels_nest_below_source_chapters(tmp_path):
    outline = [
        {"title": "Workbook", "level": 1, "parent_title": "", "page": 1, "source": "fallback"},
        {"title": "一、单选题", "level": 3, "parent_title": "", "page": 2, "source": "fallback"},
    ]
    canonical = _canonical(tmp_path, outline)
    (canonical / "clean.md").write_text(
        "<!-- page_idx: 0 -->\n# Workbook\n"
        "## Chapter A\n## 一、单选题\n## 二、填空题\n"
        "## Chapter B\n## 一、单选题\n## 二、填空题\n"
        "## Chapter C\n## 一、单选题\n## 二、填空题\n",
        encoding="utf-8",
    )

    result = build_outline_artifact(canonical, tmp_path / "output")
    document = json.loads((tmp_path / "output" / "outline.json").read_text())

    assert result["status"] == "passed"
    assert document["canonical_heading_reconstruction"]["local_heading_grouping"]["changed_count"] == 6
    assert [row["level"] for row in document["nodes"]] == [1, 2, 3, 3, 2, 3, 3, 2, 3, 3]
    assert document["nodes"][2]["parent_title"] == "Chapter A"


def test_missing_parent_and_duplicate_assignment_are_hard_blockers(tmp_path):
    outline = [{"title": "1.1 Functions", "level": 2, "parent_title": "Chapter 1", "page": 5, "source": "body_heading"}]
    canonical = _canonical(tmp_path, outline)
    _write_jsonl(
        canonical / "raw_block_assignments.jsonl",
        [{"block_ref": "block-1"}, {"block_ref": "block-1"}],
    )

    result = build_outline_artifact(canonical, tmp_path / "output")

    codes = {row["code"] for row in result["blockers"]}
    assert "outline_parent_missing" in codes
    assert "blocks_assigned_more_than_once" in codes


def test_parent_is_repaired_to_nearest_preceding_level(tmp_path):
    outline = [
        {"title": "Unit 1", "level": 1, "parent_title": "", "page": 4, "source": "contents"},
        {"title": "Number", "level": 2, "parent_title": "Unit 1", "page": 4, "source": "contents"},
        {"title": "Fractions", "level": 3, "parent_title": "Unit 1", "page": 5, "source": "contents"},
    ]
    canonical = _canonical(tmp_path, outline)

    result = build_outline_artifact(canonical, tmp_path / "output")
    document = json.loads((tmp_path / "output" / "outline.json").read_text())

    assert result["status"] == "passed"
    assert document["nodes"][2]["parent_title"] == "Number"
    assert document["parent_repairs"][0]["reason"] == "nearest_preceding_level_parent"


def test_missing_parent_is_not_invented_without_preceding_parent(tmp_path):
    outline = [{"title": "Orphan lesson", "level": 2, "parent_title": "", "page": 5, "source": "contents"}]
    canonical = _canonical(tmp_path, outline)

    result = build_outline_artifact(canonical, tmp_path / "output")

    assert "outline_parent_missing" in {row["code"] for row in result["blockers"]}


def test_same_local_title_under_different_ancestors_is_not_duplicate(tmp_path):
    outline = [
        {"title": "Unit 1", "level": 1, "parent_title": "", "page": 1, "source": "contents"},
        {"title": "Review", "level": 2, "parent_title": "Unit 1", "page": 2, "source": "contents"},
        {"title": "Unit 2", "level": 1, "parent_title": "", "page": 3, "source": "contents"},
        {"title": "Review", "level": 2, "parent_title": "Unit 2", "page": 4, "source": "contents"},
    ]
    canonical = _canonical(tmp_path, outline)

    result = build_outline_artifact(canonical, tmp_path / "output")

    assert result["status"] == "passed"


def test_flat_outline_gains_source_evidenced_repeated_body_labels(tmp_path):
    outline = [
        {"title": "Article A", "level": 1, "parent_title": "", "page": 1, "source": "body"},
        {"title": "Article B", "level": 1, "parent_title": "", "page": 2, "source": "body"},
        {"title": "Article C", "level": 1, "parent_title": "", "page": 3, "source": "body"},
    ]
    canonical = _canonical(tmp_path, outline)
    (canonical / "clean.md").write_text(
        "<!-- page_idx: 1 -->\n# Article A\nVocabulary\nBody A\n"
        "<!-- page_idx: 2 -->\n# Article B\nVocabulary\nBody B\n"
        "<!-- page_idx: 3 -->\n# Article C\nVocabulary\nBody C\n",
        encoding="utf-8",
    )

    result = build_outline_artifact(canonical, tmp_path / "output")
    document = json.loads((tmp_path / "output" / "outline.json").read_text())

    assert result["status"] == "passed"
    assert document["max_depth"] == 2
    assert document["augmentation"]["added_count"] == 3


def test_flat_outline_uses_source_page_ranges_when_root_headings_are_not_h1(tmp_path):
    outline = [
        {"title": "Article A", "level": 1, "parent_title": "", "page": 1, "source": "body"},
        {"title": "Article B", "level": 1, "parent_title": "", "page": 2, "source": "body"},
        {"title": "Article C", "level": 1, "parent_title": "", "page": 3, "source": "body"},
    ]
    canonical = _canonical(tmp_path, outline)
    (canonical / "clean.md").write_text(
        "<!-- page_idx: 0 -->\n## Article A\n### Vocabulary\nBody A\n"
        "<!-- page_idx: 1 -->\n## Article B\n### Vocabulary\nBody B\n"
        "<!-- page_idx: 2 -->\n## Article C\n### Vocabulary\nBody C\n",
        encoding="utf-8",
    )

    result = build_outline_artifact(canonical, tmp_path / "output")
    document = json.loads((tmp_path / "output" / "outline.json").read_text())

    assert result["status"] == "passed"
    assert [row["title"] for row in document["nodes"] if row["level"] == 2] == [
        "Vocabulary",
        "Vocabulary",
        "Vocabulary",
    ]


def test_repeated_running_units_adopt_trusted_toc_children_transactionally(tmp_path):
    outline = [
        {"title": "Starter", "level": 1, "parent_title": "", "page": 1, "source": "contents"},
        {"title": "Starter topic", "level": 2, "parent_title": "Starter", "page": 1, "source": "contents_detail"},
        {"title": "Untrusted body heading", "level": 1, "parent_title": "", "page": 2, "source": "popo_structural_heading"},
    ]
    for unit in range(1, 4):
        outline.extend([
            {"title": f"{unit} Lesson", "level": 2, "parent_title": "", "page": unit * 4, "source": "contents"},
            {"title": f"Grammar {unit}A", "level": 3, "parent_title": f"{unit} Lesson", "page": unit * 4, "source": "contents_detail"},
            {"title": f"Grammar {unit}B", "level": 3, "parent_title": f"{unit} Lesson", "page": unit * 4 + 1, "source": "contents_detail"},
        ])
    canonical = _canonical(tmp_path, outline)
    blocks = []
    for unit in range(1, 4):
        for offset in range(3):
            blocks.append({
                "block_id": f"unit-{unit}-{offset}",
                "type": "header",
                "content": f"UNIT {unit}",
                "page_idx": unit * 4 - 1 + offset,
            })
    _write_json(canonical / "blocks.json", {"blocks": blocks})

    result = build_outline_artifact(canonical, tmp_path / "output")
    document = json.loads((tmp_path / "output" / "outline.json").read_text())

    assert result["status"] == "passed"
    assert document["running_root_reconciliation"]["method"] == "trusted_outline_with_repeated_running_roots"
    assert document["running_root_reconciliation"]["removed_untrusted_root_titles"] == ["Untrusted body heading"]
    assert [row["title"] for row in document["nodes"] if row["source"] == "repeated_running_hierarchy_label"] == [
        "UNIT 1",
        "UNIT 2",
        "UNIT 3",
    ]


def test_repeated_question_labels_are_not_promoted(tmp_path):
    outline = [
        {"title": "A", "level": 1, "parent_title": "", "page": 1, "source": "body"},
        {"title": "B", "level": 1, "parent_title": "", "page": 2, "source": "body"},
        {"title": "C", "level": 1, "parent_title": "", "page": 3, "source": "body"},
    ]
    canonical = _canonical(tmp_path, outline)
    (canonical / "clean.md").write_text(
        "<!-- page_idx: 1 -->\n# A\n1. Question\n"
        "<!-- page_idx: 2 -->\n# B\n1. Question\n"
        "<!-- page_idx: 3 -->\n# C\n1. Question\n",
        encoding="utf-8",
    )

    result = build_outline_artifact(canonical, tmp_path / "output")

    assert result["status"] == "review"
    assert "outline_depth_out_of_range" in {row["code"] for row in result["blockers"]}


def test_repeated_option_values_are_not_promoted(tmp_path):
    outline = [
        {"title": "A", "level": 1, "parent_title": "", "page": 1, "source": "body"},
        {"title": "B", "level": 1, "parent_title": "", "page": 2, "source": "body"},
        {"title": "C", "level": 1, "parent_title": "", "page": 3, "source": "body"},
    ]
    canonical = _canonical(tmp_path, outline)
    (canonical / "clean.md").write_text(
        "<!-- page_idx: 1 -->\n# A\n(A)-1\n"
        "<!-- page_idx: 2 -->\n# B\n(A)-1\n"
        "<!-- page_idx: 3 -->\n# C\n(A)-1\n",
        encoding="utf-8",
    )

    result = build_outline_artifact(canonical, tmp_path / "output")

    assert result["status"] == "review"


def test_numeric_label_family_can_form_source_evidenced_children(tmp_path):
    outline = [
        {"title": "Chapter A", "level": 1, "parent_title": "", "page": 1, "source": "body"},
        {"title": "Chapter B", "level": 1, "parent_title": "", "page": 2, "source": "body"},
        {"title": "Chapter C", "level": 1, "parent_title": "", "page": 3, "source": "body"},
    ]
    canonical = _canonical(tmp_path, outline)
    (canonical / "clean.md").write_text(
        "<!-- page_idx: 1 -->\n# Chapter A\nQuestions 1-4\n"
        "<!-- page_idx: 2 -->\n# Chapter B\nQuestions 5-9\n"
        "<!-- page_idx: 3 -->\n# Chapter C\nQuestions 10-12\n",
        encoding="utf-8",
    )

    result = build_outline_artifact(canonical, tmp_path / "output")
    document = json.loads((tmp_path / "output" / "outline.json").read_text())

    assert result["status"] == "passed"
    children = [row for row in document["nodes"] if row["level"] == 2]
    assert [row["title"] for row in children] == ["Questions 1-4", "Questions 5-9", "Questions 10-12"]


def test_numeric_label_family_can_be_recovered_from_source_blocks(tmp_path):
    outline = [
        {"title": "Chapter A", "level": 1, "parent_title": "", "page": 1, "source": "body"},
        {"title": "Chapter B", "level": 1, "parent_title": "", "page": 3, "source": "body"},
        {"title": "Chapter C", "level": 1, "parent_title": "", "page": 5, "source": "body"},
    ]
    canonical = _canonical(tmp_path, outline)
    _write_json(
        canonical / "blocks.json",
        {
            "blocks": [
                {"block_id": "b1", "type": "header", "content": "Questions 1-4", "page_idx": 1, "bbox": [0, 10, 100, 20]},
                {"block_id": "b2", "type": "header", "content": "Questions 5-9", "page_idx": 3, "bbox": [0, 10, 100, 20]},
                {"block_id": "b3", "type": "header", "content": "Questions 10-12", "page_idx": 5, "bbox": [0, 10, 100, 20]},
            ]
        },
    )

    result = build_outline_artifact(canonical, tmp_path / "output")

    assert result["status"] == "passed"


def test_solution_booklet_uses_one_source_evidenced_role_child_not_question_stems(tmp_path):
    canonical = _canonical(tmp_path, [])
    selected = {"title": "2026 AMC8 Solutions", "level": 1, "parent_title": "", "page": 1, "source": "body", "candidate_ids": ["root"]}
    _write_json(canonical / "outline_decision.json", {"selected_outline": [selected], "final_outline": []})
    _write_jsonl(canonical / "outline_candidates.jsonl", [{"candidate_id": "root", "title_text": "2026 AMC8 Solutions", "page": 1, "source": "body"}])
    _write_jsonl(canonical / "raw_units.jsonl", [{"title": "2026 AMC8 Solutions", "level": 1, "block_ids": ["b1"]}])
    _write_jsonl(canonical / "raw_block_assignments.jsonl", [{"block_ref": "b1"}])
    (canonical / "clean.md").write_text(
        "<!-- page_idx: 0 -->\n# 2026 AMC8 Solutions\n"
        + "\n".join(f"{number}. Source question {number}?\nSolution:" for number in range(1, 7))
        + "\n",
        encoding="utf-8",
    )

    result = build_outline_artifact(canonical, tmp_path / "output")
    document = json.loads((tmp_path / "output" / "outline.json").read_text())

    assert result["status"] == "passed"
    assert document["root_restoration"]["applied"] is True
    assert document["augmentation"]["method"] == "sequential_numbered_body_sections"
    assert [row["title"] for row in document["nodes"]] == ["2026 AMC8 Solutions", "Solutions"]
    assert document["max_depth"] == 2


def test_numbered_question_stems_are_removed_from_outline(tmp_path):
    outline = [
        {"title": "2026 AMC8 Solutions", "level": 1, "parent_title": "", "page": 1, "source": "body"},
        {"title": "1. What is the value of the expression?", "level": 2, "parent_title": "2026 AMC8 Solutions", "page": 1, "source": "body"},
        {"title": "2. A very long question " + "with source detail " * 8, "level": 2, "parent_title": "2026 AMC8 Solutions", "page": 2, "source": "body"},
    ]
    canonical = _canonical(tmp_path, outline)
    (canonical / "clean.md").write_text(
        "<!-- page_idx: 0 -->\n# 2026 AMC8 Solutions\n"
        + "\n".join(f"{number}. Source question {number}?\nSolution:" for number in range(1, 7))
        + "\n",
        encoding="utf-8",
    )

    result = build_outline_artifact(canonical, tmp_path / "output")
    document = json.loads((tmp_path / "output" / "outline.json").read_text())

    assert result["status"] == "passed"
    assert document["question_heading_filter"]["removed_count"] == 2
    assert [row["title"] for row in document["nodes"]] == ["2026 AMC8 Solutions", "Solutions"]


def test_repeated_running_labels_restore_missing_numbered_root(tmp_path):
    outline = [
        {"title": "Section 1: Skills", "level": 1, "parent_title": "", "page": 2, "source": "contents"},
        {"title": "Chapter 1", "level": 2, "parent_title": "Section 1: Skills", "page": 3, "source": "contents"},
        {"title": "Chapter 2", "level": 2, "parent_title": "", "page": 8, "source": "contents"},
    ]
    canonical = _canonical(tmp_path, outline)
    blocks = []
    for page in (1, 2, 3):
        blocks.append({"block_id": f"s1-{page}", "type": "footer", "content": "Section 1: Skills", "page_idx": page, "bbox": [0, 900, 100, 920]})
    for page in (7, 8, 9):
        blocks.append({"block_id": f"s2-{page}", "type": "footer", "content": "Section 2: Practice", "page_idx": page, "bbox": [0, 900, 100, 920]})
    _write_json(canonical / "blocks.json", {"blocks": blocks})

    result = build_outline_artifact(canonical, tmp_path / "output")
    document = json.loads((tmp_path / "output" / "outline.json").read_text())

    assert result["status"] == "passed"
    assert document["running_root_reconciliation"]["added_count"] == 1
    chapter_2 = next(row for row in document["nodes"] if row["title"] == "Chapter 2")
    assert chapter_2["parent_title"] == "Section 2: Practice"


def test_running_unit_labels_are_alias_evidence_for_numbered_roots(tmp_path):
    outline = [
        {"title": "1 Introductions", "level": 1, "parent_title": "", "page": 2, "source": "contents"},
        {"title": "Names", "level": 2, "parent_title": "1 Introductions", "page": 3, "source": "contents"},
        {"title": "2 Hobbies", "level": 1, "parent_title": "", "page": 6, "source": "contents"},
        {"title": "Free time", "level": 2, "parent_title": "2 Hobbies", "page": 7, "source": "contents"},
    ]
    canonical = _canonical(tmp_path, outline)
    blocks = []
    for ordinal, start in ((1, 1), (2, 5)):
        for page in range(start, start + 3):
            blocks.append({"block_id": f"u{ordinal}-{page}", "type": "header", "content": f"Unit {ordinal}", "page_idx": page})
    _write_json(canonical / "blocks.json", {"blocks": blocks})

    result = build_outline_artifact(canonical, tmp_path / "output")
    document = json.loads((tmp_path / "output" / "outline.json").read_text())

    assert result["status"] == "passed"
    assert document["running_root_reconciliation"]["added_count"] == 0
    assert {row["title"] for row in document["nodes"] if row["level"] == 1} == {"1 Introductions", "2 Hobbies"}
    assert all(row["action"] == "attach_running_alias" for row in document["running_root_reconciliation"]["records"])


def test_self_nested_problem_set_root_collapses_under_real_chapter():
    nodes = [
        {"title": "Vector Spaces", "level": 1, "parent_title": ""},
        {"title": "Previous Exercise", "level": 2, "parent_title": "Vector Spaces"},
        {"title": "Problem Set 8", "level": 1, "parent_title": ""},
        {"title": "Problem Set 8", "level": 2, "parent_title": "Problem Set 8"},
        {"title": "LEVEL 1", "level": 2, "parent_title": "Problem Set 8"},
        {"title": "Validity", "level": 1, "parent_title": ""},
    ]

    report = _collapse_self_nested_roots(nodes)

    assert report["removed_count"] == 1
    assert [node["title"] for node in nodes] == ["Vector Spaces", "Previous Exercise", "Problem Set 8", "LEVEL 1", "Validity"]
    assert nodes[2]["parent_title"] == "Vector Spaces"
    assert nodes[3]["parent_title"] == "Vector Spaces"
