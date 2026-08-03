import json

from app.workflow_v2.outline_application import apply_accepted_outline


def _write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def test_existing_plain_labels_are_promoted_without_text_change(tmp_path):
    canonical = tmp_path / "canonical"
    outline = tmp_path / "outline"
    canonical.mkdir()
    outline.mkdir()
    (canonical / "clean.md").write_text("<!-- page_idx: 0 -->\n# Article\nVocabulary\nBody\n", encoding="utf-8")
    _write(canonical / "blocks.json", {"blocks": []})
    _write(outline / "outline-validation.json", {"status": "passed"})
    _write(
        outline / "outline.json",
        {
            "nodes": [
                {"id": "n1", "title": "Article", "level": 1, "evidence": []},
                {"id": "n2", "title": "Vocabulary", "level": 2, "parent_title": "Article", "evidence": [{"clean_line": 3}]},
            ]
        },
    )

    report = apply_accepted_outline(canonical, outline, tmp_path / "semantic.md")

    assert report["status"] == "passed"
    assert "## Vocabulary" in (tmp_path / "semantic.md").read_text()
    assert "Body" in (tmp_path / "semantic.md").read_text()
    assert "<!-- outline_node_id: n1; parent_outline_node_id:" in (tmp_path / "semantic.md").read_text()
    assert "<!-- outline_node_id: n2; parent_outline_node_id: n1;" in (tmp_path / "semantic.md").read_text()


def test_source_heading_missing_from_clean_is_restored_at_page_marker(tmp_path):
    canonical = tmp_path / "canonical"
    outline = tmp_path / "outline"
    canonical.mkdir()
    outline.mkdir()
    (canonical / "clean.md").write_text("<!-- page_idx: 2 -->\n# Chapter\nQuestion body\n", encoding="utf-8")
    _write(canonical / "blocks.json", {"blocks": [{"block_id": "b1", "source_order": 10, "page_idx": 2, "content": "Questions 1-4"}]})
    _write(outline / "outline-validation.json", {"status": "passed"})
    _write(
        outline / "outline.json",
        {
            "nodes": [
                {"id": "n1", "title": "Chapter", "level": 1, "evidence": []},
                {"id": "n2", "title": "Questions 1-4", "level": 2, "parent_title": "Chapter", "evidence": [{"source_block_id": "b1"}]},
            ]
        },
    )

    report = apply_accepted_outline(canonical, outline, tmp_path / "semantic.md")
    text = (tmp_path / "semantic.md").read_text()

    assert report["restored_source_heading_count"] == 1
    assert text.index("# Chapter") < text.index("## Questions 1-4")


def test_source_page_precedes_unrelated_repeated_block_evidence_for_root(tmp_path):
    canonical = tmp_path / "canonical"
    outline = tmp_path / "outline"
    canonical.mkdir()
    outline.mkdir()
    (canonical / "clean.md").write_text(
        "<!-- page_idx: 0 -->\nFront reference\n"
        "<!-- page_idx: 4 -->\nChapter body\n",
        encoding="utf-8",
    )
    _write(canonical / "blocks.json", {"blocks": [{
        "block_id": "front-reference",
        "source_order": 1,
        "page_idx": 0,
        "content": "Chapter 5: Comprehension",
    }]})
    _write(outline / "outline-validation.json", {"status": "passed"})
    _write(outline / "outline.json", {"nodes": [{
        "id": "n1",
        "title": "Chapter 5: Comprehension",
        "level": 1,
        "source_page": 5,
        "evidence": [{"block_ids": ["front-reference"]}],
    }]})

    report = apply_accepted_outline(canonical, outline, tmp_path / "semantic.md")
    text = (tmp_path / "semantic.md").read_text()

    assert report["actions"][0]["method"] == "after_outline_source_page_marker"
    assert text.index("<!-- page_idx: 4 -->") < text.index("# Chapter 5: Comprehension")


