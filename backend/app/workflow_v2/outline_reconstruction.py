from __future__ import annotations

import json
import math
import os
import re
from collections import Counter, defaultdict
from pathlib import Path


REQUIRED_INPUTS = {
    "outline_decision.json",
    "outline_candidates.jsonl",
    "popo_outline.json",
    "raw_units.jsonl",
    "raw_block_assignments.jsonl",
    "unassigned_blocks.jsonl",
}


class OutlineReconstructionError(RuntimeError):
    pass


def build_outline_artifact(canonical_dir: Path, output_dir: Path) -> dict:
    missing = sorted(name for name in REQUIRED_INPUTS if not (canonical_dir / name).is_file())
    if missing:
        raise OutlineReconstructionError(f"canonical outline evidence is incomplete: {', '.join(missing)}")

    output_dir.mkdir(parents=True, exist_ok=True)
    decision = _read_json(canonical_dir / "outline_decision.json")
    popo = _read_json(canonical_dir / "popo_outline.json")
    candidates = _read_jsonl(canonical_dir / "outline_candidates.jsonl")
    units = _read_jsonl(canonical_dir / "raw_units.jsonl")
    assignments = _read_jsonl(canonical_dir / "raw_block_assignments.jsonl")
    unassigned = _read_jsonl(canonical_dir / "unassigned_blocks.jsonl")

    arbitration = _maybe_arbitrate_weak_outline(decision, candidates)
    if arbitration.get("applied"):
        decision = arbitration.pop("decision")
    candidate_by_id = {str(row.get("candidate_id") or ""): row for row in candidates}
    nodes = _nodes_from_decision(decision, candidate_by_id)

    source_visible_toc = _reconstruct_source_visible_toc(canonical_dir, popo)
    if source_visible_toc["applied"]:
        nodes = source_visible_toc.pop("nodes")
        canonical_heading_reconstruction = {
            "applied": False,
            "reason": "source_visible_toc_reconstruction_applied",
        }
    else:
        canonical_heading_reconstruction = _reconstruct_invalid_outline_from_canonical_headings(canonical_dir, nodes)

    numbered_textbook_reconstruction = _reconstruct_numbered_textbook_sections(canonical_dir)
    numbered_reconstruction = _reconstruct_numbered_chapter_topics(canonical_dir)
    if source_visible_toc["applied"]:
        numbered_textbook_reconstruction = {"applied": False, "reason": "source_visible_toc_reconstruction_applied"}
        numbered_reconstruction = {"applied": False, "reason": "source_visible_toc_reconstruction_applied"}
        question_heading_filter = {"applied": False, "reason": "source_visible_toc_reconstruction_applied", "removed_count": 0}
        root_restoration = {"applied": False, "reason": "source_visible_toc_reconstruction_applied"}
        running_root_reconciliation = {"applied": False, "reason": "source_visible_toc_reconstruction_applied"}
        metadata_title_augmentation = {"applied": False, "reason": "source_visible_toc_reconstruction_applied", "added_count": 0}
        augmentation = {"applied": False, "reason": "source_visible_toc_reconstruction_applied"}
    elif numbered_textbook_reconstruction["applied"]:
        nodes = numbered_textbook_reconstruction.pop("nodes")
        numbered_reconstruction = {"applied": False, "reason": "numbered_textbook_section_reconstruction_applied"}
        question_heading_filter = {"applied": False, "reason": "numbered_textbook_section_reconstruction_applied", "removed_count": 0}
        root_restoration = {"applied": False, "reason": "numbered_textbook_section_reconstruction_applied"}
        running_root_reconciliation = {"applied": False, "reason": "numbered_textbook_section_reconstruction_applied"}
        metadata_title_augmentation = {"applied": False, "reason": "numbered_textbook_section_reconstruction_applied", "added_count": 0}
        augmentation = {"applied": False, "reason": "numbered_textbook_section_reconstruction_applied"}
    elif numbered_reconstruction["applied"]:
        nodes = numbered_reconstruction.pop("nodes")
        numbered_reconstruction.pop("coverage_units")
        question_heading_filter = {"applied": False, "reason": "numbered_chapter_topic_reconstruction_applied", "removed_count": 0}
        root_restoration = {"applied": False, "reason": "numbered_chapter_topic_reconstruction_applied"}
        running_root_reconciliation = {"applied": False, "reason": "numbered_chapter_topic_reconstruction_applied"}
        augmentation = {"applied": False, "reason": "numbered_chapter_topic_reconstruction_applied"}
    else:
        question_heading_filter = _remove_question_like_outline_nodes(nodes)
        root_restoration = _restore_single_source_root(decision, units, canonical_dir, candidate_by_id, nodes)
        running_root_reconciliation = _reconcile_running_hierarchy_roots(canonical_dir, nodes)
        metadata_title_augmentation = _augment_outline_from_article_metadata(canonical_dir, nodes)
        augmentation = _augment_flat_outline_from_repeated_labels(canonical_dir, nodes)
    if numbered_reconstruction["applied"]:
        metadata_title_augmentation = {"applied": False, "reason": "numbered_chapter_topic_reconstruction_applied", "added_count": 0}
    leading_orphan_filter = _remove_leading_orphan_outline_nodes(nodes)
    self_nested_root_filter = _collapse_self_nested_roots(nodes)
    duplicate_filter = _deduplicate_outline_nodes(nodes)
    parent_repairs = _normalize_parent_links(nodes)
    heading_evidence_alignment = _align_nodes_to_canonical_headings(canonical_dir, nodes)
    coverage_units = _body_coverage_units(nodes)
    hierarchy = _validate_hierarchy(nodes)
    evidence_report = _validate_evidence(nodes)
    coverage = _validate_body_coverage(
        nodes,
        coverage_units,
        assignments,
        unassigned,
        raw_unit_count=len(units),
    )
    max_depth = max((row["level"] for row in nodes), default=0)
    depth_ok = 2 <= max_depth <= 3 and all(1 <= row["level"] <= 3 for row in nodes)
    blockers = []
    if not nodes:
        blockers.append({"code": "outline_empty", "message": "No source-evidenced outline nodes were reconstructed."})
    if not depth_ok:
        blockers.append({
            "code": "outline_depth_out_of_range",
            "message": f"Observed outline depth is {max_depth}; accepted depth is 2 or 3.",
        })
    if len(candidates) >= 20 and len(nodes) <= 1:
        blockers.append({
            "code": "outline_arbitration_required",
            "candidate_count": len(candidates),
            "selected_node_count": len(nodes),
            "arbitration": arbitration.get("reason"),
        })
    blockers.extend(hierarchy["blockers"])
    blockers.extend(evidence_report["blockers"])
    blockers.extend(coverage["blockers"])

    status = "passed" if not blockers else "review"
    outline_document = {
        "schema": "luceon.outline-reconstruction/v1",
        "status": status,
        "node_count": len(nodes),
        "max_depth": max_depth,
        "nodes": nodes,
        "parent_repairs": parent_repairs,
        "augmentation": augmentation,
        "root_restoration": root_restoration,
        "running_root_reconciliation": running_root_reconciliation,
        "metadata_title_augmentation": metadata_title_augmentation,
        "question_heading_filter": question_heading_filter,
        "leading_orphan_filter": leading_orphan_filter,
        "self_nested_root_filter": self_nested_root_filter,
        "duplicate_filter": duplicate_filter,
        "heading_evidence_alignment": heading_evidence_alignment,
        "numbered_textbook_section_reconstruction": numbered_textbook_reconstruction,
        "numbered_chapter_topic_reconstruction": numbered_reconstruction,
        "source_visible_toc_reconstruction": source_visible_toc,
        "canonical_heading_reconstruction": canonical_heading_reconstruction,
        "bounded_outline_arbitration": arbitration,
    }
    validation = {
        "schema": "luceon.outline-validation/v1",
        "status": status,
        "gates": {
            "outline_depth_is_two_or_three": depth_ok,
            "outline_titles_are_source_evidenced": evidence_report["passed"],
            "outline_has_no_duplicates_or_level_jumps": hierarchy["passed"],
            "outline_and_body_are_bidirectionally_covered": coverage["passed"],
            "unresolved_outline_items_block_acceptance": not blockers,
        },
        "blockers": blockers,
    }
    source_titles = {_normalize_title(row.get("title") or row.get("anchor_title")) for row in popo.get("outline") or []}
    final_titles = {row["normalized_title"] for row in nodes}
    diff = {
        "schema": "luceon.outline-diff/v1",
        "source_outline_count": len(popo.get("outline") or []),
        "final_outline_count": len(nodes),
        "source_only_titles": sorted(source_titles - final_titles),
        "final_only_titles": sorted(final_titles - source_titles),
    }

    _write_json(output_dir / "outline.json", outline_document)
    _write_json(output_dir / "outline-validation.json", validation)
    _write_json(output_dir / "outline-diff-report.json", diff)
    _write_json(output_dir / "outline-body-coverage.json", coverage)
    _write_json(output_dir / "source-evidence.json", evidence_report)
    _write_json(output_dir / "unresolved-items.json", {"schema": "luceon.outline-unresolved/v1", "items": blockers})
    return validation


