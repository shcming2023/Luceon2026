from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path


class OutlineApplicationError(RuntimeError):
    pass


def apply_accepted_outline(canonical_dir: Path, outline_dir: Path, output_path: Path) -> dict:
    outline = _read_object(outline_dir / "outline.json")
    validation = _read_object(outline_dir / "outline-validation.json")
    if validation.get("status") != "passed":
        raise OutlineApplicationError("only an accepted outline can be applied")
    nodes = outline.get("nodes") or []
    _bind_parent_ids(nodes)
    lines = (canonical_dir / "clean.md").read_text(encoding="utf-8").splitlines()
    blocks = {
        str(row.get("block_id") or ""): row
        for row in _read_object(canonical_dir / "blocks.json").get("blocks") or []
    }
    heading_lines: dict[str, list[int]] = defaultdict(list)
    plain_lines: dict[str, list[int]] = defaultdict(list)
    page_markers = {}
    page_by_line = {}
    current_page = None
    for index, raw in enumerate(lines):
        stripped = raw.strip()
        page = re.fullmatch(r"<!--\s*page_idx:\s*(\d+)\s*-->", stripped)
        if page:
            current_page = int(page.group(1))
            page_markers[current_page] = index
        page_by_line[index] = current_page
        heading = re.fullmatch(r"#{1,6}\s+(.+?)\s*", stripped)
        if heading:
            heading_lines[_normalize(heading.group(1))].append(index)
        elif stripped:
            plain_lines[_normalize(_strip_inline_markdown(stripped))].append(index)
    next_page_by_line = {}
    next_page = None
    for index in range(len(lines) - 1, -1, -1):
        page = re.fullmatch(r"<!--\s*page_idx:\s*(\d+)\s*-->", lines[index].strip())
        if page:
            next_page = int(page.group(1))
        next_page_by_line[index] = next_page

    replacements = {}
    anchored_positions = {}
    actions = []
    unresolved = []
    for node in nodes:
        title = str(node.get("title") or "").strip()
        level = int(node.get("level") or 0)
        target = f"{'#' * level} {title}"
        running_hierarchy_node = _is_running_hierarchy_node(node)
        line_index = None if running_hierarchy_node else _evidenced_clean_line(node, lines)
        method = "evidenced_clean_line"
        if line_index is None and not running_hierarchy_node:
            matches = heading_lines.get(_normalize(title), [])
            page_match = _source_page_match(node, matches, page_by_line, next_page_by_line)
            if page_match is not None:
                line_index = page_match
                method = "source_page_heading"
            elif _integer(node.get("source_page")) is None and len(matches) == 1:
                line_index = matches[0]
                method = "existing_unique_heading"
        if line_index is None and not running_hierarchy_node:
            matches = plain_lines.get(_normalize(title), [])
            page_match = _source_page_match(node, matches, page_by_line, next_page_by_line)
            if page_match is not None:
                line_index = page_match
                method = "source_page_plain_label"
            elif _integer(node.get("source_page")) is None and len(matches) == 1:
                line_index = matches[0]
                method = "existing_unique_plain_label"
        if line_index is not None:
            replacements[line_index] = (_outline_marker(node), target)
            anchored_positions[node["id"]] = line_index
            actions.append({"node_id": node["id"], "action": "promote_or_relevel", "line": line_index + 1, "method": method})

    insert_before: dict[int, list[tuple[int, str, dict]]] = defaultdict(list)
    node_by_title = {_normalize(node.get("title")): node for node in nodes}
    node_by_path = {
        tuple(_normalize(value) for value in node.get("path") or [node.get("title")]): node
        for node in nodes
    }
    planned_positions = dict(anchored_positions)
    for node_index, node in enumerate(nodes):
        if node["id"] in anchored_positions:
            continue
        block = _source_block_for_node(node, blocks)
        insertion = None
        method = ""
        descendant_lines = [
            position
            for later in nodes[node_index + 1 :]
            if _is_descendant(node, later)
            for position in [
                _prospective_insertion_line(later, blocks, page_markers, anchored_positions)
            ]
            if position is not None
        ]
        if block is None and descendant_lines:
            insertion = min(descendant_lines)
            method = "before_first_anchored_descendant"
        source_page = _integer(node.get("source_page"))
        source_page_idx = source_page - 1 if source_page is not None else None
        if insertion is None and source_page_idx in page_markers:
            insertion = page_markers[source_page_idx] + 1
            parent = _parent_node(node, node_by_path, node_by_title)
            if parent and parent.get("id") in anchored_positions:
                insertion = max(insertion, anchored_positions[parent["id"]] + 1)
            method = "after_outline_source_page_marker"
        if insertion is None and block:
            page_idx = _integer(block.get("page_idx"))
            if page_idx in page_markers:
                insertion = page_markers[page_idx] + 1
                parent = _parent_node(node, node_by_path, node_by_title)
                if parent and parent.get("id") in anchored_positions:
                    insertion = max(insertion, anchored_positions[parent["id"]] + 1)
                method = "after_source_page_marker"
        if insertion is None and int(node.get("level") or 0) == 1:
            for later in nodes[node_index + 1 :]:
                if later["id"] in anchored_positions:
                    insertion = anchored_positions[later["id"]]
                    method = "before_next_anchored_descendant"
                    break
        parent = _parent_node(node, node_by_path, node_by_title)
        if insertion is not None and parent and parent.get("id") in planned_positions:
            parent_position = planned_positions[parent["id"]]
            if parent.get("id") in anchored_positions:
                parent_position += 1
            insertion = max(insertion, parent_position)
        if insertion is None:
            unresolved.append({"node_id": node["id"], "title": node["title"], "code": "outline_node_has_no_clean_insertion_anchor"})
            continue
        target = f"{'#' * int(node['level'])} {node['title']}"
        detail = {"node_id": node["id"], "action": "restore_source_heading", "insert_before_line": insertion + 1, "method": method, "source_block_id": block.get("block_id") if block else ""}
        insert_before[insertion].append((node_index, _outline_marker(node), target, detail))
        planned_positions[node["id"]] = insertion
        actions.append(detail)

    if unresolved:
        raise OutlineApplicationError("accepted outline could not be applied: " + "; ".join(row["node_id"] for row in unresolved))

    rendered = []
    for index in range(len(lines) + 1):
        for _order, marker, heading, _detail in sorted(insert_before.get(index, []), key=lambda row: row[0]):
            rendered.extend([marker, heading, ""])
        if index < len(lines):
            if index in replacements:
                marker, heading = replacements[index]
                rendered.extend([marker, heading])
            elif re.fullmatch(r"#{1,6}\s+(.+?)\s*", lines[index].strip()):
                rendered.append(re.sub(r"^#{1,6}\s+", "", lines[index].strip()))
                actions.append({"action": "demote_unselected_heading", "line": index + 1})
            else:
                rendered.append(lines[index])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(rendered).rstrip() + "\n", encoding="utf-8")
    report = {
        "schema": "luceon.outline-application/v1",
        "status": "passed",
        "outline_node_count": len(nodes),
        "promoted_or_releveled_count": sum(row["action"] == "promote_or_relevel" for row in actions),
        "restored_source_heading_count": sum(row["action"] == "restore_source_heading" for row in actions),
        "demoted_unselected_heading_count": sum(row["action"] == "demote_unselected_heading" for row in actions),
        "actions": actions,
        "unresolved": [],
    }
    return report