def test_parent_precedes_child_when_both_are_restored_at_same_page_marker(tmp_path):
    canonical = tmp_path / "canonical"
    outline = tmp_path / "outline"
    canonical.mkdir()
    outline.mkdir()
    (canonical / "clean.md").write_text("<!-- page_idx: 4 -->\nBody\n", encoding="utf-8")
    _write(canonical / "blocks.json", {"blocks": [{
        "block_id": "late-footer",
        "source_order": 99,
        "page_idx": 4,
        "content": "Chapter 1: Skills",
    }]})
    _write(outline / "outline-validation.json", {"status": "passed"})
    _write(outline / "outline.json", {"nodes": [
        {
            "id": "chapter",
            "title": "Chapter 1: Skills",
            "level": 1,
            "source_page": 5,
            "evidence": [{"block_ids": ["late-footer"]}],
        },
        {
            "id": "topic",
            "title": "1.1 Introduction",
            "level": 2,
            "parent_title": "Chapter 1: Skills",
            "source_page": 5,
            "evidence": [],
        },
    ]})

    apply_accepted_outline(canonical, outline, tmp_path / "semantic.md")
    text = (tmp_path / "semantic.md").read_text()

    assert text.index("# Chapter 1: Skills") < text.index("## 1.1 Introduction")


def test_unanchored_parent_is_inserted_immediately_before_its_first_anchored_descendant(tmp_path):
    canonical = tmp_path / "canonical"
    outline = tmp_path / "outline"
    canonical.mkdir()
    outline.mkdir()
    (canonical / "clean.md").write_text(
        "<!-- page_idx: 4 -->\n# Unit 1\nFirst topic body\n"
        "<!-- page_idx: 5 -->\nSecond topic body\n",
        encoding="utf-8",
    )
    _write(canonical / "blocks.json", {"blocks": []})
    _write(outline / "outline-validation.json", {"status": "passed"})
    _write(outline / "outline.json", {"nodes": [
        {"id": "root", "title": "Unit 1", "level": 1, "source_page": 5, "path": ["Unit 1"], "evidence": []},
        {"id": "first-parent", "title": "Number", "level": 2, "source_page": 5, "path": ["Unit 1", "Number"], "evidence": []},
        {"id": "first-child", "title": "First topic body", "level": 3, "source_page": 5, "path": ["Unit 1", "Number", "First topic body"], "evidence": []},
        {"id": "second-parent", "title": "Algebra", "level": 2, "source_page": 5, "path": ["Unit 1", "Algebra"], "evidence": []},
        {"id": "second-child", "title": "Second topic body", "level": 3, "source_page": 6, "path": ["Unit 1", "Algebra", "Second topic body"], "evidence": []},
    ]})

    report = apply_accepted_outline(canonical, outline, tmp_path / "semantic.md")
    text = (tmp_path / "semantic.md").read_text()

    assert text.index("# Unit 1") < text.index("## Number") < text.index("### First topic body") < text.index("## Algebra")
    assert text.index("## Algebra") < text.index("### Second topic body")
    assert sum(row.get("method") == "before_first_anchored_descendant" for row in report["actions"]) >= 2


def test_legacy_numeric_block_evidence_resolves_canonical_block_id(tmp_path):
    canonical = tmp_path / "canonical"
    outline = tmp_path / "outline"
    canonical.mkdir()
    outline.mkdir()
    (canonical / "clean.md").write_text("<!-- page_idx: 2 -->\n# Chapter\nBody\n", encoding="utf-8")
    _write(canonical / "blocks.json", {"blocks": [{"block_id": "content-list-000042", "source_order": 42, "page_idx": 2, "content": "Topic"}]})
    _write(outline / "outline-validation.json", {"status": "passed"})
    _write(
        outline / "outline.json",
        {
            "nodes": [
                {"id": "n1", "title": "Chapter", "level": 1, "evidence": []},
                {"id": "n2", "title": "Topic", "level": 2, "parent_title": "Chapter", "evidence": [{"block_ids": [42]}]},
            ]
        },
    )

    report = apply_accepted_outline(canonical, outline, tmp_path / "semantic.md")

    assert report["restored_source_heading_count"] == 1


def test_unselected_existing_heading_is_demoted_without_deleting_text(tmp_path):
    canonical = tmp_path / "canonical"
    outline = tmp_path / "outline"
    canonical.mkdir()
    outline.mkdir()
    (canonical / "clean.md").write_text("# Chapter\n## Running header\nBody\n", encoding="utf-8")
    _write(canonical / "blocks.json", {"blocks": []})
    _write(outline / "outline-validation.json", {"status": "passed"})
    _write(outline / "outline.json", {"nodes": [{"id": "n1", "title": "Chapter", "level": 1, "evidence": []}]})

    report = apply_accepted_outline(canonical, outline, tmp_path / "semantic.md")
    text = (tmp_path / "semantic.md").read_text()

    assert report["demoted_unselected_heading_count"] == 1
    assert "Running header" in text
    assert "## Running header" not in text