def _nodes_from_decision(decision: dict, candidate_by_id: dict[str, dict]) -> list[dict]:
    nodes = []
    for order, source in enumerate(decision.get("final_outline") or []):
        candidate_ids = sorted({str(value) for value in source.get("candidate_ids") or [] if str(value)})
        evidence = [candidate_by_id[value] for value in candidate_ids if value in candidate_by_id]
        nodes.append(
            {
                "id": f"outline-{order + 1:04d}",
                "order": order,
                "title": str(source.get("title") or "").strip(),
                "normalized_title": _normalize_title(source.get("title")),
                "level": int(source.get("level") or 0),
                "parent_title": str(source.get("parent_title") or "").strip(),
                "source_page": _positive_int(source.get("page") or source.get("start_page")),
                "source": str(source.get("source") or ""),
                "candidate_ids": candidate_ids,
                "evidence": evidence,
            }
        )
    return nodes


def _needs_bounded_arbitration(decision: dict, candidates: list[dict]) -> bool:
    rows = decision.get("final_outline") or []
    placeholder = any(_normalize_title(row.get("title")) in {"default title", "untitled", "document"} for row in rows)
    return placeholder or (len(candidates) >= 20 and len(rows) <= 1)


def _maybe_arbitrate_weak_outline(decision: dict, candidates: list[dict]) -> dict:
    if not _needs_bounded_arbitration(decision, candidates):
        return {"applied": False, "reason": "deterministic_outline_not_ambiguous"}
    enabled = os.getenv("WORKFLOW_V2_OUTLINE_ARBITRATION_ENABLED", "1").strip().lower() not in {"0", "false", "no"}
    if not enabled:
        return {"applied": False, "reason": "bounded_outline_arbitration_disabled"}
    if not os.getenv("DEEPSEEK_API_KEY"):
        return {"applied": False, "reason": "bounded_outline_arbitration_key_unavailable"}
    from app.workflow_v2.runtime.canonical.scripts.build_outline_decision import maybe_review_with_llm

    reviewed = maybe_review_with_llm(
        json.loads(json.dumps(decision)),
        candidates,
        enabled=True,
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        timeout=int(os.getenv("WORKFLOW_V2_OUTLINE_ARBITRATION_TIMEOUT", "90")),
        max_risk_candidates=min(80, max(20, len(candidates))),
    )
    improved = reviewed.get("final_outline") or []
    if improved == (decision.get("final_outline") or []):
        return {
            "applied": False,
            "reason": "bounded_outline_arbitration_did_not_resolve",
            "llm": reviewed.get("llm") or {},
        }
    return {
        "applied": True,
        "reason": "ambiguous_candidate_subset_arbitrated",
        "candidate_count": len(candidates),
        "reviewed_candidate_limit": min(80, max(20, len(candidates))),
        "llm": reviewed.get("llm") or {},
        "decision": reviewed,
    }


ARTICLE_METADATA_RE = re.compile(r"^语篇类型\s*[:：].*?词数\s*[:：].*?难度\s*[:：]", re.I)


def _augment_outline_from_article_metadata(canonical_dir: Path, nodes: list[dict]) -> dict:
    """Add source-evidenced article roots that a noisy outline omitted."""
    lines = (canonical_dir / "clean.md").read_text(encoding="utf-8").splitlines()
    page_idx = None
    page_start = 0
    existing = {row["normalized_title"] for row in nodes}
    additions = []
    for index, raw in enumerate(lines):
        stripped = raw.strip()
        page = re.fullmatch(r"<!--\s*page_idx:\s*(\d+)\s*-->", stripped)
        if page:
            page_idx = int(page.group(1))
            page_start = index + 1
            continue
        if not ARTICLE_METADATA_RE.match(stripped):
            continue
        fragments = []
        heading_chain = []
        for line_index in range(index - 1, page_start - 1, -1):
            candidate = lines[line_index].strip()
            if not candidate:
                continue
            heading = re.fullmatch(r"(#{1,3})\s+(.+?)\s*", candidate)
            if heading:
                heading_chain.append((line_index, len(heading.group(1)), heading.group(2).strip()))
                if len(heading_chain) >= 3:
                    break
                continue
            if heading_chain:
                break
            if not _metadata_title_fragment(candidate):
                break
            fragments.append((line_index, candidate))
            if len(fragments) >= 3:
                break
        if heading_chain:
            heading_chain.reverse()
            parent_by_level = {}
            for line_index, level, title in heading_chain:
                normalized = _normalize_title(title)
                parent_title = parent_by_level.get(level - 1, "") if level > 1 else ""
                parent_by_level[level] = title
                if not normalized or normalized in existing:
                    continue
                additions.append(
                    _metadata_outline_node(
                        title=title,
                        level=level,
                        parent_title=parent_title,
                        page_idx=page_idx,
                        clean_lines=[line_index + 1],
                        metadata_line=index + 1,
                        method="article_metadata_heading_chain",
                    )
                )
                existing.add(normalized)
            continue
        fragments.reverse()
        if not fragments:
            continue
        title = re.sub(r"\s+", " ", " ".join(value for _line, value in fragments)).strip()
        normalized = _normalize_title(title)
        if not normalized or normalized in existing:
            continue
        additions.append(_metadata_outline_node(
            title=title,
            level=1,
            parent_title="",
            page_idx=page_idx,
            clean_lines=[line_number + 1 for line_number, _value in fragments],
            metadata_line=index + 1,
            method="article_metadata_title_fragments",
        ))
        existing.add(normalized)
    if not additions:
        return {"applied": False, "reason": "no_omitted_article_metadata_titles", "added_count": 0}
    nodes.extend(additions)
    nodes.sort(key=lambda row: (
        row.get("source_page") or 10**9,
        (row.get("evidence") or [{}])[0].get("clean_line") or row.get("order") or 0,
    ))
    for order, node in enumerate(nodes):
        node["id"] = f"outline-{order + 1:04d}"
        node["order"] = order
    return {
        "applied": True,
        "method": "article_metadata_title_fragments",
        "added_count": len(additions),
        "titles": [row["title"] for row in additions],
    }


def _metadata_outline_node(*, title: str, level: int, parent_title: str, page_idx: int | None, clean_lines: list[int], metadata_line: int, method: str) -> dict:
    return {
        "id": "",
        "order": 0,
        "title": title,
        "normalized_title": _normalize_title(title),
        "level": level,
        "parent_title": parent_title,
        "source_page": page_idx + 1 if page_idx is not None else None,
        "source": method,
        "candidate_ids": [],
        "evidence": [{
            "method": method,
            "clean_line": clean_lines[0],
            "clean_lines": clean_lines,
            "page_idx": page_idx,
            "metadata_line": metadata_line,
        }],
    }


def _metadata_title_fragment(value: str) -> bool:
    text = re.sub(r"[*_`]", "", value).strip()
    if not text or len(text) > 100 or value.startswith(("!", "<", "$", "\\", "|")):
        return False
    if re.search(r"[.!?。！？;；,，:]$", text):
        return False
    if re.match(r"^(?:\d+[.)、]|[A-Ha-h][.)、])\s+", text):
        return False
    tokens = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?|[\u4e00-\u9fff]", text)
    return 1 <= len(tokens) <= 20


TOC_MARKER_RE = re.compile(r"^(?:目录|contents|table\s+of\s+contents)$", re.I)
CHINESE_TOC_ROOT_RE = re.compile(r"^[一二三四五六七八九十百零〇]+[、.．]\s*\S")
ENGLISH_TOC_ROOT_RE = re.compile(r"^(?:chapter|unit|part)\s+(?:\d+|[ivxlcdm]+)\b", re.I)
TOC_TRAILING_PAGE_RE = re.compile(r"^(?P<title>.+?)(?:\s*[.．·…]{2,}\s*|\s+)(?P<page>\d{1,4})\s*$")
TOC_CHILD_RE = re.compile(r"^(?:\d{1,2}\s+\S|第\s*\d+\s*课时\s+\S|第[一二三四五六七八九十百零〇]+单元(?:知识导图|综合练习)\b)")