def _bind_parent_ids(nodes: list[dict]) -> None:
    stack: list[dict] = []
    for node in nodes:
        level = int(node.get("level") or 0)
        while stack and int(stack[-1].get("level") or 0) >= level:
            stack.pop()
        parent = stack[-1] if stack and int(stack[-1].get("level") or 0) == level - 1 else None
        node["parent_id"] = None if level == 1 else str((parent or {}).get("id") or "") or None
        stack.append(node)


def _outline_marker(node: dict) -> str:
    parent_id = str(node.get("parent_id") or "")
    return (
        "<!-- outline_node_id: "
        f"{node.get('id')}; parent_outline_node_id: {parent_id}; "
        f"source_page: {node.get('source_page') or ''} -->"
    )


def _evidenced_clean_line(node: dict, lines: list[str]) -> int | None:
    for evidence in node.get("evidence") or []:
        number = _integer(evidence.get("clean_line"))
        if number is None or number < 1 or number > len(lines):
            continue
        line = _strip_inline_markdown(lines[number - 1].strip())
        if _normalize(line) == _normalize(node.get("title")):
            return number - 1
    return None


def _source_block_for_node(node: dict, blocks: dict[str, dict]) -> dict | None:
    for evidence in node.get("evidence") or []:
        block_id = str(evidence.get("source_block_id") or "")
        if block_id in blocks:
            return blocks[block_id]
        for candidate in evidence.get("block_ids") or []:
            candidate = str(candidate)
            candidate_ids = [candidate]
            if candidate.isdigit():
                candidate_ids.append(f"content-list-{int(candidate):06d}")
            for candidate_id in candidate_ids:
                if candidate_id in blocks:
                    return blocks[candidate_id]
    return None


def _is_descendant(parent: dict, candidate: dict) -> bool:
    parent_path = [_normalize(value) for value in parent.get("path") or [parent.get("title")]]
    candidate_path = [_normalize(value) for value in candidate.get("path") or [candidate.get("title")]]
    return len(candidate_path) > len(parent_path) and candidate_path[: len(parent_path)] == parent_path


def _parent_node(node: dict, node_by_path: dict[tuple[str, ...], dict], node_by_title: dict[str, dict]) -> dict | None:
    path = tuple(_normalize(value) for value in node.get("path") or [])
    if len(path) > 1 and path[:-1] in node_by_path:
        return node_by_path[path[:-1]]
    return node_by_title.get(_normalize(node.get("parent_title")))


def _is_running_hierarchy_node(node: dict) -> bool:
    return any(
        evidence.get("method") == "repeated_running_hierarchy_label"
        for evidence in node.get("evidence") or []
    )


def _prospective_insertion_line(
    node: dict,
    blocks: dict[str, dict],
    page_markers: dict[int, int],
    anchored_positions: dict[str, int],
) -> int | None:
    if node.get("id") in anchored_positions:
        return anchored_positions[node["id"]]
    source_page = _integer(node.get("source_page"))
    source_page_idx = source_page - 1 if source_page is not None else None
    if source_page_idx in page_markers:
        return page_markers[source_page_idx] + 1
    block = _source_block_for_node(node, blocks)
    page_idx = _integer(block.get("page_idx")) if block else None
    return page_markers.get(page_idx, None) + 1 if page_idx in page_markers else None


def _source_page_match(
    node: dict,
    matches: list[int],
    page_by_line: dict[int, int | None],
    next_page_by_line: dict[int, int | None],
) -> int | None:
    source_page = _integer(node.get("source_page"))
    if source_page is None or not matches:
        return None
    for expected_page in (source_page - 1, source_page):
        on_page = [
            line for line in matches
            if expected_page in {page_by_line.get(line), next_page_by_line.get(line)}
        ]
        if len(on_page) == 1:
            return on_page[0]
    return None


def _strip_inline_markdown(value: str) -> str:
    return re.sub(r"^[*_`]+|[*_`]+$", "", value).strip()


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
        raise OutlineApplicationError(f"{path.name} must contain an object")
    return value
