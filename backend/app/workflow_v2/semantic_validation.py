from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path


class SemanticValidationError(RuntimeError):
    pass


def validate_semantic_artifact(canonical_dir: Path, outline_dir: Path, annotation_dir: Path, *, semantic_markdown: Path | None = None) -> dict:
    outline = _read_object(outline_dir / "outline.json")
    outline_validation = _read_object(outline_dir / "outline-validation.json")
    if outline_validation.get("status") != "passed":
        raise SemanticValidationError("semantic annotation requires an accepted outline artifact")
    sections = _read_array(annotation_dir / "sections.json")
    assets = _read_array(annotation_dir / "assets.json")
    media = _read_array(annotation_dir / "media.json")
    review_items = _read_array(annotation_dir / "review_items.json")

    outline_nodes = outline.get("nodes") or []
    outline_by_id = {str(row.get("id") or ""): row for row in outline_nodes if row.get("id")}
    outline_ids = [str(row.get("id") or "") for row in outline_nodes]
    section_outline_ids = [str(row.get("outline_node_id") or "") for row in sections]
    missing_sections = _counter_delta(outline_ids, section_outline_ids)
    extra_sections = sorted(
        value for value in (Counter(section_outline_ids) - Counter(outline_ids)).elements()
        if value
    )
    unbound_sections = [str(row.get("id") or "") for row in sections if not row.get("outline_node_id")]
    binding_mismatches = _validate_outline_bindings(outline_nodes, outline_by_id, sections)

    content_lines, image_lines, components = _canonical_lines(semantic_markdown or canonical_dir / "clean.md")
    coverage = Counter()
    owners_by_line: dict[int, list[str]] = {}
    for asset in assets:
        span = asset.get("source_span") or {}
        start = _integer(span.get("start_line"))
        end = _integer(span.get("end_line"))
        if start is None or end is None or end < start:
            continue
        for line in content_lines:
            if start <= line <= end:
                coverage[line] += 1
                owners_by_line.setdefault(line, []).append(str(asset.get("id") or ""))
    unassigned_lines = sorted(line for line in content_lines if coverage[line] == 0)
    multiply_assigned_lines = sorted(line for line in content_lines if coverage[line] > 1)

    media_sources = Counter(_normalized_path(row.get("source") or row.get("src") or row.get("path")) for row in media)
    media_sources.pop("", None)
    clean_images = Counter(image_lines.values())
    missing_media = list((clean_images - media_sources).elements())
    extra_media = list((media_sources - clean_images).elements())

    blockers = []
    if missing_sections:
        blockers.append({"code": "outline_nodes_missing_from_semantic_sections", "signatures": missing_sections[:200]})
    if extra_sections:
        blockers.append({"code": "semantic_sections_not_in_accepted_outline", "outline_node_ids": extra_sections[:200]})
    if unbound_sections:
        blockers.append({"code": "semantic_sections_without_outline_node_id", "section_ids": unbound_sections[:200]})
    blockers.extend(binding_mismatches)
    if unassigned_lines:
        blockers.append({"code": "clean_content_lines_unassigned", "count": len(unassigned_lines), "lines": unassigned_lines[:200]})
    if multiply_assigned_lines:
        blockers.append({"code": "clean_content_lines_assigned_more_than_once", "count": len(multiply_assigned_lines), "lines": multiply_assigned_lines[:200]})
    if missing_media:
        blockers.append({"code": "clean_images_missing_from_semantic_media", "image_refs": missing_media[:200]})
    if extra_media:
        blockers.append({"code": "semantic_media_without_clean_image", "image_refs": extra_media[:200]})
    if review_items:
        blockers.append({"code": "semantic_review_items_open", "count": len(review_items)})

    component_relations = _build_component_relations(components, owners_by_line)
    if component_relations["unassigned_component_lines"]:
        blockers.append({"code": "semantic_component_lines_unassigned", "lines": component_relations["unassigned_component_lines"][:200]})
    if component_relations["multiply_assigned_component_lines"]:
        blockers.append({"code": "semantic_component_lines_assigned_more_than_once", "lines": component_relations["multiply_assigned_component_lines"][:200]})

    coverage_report = {
        "schema": "luceon.semantic-block-coverage/v1",
        "clean_content_line_count": len(content_lines),
        "assigned_once_count": sum(coverage[line] == 1 for line in content_lines),
        "unassigned_lines": unassigned_lines,
        "multiply_assigned_lines": multiply_assigned_lines,
        "clean_image_count": sum(clean_images.values()),
        "semantic_media_count": sum(media_sources.values()),
        "clean_table_count": len(components["table"]),
        "component_counts": {key: len(value) for key, value in components.items()},
    }
    validation = {
        "schema": "luceon.semantic-validation/v1",
        "status": "passed" if not blockers else "review",
        "gates": {
            "outline_and_sections_match_bidirectionally": not missing_sections and not extra_sections and not unbound_sections and not binding_mismatches,
            "every_clean_content_line_assigned_exactly_once": not unassigned_lines and not multiply_assigned_lines,
            "image_relations_are_complete": not missing_media and not extra_media,
            "semantic_review_items_are_closed": not review_items,
            "question_formula_table_and_answer_components_assigned_exactly_once": not component_relations["unassigned_component_lines"] and not component_relations["multiply_assigned_component_lines"],
        },
        "outline_node_count": len(outline_nodes),
        "semantic_section_count": len(sections),
        "title_signature_diagnostics": {
            "outline": [_outline_signature(row) for row in outline_nodes],
            "semantic": [_outline_signature(row) for row in sections],
        },
        "blockers": blockers,
    }
    _write_json(annotation_dir / "accepted-outline.json", outline)
    _write_json(annotation_dir / "block-coverage.json", coverage_report)
    _write_json(annotation_dir / "component-relations.json", component_relations)
    _write_json(annotation_dir / "semantic-validation.json", validation)
    return validation