def _reconstruct_source_visible_toc(canonical_dir: Path, popo: dict) -> dict:
    """Recover a high-confidence hierarchy from source-visible TOC blocks."""
    blocks_path = canonical_dir / "blocks.json"
    if not blocks_path.is_file():
        return {"applied": False, "reason": "source_blocks_unavailable"}
    blocks = _read_json(blocks_path).get("blocks") or []
    reported_last_front_page = _nonnegative_int(popo.get("last_front_page"))
    marker_blocks = [
        row for row in blocks
        if TOC_MARKER_RE.fullmatch(str(row.get("content") or "").strip())
        and _nonnegative_int(row.get("page_idx")) is not None
        and (reported_last_front_page is None or int(row["page_idx"]) <= reported_last_front_page)
    ]
    if not marker_blocks:
        return {"applied": False, "reason": "source_visible_toc_marker_not_found"}

    toc_start = min(int(row["page_idx"]) for row in marker_blocks)
    toc_end = reported_last_front_page if reported_last_front_page is not None and reported_last_front_page >= toc_start else toc_start
    toc_blocks = [
        row for row in blocks
        if toc_start <= int(row.get("page_idx") if row.get("page_idx") is not None else -1) <= toc_end
        and str(row.get("type") or "") != "page_number"
    ]
    toc_blocks.sort(key=lambda row: (int(row.get("page_idx") or 0), int(row.get("source_order") or 0)))

    nodes = []
    roots = []
    current_root = None
    current_level_two = None
    for block in toc_blocks:
        block_id = block.get("block_id")
        toc_page_idx = int(block.get("page_idx") or 0)
        for block_line, raw_line in enumerate(str(block.get("content") or "").splitlines(), 1):
            line = re.sub(r"\s+", " ", raw_line).strip()
            if not line or TOC_MARKER_RE.fullmatch(line):
                continue
            if CHINESE_TOC_ROOT_RE.match(line) or ENGLISH_TOC_ROOT_RE.match(line):
                current_root = _toc_node(
                    title=line,
                    level=1,
                    parent_title="",
                    printed_page=None,
                    toc_page_idx=toc_page_idx,
                    block_id=block_id,
                    block_line=block_line,
                )
                nodes.append(current_root)
                roots.append(current_root)
                current_level_two = None
                continue
            if current_root is None:
                continue
            entry = TOC_TRAILING_PAGE_RE.fullmatch(line)
            if not entry:
                continue
            title = entry.group("title").strip(" .．·…")
            if not TOC_CHILD_RE.match(title):
                continue
            printed_page = int(entry.group("page"))
            is_lesson = bool(re.match(r"^第\s*\d+\s*课时\s+\S", title))
            level = 3 if is_lesson and current_level_two is not None else 2
            parent = current_level_two if level == 3 else current_root
            node = _toc_node(
                title=title,
                level=level,
                parent_title=parent["title"],
                printed_page=printed_page,
                toc_page_idx=toc_page_idx,
                block_id=block_id,
                block_line=block_line,
            )
            nodes.append(node)
            if level == 2:
                current_level_two = node

    child_count = sum(row["level"] > 1 for row in nodes)
    if not roots or child_count < 2:
        return {
            "applied": False,
            "reason": "source_visible_toc_has_insufficient_hierarchy_evidence",
            "root_count": len(roots),
            "child_count": child_count,
            "toc_pages": [toc_start, toc_end],
        }

    page_offset, offset_evidence = _infer_toc_printed_page_offset(blocks, nodes, toc_end, canonical_dir)
    if page_offset is None:
        return {
            "applied": False,
            "reason": "source_visible_toc_page_mapping_unresolved",
            "root_count": len(roots),
            "child_count": child_count,
            "toc_pages": [toc_start, toc_end],
        }
    for node in nodes:
        printed_page = node.get("printed_page")
        if printed_page is not None:
            node["source_page"] = int(printed_page) + page_offset
            node["evidence"][0]["source_page"] = node["source_page"]
            node["evidence"][0]["printed_page_offset"] = page_offset
    for root in roots:
        # Paths are normalized later; the next root boundary is the stable source ordering signal here.
        root_index = nodes.index(root)
        next_root_index = next((index for index in range(root_index + 1, len(nodes)) if nodes[index]["level"] == 1), len(nodes))
        descendants = [row for row in nodes[root_index + 1:next_root_index] if row.get("source_page") is not None]
        root["source_page"] = min((row["source_page"] for row in descendants), default=toc_start + 1)
        root["evidence"][0]["source_page"] = root["source_page"]
        root["evidence"][0]["printed_page_offset"] = page_offset
    for order, node in enumerate(nodes):
        node["id"] = f"outline-{order + 1:04d}"
        node["order"] = order
    _normalize_parent_links(nodes)
    return {
        "applied": True,
        "reason": "source_visible_toc_blocks_with_resolved_printed_page_mapping",
        "toc_pages": [toc_start, toc_end],
        "root_count": len(roots),
        "child_count": child_count,
        "node_count": len(nodes),
        "printed_page_offset": page_offset,
        "offset_evidence": offset_evidence,
        "nodes": nodes,
    }


def _toc_node(*, title: str, level: int, parent_title: str, printed_page: int | None, toc_page_idx: int, block_id, block_line: int) -> dict:
    return {
        "id": "",
        "order": 0,
        "title": title,
        "normalized_title": _normalize_title(title),
        "level": level,
        "parent_title": parent_title,
        "printed_page": printed_page,
        "source_page": None,
        "source": "source_visible_toc_block",
        "candidate_ids": [],
        "evidence": [{
            "method": "source_visible_toc_block",
            "source_block_id": block_id,
            "source_block_line": block_line,
            "toc_page_idx": toc_page_idx,
            "printed_page": printed_page,
        }],
    }


def _infer_toc_printed_page_offset(blocks: list[dict], nodes: list[dict], toc_end: int, canonical_dir: Path) -> tuple[int | None, list[dict]]:
    body_titles: dict[str, list[int]] = defaultdict(list)
    for block in blocks:
        page_idx = block.get("page_idx")
        if page_idx is None or int(page_idx) <= toc_end:
            continue
        for raw_line in str(block.get("content") or "").splitlines():
            normalized = _normalize_title(raw_line)
            if normalized:
                body_titles[normalized].append(int(page_idx) + 1)
    evidence = []
    offsets = []
    for node in nodes:
        printed_page = node.get("printed_page")
        matches = body_titles.get(node["normalized_title"]) or []
        if printed_page is None or not matches:
            continue
        source_page = min(matches)
        offset = source_page - int(printed_page)
        offsets.append(offset)
        evidence.append({"title": node["title"], "printed_page": printed_page, "source_page": source_page, "offset": offset})
    if offsets:
        return Counter(offsets).most_common(1)[0][0], evidence

    clean_path = canonical_dir / "clean.md"
    if clean_path.is_file():
        first_page = next(
            (int(match.group(1)) + 1 for line in clean_path.read_text(encoding="utf-8").splitlines() if (match := re.fullmatch(r"<!--\s*page_idx:\s*(\d+)\s*-->", line.strip()))),
            None,
        )
        printed_pages = [int(row["printed_page"]) for row in nodes if row.get("printed_page") is not None]
        if first_page is not None and printed_pages:
            return first_page - min(printed_pages), [{"method": "first_clean_body_page", "source_page": first_page, "printed_page": min(printed_pages)}]
    return None, []


def _reconstruct_invalid_outline_from_canonical_headings(canonical_dir: Path, nodes: list[dict]) -> dict:
    """Use traceable canonical headings only when the earlier outline is invalid."""
    levels = [int(row.get("level") or 0) for row in nodes]
    current_depth = max(levels, default=0)
    has_level_jump = any(previous and level > previous + 1 for previous, level in zip(levels, levels[1:]))
    if nodes and 2 <= current_depth <= 3 and not has_level_jump:
        return {"applied": False, "reason": "existing_outline_is_valid_or_repairable", "node_count": len(nodes)}

    page_idx = None
    stack: list[dict] = []
    recovered = []
    for line_number, raw in enumerate((canonical_dir / "clean.md").read_text(encoding="utf-8").splitlines(), 1):
        page = re.fullmatch(r"<!--\s*page_idx:\s*(\d+)\s*-->", raw.strip())
        if page:
            page_idx = int(page.group(1))
            continue
        heading = re.fullmatch(r"(#{1,3})\s+(.+?)\s*", raw.strip())
        if not heading:
            continue
        level = len(heading.group(1))
        title = heading.group(2).strip()
        while stack and int(stack[-1]["level"]) >= level:
            stack.pop()
        parent = stack[-1] if stack and int(stack[-1]["level"]) == level - 1 else None
        node = {
            "id": f"outline-{len(recovered) + 1:04d}",
            "order": len(recovered),
            "title": title,
            "normalized_title": _normalize_title(title),
            "level": level,
            "parent_title": parent["title"] if parent else "",
            "source_page": page_idx + 1 if page_idx is not None else None,
            "source": "canonical_clean_heading",
            "candidate_ids": [],
            "evidence": [{"method": "canonical_clean_heading", "clean_line": line_number, "page_idx": page_idx}],
        }
        recovered.append(node)
        stack.append(node)

    local_heading_grouping = _nest_repeated_local_headings(recovered)
    discarded_questions = [row for row in recovered if _looks_like_numbered_question(row.get("title"))]
    if discarded_questions:
        recovered = [row for row in recovered if row not in discarded_questions]
        for order, node in enumerate(recovered):
            node["id"] = f"outline-{order + 1:04d}"
            node["order"] = order
    _normalize_parent_links(recovered)
    recovered_depth = max((row["level"] for row in recovered), default=0)
    recovered_hierarchy = _validate_hierarchy(recovered)
    if not recovered or not (2 <= recovered_depth <= 3) or not recovered_hierarchy["passed"]:
        return {
            "applied": False,
            "reason": "canonical_heading_hierarchy_is_not_valid",
            "node_count": len(recovered),
            "max_depth": recovered_depth,
            "discarded_question_heading_count": len(discarded_questions),
            "local_heading_grouping": local_heading_grouping,
        }
    nodes[:] = recovered
    return {
        "applied": True,
        "reason": "invalid_earlier_outline_replaced_by_traceable_canonical_headings",
        "node_count": len(recovered),
        "max_depth": recovered_depth,
        "discarded_question_heading_count": len(discarded_questions),
        "discarded_question_titles": [row["title"] for row in discarded_questions],
        "local_heading_grouping": local_heading_grouping,
    }


