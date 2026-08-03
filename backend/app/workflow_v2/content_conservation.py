from __future__ import annotations

import hashlib
import html
import json
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path


ALLOWLISTED_ACTIONS = {
    "preserve",
    "remove_empty",
    "remove_out_of_scope",
    "remove_repeated_header_footer",
    "remove_structural_page_noise",
    "transform_to_outline_heading",
}


class ContentConservationError(RuntimeError):
    pass


def build_canonical_conservation(canonical_dir: Path) -> dict:
    manifest = _read_object(canonical_dir / "manifest.json")
    source_root = Path(str(manifest.get("source_root") or ""))
    content_file = source_root / str(manifest.get("content_file") or "")
    if content_file.is_file():
        source = json.loads(content_file.read_text(encoding="utf-8"))
        if isinstance(source, dict):
            source = source.get("content_list") or source.get("items") or source.get("blocks")
    else:
        snapshot_path = canonical_dir / "blocks.json"
        if not snapshot_path.is_file():
            raise ContentConservationError("canonical source content list and block snapshot are unavailable")
        source = (_read_object(snapshot_path).get("blocks") or [])
        source = sorted(source, key=lambda row: int(row.get("source_order") or 0))
    if not isinstance(source, list):
        raise ContentConservationError("canonical source content list must be an array")

    assignments = _read_jsonl(canonical_dir / "raw_block_assignments.jsonl")
    assignment_counts = Counter(str(row.get("block_ref") or "") for row in assignments if row.get("block_ref"))
    assigned = set(assignment_counts)
    duplicate_assignments = sorted(ref for ref, count in assignment_counts.items() if count > 1)
    output_refs_by_block: dict[str, list[str]] = defaultdict(list)
    for row in assignments:
        block_ref = str(row.get("block_ref") or "")
        if not block_ref:
            continue
        output_refs_by_block[block_ref].extend(
            str(value) for value in row.get("output_refs") or [] if str(value)
        )
        if row.get("unit_id"):
            output_refs_by_block[block_ref].append(f"unit:{row['unit_id']}")
    included = manifest.get("included_page_range") or {}
    start_page = _integer(included.get("start_page"))
    end_page = _integer(included.get("end_page"))
    repeated_edge_text = _repeated_edge_text(source)
    outline_refs = _selected_outline_block_refs(canonical_dir, source)
    image_report = _read_object(canonical_dir / "image_closure_report.json")
    unresolved_markdown_images = sorted(
        str(value).replace("\\", "/")
        for value in image_report.get("markdown_refs_missing_from_source") or []
    )
    clean_blocks = _parse_clean_blocks(canonical_dir / "clean.md")
    excluded_body_sequences = _excluded_numbered_body_sequences(source, start_page, end_page)
    clean_component_index: dict[tuple, set[str]] = defaultdict(set)
    for clean_block in clean_blocks:
        clean_component_index[_conservation_key(clean_block)].add(clean_block["type"])
    resegmentation_targets = {
        _conservation_key(
            {
                **component,
                "page_idx": raw.get("page_idx"),
                "type": "table" if raw.get("type") == "table" and component.get("role") == "content" else "text",
            }
        )
        for raw in source
        if isinstance(raw, dict)
        for component in _semantic_components(raw)
    }
    clean_resegmented_index = _clean_resegmented_index(clean_blocks, resegmentation_targets)

    blocks = []
    ledger = []
    unexplained = []
    for index, raw in enumerate(source):
        if not isinstance(raw, dict):
            raw = {"type": "unknown", "value": raw}
        block_id = f"content-list-{index:06d}"
        page_idx = _integer(raw.get("page_idx"))
        block_type = str(raw.get("type") or "unknown")
        semantic_components = _semantic_components(raw)
        content = _semantic_content(raw)
        normalized = _normalize_text(content)
        image_ref = _image_ref(raw)
        record = {
            "block_id": block_id,
            "source_order": index,
            "page_idx": page_idx,
            "type": block_type,
            "bbox": raw.get("bbox") if isinstance(raw.get("bbox"), list) else [],
            "content": content,
            "semantic_components": semantic_components,
            "image_ref": image_ref,
            "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }
        blocks.append(record)
        clean_disposition = _exact_clean_disposition(record, clean_component_index, clean_resegmented_index)
        if not normalized and not image_ref:
            action, reason = "remove_empty", "source_block_has_no_semantic_content"
        elif page_idx is not None and start_page is not None and page_idx < start_page:
            action, reason = "remove_out_of_scope", "before_verified_body_range"
        elif page_idx is not None and end_page is not None and page_idx > end_page:
            action, reason = "remove_out_of_scope", "after_verified_body_range"
        elif _is_structural_page_noise(raw, normalized):
            action, reason = "remove_structural_page_noise", "typed_page_noise_at_page_edge"
        elif clean_disposition == "content":
            action, reason = "preserve", "source_components_present_in_canonical_clean"
        elif block_id in outline_refs:
            action, reason = "transform_to_outline_heading", "source_content_is_bound_to_a_canonical_heading"
        elif clean_disposition == "heading":
            action, reason = "transform_to_outline_heading", "source_content_is_bound_to_a_canonical_heading"
        elif block_id in assigned:
            action, reason = "preserve", "assigned_to_canonical_body"
        elif block_type == "text" and normalized in repeated_edge_text:
            action, reason = "remove_repeated_header_footer", "unassigned_text_repeated_at_stable_page_edge"
        else:
            action, reason = "review", "source_block_has_no_output_assignment_or_allowlisted_removal"
        entry = {
            "block_id": block_id,
            "action": action,
            "reason": reason,
            "page_idx": page_idx,
            "type": block_type,
            "source_content_sha256": record["content_sha256"],
            "output_refs": output_refs_by_block.get(block_id, []) + outline_refs.get(block_id, []),
        }
        ledger.append(entry)
        if action == "review":
            unexplained.append(entry)

    counts = defaultdict(int)
    for row in ledger:
        counts[row["action"]] += 1
    blockers = []
    if duplicate_assignments:
        blockers.append({"code": "source_blocks_assigned_more_than_once", "block_ids": duplicate_assignments})
    if unexplained:
        blockers.append({"code": "unexplained_source_block_loss", "count": len(unexplained), "block_ids": [row["block_id"] for row in unexplained[:200]]})
    invalid_actions = sorted({row["action"] for row in ledger if row["action"] not in ALLOWLISTED_ACTIONS and row["action"] != "review"})
    if invalid_actions:
        blockers.append({"code": "cleaning_action_not_allowlisted", "actions": invalid_actions})
    if unresolved_markdown_images:
        blockers.append({"code": "image_lineage_unresolved", "image_refs": unresolved_markdown_images})
    if excluded_body_sequences:
        blockers.append(
            {
                "code": "body_content_detected_outside_included_page_range",
                "sequences": excluded_body_sequences,
            }
        )
    clean_map = _map_preserved_blocks_to_clean(canonical_dir, blocks, ledger, outline_refs)
    _bind_clean_output_refs(ledger, clean_map)
    page_conservation = _build_page_conservation(blocks, ledger, _parse_clean_blocks(canonical_dir / "clean.md"))
    if clean_map["missing_source_block_ids"]:
        blockers.append(
            {
                "code": "preserved_source_blocks_missing_from_clean",
                "count": len(clean_map["missing_source_block_ids"]),
                "block_ids": clean_map["missing_source_block_ids"][:200],
            }
        )
    if clean_map["unmapped_clean_block_ids"]:
        blockers.append(
            {
                "code": "clean_blocks_without_source_lineage",
                "count": len(clean_map["unmapped_clean_block_ids"]),
                "block_ids": clean_map["unmapped_clean_block_ids"][:200],
            }
        )

    validation = {
        "schema": "luceon.canonical-conservation-validation/v1",
        "status": "passed" if not blockers else "review",
        "source_block_count": len(blocks),
        "ledger_entry_count": len(ledger),
        "action_counts": dict(sorted(counts.items())),
        "assigned_block_count": len(assigned),
        "unexplained_block_count": len(unexplained),
        "page_conservation": {
            "page_count": page_conservation["page_count"],
            "exact_page_count": page_conservation["exact_page_count"],
            "changed_page_count": page_conservation["changed_page_count"],
        },
        "gates": {
            "every_source_block_has_a_ledger_entry": len(blocks) == len(ledger),
            "assigned_blocks_are_unique": not duplicate_assignments,
            "cleaning_actions_are_allowlisted": not invalid_actions,
            "no_unexplained_content_loss": not unexplained,
            "image_lineage_is_resolved": not unresolved_markdown_images,
            "excluded_scope_has_no_numbered_body_sequence": not excluded_body_sequences,
            "preserved_blocks_exist_in_clean": not clean_map["missing_source_block_ids"],
            "clean_blocks_have_source_lineage": not clean_map["unmapped_clean_block_ids"],
        },
        "blockers": blockers,
    }
    _write_json(canonical_dir / "blocks.json", {"schema": "luceon.canonical-blocks/v1", "blocks": blocks})
    _write_json(canonical_dir / "cleaning-ledger.json", {"schema": "luceon.cleaning-ledger/v1", "allowlisted_actions": sorted(ALLOWLISTED_ACTIONS), "entries": ledger})
    _write_json(canonical_dir / "clean-block-map.json", clean_map)
    _write_json(canonical_dir / "page-content-conservation.json", page_conservation)
    _write_json(canonical_dir / "source-map.json", _build_source_map(blocks, ledger, clean_map))
    _write_json(canonical_dir / "canonical-validation.json", validation)
    return validation


def _excluded_numbered_body_sequences(source: list, start_page: int | None, end_page: int | None) -> list[dict]:
    observations = []
    for source_order, raw in enumerate(source):
        if not isinstance(raw, dict):
            continue
        page_idx = _integer(raw.get("page_idx"))
        if page_idx is None or (
            (start_page is None or page_idx >= start_page)
            and (end_page is None or page_idx <= end_page)
        ):
            continue
        text = _semantic_content(raw).strip()
        match = re.match(r"^(\d{1,3})[.)]\s+\S", text)
        if match:
            observations.append(
                {
                    "number": int(match.group(1)),
                    "page_idx": page_idx,
                    "source_order": source_order,
                    "text": text[:160],
                }
            )
    sequences = []
    current = []
    for row in observations:
        if current and row["number"] != current[-1]["number"] + 1:
            if len(current) >= 5:
                sequences.append(current)
            current = []
        current.append(row)
    if len(current) >= 5:
        sequences.append(current)
    return [
        {
            "start_number": rows[0]["number"],
            "end_number": rows[-1]["number"],
            "start_page_idx": rows[0]["page_idx"],
            "end_page_idx": rows[-1]["page_idx"],
            "count": len(rows),
            "source_orders": [row["source_order"] for row in rows],
            "sample": [row["text"] for row in rows[:3]],
        }
        for rows in sequences
    ]


def _repeated_edge_text(source: list) -> set[str]:
    observations: dict[tuple[str, str], list[tuple[int, float]]] = defaultdict(list)
    for raw in source:
        if not isinstance(raw, dict) or str(raw.get("type") or "") != "text":
            continue
        bbox = raw.get("bbox")
        page = _integer(raw.get("page_idx"))
        text = _normalize_text(_semantic_content(raw))
        if page is None or not text or not isinstance(bbox, list) or len(bbox) < 4:
            continue
        try:
            top, bottom = float(bbox[1]), float(bbox[3])
        except (TypeError, ValueError):
            continue
        if top <= 120:
            observations[(text, "top")].append((page, top))
        if bottom >= 700:
            observations[(text, "bottom")].append((page, bottom))
    repeated = set()
    for (text, _edge), rows in observations.items():
        pages = {page for page, _position in rows}
        positions = [position for _page, position in rows]
        if len(pages) >= 3 and max(positions) - min(positions) <= 40:
            repeated.add(text)
    return repeated


def _exact_clean_disposition(source: dict, clean_component_index: dict[tuple, set[str]], clean_resegmented_index: set[tuple]) -> str:
    components = _source_components(source)
    if not components:
        return ""
    matched_types = set()
    for component in components:
        types = clean_component_index.get(_conservation_key(component)) or set()
        if not types and _conservation_key(component) not in clean_resegmented_index:
            return ""
        matched_types.update(types)
    return "content" if not matched_types or matched_types - {"heading"} else "heading"


def _clean_resegmented_index(clean_blocks: list[dict], targets: set[tuple]) -> set[tuple]:
    pages: dict[int | None, list[dict]] = defaultdict(list)
    for row in clean_blocks:
        if row.get("type") not in {"page_marker", "internal_marker", "heading", "image", "table"}:
            pages[_integer(row.get("page_idx"))].append(row)
    keys = set()
    for page_idx, rows in pages.items():
        for start in range(len(rows)):
            for width in range(2, min(100, len(rows) - start) + 1):
                content = _joined_content(rows[start : start + width])
                key = (page_idx, "text", content)
                if content and key in targets:
                    keys.add(key)
    return keys


def _selected_outline_block_refs(canonical_dir: Path, source: list) -> dict[str, list[str]]:
    decision = _read_object(canonical_dir / "outline_decision.json")
    candidates = _read_jsonl(canonical_dir / "outline_candidates.jsonl")
    selected = {
        str(candidate_id)
        for node in decision.get("final_outline") or []
        for candidate_id in node.get("candidate_ids") or []
    }
    refs: dict[str, list[str]] = defaultdict(list)
    recovery_path = canonical_dir / "outline_free_table_recovery_report.json"
    recovery = _read_object(recovery_path) if recovery_path.is_file() else {}
    if recovery.get("changed") is True:
        source_order = _integer(recovery.get("root_source_order"))
        title = str(recovery.get("root_title") or "").strip()
        if source_order is not None and 0 <= source_order < len(source) and title:
            refs[f"content-list-{source_order:06d}"].append(f"outline:{title}")
    for candidate in candidates:
        candidate_id = str(candidate.get("candidate_id") or "")
        if candidate_id not in selected:
            continue
        title = str(candidate.get("title_text") or "")
        for block_id in candidate.get("block_ids") or []:
            refs[str(block_id)].append(f"outline:{title}")
    source_titles: dict[str, list[tuple[str, int | None]]] = defaultdict(list)
    source_heading_identities: dict[str, list[tuple[str, int | None]]] = defaultdict(list)
    for index, raw in enumerate(source):
        if not isinstance(raw, dict):
            continue
        title = _normalize_text(_semantic_content(raw))
        if title:
            source_titles[title].append((f"content-list-{index:06d}", _integer(raw.get("page_idx"))))
            source_heading_identities[_normalize_heading_identity(title)].append((f"content-list-{index:06d}", _integer(raw.get("page_idx"))))
    for node in decision.get("final_outline") or []:
        title = str(node.get("title") or "").strip()
        normalized_title = _normalize_text(title)
        matches = source_titles.get(normalized_title, [])
        if not matches:
            alias = _strip_numbered_heading_prefix(normalized_title)
            matches = source_titles.get(alias, []) if alias else []
        if not matches:
            matches = source_heading_identities.get(_normalize_heading_identity(normalized_title), [])
        page = _integer(node.get("page") or node.get("start_page"))
        page_matches = [row for row in matches if page is not None and row[1] in {page, page - 1}]
        selected_matches = page_matches if len(page_matches) == 1 else matches if len(matches) == 1 else []
        for block_id, _page_idx in selected_matches:
            refs[block_id].append(f"outline:{title}")
    return refs


def _strip_numbered_heading_prefix(title: str) -> str:
    return re.sub(r"^(?:chapter|unit|part|section)\s+\d+[a-z]?\s+", "", title, count=1, flags=re.I).strip()


def _normalize_heading_identity(title: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", str(title or "").casefold()).strip()


def _is_structural_page_noise(raw: dict, normalized: str) -> bool:
    block_type = str(raw.get("type") or "")
    if block_type not in {"header", "footer", "page_number"}:
        return False
    bbox = raw.get("bbox")
    if not isinstance(bbox, list) or len(bbox) < 4:
        return False
    try:
        top, bottom = float(bbox[1]), float(bbox[3])
    except (TypeError, ValueError):
        return False
    at_edge = top <= 130 or bottom >= 700
    if block_type == "page_number":
        return at_edge and bool(re.fullmatch(r"[\divxlcdm.\-–— ]{1,12}", normalized))
    return at_edge


def _map_preserved_blocks_to_clean(canonical_dir: Path, blocks: list[dict], ledger: list[dict], outline_refs: dict[str, list[str]]) -> dict:
    clean_path = canonical_dir / "clean.md"
    if not clean_path.is_file():
        raise ContentConservationError("clean.md is unavailable for source conservation mapping")
    clean_blocks = _parse_clean_blocks(clean_path)
    source_by_id = {row["block_id"]: row for row in blocks}
    available: dict[tuple, list[dict]] = defaultdict(list)
    outline_titles = {
        _normalize_text(ref.removeprefix("outline:"))
        for refs in outline_refs.values()
        for ref in refs
        if ref.startswith("outline:")
    }
    decision = _read_object(canonical_dir / "outline_decision.json")
    outline_titles.update(
        _normalize_text(row.get("title"))
        for row in decision.get("final_outline") or []
        if _normalize_text(row.get("title"))
    )
    outline_titles.update(
        _normalize_text(source_by_id[row["block_id"]].get("content"))
        for row in ledger
        if row["action"] == "transform_to_outline_heading" and row["block_id"] in source_by_id
    )
    mappings = []
    mapped_outline_source_ids = set()
    transformed_ids = {
        row["block_id"]
        for row in ledger
        if row["action"] == "transform_to_outline_heading"
    }
    for block in clean_blocks:
        if block["type"] == "heading" and _normalize_text(block["content"]) in outline_titles:
            ref_sources = [
                source_by_id[block_id]
                for block_id, refs in outline_refs.items()
                if block_id in source_by_id
                and block_id in transformed_ids
                and abs(
                    (_integer(source_by_id[block_id].get("page_idx")) or 0)
                    - (_integer(block.get("page_idx")) or 0)
                ) <= 1
                and any(_normalize_text(ref.removeprefix("outline:")) == _normalize_text(block["content"]) for ref in refs)
            ]
            sources = ref_sources or [
                source for source in source_by_id.values()
                if _normalize_text(source.get("content")) == _normalize_text(block["content"])
                and _integer(source.get("page_idx")) == _integer(block.get("page_idx"))
            ]
            source_candidates = sorted(
                (source for source in sources if source["block_id"] not in mapped_outline_source_ids),
                key=lambda source: source["block_id"],
            )
            if source_candidates:
                source = source_candidates[0]
                mapped_outline_source_ids.add(source["block_id"])
                block["disposition"] = "outline_heading"
                mappings.append({
                    "source_block_id": source["block_id"],
                    "source_component": "content",
                    "clean_block_id": block["block_id"],
                    "page_idx": block["page_idx"],
                    "match": "source_evidenced_outline_heading_transform" if ref_sources else "exact_source_to_outline_heading",
                })
                continue
            if not sources:
                block["disposition"] = "outline_heading"
                continue
        available[_conservation_key(block)].append(block)

    missing_components = []
    pre_mapped_components = {
        (row["source_block_id"], row["source_component"])
        for row in mappings
    }
    for entry in ledger:
        if entry["action"] != "preserve":
            continue
        source = source_by_id[entry["block_id"]]
        components = _source_components(source)
        expanded_components = []
        planned = Counter()
        for component in components:
            lines = [line.strip() for line in str(component.get("content") or "").splitlines() if line.strip()]
            line_components = [
                {**component, "source_component": f"{component['source_component']}:line:{index}", "content": line}
                for index, line in enumerate(lines)
            ]
            needed = Counter(_conservation_key(row) for row in line_components)
            if len(line_components) > 1 and all(
                len(available.get(key) or []) >= planned[key] + count
                for key, count in needed.items()
            ):
                expanded_components.extend(line_components)
                planned.update(needed)
            else:
                expanded_components.append(component)
                planned[_conservation_key(component)] += 1
        components = expanded_components
        for component in components:
            if (source["block_id"], component["source_component"]) in pre_mapped_components:
                continue
            key = _conservation_key(component)
            candidates = available.get(key) or []
            if not candidates:
                missing_components.append(component)
                continue
            clean = candidates.pop(0)
            clean["disposition"] = "mapped"
            mappings.append(
                {
                    "source_block_id": source["block_id"],
                    "source_component": component["source_component"],
                    "clean_block_id": clean["block_id"],
                    "page_idx": source["page_idx"],
                    "match": "exact_normalized_component_and_page",
                }
            )
    unmapped = [
        row
        for row in clean_blocks
        if not row.get("disposition") and row["type"] not in {"page_marker", "internal_marker"}
    ]
    correction_operations = _match_allowlisted_ocr_corrections(
        canonical_dir,
        missing_components,
        unmapped,
    )
    numeric_formatting_operations = _match_allowlisted_numeric_grouping_changes(
        missing_components,
        unmapped,
    )
    correction_operations.extend(numeric_formatting_operations)
    corrected_source_components = {
        (row["source_block_id"], row["source_component"])
        for row in correction_operations
    }
    corrected_clean_ids = {row["clean_block_id"] for row in correction_operations}
    for row in correction_operations:
        mappings.append({
            "source_block_id": row["source_block_id"],
            "source_component": row["source_component"],
            "clean_block_id": row["clean_block_id"],
            "page_idx": row["page_idx"],
            "match": "allowlisted_ocr_correction",
        })
    missing_components = [
        row for row in missing_components
        if (row["source_block_id"], row["source_component"]) not in corrected_source_components
    ]
    unmapped = [row for row in unmapped if row["block_id"] not in corrected_clean_ids]
    resegmented = _match_resegmented_blocks(missing_components, unmapped)
    for row in resegmented:
        mappings.extend(row["mappings"])
    resegmented_source_components = {
        tuple(component)
        for row in resegmented
        for component in row["source_components"]
    }
    resegmented_clean_ids = {clean_id for row in resegmented for clean_id in row["clean_block_ids"]}
    missing_ids = sorted({
        row["source_block_id"]
        for row in missing_components
        if (row["source_block_id"], row["source_component"]) not in resegmented_source_components
    })
    unmapped_ids = [row["block_id"] for row in unmapped if row["block_id"] not in resegmented_clean_ids]
    for mapping in mappings:
        source = source_by_id.get(str(mapping.get("source_block_id") or "")) or {}
        mapping["source_locator"] = _stable_source_locator(source)
    return {
        "schema": "luceon.clean-block-map/v1",
        "source_preserved_count": sum(row["action"] == "preserve" for row in ledger),
        "clean_block_count": len(clean_blocks),
        "mapped_count": len(mappings),
        "missing_source_block_ids": missing_ids,
        "unmapped_clean_block_ids": unmapped_ids,
        "split_merge_operations": [
            {key: value for key, value in row.items() if key != "mappings"}
            for row in resegmented
        ],
        "ocr_correction_operations": correction_operations,
        "mappings": mappings,
        "media_counts": _media_counts(blocks, ledger, clean_blocks),
    }


def _stable_source_locator(source: dict) -> dict:
    locator = {
        "block_id": str(source.get("block_id") or ""),
        "source_order": _integer(source.get("source_order")),
        "page_idx": _integer(source.get("page_idx")),
        "bbox": source.get("bbox") if isinstance(source.get("bbox"), list) else [],
        "content_sha256": str(source.get("content_sha256") or ""),
    }
    locator["locator_sha256"] = hashlib.sha256(
        json.dumps(locator, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return locator


def _bind_clean_output_refs(ledger: list[dict], clean_map: dict) -> None:
    refs_by_source: dict[str, set[str]] = defaultdict(set)
    for mapping in clean_map.get("mappings") or []:
        source_id = str(mapping.get("source_block_id") or "")
        clean_id = str(mapping.get("clean_block_id") or "")
        if source_id and clean_id:
            refs_by_source[source_id].add(f"clean:{clean_id}")
    for entry in ledger:
        if entry.get("action") != "preserve":
            continue
        refs = {str(value) for value in entry.get("output_refs") or [] if str(value)}
        refs.update(refs_by_source.get(str(entry.get("block_id") or ""), set()))
        entry["output_refs"] = sorted(refs)


def _match_allowlisted_ocr_corrections(canonical_dir: Path, source_components: list[dict], clean_blocks: list[dict]) -> list[dict]:
    report_path = canonical_dir / "math_ocr_repair_report.json"
    if not report_path.is_file():
        return []
    report = _read_object(report_path)
    operations = []
    used_source_components = set()
    used_clean_ids = set()
    for example in report.get("operations") or report.get("examples") or []:
        before = str(example.get("before") or "")
        after = str(example.get("after") or "")
        line = _integer(example.get("line"))
        if not before or not after or line is None:
            continue
        clean_matches = [
            row for row in clean_blocks
            if row["block_id"] not in used_clean_ids
            and row["block_id"] == f"clean-line-{line:06d}"
            and (
                _normalize_markup(row.get("content") or "") == _normalize_markup(after)
                or _normalize_markup(after) in _normalize_markup(row.get("content") or "")
            )
        ]
        if len(clean_matches) != 1:
            continue
        clean = clean_matches[0]
        source_matches = sorted(
            (
                row for row in source_components
                if (row["source_block_id"], row["source_component"]) not in used_source_components
                and _integer(row.get("page_idx")) == _integer(clean.get("page_idx"))
                and (
                    _normalize_markup(row.get("content") or "") == _normalize_markup(before)
                    or (
                        _normalize_markup(before) in _normalize_markup(row.get("content") or "")
                        and _normalize_markup(row.get("content") or "").replace(
                            _normalize_markup(before), _normalize_markup(after), 1
                        ) == _normalize_markup(clean.get("content") or "")
                    )
                )
            ),
            key=lambda row: row["source_block_id"],
        )
        if not source_matches:
            continue
        source = source_matches[0]
        used_source_components.add((source["source_block_id"], source["source_component"]))
        used_clean_ids.add(clean["block_id"])
        operations.append({
            "operation": "ocr_correction",
            "source_block_id": source["source_block_id"],
            "source_component": source["source_component"],
            "clean_block_id": clean["block_id"],
            "page_idx": clean.get("page_idx"),
            "before": before,
            "after": after,
            "substitution_count": int(example.get("substitutions") or 0),
            "reason": "deterministic_math_digit_spacing_repair",
            "evidence": "math_ocr_repair_report.json",
        })
    return operations


def _match_allowlisted_numeric_grouping_changes(source_components: list[dict], clean_blocks: list[dict]) -> list[dict]:
    """Match table values that only lost thousands-group spaces during math cleanup."""
    operations = []
    used_source_components = set()
    used_clean_ids = set()
    for clean in clean_blocks:
        if clean.get("type") != "table":
            continue
        clean_key = _numeric_grouping_key(clean.get("content") or "")
        candidates = [
            source for source in source_components
            if source.get("type") == "table"
            and (source["source_block_id"], source["source_component"]) not in used_source_components
            and _integer(source.get("page_idx")) == _integer(clean.get("page_idx"))
            and _numeric_grouping_key(source.get("content") or "") == clean_key
            and _normalize_markup(source.get("content") or "") != _normalize_markup(clean.get("content") or "")
        ]
        if len(candidates) != 1:
            continue
        source = candidates[0]
        used_source_components.add((source["source_block_id"], source["source_component"]))
        used_clean_ids.add(clean["block_id"])
        operations.append({
            "operation": "numeric_grouping_format_normalization",
            "source_block_id": source["source_block_id"],
            "source_component": source["source_component"],
            "clean_block_id": clean["block_id"],
            "page_idx": clean.get("page_idx"),
            "before": source.get("content") or "",
            "after": clean.get("content") or "",
            "reason": "remove_thousands_group_spaces_inside_numeric_table_cells",
        })
    return operations


def _numeric_grouping_key(value: str) -> str:
    normalized = _normalize_markup(value)
    return re.sub(r"(?<=\d)[ \u00a0](?=\d{3}(?:\D|$))", "", normalized)


def _match_resegmented_blocks(source_blocks: list[dict], clean_blocks: list[dict]) -> list[dict]:
    operations = []
    source_pages: dict[int | None, list[dict]] = defaultdict(list)
    clean_pages: dict[int | None, list[dict]] = defaultdict(list)
    for row in source_blocks:
        if not row.get("image_ref") and row.get("type") not in {"image", "table"}:
            source_pages[_integer(row.get("page_idx"))].append(row)
    for row in clean_blocks:
        if row.get("type") not in {"image", "table", "heading"}:
            clean_pages[_integer(row.get("page_idx"))].append(row)

    for page_idx, sources in source_pages.items():
        cleans = clean_pages.get(page_idx, [])
        source_targets = {
            _joined_content(sources[start : start + width])
            for start in range(len(sources))
            for width in range(1, min(4, len(sources) - start) + 1)
        }
        clean_windows: dict[str, list[list[dict]]] = defaultdict(list)
        for clean_start in range(len(cleans)):
            for clean_width in range(1, min(100, len(cleans) - clean_start) + 1):
                window = cleans[clean_start : clean_start + clean_width]
                text = _joined_content(window)
                if text and text in source_targets:
                    clean_windows[text].append(window)
        used_source_components = set()
        used_clean_ids = set()
        for source_start in range(len(sources)):
            for source_width in range(1, min(4, len(sources) - source_start) + 1):
                source_window = sources[source_start : source_start + source_width]
                if any(
                    (row["source_block_id"], row["source_component"]) in used_source_components
                    for row in source_window
                ):
                    continue
                source_text = _joined_content(source_window)
                candidates = clean_windows.get(source_text) or []
                clean_window = next(
                    (window for window in candidates if not any(row["block_id"] in used_clean_ids for row in window)),
                    None,
                )
                if not clean_window or source_width == len(clean_window) == 1:
                    continue
                source_ids = [row["source_block_id"] for row in source_window]
                source_components = [
                    [row["source_block_id"], row["source_component"]]
                    for row in source_window
                ]
                clean_ids = [row["block_id"] for row in clean_window]
                operation = "split" if len(source_ids) == 1 else "merge" if len(clean_ids) == 1 else "resegment"
                operations.append(
                    {
                        "operation": operation,
                        "page_idx": page_idx,
                        "source_block_ids": source_ids,
                        "source_components": source_components,
                        "clean_block_ids": clean_ids,
                        "normalized_content_sha256": hashlib.sha256(_joined_content(source_window).encode("utf-8")).hexdigest(),
                        "mappings": [
                            {
                                "source_block_id": source_id,
                                "source_component": source_window[index]["source_component"],
                                "clean_block_ids": clean_ids,
                                "page_idx": page_idx,
                                "match": f"exact_normalized_{operation}",
                            }
                            for index, source_id in enumerate(source_ids)
                        ],
                    }
                )
                used_source_components.update(tuple(component) for component in source_components)
                used_clean_ids.update(clean_ids)
                break
    return operations


def _joined_content(rows: list[dict]) -> str:
    return _normalize_markup(" ".join(str(row.get("content") or "") for row in rows))


def _media_counts(blocks: list[dict], ledger: list[dict], clean_blocks: list[dict]) -> dict:
    accepted = {
        row["block_id"]
        for row in ledger
        if row["action"] in {"preserve", "transform_to_outline_heading"}
    }
    source = [row for row in blocks if row["block_id"] in accepted]
    return {
        "source_images": sum(bool(row.get("image_ref")) for row in source),
        "clean_images": sum(row.get("type") == "image" for row in clean_blocks),
        "source_tables": sum(row.get("type") == "table" for row in source),
        "clean_tables": sum(row.get("type") == "table" for row in clean_blocks),
        "source_formula_blocks": sum(row.get("type") in {"equation", "formula", "inline_equation", "interline_equation"} for row in source),
        "clean_math_lines": sum(bool(re.search(r"(?:\$|\\\[|\\begin\{(?:equation|align|array))", str(row.get("content") or ""))) for row in clean_blocks),
    }


def _build_page_conservation(blocks: list[dict], ledger: list[dict], clean_blocks: list[dict]) -> dict:
    accepted = {
        row["block_id"]
        for row in ledger
        if row["action"] in {"preserve", "transform_to_outline_heading"}
    }
    source_pages: dict[int | None, list[str]] = defaultdict(list)
    clean_pages: dict[int | None, list[str]] = defaultdict(list)
    for block in blocks:
        if block["block_id"] in accepted and not block.get("image_ref"):
            source_pages[_integer(block.get("page_idx"))].extend(_content_tokens(block.get("content")))
    for block in clean_blocks:
        if block["type"] not in {"page_marker", "internal_marker", "image"}:
            clean_pages[_integer(block.get("page_idx"))].extend(_content_tokens(block.get("content")))

    rows = []
    for page_idx in sorted(set(source_pages) | set(clean_pages), key=lambda value: (-1 if value is None else value)):
        source_tokens = source_pages.get(page_idx, [])
        clean_tokens = clean_pages.get(page_idx, [])
        matcher = SequenceMatcher(a=source_tokens, b=clean_tokens, autojunk=False)
        missing = []
        added = []
        operations = []
        for tag, a1, a2, b1, b2 in matcher.get_opcodes():
            if tag == "equal":
                continue
            missing.extend(source_tokens[a1:a2])
            added.extend(clean_tokens[b1:b2])
            operations.append({"tag": tag, "source_range": [a1, a2], "clean_range": [b1, b2]})
        rows.append(
            {
                "page_idx": page_idx,
                "status": "exact" if not operations else "changed",
                "source_token_count": len(source_tokens),
                "clean_token_count": len(clean_tokens),
                "matching_ratio": round(matcher.ratio(), 6),
                "missing_token_count": len(missing),
                "added_token_count": len(added),
                "missing_token_sample": missing[:80],
                "added_token_sample": added[:80],
                "operations": operations[:100],
            }
        )
    return {
        "schema": "luceon.page-content-conservation/v1",
        "page_count": len(rows),
        "exact_page_count": sum(row["status"] == "exact" for row in rows),
        "changed_page_count": sum(row["status"] == "changed" for row in rows),
        "pages": rows,
    }


def _build_source_map(blocks: list[dict], ledger: list[dict], clean_map: dict) -> dict:
    clean_refs: dict[str, list[str]] = defaultdict(list)
    for mapping in clean_map.get("mappings") or []:
        clean_ids = mapping.get("clean_block_ids") or [mapping.get("clean_block_id")]
        clean_refs[str(mapping.get("source_block_id") or "")].extend(str(value) for value in clean_ids if value)
    ledger_by_id = {row["block_id"]: row for row in ledger}
    return {
        "schema": "luceon.source-map/v1",
        "source_block_count": len(blocks),
        "records": [
            {
                "source_block_id": block["block_id"],
                "source_order": block["source_order"],
                "page_idx": block["page_idx"],
                "type": block["type"],
                "content_sha256": block["content_sha256"],
                "action": ledger_by_id[block["block_id"]]["action"],
                "reason": ledger_by_id[block["block_id"]]["reason"],
                "canonical_refs": ledger_by_id[block["block_id"]]["output_refs"],
                "clean_block_ids": clean_refs.get(block["block_id"], []),
            }
            for block in blocks
        ],
    }


def _content_tokens(value) -> list[str]:
    text = str(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[`*_]", "", text)
    text = text.replace("\\_", "_")
    return [token.casefold() for token in re.findall(r"\\[A-Za-z]+|[A-Za-z]+(?:'[A-Za-z]+)?|\d+(?:\.\d+)?|[\u4e00-\u9fff]", text)]


def _parse_clean_blocks(path: Path) -> list[dict]:
    blocks = []
    page_idx = None
    lines = path.read_text(encoding="utf-8").splitlines()
    index = 0
    while index < len(lines):
        line_number = index + 1
        raw = lines[index]
        stripped = raw.strip()
        index += 1
        if not stripped:
            continue
        page_match = re.fullmatch(r"<!--\s*page_idx:\s*(\d+)\s*-->", stripped)
        if page_match:
            page_idx = int(page_match.group(1))
            blocks.append({"block_id": f"clean-line-{line_number:06d}", "type": "page_marker", "page_idx": page_idx, "content": stripped, "image_ref": ""})
            continue
        if re.fullmatch(r"<!--.*?-->", stripped, re.S):
            blocks.append({"block_id": f"clean-line-{line_number:06d}", "type": "internal_marker", "page_idx": page_idx, "content": stripped, "image_ref": ""})
            continue
        heading_match = re.fullmatch(r"#{1,6}\s+(.+?)\s*", stripped)
        if stripped.startswith("![") and not re.search(r"\]\([^)]+\)\s*$", stripped):
            parts = [stripped]
            while index < len(lines) and not re.search(r"\]\([^)]+\)\s*$", parts[-1]):
                parts.append(lines[index].strip())
                index += 1
            stripped = "\n".join(parts)
        if stripped == "$$":
            parts = [stripped]
            while index < len(lines):
                parts.append(lines[index].strip())
                index += 1
                if parts[-1] == "$$":
                    break
            stripped = "\n".join(parts)
        if stripped.lower().startswith("<table") and "</table>" not in stripped.lower():
            parts = [stripped]
            while index < len(lines):
                parts.append(lines[index].strip())
                index += 1
                if "</table>" in parts[-1].lower():
                    break
            stripped = "\n".join(parts)
        image_match = re.fullmatch(r"!\[(.*?)\]\(([^)]+)\)\s*", stripped, re.S)
        if heading_match:
            block_type, content, image_ref = "heading", heading_match.group(1), ""
        elif image_match:
            block_type, content, image_ref = "image", image_match.group(1), image_match.group(2).replace("\\", "/")
        elif stripped.startswith("<table"):
            block_type, content, image_ref = "table", stripped, ""
        else:
            block_type, content, image_ref = "text", stripped, ""
        blocks.append({"block_id": f"clean-line-{line_number:06d}", "type": block_type, "page_idx": page_idx, "content": content, "image_ref": image_ref})
    return blocks


def _conservation_key(block: dict) -> tuple:
    page_idx = _integer(block.get("page_idx"))
    image_ref = str(block.get("image_ref") or "").replace("\\", "/")
    if image_ref:
        return page_idx, "image", image_ref
    block_type = str(block.get("type") or "text")
    if block_type not in {"table", "image"}:
        block_type = "text"
    content = _normalize_markup(str(block.get("content") or ""))
    return page_idx, block_type, content


def _source_components(source: dict) -> list[dict]:
    components = []
    content = str(source.get("content") or "")
    source_type = str(source.get("type") or "").casefold()
    image_ref = str(source.get("image_ref") or "")
    semantic_components = source.get("semantic_components") if isinstance(source.get("semantic_components"), list) else []
    authoritative_content = []
    for row in semantic_components:
        if not isinstance(row, dict) or not _normalize_text(row.get("content") or ""):
            continue
        role = str(row.get("role") or "content")
        if source_type in {"chart", "figure", "image", "diagram"} and image_ref and role != "caption":
            continue
        authoritative_content.append((role, str(row["content"])))
    if not semantic_components and (source_type not in {"chart", "figure", "image", "diagram"} or not image_ref):
        authoritative_content.append(("content", content))
    image_is_primary = bool(image_ref) and not (source_type == "table" and _normalize_text(content))
    for role, component_content in authoritative_content:
        block_type = "table" if source.get("type") == "table" and role == "content" else "text"
        components.append({
            "source_block_id": source["block_id"],
            "source_component": role,
            "page_idx": source.get("page_idx"),
            "type": block_type,
            "content": component_content,
            "image_ref": "",
        })
    if image_is_primary:
        components.append({
            "source_block_id": source["block_id"],
            "source_component": "image",
            "page_idx": source.get("page_idx"),
            "type": "image",
            "content": "",
            "image_ref": image_ref,
        })
    return components


def _normalize_markup(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r">\s+<", "><", value)
    emphasis = re.fullmatch(r"(\*{1,2}|_{1,2})(.+)\1", value.strip(), re.S)
    if emphasis:
        value = emphasis.group(2)
    return _normalize_text(value)


def _semantic_content(raw: dict) -> str:
    list_items = raw.get("list_items")
    if isinstance(list_items, list):
        return "\n".join(str(value).strip() for value in list_items if str(value).strip())
    for key in ("text", "latex", "content", "html", "table_body"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if str(raw.get("type") or "") == "table" and raw.get("table_body") is not None:
        return json.dumps(raw.get("table_body"), ensure_ascii=False, sort_keys=True)
    return ""


def _semantic_components(raw: dict) -> list[dict]:
    rows = []

    def append(role: str, value, *, preserve_multiplicity: bool = False) -> None:
        if isinstance(value, list):
            value = " ".join(str(item).strip() for item in value if str(item).strip())
        text = str(value or "").strip()
        if text and (
            preserve_multiplicity
            or all(_normalize_text(item["content"]) != _normalize_text(text) for item in rows)
        ):
            rows.append({"role": role, "content": text})

    for existing in raw.get("semantic_components") or []:
        if isinstance(existing, dict):
            append(str(existing.get("role") or "content"), existing.get("content"))
    list_items = raw.get("list_items")
    if isinstance(list_items, list):
        for index, item in enumerate(list_items):
            append(f"item:{index}", item, preserve_multiplicity=True)
        return rows
    append("caption", raw.get("image_caption"))
    append("caption", raw.get("table_caption"))
    append("content", _semantic_content(raw))
    return rows


def _image_ref(raw: dict) -> str:
    for key in ("img_path", "image_path", "image_ref", "path"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().replace("\\", "/")
    return ""


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _read_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ContentConservationError(f"{path.name} must contain an object")
    return value


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _integer(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
