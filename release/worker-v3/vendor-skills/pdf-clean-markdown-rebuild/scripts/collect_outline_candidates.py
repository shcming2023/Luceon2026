#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import popo_structure as ps


def stable_id(prefix: str, payload: dict[str, Any]) -> str:
    basis = "|".join(
        str(payload.get(key) or "")
        for key in ("candidate_type", "title_text", "source", "page", "level_hint", "parent_hint", "source_path")
    )
    digest = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def candidate_record(
    candidate_type: str,
    title: str,
    *,
    source: str = "",
    page: int | None = None,
    bbox: Any = None,
    block_ids: list[Any] | None = None,
    parent_hint: str = "",
    level_hint: int | None = None,
    confidence: float = 0.5,
    evidence: dict[str, Any] | None = None,
    needs_llm: bool = False,
    needs_visual: bool = False,
    source_path: str = "",
) -> dict[str, Any]:
    title = ps.clean_display_title(title)
    record = {
        "schema": "luceon-outline-candidate/v1",
        "candidate_id": "",
        "candidate_type": candidate_type,
        "title_text": title,
        "normalized_title": ps.normalize(title),
        "source": source,
        "source_path": source_path,
        "page": page,
        "bbox": bbox,
        "block_ids": block_ids or [],
        "parent_hint": ps.clean_display_title(parent_hint),
        "level_hint": level_hint,
        "confidence": round(float(confidence), 3),
        "needs_llm": bool(needs_llm),
        "needs_visual": bool(needs_visual),
        "evidence": evidence or {},
    }
    record["candidate_id"] = stable_id(candidate_type[:4] or "cand", record)
    return record


def row_candidate(row: dict[str, Any], candidate_type: str, *, confidence: float, needs_llm: bool = False) -> dict[str, Any] | None:
    title = ps.row_match_title(row)
    if not ps.clean_display_title(title):
        return None
    page = ps.first_page(row)
    parent_hint = ""
    path = row.get("path") or []
    if len(path) >= 2:
        parent_hint = path[-2]
    return candidate_record(
        candidate_type,
        title,
        source="popo_document_tree",
        page=page,
        bbox=row.get("bbox"),
        block_ids=row.get("block_ids") or [],
        parent_hint=parent_hint,
        level_hint=row.get("level"),
        confidence=confidence,
        needs_llm=needs_llm,
        needs_visual=row.get("type") == "image",
        source_path=row.get("path_text") or " / ".join(str(x) for x in path),
        evidence={
            "row_type": row.get("type"),
            "depth": row.get("depth"),
            "children_count": row.get("children_count"),
            "first_child_title": row.get("first_child_title"),
            "content_excerpt": ps.clean_text(row.get("content"))[:500],
        },
    )


def toc_candidate(entry: dict[str, Any]) -> dict[str, Any]:
    source = str(entry.get("source") or "contents")
    needs_llm = source not in {"contents", "contents_detail", "contents_category"}
    return candidate_record(
        "toc_outline_entry",
        entry.get("title") or "",
        source=source,
        page=entry.get("source_page"),
        parent_hint=entry.get("parent_title") or "",
        level_hint=entry.get("level"),
        confidence=0.86 if not needs_llm else 0.62,
        needs_llm=needs_llm,
        source_path=source,
        evidence={
            "kind": entry.get("kind"),
            "printed_page": entry.get("printed_page"),
            "source": source,
        },
    )


def outline_candidate(entry: dict[str, Any]) -> dict[str, Any]:
    source = str(entry.get("source") or "")
    weak_sources = {"synthetic_parent_from_topic", "popo_primary_title_fallback", "popo_tree_hierarchy_fallback"}
    return candidate_record(
        "selected_outline_entry",
        entry.get("title") or "",
        source=source,
        page=entry.get("start_page"),
        block_ids=entry.get("block_ids") or [],
        parent_hint=entry.get("parent_title") or "",
        level_hint=entry.get("level"),
        confidence=0.9 if source in {"contents", "contents_detail", "popo_body_lesson", "toc_reading_body_heading"} else 0.7,
        needs_llm=source in weak_sources or bool(entry.get("validation_required")),
        needs_visual=bool(entry.get("needs_visual_review") or entry.get("image_continuation_block_ids")),
        source_path=source,
        evidence={
            key: entry.get(key)
            for key in [
                "kind",
                "anchor_title",
                "anchor_method",
                "match_titles",
                "title_completed_from",
                "depth",
                "popo_level",
                "emission_order",
            ]
            if key in entry
        },
    )