def _nest_repeated_local_headings(nodes: list[dict]) -> dict:
    """Nest repeated Chinese exercise labels below source-visible chapter headings."""
    roots = [row for row in nodes if int(row.get("level") or 0) == 1]
    level_two = [row for row in nodes if int(row.get("level") or 0) == 2]
    local = [row for row in level_two if re.match(r"^[一二三四五六七八九十百]+、", str(row.get("title") or ""))]
    groups = [row for row in level_two if row not in local]
    if len(roots) != 1 or len(groups) < 3 or len(local) < len(groups) * 2:
        return {
            "applied": False,
            "reason": "insufficient_repeated_local_heading_groups",
            "group_count": len(groups),
            "local_heading_count": len(local),
        }
    current_group = None
    changed = []
    for node in nodes:
        if node in groups:
            current_group = node
        elif node in local and current_group is not None:
            node["level"] = 3
            node["parent_title"] = current_group["title"]
            changed.append(node)
    return {
        "applied": bool(changed),
        "reason": "repeated_local_labels_nested_under_preceding_source_chapter",
        "group_count": len(groups),
        "local_heading_count": len(local),
        "changed_count": len(changed),
    }


def _reconstruct_numbered_chapter_topics(canonical_dir: Path) -> dict:
    blocks_path = canonical_dir / "blocks.json"
    if not blocks_path.is_file():
        return {"applied": False, "reason": "source_blocks_unavailable"}
    blocks = _read_json(blocks_path).get("blocks") or []
    chapter_labels: dict[int, Counter] = defaultdict(Counter)
    chapter_label_blocks: dict[tuple[int, str], list[dict]] = defaultdict(list)
    topic_markers: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for block in blocks:
        content = str(block.get("content") or "").strip()
        chapter_match = re.match(r"^Chapter\s+(\d+)\s*:\s*(.+?)\s*$", content, re.I)
        if chapter_match and str(block.get("type") or "") in {"header", "footer"}:
            chapter_number = int(chapter_match.group(1))
            chapter_labels[chapter_number][content] += 1
            chapter_label_blocks[(chapter_number, content)].append(block)
        topic_match = re.fullmatch(r"Chapter\s+(\d+)\.\s*Topic\s+(\d+)", content, re.I)
        if topic_match:
            topic_markers[(int(topic_match.group(1)), int(topic_match.group(2)))].append(block)
    if len(chapter_labels) < 2 or len(topic_markers) < 5:
        return {"applied": False, "reason": "insufficient_numbered_chapter_topic_evidence"}

    chosen_chapters = {}
    for chapter_number, counts in chapter_labels.items():
        title, count = counts.most_common(1)[0]
        if count < 2:
            return {"applied": False, "reason": "chapter_running_label_not_repeated", "chapter": chapter_number}
        chosen_chapters[chapter_number] = (title, chapter_label_blocks[(chapter_number, title)])
    if any(chapter_number not in chosen_chapters for chapter_number, _topic_number in topic_markers):
        return {"applied": False, "reason": "topic_without_repeated_chapter_label"}

    by_order = {int(block.get("source_order") or 0): block for block in blocks}
    topic_rows = []
    for (chapter_number, topic_number), marker_rows in sorted(topic_markers.items()):
        marker = min(marker_rows, key=lambda row: int(row.get("source_order") or 0))
        marker_order = int(marker.get("source_order") or 0)
        title_block = None
        for order in range(marker_order + 1, min(marker_order + 8, len(blocks))):
            candidate = by_order.get(order)
            if not candidate or candidate.get("page_idx") != marker.get("page_idx"):
                break
            content = str(candidate.get("content") or "").strip()
            if not content or str(candidate.get("type") or "") in {"image", "header", "footer", "page_number"}:
                continue
            title_block = candidate
            break
        if title_block is None:
            return {
                "applied": False,
                "reason": "topic_title_not_found_after_first_marker",
                "chapter": chapter_number,
                "topic": topic_number,
            }
        topic_rows.append((chapter_number, topic_number, marker, title_block, len(marker_rows)))

    for chapter_number in chosen_chapters:
        numbers = sorted(topic for chapter, topic in topic_markers if chapter == chapter_number)
        if not numbers or numbers != list(range(numbers[0], numbers[-1] + 1)):
            return {"applied": False, "reason": "topic_sequence_has_gap", "chapter": chapter_number, "topics": numbers}

    nodes = []
    coverage_units = []
    topics_by_chapter: dict[int, list[tuple]] = defaultdict(list)
    for row in topic_rows:
        topics_by_chapter[row[0]].append(row)
    for chapter_number in sorted(chosen_chapters):
        chapter_title, label_rows = chosen_chapters[chapter_number]
        first_topic = min(topics_by_chapter[chapter_number], key=lambda row: row[1])
        chapter_node = {
            "id": "",
            "order": 0,
            "title": chapter_title,
            "normalized_title": _normalize_title(chapter_title),
            "level": 1,
            "parent_title": "",
            "source_page": int(first_topic[2].get("page_idx")) + 1,
            "source": "repeated_chapter_label_majority",
            "candidate_ids": [],
            "evidence": [{
                "method": "repeated_chapter_running_label_majority",
                "block_ids": [row.get("block_id") for row in label_rows],
                "page_indices": sorted({row.get("page_idx") for row in label_rows}),
                "occurrence_count": len(label_rows),
            }],
        }
        nodes.append(chapter_node)
        coverage_units.append({"title": chapter_title})
        for _chapter, topic_number, marker, title_block, marker_count in sorted(topics_by_chapter[chapter_number], key=lambda row: row[1]):
            title = f"{chapter_number}.{topic_number} {str(title_block.get('content') or '').strip()}"
            nodes.append({
                "id": "",
                "order": 0,
                "title": title,
                "normalized_title": _normalize_title(title),
                "level": 2,
                "parent_title": chapter_title,
                "source_page": int(marker.get("page_idx")) + 1,
                "source": "numbered_chapter_topic_source_pair",
                "candidate_ids": [],
                "evidence": [{
                    "method": "first_numbered_topic_marker_followed_by_same_page_title",
                    "marker_block_id": marker.get("block_id"),
                    "marker_bbox": marker.get("bbox") or [],
                    "title_block_id": title_block.get("block_id"),
                    "title_bbox": title_block.get("bbox") or [],
                    "page_idx": marker.get("page_idx"),
                    "marker_occurrence_count": marker_count,
                }],
            })
            coverage_units.append({"title": title})
    for order, node in enumerate(nodes):
        node["id"] = f"outline-{order + 1:04d}"
        node["order"] = order
    return {
        "applied": True,
        "method": "numbered_chapter_topic_source_pairs",
        "chapter_count": len(chosen_chapters),
        "topic_count": len(topic_rows),
        "minimum_chapters": 2,
        "minimum_topics": 5,
        "nodes": nodes,
        "coverage_units": coverage_units,
    }


def _reconstruct_numbered_textbook_sections(canonical_dir: Path) -> dict:
    """Recover chapter/section hierarchy from explicit numbered Markdown headings."""
    clean_path = canonical_dir / "clean.md"
    if not clean_path.is_file():
        return {"applied": False, "reason": "canonical_clean_markdown_unavailable"}

    page_idx = None
    heading_rows = []
    chapter_rows: dict[int, list[dict]] = defaultdict(list)
    section_rows: dict[int, dict[int, dict]] = defaultdict(dict)
    for line_number, raw in enumerate(clean_path.read_text(encoding="utf-8").splitlines(), 1):
        page = re.fullmatch(r"<!--\s*page_idx:\s*(\d+)\s*-->", raw.strip())
        if page:
            page_idx = int(page.group(1))
            continue
        heading = re.fullmatch(r"#{1,3}\s+(.+?)\s*", raw.strip())
        if not heading:
            continue
        title = heading.group(1).strip()
        quoted = bool(re.match(r"^>\s*", title))
        plain_title = re.sub(r"^>\s*", "", title)
        heading_rows.append({
            "title": plain_title,
            "line": line_number,
            "page_idx": page_idx,
            "quoted": quoted,
        })
        section = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\s+(.+)", plain_title)
        if section:
            if not quoted and _looks_like_numbered_question(plain_title):
                continue
            chapter_number = int(section.group(1))
            section_number = int(section.group(2))
            section_rows[chapter_number].setdefault(section_number, {
                "title": f"{chapter_number}.{section_number} {section.group(3).strip()}",
                "line": line_number,
                "page_idx": page_idx,
            })
            continue
        chapter = re.fullmatch(r"(\d{1,2})\s+([^.!?。！？].{1,119})", plain_title)
        if chapter:
            chapter_rows[int(chapter.group(1))].append({
                "title": plain_title,
                "line": line_number,
                "page_idx": page_idx,
            })

    reconstructed_roots = {}
    skipped_chapters = []
    for chapter_number, sections in section_rows.items():
        first_section_line = min(row["line"] for row in sections.values())
        root = _numbered_chapter_root(
            chapter_number,
            first_section_line,
            chapter_rows.get(chapter_number) or [],
            heading_rows,
        )
        if root:
            reconstructed_roots[chapter_number] = root
        else:
            skipped_chapters.append(chapter_number)

    eligible_chapters = sorted(reconstructed_roots)
    section_count = sum(len(section_rows[number]) for number in eligible_chapters)
    if len(eligible_chapters) < 2 or section_count < 5:
        return {
            "applied": False,
            "reason": "insufficient_explicit_numbered_textbook_hierarchy",
            "chapter_count": len(eligible_chapters),
            "section_count": section_count,
            "skipped_chapters": skipped_chapters,
        }

    nodes = []
    for chapter_number in eligible_chapters:
        sections = section_rows[chapter_number]
        root = reconstructed_roots[chapter_number]
        nodes.append({
            "id": "",
            "order": 0,
            "title": root["title"],
            "normalized_title": _normalize_title(root["title"]),
            "level": 1,
            "parent_title": "",
            "source_page": (root["page_idx"] + 1) if root["page_idx"] is not None else None,
            "source": "explicit_numbered_textbook_heading",
            "candidate_ids": [],
            "evidence": [{
                "method": "explicit_numbered_chapter_heading",
                "clean_line": root["line"],
                "page_idx": root["page_idx"],
            }],
        })
        for section_number in sorted(sections):
            row = sections[section_number]
            nodes.append({
                "id": "",
                "order": 0,
                "title": row["title"],
                "normalized_title": _normalize_title(row["title"]),
                "level": 2,
                "parent_title": root["title"],
                "source_page": (row["page_idx"] + 1) if row["page_idx"] is not None else None,
                "source": "explicit_numbered_textbook_heading",
                "candidate_ids": [],
                "evidence": [{
                    "method": "explicit_numbered_section_heading",
                    "clean_line": row["line"],
                    "page_idx": row["page_idx"],
                }],
            })
    for order, node in enumerate(nodes):
        node["id"] = f"outline-{order + 1:04d}"
        node["order"] = order
    return {
        "applied": True,
        "method": "explicit_numbered_textbook_headings",
        "chapter_count": len(eligible_chapters),
        "section_count": section_count,
        "skipped_chapters": skipped_chapters,
        "nodes": nodes,
    }


