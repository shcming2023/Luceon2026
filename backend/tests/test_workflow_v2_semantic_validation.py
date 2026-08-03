import json

from app.workflow_v2.semantic_validation import validate_semantic_artifact


def _write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def _fixture(tmp_path):
    canonical = tmp_path / "canonical"
    outline = tmp_path / "outline"
    annotation = tmp_path / "annotation"
    canonical.mkdir()
    outline.mkdir()
    annotation.mkdir()
    (canonical / "clean.md").write_text("<!-- page_idx: 1 -->\n# Chapter 1\n## Lesson 1\nQuestion text\n", encoding="utf-8")
    nodes = [
        {"id": "o1", "title": "Chapter 1", "level": 1, "parent_id": None},
        {"id": "o2", "title": "Lesson 1", "level": 2, "parent_id": "o1"},
    ]
    _write(outline / "outline.json", {"nodes": nodes})
    _write(outline / "outline-validation.json", {"status": "passed"})
    sections = [
        {"id": "s1", "outline_node_id": "o1", "parent_outline_node_id": None, "title": "Chapter 1", "level": 1, "parent_id": None, "source_span": {"start_line": 2, "end_line": 4}},
        {"id": "s2", "outline_node_id": "o2", "parent_outline_node_id": "o1", "title": "Lesson 1", "level": 2, "parent_id": "s1", "source_span": {"start_line": 3, "end_line": 4}},
    ]
    _write(annotation / "sections.json", sections)
    _write(annotation / "assets.json", [{"id": "a1", "source_span": {"start_line": 4, "end_line": 4}}])
    _write(annotation / "media.json", [])
    _write(annotation / "review_items.json", [])
    return canonical, outline, annotation


def test_semantic_artifact_must_match_outline_and_cover_clean_once(tmp_path):
    canonical, outline, annotation = _fixture(tmp_path)

    result = validate_semantic_artifact(canonical, outline, annotation)

    assert result["status"] == "passed"
    assert all(result["gates"].values())
    assert (annotation / "accepted-outline.json").is_file()
    assert (annotation / "component-relations.json").is_file()


def test_semantic_extra_section_and_duplicate_coverage_are_blocked(tmp_path):
    canonical, outline, annotation = _fixture(tmp_path)
    sections = json.loads((annotation / "sections.json").read_text())
    sections.append({"id": "s3", "outline_node_id": "invented", "parent_outline_node_id": "o1", "title": "Invented", "level": 2, "parent_id": "s1", "source_span": {"start_line": 5, "end_line": 5}})
    _write(annotation / "sections.json", sections)
    _write(
        annotation / "assets.json",
        [
            {"id": "a1", "source_span": {"start_line": 4, "end_line": 4}},
            {"id": "a2", "source_span": {"start_line": 4, "end_line": 4}},
        ],
    )

    result = validate_semantic_artifact(canonical, outline, annotation)

    codes = {row["code"] for row in result["blockers"]}
    assert "semantic_sections_not_in_accepted_outline" in codes
    assert "clean_content_lines_assigned_more_than_once" in codes


def test_semantic_components_receive_auditable_asset_and_question_relations(tmp_path):
    canonical, outline, annotation = _fixture(tmp_path)
    (canonical / "clean.md").write_text(
        "<!-- page_idx: 1 -->\n# Chapter 1\n## Lesson 1\n"
        "1. What is $x+1$?\nA. 1\nB. 2\nSolution: B\nWrite: \\_\\_\\_\\_\n"
        "<table><tr><td>1</td></tr></table>\n",
        encoding="utf-8",
    )
    _write(annotation / "assets.json", [{"id": "a1", "source_span": {"start_line": 4, "end_line": 9}}])

    result = validate_semantic_artifact(canonical, outline, annotation)
    relations = json.loads((annotation / "component-relations.json").read_text())

    assert result["status"] == "passed"
    assert relations["component_counts"] == {
        "question": 1,
        "option": 2,
        "formula": 1,
        "table": 1,
        "image": 0,
        "answer": 1,
        "writing_space": 1,
    }
    assert relations["relation_count"] == 7
    assert all(row["owner_asset_id"] == "a1" for row in relations["relations"])


def test_semantic_media_matches_image_with_bracketed_alt_text(tmp_path):
    canonical, outline, annotation = _fixture(tmp_path)
    (canonical / "clean.md").write_text(
        "<!-- page_idx: 1 -->\n# Chapter 1\n## Lesson 1\n"
        "![Join edges [AB] and [CB]](images/net.jpg)\n",
        encoding="utf-8",
    )
    _write(annotation / "assets.json", [{"id": "a1", "source_span": {"start_line": 4, "end_line": 4}}])
    _write(annotation / "media.json", [{"src": "images/net.jpg"}])

    result = validate_semantic_artifact(canonical, outline, annotation)

    assert result["status"] == "passed"
    assert result["gates"]["image_relations_are_complete"] is True


def test_repeated_titles_are_bound_by_stable_outline_ids(tmp_path):
    canonical, outline, annotation = _fixture(tmp_path)
    nodes = [
        {"id": "root-a", "title": "Unit A", "level": 1, "parent_id": None},
        {"id": "questions-a", "title": "Questions", "level": 2, "parent_id": "root-a"},
        {"id": "root-b", "title": "Unit B", "level": 1, "parent_id": None},
        {"id": "questions-b", "title": "Questions", "level": 2, "parent_id": "root-b"},
    ]
    _write(outline / "outline.json", {"nodes": nodes})
    sections = [
        {"id": "s1", "outline_node_id": "root-a", "parent_outline_node_id": None, "title": "Unit A", "level": 1, "parent_id": None, "source_span": {"start_line": 2, "end_line": 4}},
        {"id": "s2", "outline_node_id": "questions-a", "parent_outline_node_id": "root-a", "title": "Questions", "level": 2, "parent_id": "s1", "source_span": {"start_line": 3, "end_line": 4}},
        {"id": "s3", "outline_node_id": "root-b", "parent_outline_node_id": None, "title": "Unit B", "level": 1, "parent_id": None, "source_span": {"start_line": 5, "end_line": 7}},
        {"id": "s4", "outline_node_id": "questions-b", "parent_outline_node_id": "root-b", "title": "Questions", "level": 2, "parent_id": "s3", "source_span": {"start_line": 6, "end_line": 7}},
    ]
    _write(annotation / "sections.json", sections)

    result = validate_semantic_artifact(canonical, outline, annotation)

    assert result["gates"]["outline_and_sections_match_bidirectionally"] is True


def test_title_match_cannot_hide_wrong_parent_id(tmp_path):
    canonical, outline, annotation = _fixture(tmp_path)
    sections = json.loads((annotation / "sections.json").read_text())
    sections[1]["parent_outline_node_id"] = None
    _write(annotation / "sections.json", sections)

    result = validate_semantic_artifact(canonical, outline, annotation)

    assert "semantic_section_parent_binding_mismatch" in {row["code"] for row in result["blockers"]}