def collect_candidates(root: Path) -> dict[str, Any]:
    root = root.expanduser().resolve()
    tree = ps.load_json(ps.tree_path(root))
    rows = list(ps.walk_tree(tree))
    last_front = ps.front_last_page(rows)

    flat_contents = ps.collect_flat_contents_entries(root)
    markdown_contents = ps.collect_markdown_contents_entries(root)
    if flat_contents:
        markdown_contents = ps.remove_duplicate_detail_entries(flat_contents, markdown_contents)
    row_contents = ps.collect_contents_entries(rows)
    if flat_contents or markdown_contents:
        row_contents = [entry for entry in row_contents if entry.get("kind") in {"part", "chapter", "unit"}]
    contents_entries = ps.dedupe_entries(flat_contents + markdown_contents + row_contents)

    outline = ps.build_outline(root)
    selected_outline = outline.get("outline") if isinstance(outline, dict) else []
    candidates: list[dict[str, Any]] = []

    for entry in contents_entries:
        if ps.clean_display_title(entry.get("title")):
            candidates.append(toc_candidate(entry))

    for entry in selected_outline or []:
        if ps.clean_display_title(entry.get("title")):
            candidates.append(outline_candidate(entry))

    lesson_entries = [entry for entry in selected_outline or [] if entry.get("kind") == "lesson"]
    for entry in ps.collect_toc_reading_topic_entries(rows, lesson_entries, last_front):
        if ps.clean_display_title(entry.get("title")):
            candidates.append(outline_candidate(entry))

    for row in rows:
        title = ps.clean_display_title(ps.row_match_title(row))
        if not title:
            continue
        page = ps.first_page(row)
        if page is not None and page <= last_front:
            continue
        candidate_type = ""
        confidence = 0.0
        needs_llm = False
        if ps.lesson_code_label(title):
            candidate_type = "body_lesson_heading"
            confidence = 0.82
        elif ps.is_inter_unit_module_title(title):
            candidate_type = "body_inter_unit_module"
            confidence = 0.78
        elif ps.classify_title(title):
            candidate_type = "body_structural_heading"
            confidence = 0.72
            needs_llm = True
        elif row.get("type") == "image" and ps.clean_text(row.get("content")):
            candidate_type = "image_ocr_title_or_caption"
            confidence = 0.48
            needs_llm = True
        if not candidate_type:
            continue
        record = row_candidate(row, candidate_type, confidence=confidence, needs_llm=needs_llm)
        if record:
            candidates.append(record)

    deduped = []
    seen = set()
    for candidate in candidates:
        key = (
            candidate.get("candidate_type"),
            candidate.get("normalized_title"),
            candidate.get("page"),
            tuple(candidate.get("block_ids") or []),
            candidate.get("parent_hint"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)

    summary = {
        "schema": "luceon-outline-candidates/v1",
        "created_at": datetime.utcnow().isoformat() + "Z",
        "root": str(root),
        "tree_file": str(ps.tree_path(root).name),
        "row_count": len(rows),
        "last_front_page": last_front,
        "candidate_count": len(deduped),
        "candidate_type_counts": {},
        "needs_llm_count": sum(1 for item in deduped if item.get("needs_llm")),
        "needs_visual_count": sum(1 for item in deduped if item.get("needs_visual")),
        "outline_available": bool(outline.get("available")) if isinstance(outline, dict) else False,
        "selected_outline_count": len(selected_outline or []),
    }
    for item in deduped:
        key = str(item.get("candidate_type") or "unknown")
        summary["candidate_type_counts"][key] = summary["candidate_type_counts"].get(key, 0) + 1
    return {"summary": summary, "candidates": deduped, "outline": outline}


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect auditable outline candidates from MinerU-Popo rebuild input.")
    parser.add_argument("root", type=Path)
    parser.add_argument("--out-jsonl", type=Path, default=None)
    parser.add_argument("--out-summary", type=Path, default=None)
    parser.add_argument("--out-outline", type=Path, default=None)
    args = parser.parse_args()

    result = collect_candidates(args.root)
    if args.out_jsonl:
        write_jsonl(args.out_jsonl.expanduser().resolve(), result["candidates"])
    if args.out_summary:
        args.out_summary.expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        args.out_summary.expanduser().resolve().write_text(
            json.dumps(result["summary"], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if args.out_outline:
        args.out_outline.expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        args.out_outline.expanduser().resolve().write_text(
            json.dumps(result["outline"], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if not args.out_jsonl and not args.out_summary and not args.out_outline:
        print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