_CHAPTER_TITLE_CONNECTORS = {"and", "for", "from", "in", "of", "on", "the", "to", "with"}
_NON_CHAPTER_HEADINGS = {
    "activity",
    "background",
    "challenge",
    "check your progress",
    "continued",
    "focus",
    "getting started",
    "in this topic you will",
    "key words",
    "method",
    "practice",
    "project",
    "questions",
    "summary checklist",
    "think like a scientist",
    "you will need",
}


def _numbered_chapter_root(
    chapter_number: int,
    first_section_line: int,
    numbered_rows: list[dict],
    heading_rows: list[dict],
) -> dict | None:
    preceding = [row for row in numbered_rows if row["line"] < first_section_line]
    if preceding:
        root = dict(preceding[-1])
        words = root["title"].split()
        following = [
            row
            for row in heading_rows
            if root["line"] < row["line"] < first_section_line and not row["quoted"]
        ]
        if words and words[-1].lower() in _CHAPTER_TITLE_CONNECTORS and following:
            fragment = following[0]["title"].strip()
            if _plausible_chapter_fragment(fragment):
                root["title"] = f"{root['title']} {fragment}"
        return root

    preceding_headings = [
        row
        for row in heading_rows
        if 0 < first_section_line - row["line"] <= 12 and not row["quoted"]
    ]
    for row in reversed(preceding_headings):
        fragment = row["title"].strip()
        if _plausible_chapter_fragment(fragment):
            return {
                "title": f"{chapter_number} {fragment}",
                "line": row["line"],
                "page_idx": row["page_idx"],
            }
    return None


def _plausible_chapter_fragment(value: str) -> bool:
    normalized = _normalize_title(value)
    if not normalized or normalized in _NON_CHAPTER_HEADINGS:
        return False
    if re.match(r"^\d{1,2}(?:\.\d{1,2})?\b", value):
        return False
    words = value.split()
    return 1 <= len(words) <= 12 and not value.endswith(("?", "？", ".", "。", ":", "："))


def _align_nodes_to_canonical_headings(canonical_dir: Path, nodes: list[dict]) -> dict:
    """Attach unique exact-title Clean line evidence when candidate evidence is shared."""
    clean_path = canonical_dir / "clean.md"
    if not clean_path.is_file() or not nodes:
        return {"applied": False, "reason": "canonical_headings_or_outline_unavailable", "aligned_count": 0}
    page_idx = None
    headings: dict[str, list[dict]] = defaultdict(list)
    for line_number, raw in enumerate(clean_path.read_text(encoding="utf-8").splitlines(), 1):
        page = re.fullmatch(r"<!--\s*page_idx:\s*(\d+)\s*-->", raw.strip())
        if page:
            page_idx = int(page.group(1))
            continue
        heading = re.fullmatch(r"#{1,3}\s+(.+?)\s*", raw.strip())
        if not heading:
            continue
        title = re.sub(r"^>\s*", "", heading.group(1).strip())
        headings[_normalize_title(title)].append({
            "line": line_number,
            "page_idx": page_idx,
        })

    used_lines = {
        int(evidence["clean_line"])
        for node in nodes
        for evidence in node.get("evidence") or []
        if _positive_int(evidence.get("clean_line")) is not None
    }
    aligned = []
    for node in nodes:
        if any(_positive_int(row.get("clean_line")) is not None for row in node.get("evidence") or []):
            continue
        candidates = [row for row in headings.get(node["normalized_title"], []) if row["line"] not in used_lines]
        if not candidates:
            continue
        source_page = _positive_int(node.get("source_page"))
        chosen = min(
            candidates,
            key=lambda row: (
                abs(((row["page_idx"] + 1) if row["page_idx"] is not None else 10**9) - source_page)
                if source_page is not None else 0,
                row["line"],
            ),
        )
        node.setdefault("evidence", []).append({
            "method": "canonical_heading_exact_title_alignment",
            "clean_line": chosen["line"],
            "page_idx": chosen["page_idx"],
        })
        used_lines.add(chosen["line"])
        aligned.append({"node_id": node["id"], "clean_line": chosen["line"]})
    return {
        "applied": bool(aligned),
        "reason": "exact_title_clean_heading_evidence_attached" if aligned else "no_unambiguous_exact_title_alignment",
        "aligned_count": len(aligned),
        "records": aligned,
    }


def _validate_hierarchy(nodes: list[dict]) -> dict:
    blockers = []
    seen = set()
    prior_titles: dict[str, list[dict]] = {}
    prior_by_id: dict[str, dict] = {}
    previous_level = 0
    for node in nodes:
        if _normalize_title(node.get("title")) in {"default title", "untitled", "document"}:
            blockers.append({"code": "outline_placeholder_title", "node_id": node["id"], "title": node["title"]})
        if (
            node.get("source") != "explicit_numbered_textbook_heading"
            and _looks_like_numbered_question(node.get("title"))
        ):
            blockers.append({"code": "question_stem_used_as_outline_node", "node_id": node["id"], "title": node["title"]})
        key = tuple(_normalize_title(value) for value in node.get("path") or [node["title"]])
        if key in seen:
            blockers.append({"code": "duplicate_outline_node", "node_id": node["id"], "title": node["title"]})
        seen.add(key)
        level = node["level"]
        if previous_level and level > previous_level + 1:
            blockers.append({"code": "outline_level_jump", "node_id": node["id"], "from": previous_level, "to": level})
        if level == 1 and node["parent_title"]:
            blockers.append({"code": "root_has_parent", "node_id": node["id"], "parent_title": node["parent_title"]})
        if level > 1:
            parents = prior_titles.get(_normalize_title(node["parent_title"]), [])
            parent = prior_by_id.get(str(node.get("parent_id") or "")) or next(
                (candidate for candidate in reversed(parents) if candidate["level"] == level - 1),
                None,
            )
            if not parent:
                blockers.append({"code": "outline_parent_missing", "node_id": node["id"], "parent_title": node["parent_title"]})
            elif (
                _positive_int(node.get("source_page")) is not None
                and _positive_int(parent.get("source_page")) is not None
                and int(node["source_page"]) < int(parent["source_page"])
            ):
                blockers.append({
                    "code": "outline_child_precedes_parent_source_page",
                    "node_id": node["id"],
                    "parent_id": parent["id"],
                    "child_source_page": node["source_page"],
                    "parent_source_page": parent["source_page"],
                })
        prior_titles.setdefault(node["normalized_title"], []).append(node)
        prior_by_id[str(node.get("id") or "")] = node
        previous_level = level
    return {"schema": "luceon.outline-hierarchy-validation/v1", "passed": not blockers, "blockers": blockers}


def _remove_leading_orphan_outline_nodes(nodes: list[dict]) -> dict:
    """Discard non-root candidates that occur before the first evidenced root."""
    first_root = next((index for index, node in enumerate(nodes) if node.get("level") == 1), None)
    if first_root in {None, 0}:
        return {"applied": False, "removed_count": 0, "removed_nodes": []}
    removed = [node for node in nodes[:first_root] if int(node.get("level") or 0) > 1 and not node.get("parent_title")]
    if len(removed) != first_root:
        return {"applied": False, "removed_count": 0, "removed_nodes": []}
    del nodes[:first_root]
    for order, node in enumerate(nodes):
        node["id"] = f"outline-{order + 1:04d}"
        node["order"] = order
    return {
        "applied": True,
        "removed_count": len(removed),
        "removed_nodes": [{"title": node.get("title"), "level": node.get("level")} for node in removed],
    }


def _deduplicate_outline_nodes(nodes: list[dict]) -> dict:
    """Keep the first occurrence of an identical title under the same parent."""
    seen = set()
    kept = []
    removed = []
    for node in nodes:
        key = (
            int(node.get("level") or 0),
            _normalize_title(node.get("parent_title")),
            _normalize_title(node.get("title")),
        )
        if key in seen:
            removed.append(node)
            continue
        seen.add(key)
        kept.append(node)
    if not removed:
        return {"applied": False, "removed_count": 0, "removed_nodes": []}
    nodes[:] = kept
    for order, node in enumerate(nodes):
        node["id"] = f"outline-{order + 1:04d}"
        node["order"] = order
    return {
        "applied": True,
        "removed_count": len(removed),
        "removed_nodes": [{"title": node.get("title"), "parent_title": node.get("parent_title")} for node in removed],
    }