def test_duplicate_heading_is_selected_by_source_page(tmp_path):
    canonical = tmp_path / "canonical"
    outline = tmp_path / "outline"
    canonical.mkdir()
    outline.mkdir()
    (canonical / "clean.md").write_text(
        "<!-- page_idx: 4 -->\n# Chapter 1\n## Review\nFirst\n"
        "<!-- page_idx: 9 -->\n# Chapter 2\n## Review\nSecond\n",
        encoding="utf-8",
    )
    _write(canonical / "blocks.json", {"blocks": []})
    _write(outline / "outline-validation.json", {"status": "passed"})
    _write(outline / "outline.json", {"nodes": [
        {"id": "n1", "title": "Chapter 1", "level": 1, "source_page": 5, "evidence": []},
        {"id": "n2", "title": "Review", "level": 2, "parent_title": "Chapter 1", "source_page": 5, "evidence": []},
        {"id": "n3", "title": "Chapter 2", "level": 1, "source_page": 10, "evidence": []},
    ]})

    report = apply_accepted_outline(canonical, outline, tmp_path / "semantic.md")
    text = (tmp_path / "semantic.md").read_text()

    assert any(row.get("method") == "source_page_heading" for row in report["actions"])
    assert text.count("## Review") == 1
    assert text.count("Review") == 2


def test_unique_title_on_unrelated_page_is_not_used_as_source_page_anchor(tmp_path):
    canonical = tmp_path / "canonical"
    outline = tmp_path / "outline"
    canonical.mkdir()
    outline.mkdir()
    (canonical / "clean.md").write_text(
        "<!-- page_idx: 4 -->\nTopic body\n"
        "<!-- page_idx: 20 -->\n## Number\nUnrelated later body\n",
        encoding="utf-8",
    )
    _write(canonical / "blocks.json", {"blocks": []})
    _write(outline / "outline-validation.json", {"status": "passed"})
    _write(outline / "outline.json", {"nodes": [
        {"id": "root", "title": "Unit 1", "level": 1, "source_page": 5, "path": ["Unit 1"], "evidence": []},
        {"id": "parent", "title": "Number", "level": 2, "source_page": 5, "path": ["Unit 1", "Number"], "evidence": []},
        {"id": "child", "title": "Topic body", "level": 3, "source_page": 5, "path": ["Unit 1", "Number", "Topic body"], "evidence": []},
    ]})

    apply_accepted_outline(canonical, outline, tmp_path / "semantic.md")
    text = (tmp_path / "semantic.md").read_text()

    assert text.index("## Number") < text.index("### Topic body")
    assert "Unrelated later body" in text


def test_running_footer_evidence_restores_root_at_page_start_not_footer_position(tmp_path):
    canonical = tmp_path / "canonical"
    outline = tmp_path / "outline"
    canonical.mkdir()
    outline.mkdir()
    (canonical / "clean.md").write_text(
        "<!-- page_idx: 4 -->\nFirst topic\nUNIT 1\n",
        encoding="utf-8",
    )
    _write(canonical / "blocks.json", {"blocks": [{
        "block_id": "footer-unit",
        "page_idx": 4,
        "type": "footer",
        "content": "UNIT 1",
    }]})
    _write(outline / "outline-validation.json", {"status": "passed"})
    _write(outline / "outline.json", {"nodes": [
        {
            "id": "root",
            "title": "Unit 1",
            "level": 1,
            "source_page": 5,
            "path": ["Unit 1"],
            "evidence": [{
                "method": "repeated_running_hierarchy_label",
                "block_ids": ["footer-unit"],
            }],
        },
        {
            "id": "child",
            "title": "First topic",
            "level": 2,
            "parent_title": "Unit 1",
            "source_page": 5,
            "path": ["Unit 1", "First topic"],
            "evidence": [],
        },
    ]})

    apply_accepted_outline(canonical, outline, tmp_path / "semantic.md")
    text = (tmp_path / "semantic.md").read_text()

    assert text.index("# Unit 1") < text.index("## First topic")
    assert "\nUNIT 1\n" in text