def _outline_signature(row: dict) -> str:
    return "|".join(
        [
            str(int(row.get("level") or 0)),
            _normalize(row.get("parent_title")),
            _normalize(row.get("title")),
        ]
    )


def _validate_outline_bindings(outline_nodes: list[dict], outline_by_id: dict[str, dict], sections: list[dict]) -> list[dict]:
    blockers = []
    section_by_outline_id = {
        str(row.get("outline_node_id") or ""): row
        for row in sections
        if row.get("outline_node_id")
    }
    bound_order = [str(row.get("outline_node_id") or "") for row in sections if row.get("outline_node_id") in outline_by_id]
    expected_order = [str(row.get("id") or "") for row in outline_nodes if row.get("id") in section_by_outline_id]
    if bound_order != expected_order:
        blockers.append({
            "code": "semantic_section_order_differs_from_accepted_outline",
            "expected_outline_node_ids": expected_order[:200],
            "actual_outline_node_ids": bound_order[:200],
        })
    previous_start = None
    for outline_id, section in section_by_outline_id.items():
        node = outline_by_id.get(outline_id)
        if not node:
            continue
        expected_parent = str(node.get("parent_id") or "") or None
        actual_parent = str(section.get("parent_outline_node_id") or "") or None
        if actual_parent != expected_parent:
            blockers.append({
                "code": "semantic_section_parent_binding_mismatch",
                "outline_node_id": outline_id,
                "expected_parent_outline_node_id": expected_parent,
                "actual_parent_outline_node_id": actual_parent,
            })
        if int(section.get("level") or 0) != int(node.get("level") or 0):
            blockers.append({
                "code": "semantic_section_level_mismatch",
                "outline_node_id": outline_id,
                "expected_level": int(node.get("level") or 0),
                "actual_level": int(section.get("level") or 0),
            })
        span = section.get("source_span") or {}
        start = _integer(span.get("start_line"))
        end = _integer(span.get("end_line"))
        if start is None or end is None or end < start:
            blockers.append({"code": "semantic_section_source_span_invalid", "outline_node_id": outline_id})
        elif previous_start is not None and start <= previous_start:
            blockers.append({"code": "semantic_section_source_order_invalid", "outline_node_id": outline_id})
        if start is not None:
            previous_start = start
    return blockers


def _counter_delta(left: list[str], right: list[str]) -> list[str]:
    return sorted((Counter(left) - Counter(right)).elements())


def _canonical_lines(path: Path) -> tuple[list[int], dict[int, str], dict[str, list[int]]]:
    content = []
    images = {}
    components = {key: [] for key in ("question", "option", "formula", "table", "image", "answer", "writing_space")}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or re.fullmatch(r"<!--.*?-->", line) or re.fullmatch(r"#{1,6}\s+.+", line):
            continue
        image = re.fullmatch(r"!\[.*\]\(([^)\r\n]+)\)", line)
        if image:
            images[number] = _normalized_path(image.group(1))
            components["image"].append(number)
            content.append(number)
            continue
        if line.startswith("<table"):
            components["table"].append(number)
        if re.search(r"(?<!\\)\$|\\\(|\\\[|\\begin\{(?:array|aligned|matrix|cases)", line):
            components["formula"].append(number)
        if re.match(r"^\d{1,3}[.)]\s+\S", line):
            components["question"].append(number)
        if re.match(r"^\(?[A-Ha-h]\)?[.)、]\s+\S", line):
            components["option"].append(number)
        if re.match(r"^(?:answer|answers|solution|solutions|答案|解答)\s*[:：]?", line, re.I):
            components["answer"].append(number)
        if re.search(r"(?:\\_){2,}|_{3,}|source_empty_chunk|答题区|书写区", line, re.I):
            components["writing_space"].append(number)
        content.append(number)
    return content, images, components


def _build_component_relations(components: dict[str, list[int]], owners_by_line: dict[int, list[str]]) -> dict:
    question_by_owner: dict[str, list[int]] = {}
    for line in components["question"]:
        owners = owners_by_line.get(line, [])
        if len(owners) == 1:
            question_by_owner.setdefault(owners[0], []).append(line)
    relations = []
    unassigned = []
    multiplied = []
    for component_type, lines in components.items():
        for line in lines:
            owners = owners_by_line.get(line, [])
            if not owners:
                unassigned.append(line)
                continue
            if len(owners) > 1:
                multiplied.append(line)
                continue
            owner = owners[0]
            prior_questions = [value for value in question_by_owner.get(owner, []) if value <= line]
            relations.append({
                "component_id": f"component-{component_type}-{line:06d}",
                "component_type": component_type,
                "source_line": line,
                "owner_asset_id": owner,
                "related_question_line": prior_questions[-1] if prior_questions and component_type != "question" else None,
                "relation": "owned_by_asset" if component_type == "question" or not prior_questions else "belongs_to_nearest_preceding_question",
            })
    return {
        "schema": "luceon.semantic-component-relations/v1",
        "status": "passed" if not unassigned and not multiplied else "review",
        "component_counts": {key: len(value) for key, value in components.items()},
        "relation_count": len(relations),
        "relations": relations,
        "unassigned_component_lines": sorted(set(unassigned)),
        "multiply_assigned_component_lines": sorted(set(multiplied)),
    }


def _normalized_path(value) -> str:
    return str(value or "").strip().replace("\\", "/")


def _normalize(value) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", str(value or "").casefold()).strip()


def _integer(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _read_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SemanticValidationError(f"{path.name} must contain an object")
    return value


def _read_array(path: Path) -> list[dict]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise SemanticValidationError(f"{path.name} must contain an array of objects")
    return value


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