def _collapse_self_nested_roots(nodes: list[dict]) -> dict:
    """Collapse a duplicated root/child label into the surrounding real root."""
    removed = []
    index = 1
    while index < len(nodes) - 1:
        root = nodes[index]
        child = nodes[index + 1]
        previous = next(
            (candidate for candidate in reversed(nodes[:index]) if int(candidate.get("level") or 0) == 1),
            None,
        )
        same_title = _normalize_title(root.get("title")) == _normalize_title(child.get("title"))
        self_parent = _normalize_title(child.get("parent_title")) == _normalize_title(root.get("title"))
        if previous is None or int(root.get("level") or 0) != 1 or int(child.get("level") or 0) != 2 or not same_title or not self_parent:
            index += 1
            continue
        parent_title = str(previous.get("title") or "")
        removed.append(root)
        nodes.pop(index)
        cursor = index
        while cursor < len(nodes) and int(nodes[cursor].get("level") or 0) > 1:
            if _normalize_title(nodes[cursor].get("parent_title")) == _normalize_title(root.get("title")):
                nodes[cursor]["parent_title"] = parent_title
            cursor += 1
        index = cursor
    for order, node in enumerate(nodes):
        node["id"] = f"outline-{order + 1:04d}"
        node["order"] = order
    return {
        "applied": bool(removed),
        "removed_count": len(removed),
        "removed_nodes": [{"title": node.get("title"), "source_page": node.get("source_page")} for node in removed],
    }


def _normalize_parent_links(nodes: list[dict]) -> list[dict]:
    stack: list[dict] = []
    repairs = []
    for node in nodes:
        level = node["level"]
        while stack and stack[-1]["level"] >= level:
            stack.pop()
        expected = stack[-1] if stack and stack[-1]["level"] == level - 1 else None
        if level == 1:
            node["parent_title"] = ""
            node["parent_id"] = None
        elif expected and _normalize_title(node["parent_title"]) != expected["normalized_title"]:
            repairs.append(
                {
                    "node_id": node["id"],
                    "title": node["title"],
                    "level": level,
                    "before_parent_title": node["parent_title"],
                    "after_parent_title": expected["title"],
                    "reason": "nearest_preceding_level_parent",
                }
            )
            node["parent_title"] = expected["title"]
        if level > 1:
            node["parent_id"] = expected["id"] if expected else None
        node["path"] = [row["title"] for row in stack] + [node["title"]]
        stack.append(node)
    return repairs


def _validate_evidence(nodes: list[dict]) -> dict:
    blockers = []
    records = []
    for node in nodes:
        supported = bool(node["source_page"] and (node["evidence"] or node["source"]))
        records.append({"node_id": node["id"], "title": node["title"], "source_page": node["source_page"], "supported": supported})
        if not supported:
            blockers.append({"code": "outline_node_without_source_evidence", "node_id": node["id"], "title": node["title"]})
    return {"schema": "luceon.outline-source-evidence/v1", "passed": not blockers, "records": records, "blockers": blockers}


def _body_coverage_units(nodes: list[dict]) -> list[dict]:
    units = []
    for node in nodes:
        anchors = []
        clean_line_anchors = []
        for evidence in node.get("evidence") or []:
            if evidence.get("source_block_id"):
                suffix = f":line:{evidence['source_block_line']}" if evidence.get("source_block_line") else ""
                anchors.append(f"source-block:{evidence['source_block_id']}{suffix}")
            if evidence.get("marker_block_id"):
                anchors.append(f"source-block:{evidence['marker_block_id']}")
            if evidence.get("clean_line"):
                clean_line_anchors.append(f"clean-line:{evidence['clean_line']}")
        if clean_line_anchors:
            anchors = clean_line_anchors
        else:
            for candidate_id in node.get("candidate_ids") or []:
                anchors.append(f"candidate:{candidate_id}")
        if not anchors and node.get("source_page"):
            anchors.append(f"page-title:{node['source_page']}:{node['normalized_title']}")
        units.append(
            {
                "node_id": node["id"],
                "title": node["title"],
                "normalized_title": node["normalized_title"],
                "anchors": sorted(set(anchors)),
            }
        )
    return units


def _validate_body_coverage(
    nodes: list[dict],
    units: list[dict],
    assignments: list[dict],
    unassigned: list[dict],
    *,
    raw_unit_count: int = 0,
) -> dict:
    blockers = []
    node_ids = {row["id"] for row in nodes}
    unit_node_ids = {str(row.get("node_id") or "") for row in units}
    missing_units = sorted(
        _normalize_title(row.get("title"))
        for row in units
        if str(row.get("node_id") or "") not in node_ids
    )
    nodes_without_units = sorted(row["normalized_title"] for row in nodes if row["id"] not in unit_node_ids)
    anchor_owners: dict[str, set[str]] = defaultdict(set)
    for unit in units:
        for anchor in unit.get("anchors") or []:
            anchor_owners[str(anchor)].add(str(unit.get("node_id") or ""))
    duplicate_anchors = sorted(anchor for anchor, owners in anchor_owners.items() if len(owners) > 1)
    anchorless_nodes = sorted(
        row["normalized_title"]
        for row in nodes
        if not next((unit.get("anchors") for unit in units if unit.get("node_id") == row["id"]), [])
    )
    refs = [str(row.get("block_ref") or "") for row in assignments if row.get("block_ref")]
    duplicate_refs = sorted(ref for ref, count in Counter(refs).items() if count > 1)
    if missing_units:
        blockers.append({"code": "body_units_missing_from_outline", "titles": missing_units})
    if nodes_without_units:
        blockers.append({"code": "outline_nodes_without_body_units", "titles": nodes_without_units})
    if anchorless_nodes:
        blockers.append({"code": "outline_nodes_without_body_anchors", "titles": anchorless_nodes})
    if duplicate_anchors:
        blockers.append({"code": "body_anchors_owned_by_multiple_outline_nodes", "anchors": duplicate_anchors})
    if duplicate_refs:
        blockers.append({"code": "blocks_assigned_more_than_once", "block_refs": duplicate_refs})
    if unassigned:
        blockers.append({"code": "body_blocks_unassigned", "count": len(unassigned)})
    return {
        "schema": "luceon.outline-body-coverage/v1",
        "passed": not blockers,
        "outline_node_count": len(nodes),
        "body_unit_count": len(units),
        "raw_semantic_unit_count": raw_unit_count,
        "raw_semantic_units_excluded_from_outline_coverage": raw_unit_count,
        "body_anchor_count": len(anchor_owners),
        "duplicate_anchor_count": len(duplicate_anchors),
        "anchorless_node_count": len(anchorless_nodes),
        "assignment_count": len(assignments),
        "unique_assignment_count": len(set(refs)),
        "missing_units": missing_units,
        "nodes_without_units": nodes_without_units,
        "unassigned_count": len(unassigned),
        "blockers": blockers,
    }


def _node_has_body_evidence(node: dict) -> bool:
    if node.get("source") in {"repeated_body_label", "numbered_body_section"}:
        return True
    return any((row.get("block_ids") or []) for row in node.get("evidence") or [])


def _augment_flat_outline_from_repeated_labels(canonical_dir: Path, nodes: list[dict]) -> dict:
    if not nodes:
        return {"method": "repeated_body_label", "applied": False, "reason": "outline_empty", "added_count": 0, "labels": []}
    non_metadata_children = [
        row for row in nodes
        if row["level"] >= 2 and not str(row.get("source") or "").startswith("article_metadata_")
    ]
    if non_metadata_children:
        return {"method": "repeated_body_label", "applied": False, "reason": "outline_not_flat", "added_count": 0, "labels": []}
    numbered = _single_root_numbered_sections(canonical_dir, nodes) if max(row["level"] for row in nodes) == 1 else []
    if numbered:
        parent = nodes[0]
        augmented = [parent, *numbered]
        for order, node in enumerate(augmented):
            node["id"] = f"outline-{order + 1:04d}"
            node["order"] = order
        nodes[:] = augmented
        return {
            "method": "sequential_numbered_body_sections",
            "applied": True,
            "added_count": len(numbered),
            "labels": [row["title"] for row in numbered],
            "minimum_sequential_items": 5,
        }
    parent_by_title = {row["normalized_title"]: row for row in nodes if row["level"] == 1}
    parents_by_page = sorted(
        parent_by_title.values(),
        key=lambda row: (row.get("source_page") or 10**9, row.get("order") or 0),
    )
    occurrences: dict[str, list[dict]] = {}
    page_idx = None
    parent = None
    for line_number, raw in enumerate((canonical_dir / "clean.md").read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        page_match = re.fullmatch(r"<!--\s*page_idx:\s*(\d+)\s*-->", line)
        if page_match:
            page_idx = int(page_match.group(1))
            parent = _parent_for_source_page(parents_by_page, page_idx)
            continue
        heading = re.fullmatch(r"#{1,6}\s+(.+?)\s*", line)
        if heading:
            heading_title = heading.group(1).strip()
            matched_parent = parent_by_title.get(_normalize_title(heading_title))
            if matched_parent is not None:
                parent = matched_parent
                continue
            line = heading_title
        if (
            parent is None
            or _normalize_title(line) in parent_by_title
            or not _repeated_label_candidate(line)
        ):
            continue
        normalized = _normalize_title(line)
        occurrences.setdefault(normalized, []).append(
            {"title": line, "line": line_number, "page_idx": page_idx, "parent_id": parent["id"], "parent_title": parent["title"], "evidence_method": "cross_chapter_exact_label"}
        )

    blocks_path = canonical_dir / "blocks.json"
    if blocks_path.is_file():
        source_blocks = _read_json(blocks_path).get("blocks") or []
        parents = sorted(parent_by_title.values(), key=lambda row: (row["source_page"] or 0, row["order"]))
        for block in source_blocks:
            title = str(block.get("content") or "").strip()
            family = _numeric_label_family(title)
            page = _positive_int(block.get("page_idx"))
            if not family or page is None or not isinstance(block.get("bbox"), list):
                continue
            parent = _parent_for_source_page(parents, page)
            if parent is None:
                continue
            normalized = _normalize_title(title)
            occurrences.setdefault(normalized, []).append(
                {
                    "title": title,
                    "line": 0,
                    "page_idx": page,
                    "parent_id": parent["id"],
                    "parent_title": parent["title"],
                    "source_block_id": block.get("block_id"),
                    "bbox": block.get("bbox"),
                    "evidence_method": "source_numeric_label_family",
                }
            )

    minimum_parents = max(3, math.ceil(len(parent_by_title) * 0.5))
    accepted = {}
    for label, rows in occurrences.items():
        first_by_parent = {}
        for row in rows:
            first_by_parent.setdefault(row["parent_id"], row)
        if len(first_by_parent) >= minimum_parents:
            accepted[label] = list(first_by_parent.values())
    family_occurrences: dict[str, list[dict]] = {}
    for rows in occurrences.values():
        for row in rows:
            family = _numeric_label_family(row["title"])
            if family:
                family_occurrences.setdefault(family, []).append(row)
    for family, rows in family_occurrences.items():
        parent_ids = {row["parent_id"] for row in rows}
        if len(parent_ids) < minimum_parents:
            continue
        accepted.setdefault(f"family:{family}", [])
        seen_lines = {(row["parent_id"], _occurrence_identity(row)) for row in accepted[f"family:{family}"]}
        for row in rows:
            exact_key = _normalize_title(row["title"])
            identity = (row["parent_id"], _occurrence_identity(row))
            if exact_key in accepted or identity in seen_lines:
                continue
            accepted[f"family:{family}"].append(row)
            seen_lines.add(identity)
    if not accepted:
        return {"method": "repeated_body_label", "applied": False, "reason": "no_cross_chapter_label", "added_count": 0, "labels": []}

    children_by_parent: dict[str, list[dict]] = {}
    for label, rows in accepted.items():
        for row in rows:
            children_by_parent.setdefault(row["parent_id"], []).append(
                {
                    "title": row["title"],
                    "normalized_title": _normalize_title(row["title"]),
                    "level": 2,
                    "parent_title": row["parent_title"],
                    "source_page": row["page_idx"] + 1 if row["page_idx"] is not None else None,
                    "source": "repeated_body_label",
                    "candidate_ids": [],
                    "evidence": [
                        {
                            "clean_line": row.get("line") or None,
                            "source_block_id": row.get("source_block_id"),
                            "bbox": row.get("bbox") or [],
                            "page_idx": row["page_idx"],
                            "method": row.get("evidence_method") or "cross_chapter_exact_label",
                        }
                    ],
                }
            )
    augmented = []
    for parent_node in nodes:
        augmented.append(parent_node)
        augmented.extend(sorted(children_by_parent.get(parent_node["id"], []), key=lambda row: (row["source_page"] or 0, row["evidence"][0].get("clean_line") or 0)))
    for order, node in enumerate(augmented):
        node["id"] = f"outline-{order + 1:04d}"
        node["order"] = order
    nodes[:] = augmented
    return {
        "method": "repeated_body_label",
        "applied": True,
        "added_count": sum(len(rows) for rows in children_by_parent.values()),
        "labels": sorted(accepted),
        "minimum_distinct_parents": minimum_parents,
    }


def _repeated_label_candidate(line: str) -> bool:
    cleaned = re.sub(r"[*_`]", "", line).strip()
    if not cleaned or len(cleaned) < 3 or len(cleaned) > 80 or line.startswith(("!", "<", "$", "\\", "|", "*", "_", "`")):
        return False
    if re.search(r"[:：]", cleaned) or re.search(r"[.!?。！？;；,，]$", cleaned):
        return False
    if re.match(r"^\(?[A-Ha-h]\)?(?:[.)、]|[-+]?\d|\s|$)", cleaned):
        return False
    if re.match(r"^(?:\(?\d+[.)、]|[IVXLCDM]+[.)、])(?:\s|$)", cleaned):
        return False
    tokens = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?|[\u4e00-\u9fff]", cleaned)
    return 1 <= len(tokens) <= 20


def _numeric_label_family(line: str) -> str:
    cleaned = re.sub(r"[*_`]", "", line).strip()
    match = re.fullmatch(r"([A-Za-z\u4e00-\u9fff][A-Za-z\u4e00-\u9fff ]{1,40})\s+(\d+(?:\s*[–—-]\s*\d+)?)", cleaned)
    if not match:
        return ""
    label = _normalize_title(match.group(1))
    return f"{label} #" if label else ""


def _parent_for_source_page(parents: list[dict], page_idx: int) -> dict | None:
    eligible = [row for row in parents if row.get("source_page") is not None and int(row["source_page"]) <= page_idx + 1]
    return eligible[-1] if eligible else None


def _occurrence_identity(row: dict) -> str:
    return str(row.get("source_block_id") or f"clean-line:{row.get('line') or 0}")


def _restore_single_source_root(decision: dict, units: list[dict], canonical_dir: Path, candidate_by_id: dict[str, dict], nodes: list[dict]) -> dict:
    if nodes:
        return {"applied": False, "reason": "final_outline_not_empty"}
    selected = decision.get("selected_outline") or []
    if len(selected) != 1 or len(units) != 1:
        return {"applied": False, "reason": "not_single_source_root"}
    candidate = selected[0]
    title = str(candidate.get("title") or "").strip()
    unit_title = str(units[0].get("title") or "").strip()
    clean_headings = [
        match.group(1).strip()
        for match in re.finditer(r"^#\s+(.+?)\s*$", (canonical_dir / "clean.md").read_text(encoding="utf-8"), re.M)
    ]
    if not title or _normalize_title(title) != _normalize_title(unit_title) or [_normalize_title(value) for value in clean_headings].count(_normalize_title(title)) != 1:
        return {"applied": False, "reason": "single_root_evidence_conflict"}
    candidate_ids = [str(value) for value in candidate.get("candidate_ids") or []]
    evidence = [candidate_by_id[value] for value in candidate_ids if value in candidate_by_id]
    nodes.append(
        {
            "id": "outline-0001",
            "order": 0,
            "title": title,
            "normalized_title": _normalize_title(title),
            "level": 1,
            "parent_title": "",
            "source_page": _positive_int(candidate.get("page") or candidate.get("start_page")),
            "source": str(candidate.get("source") or "single_source_root"),
            "candidate_ids": candidate_ids,
            "evidence": evidence,
        }
    )
    return {"applied": True, "reason": "selected_root_matches_only_raw_unit_and_clean_h1", "title": title}


def _single_root_numbered_sections(canonical_dir: Path, nodes: list[dict]) -> list[dict]:
    if len(nodes) != 1 or nodes[0]["level"] != 1:
        return []
    root_title = str(nodes[0].get("title") or "")
    if not re.search(r"\bsolutions?\b", root_title, re.I):
        return []
    candidates = []
    solution_labels = []
    page_idx = None
    for line_number, raw in enumerate((canonical_dir / "clean.md").read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        page_match = re.fullmatch(r"<!--\s*page_idx:\s*(\d+)\s*-->", line)
        if page_match:
            page_idx = int(page_match.group(1))
            continue
        match = re.match(r"^(\d{1,3})[.)]\s+(.+)$", line)
        if match:
            candidates.append({"number": int(match.group(1)), "title": line, "line": line_number, "page_idx": page_idx})
        if re.fullmatch(r"Solutions?\s*[:：]?", re.sub(r"^#{1,6}\s+", "", line), re.I):
            solution_labels.append({"line": line_number, "page_idx": page_idx, "text": line})
    chain = []
    expected = 1
    for candidate in candidates:
        if candidate["number"] == expected:
            chain.append(candidate)
            expected += 1
    if len(chain) < 5 or len(solution_labels) < 3:
        return []
    parent = nodes[0]
    first = solution_labels[0]
    return [{
        "title": "Solutions",
        "normalized_title": _normalize_title("Solutions"),
        "level": 2,
        "parent_title": parent["title"],
        "source_page": first["page_idx"] + 1 if first["page_idx"] is not None else None,
        "source": "repeated_solution_label",
        "candidate_ids": [],
        "evidence": [{
            "clean_line": first["line"],
            "page_idx": first["page_idx"],
            "method": "root_role_plus_sequential_questions_plus_repeated_solution_label",
            "question_count": len(chain),
            "solution_label_count": len(solution_labels),
        }],
    }]


def _remove_question_like_outline_nodes(nodes: list[dict]) -> dict:
    removed = [row for row in nodes if row.get("level", 0) > 1 and _looks_like_numbered_question(row.get("title"))]
    if not removed:
        return {"applied": False, "reason": "no_numbered_question_stems_in_outline", "removed_count": 0}
    nodes[:] = [row for row in nodes if row not in removed]
    for order, node in enumerate(nodes):
        node["id"] = f"outline-{order + 1:04d}"
        node["order"] = order
    return {
        "applied": True,
        "reason": "numbered_question_stems_are_body_content_not_outline_nodes",
        "removed_count": len(removed),
        "titles": [row["title"] for row in removed[:200]],
    }


def _looks_like_numbered_question(value) -> bool:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    match = re.match(r"^(?:\d{1,3}[.)]|\d{1,2}\.\d{1,2}(?:\s+[a-z])?)\s+(.+)$", text, re.I)
    if not match:
        return False
    body = match.group(1)
    return (
        body.endswith("?")
        or len(body) >= 100
        or len(re.findall(r"[.!?。！？]", body)) >= 2
        or bool(re.search(
            r"(?:完成|计算|解答|证明|填空|求解|判断|选择|作图|"
            r"\b(?:give|calculate|complete|explain|describe|state|identify|write|draw|choose|find|show|name)\b)",
            body,
            re.I,
        ))
    )


def _reconcile_running_hierarchy_roots(canonical_dir: Path, nodes: list[dict]) -> dict:
    blocks_path = canonical_dir / "blocks.json"
    if not blocks_path.is_file() or not nodes:
        return {"applied": False, "reason": "source_blocks_or_outline_unavailable", "added_count": 0, "updated_count": 0}
    groups: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for block in _read_json(blocks_path).get("blocks") or []:
        if str(block.get("type") or "") not in {"header", "footer"}:
            continue
        parsed = _structural_running_label(str(block.get("content") or ""))
        if parsed:
            groups[parsed].append(block)
    groups = {key: rows for key, rows in groups.items() if len({row.get("page_idx") for row in rows}) >= 3}
    if len(groups) < 2:
        return {"applied": False, "reason": "insufficient_repeated_hierarchy_labels", "added_count": 0, "updated_count": 0}
    node_by_title = {row["normalized_title"]: row for row in nodes}
    numbered_roots: dict[int, list[dict]] = defaultdict(list)
    for node in nodes:
        if node.get("level") != 1:
            continue
        ordinal = _leading_root_ordinal(node.get("title"))
        if ordinal is not None:
            numbered_roots[ordinal].append(node)
    alias_overlap = {
        ordinal for (_family, ordinal) in groups
        if len(numbered_roots.get(ordinal, [])) == 1
    }
    ordinal_alias_mode = len(alias_overlap) >= max(2, math.ceil(len(groups) * 0.5))
    exact_matches = sum(
        _normalize_title(Counter(str(row.get("content") or "").strip() for row in rows).most_common(1)[0][0]) in node_by_title
        for rows in groups.values()
    )
    if exact_matches == 0 and not ordinal_alias_mode:
        adopted = _adopt_repeated_running_roots(groups, nodes)
        if adopted.get("applied"):
            return adopted
    matched_groups = 0
    records = []
    for (family, ordinal), rows in sorted(groups.items(), key=lambda item: (item[0][0], item[0][1])):
        title_counts = Counter(str(row.get("content") or "").strip() for row in rows)
        title = title_counts.most_common(1)[0][0]
        normalized = _normalize_title(title)
        existing = node_by_title.get(normalized)
        alias_match = None
        if ordinal_alias_mode and len(numbered_roots.get(ordinal, [])) == 1:
            alias_match = numbered_roots[ordinal][0]
        existing = existing or alias_match
        first_page_idx = min(int(row["page_idx"]) for row in rows if row.get("page_idx") is not None)
        evidence = {
            "method": "repeated_running_hierarchy_label",
            "family": family,
            "ordinal": ordinal,
            "block_ids": [row.get("block_id") for row in rows],
            "page_indices": sorted({int(row["page_idx"]) for row in rows if row.get("page_idx") is not None}),
        }
        if existing:
            matched_groups += 1
            was_root = existing["level"] == 1
            existing["level"] = 1
            existing["parent_title"] = ""
            existing["source_page"] = first_page_idx + 1 if alias_match else first_page_idx
            existing["source"] = "repeated_running_hierarchy_label"
            existing["evidence"].append(evidence)
            action = "attach_running_alias" if alias_match and normalized != existing["normalized_title"] else ("update_root" if was_root else "promote_root")
            records.append({"action": action, "title": existing["title"], "running_title": title, "source_page": first_page_idx, "source_page_method": "first_running_page_idx_as_section_start_upper_bound"})
        else:
            node = {
                "id": "",
                "order": 0,
                "title": title,
                "normalized_title": normalized,
                "level": 1,
                "parent_title": "",
                "source_page": first_page_idx,
                "source": "repeated_running_hierarchy_label",
                "candidate_ids": [],
                "evidence": [evidence],
            }
            nodes.append(node)
            node_by_title[normalized] = node
            records.append({"action": "add_root", "title": title, "source_page": first_page_idx, "source_page_method": "first_running_page_idx_as_section_start_upper_bound"})
    if matched_groups < 1:
        return {"applied": False, "reason": "running_labels_do_not_match_outline_roots", "added_count": 0, "updated_count": 0}
    nodes.sort(key=lambda row: (row.get("source_page") or 10**9, row["level"], row.get("order") or 0))
    for order, node in enumerate(nodes):
        node["id"] = f"outline-{order + 1:04d}"
        node["order"] = order
    return {
        "applied": True,
        "added_count": sum(row["action"] == "add_root" for row in records),
        "updated_count": sum(row["action"] in {"update_root", "promote_root", "attach_running_alias"} for row in records),
        "records": records,
    }


def _adopt_repeated_running_roots(groups: dict[tuple[str, int], list[dict]], nodes: list[dict]) -> dict:
    families = {family for family, _ordinal in groups}
    trusted_sources = {"contents", "contents_detail", "contents_category"}
    trusted_nodes = [node for node in nodes if node.get("source") in trusted_sources]
    untrusted_roots = [
        node for node in nodes
        if node.get("level") == 1 and node.get("source") not in trusted_sources
    ]
    if (
        len(families) != 1
        or len(groups) < 3
        or len(trusted_nodes) < 10
        or len(untrusted_roots) > max(2, math.ceil(len(trusted_nodes) * 0.1))
    ):
        return {"applied": False, "reason": "running_roots_lack_trusted_outline_support"}

    retained = [node for node in nodes if node not in untrusted_roots]
    added = []
    for (family, ordinal), rows in sorted(groups.items(), key=lambda item: item[0][1]):
        title = Counter(str(row.get("content") or "").strip() for row in rows).most_common(1)[0][0]
        first_page_idx = min(int(row["page_idx"]) for row in rows if row.get("page_idx") is not None)
        added.append({
            "id": "",
            "order": 0,
            "title": title,
            "normalized_title": _normalize_title(title),
            "level": 1,
            "parent_title": "",
            "source_page": first_page_idx + 1,
            "source": "repeated_running_hierarchy_label",
            "candidate_ids": [],
            "evidence": [{
                "method": "repeated_running_hierarchy_label",
                "family": family,
                "ordinal": ordinal,
                "block_ids": [row.get("block_id") for row in rows],
                "page_indices": sorted({int(row["page_idx"]) for row in rows if row.get("page_idx") is not None}),
            }],
            "_running_root": True,
        })

    combined = retained + added
    combined.sort(
        key=lambda node: (
            node.get("source_page") or 10**9,
            0 if node.get("level") == 1 and not node.get("_running_root") else 1 if node.get("_running_root") else 2,
            node.get("order") or 0,
        )
    )
    for order, node in enumerate(combined):
        node.pop("_running_root", None)
        node["id"] = f"outline-{order + 1:04d}"
        node["order"] = order
    nodes[:] = combined
    return {
        "applied": True,
        "method": "trusted_outline_with_repeated_running_roots",
        "added_count": len(added),
        "updated_count": 0,
        "removed_untrusted_root_count": len(untrusted_roots),
        "removed_untrusted_root_titles": [node["title"] for node in untrusted_roots],
        "minimum_running_root_count": 3,
        "minimum_trusted_outline_node_count": 10,
    }


def _structural_running_label(value: str) -> tuple[str, int] | None:
    text = re.sub(r"\s+", " ", value).strip()
    match = re.match(r"^(part|section|unit)\s+(\d{1,3})\b", text, re.I)
    if not match:
        return None
    return match.group(1).casefold(), int(match.group(2))


def _leading_root_ordinal(value: str) -> int | None:
    match = re.match(r"^(?:(?:chapter|unit|part|section)\s+)?(\d{1,3})\b", str(value or "").strip(), re.I)
    return int(match.group(1)) if match else None


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise OutlineReconstructionError(f"{path.name} must contain an object")
    return value


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise OutlineReconstructionError(f"{path.name}:{number} must contain an object")
        rows.append(value)
    return rows


def _normalize_title(value) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", str(value or "").casefold()).strip()


def _positive_int(value) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _nonnegative_int(value) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
