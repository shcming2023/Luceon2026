#!/usr/bin/env python3
import argparse
import hashlib
import html
import json
import os
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    from popo_structure import build_outline as build_popo_outline
except Exception:
    build_popo_outline = None

try:
    from collect_outline_candidates import collect_candidates, write_jsonl
except Exception:
    collect_candidates = None
    write_jsonl = None

try:
    from build_outline_decision import build_decision, close_numbered_outline_hierarchy, maybe_review_with_llm
except Exception:
    build_decision = None
    close_numbered_outline_hierarchy = None
    maybe_review_with_llm = None

try:
    from build_visual_decisions import build_visual_decisions
except Exception:
    build_visual_decisions = None

try:
    from outline_anchor_check import write_outline_anchor_check
except Exception:
    write_outline_anchor_check = None


NOISE_TYPES = {"footer", "header", "page_number"}
IMAGE_BACKED_VISUAL_TYPES = {"image", "chart", "figure", "diagram"}
SEMANTIC_HEADER_RE = re.compile(r"^(Review\s+\d+|Mid-term Test|Final Test|Units\s+\d+\s*[~\\-]\s*\d+)\b", re.I)
FRONT_MATTER_RE = re.compile(r"^(CONTENTS|目录|本期导读|INTRODUCTION|HOW TO USE THIS BOOK|ORGANISATION|LIST OF TERMS|A WORD FROM|WELCOME TO|ENHANCED|ADDITIONAL RESOURCES|CREDITS|ILLUSTRATIONS|PHOTOS|ACKNOWLEDGMENTS|ADVISORY BOARD|REVIEWERS|For my loves)\b", re.I)
BACK_MATTER_RE = re.compile(r"^(Appendix|APPENDIX|Glossary|GLOSSARY|Index|INDEX|Answers|ANSWERS|答案|CREDITS|Credits|ACKNOWLEDGMENTS|Acknowledgments|ADVISORY BOARD|Advisory Board|REVIEWERS|Reviewers|ADDITIONAL RESOURCES|Additional Resources)\b")
BODY_START_RE = re.compile(r"^(Read the following article|Unit\s+\d+\b|Chapter\s+[A-Z]?\d+\b|第\s*\d+\s*章\b|\d+\.\d+\b|READING\s+1\b)", re.I)
UNIT_RE = re.compile(r"\bUnit\s+(\d+)\b", re.I)
STANDALONE_UNIT_NUMBER_RE = re.compile(r"^([1-9]|1[0-9]|2[0-9])$")
UNIT_TOP_HEADING_RE = re.compile(r"^Unit\s+(\d{1,2})(.*)$", re.I)
NON_UNIT_TITLE_RE = re.compile(r"^(HODDER|HINT|SELF-CHECK|Review and reflection|Unit review questions|Writing|Speaking|Reading|Key terms|Challenge|Do you remember\\?)$", re.I)


def is_back_matter_text(text):
    return bool(BACK_MATTER_RE.search(clean_text(text)))


def load_json(path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def find_flat_content(root):
    for path in sorted(root.glob("*_content_list.json")):
        data = load_json(path)
        if isinstance(data, list) and data and isinstance(data[0], dict) and "type" in data[0]:
            return path, data
    raise FileNotFoundError("No flat *_content_list.json found.")


def clean_text(text):
    text = html.unescape(str(text or ""))
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def is_noise_block(block):
    block_type = str(block.get("type") or "")
    if block_type in {"header", "footer"}:
        bbox = block.get("bbox")
        if not isinstance(bbox, list) or len(bbox) < 4:
            return False
        try:
            return float(bbox[1]) <= 130 or float(bbox[3]) >= 700
        except (TypeError, ValueError):
            return False
    if block_type != "page_number":
        return False
    text = clean_text(block.get("text"))
    bbox = block.get("bbox")
    if not isinstance(bbox, list) or len(bbox) < 4:
        return False
    try:
        at_edge = float(bbox[1]) <= 130 or float(bbox[3]) >= 700
    except (TypeError, ValueError):
        return False
    return at_edge and bool(re.fullmatch(r"[\divxlcdm.\-–— ]{1,12}", text, re.I))


MATH_SEGMENT_RE = re.compile(
    r"(?<!\\)\$\$.*?(?<!\\)\$\$|(?<!\\)\$(?!\$)(?:\\.|[^$\n])*(?<!\\)\$",
    re.S,
)
SPACED_DIGIT_RUN_RE = re.compile(r"(?<![A-Za-z\\_^])\d(?:[ \t]+\d)+(?![A-Za-z])")
GROUPED_DIGIT_RUN_RE = re.compile(r"(?<![A-Za-z\\_^\d])\d{1,3}(?:[ \t]+\d{3})+(?![A-Za-z\d])")
BRACKET_VECTOR_RE = re.compile(r"\[\s*-?\d+(?:[ \t]+-?\d+){2,}\s*\]")
SPACED_DECIMAL_RE = re.compile(r"(?<![\dA-Za-z\\])(\d)\.[ \t]+((?:\d[ \t]+)+\d)(?![A-Za-z])")
ARITHMETIC_LINE_SIGNAL_RE = re.compile(r"(?:[←→]|\\times|[×÷])")


def compact_digit_spaces(text):
    return re.sub(r"[ \t]+", "", text)


def normalize_math_digit_spacing(markdown):
    """Repair OCR-split numbers inside LaTeX math while leaving prose spacing alone."""
    repairs = []

    def repair_segment(match):
        segment = match.group(0)
        protected = []

        def protect_vector(vector_match):
            protected.append(vector_match.group(0))
            return f"@@VECTOR_{chr(65 + len(protected) - 1)}@@"

        repaired = BRACKET_VECTOR_RE.sub(protect_vector, segment)
        substitution_count = 0

        def decimal_repl(decimal_match):
            nonlocal substitution_count
            substitution_count += 1
            return decimal_match.group(1) + "." + compact_digit_spaces(decimal_match.group(2))

        def digit_repl(digit_match):
            nonlocal substitution_count
            substitution_count += 1
            return compact_digit_spaces(digit_match.group(0))

        repaired = SPACED_DECIMAL_RE.sub(decimal_repl, repaired)
        repaired = SPACED_DIGIT_RUN_RE.sub(digit_repl, repaired)
        for index, value in enumerate(protected):
            repaired = repaired.replace(f"@@VECTOR_{chr(65 + index)}@@", value)
        if repaired != segment:
            repairs.append({
                "line": markdown.count("\n", 0, match.start()) + 1,
                "substitutions": substitution_count,
                "before": segment,
                "after": repaired,
            })
        return repaired

    repaired_markdown = MATH_SEGMENT_RE.sub(repair_segment, markdown)
    return repaired_markdown, {
        "schema": "math-ocr-repair/v1",
        "changed": repaired_markdown != markdown,
        "segment_count": len(repairs),
        "substitution_count": sum(item["substitutions"] for item in repairs),
        "operations": repairs,
        "examples": [
            {**row, "before": row["before"][:240], "after": row["after"][:240]}
            for row in repairs[:20]
        ],
    }


def normalize_arithmetic_line_digit_spacing(markdown):
    """Repair OCR-split numbers in prose lines that are visibly arithmetic layouts."""
    repairs = []
    output_lines = []
    for line_number, raw_line in enumerate(markdown.splitlines(keepends=True), start=1):
        line = raw_line[:-1] if raw_line.endswith("\n") else raw_line
        ending = "\n" if raw_line.endswith("\n") else ""
        repaired = line
        substitution_count = 0
        if ARITHMETIC_LINE_SIGNAL_RE.search(line) and SPACED_DIGIT_RUN_RE.search(line):
            def decimal_repl(decimal_match):
                nonlocal substitution_count
                substitution_count += 1
                return decimal_match.group(1) + "." + compact_digit_spaces(decimal_match.group(2))

            def digit_repl(digit_match):
                nonlocal substitution_count
                substitution_count += 1
                return compact_digit_spaces(digit_match.group(0))

            repaired = SPACED_DECIMAL_RE.sub(decimal_repl, repaired)
            repaired = SPACED_DIGIT_RUN_RE.sub(digit_repl, repaired)
            if repaired != line:
                repairs.append({
                    "line": line_number,
                    "substitutions": substitution_count,
                    "before": line,
                    "after": repaired,
                })
        output_lines.append(repaired + ending)
    repaired_markdown = "".join(output_lines)
    return repaired_markdown, {
        "schema": "arithmetic-line-ocr-repair/v1",
        "changed": repaired_markdown != markdown,
        "segment_count": len(repairs),
        "substitution_count": sum(item["substitutions"] for item in repairs),
        "operations": repairs,
        "examples": [
            {**row, "before": row["before"][:240], "after": row["after"][:240]}
            for row in repairs[:20]
        ],
    }


def combine_math_ocr_repair_reports(math_report, line_report):
    return {
        "schema": "math-ocr-repair/v1",
        "changed": bool(math_report.get("changed") or line_report.get("changed")),
        "segment_count": int(math_report.get("segment_count", 0)) + int(line_report.get("segment_count", 0)),
        "math_segment_count": int(math_report.get("segment_count", 0)),
        "arithmetic_line_count": int(line_report.get("segment_count", 0)),
        "substitution_count": int(math_report.get("substitution_count", 0)) + int(line_report.get("substitution_count", 0)),
        "math_substitution_count": int(math_report.get("substitution_count", 0)),
        "arithmetic_line_substitution_count": int(line_report.get("substitution_count", 0)),
        "operations": (math_report.get("operations") or []) + (line_report.get("operations") or []),
        "examples": (math_report.get("examples") or [])[:10] + (line_report.get("examples") or [])[:10],
    }


def strip_inline_note_markers(text):
    text = str(text or "")
    text = re.sub(r"\\\(\s*\^\s*\{?\s*\d+\s*\}?\s*\\\)", " ", text)
    text = re.sub(r"\$\s*\^\s*\{?\s*\d+\s*\}?\s*\$", " ", text)
    text = re.sub(r"\^\s*\{?\s*\d+\s*\}?", " ", text)
    return text


def block_text(block):
    btype = str(block.get("type", ""))
    if btype == "list":
        return list_text(block)
    parts = []
    for key in ("text", "table_body"):
        if block.get(key):
            parts.append(str(block.get(key)))
    for key in ("image_caption", "table_caption", "image_footnote", "table_footnote"):
        value = block.get(key)
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
    return clean_text(" ".join(parts))


def page_text_map(blocks):
    pages = {}
    for block in blocks:
        page = block.get("page_idx")
        if page is None:
            continue
        text = block_text(block)
        if text:
            pages.setdefault(page, []).append(text)
    return pages


def page_blocks_map(blocks):
    pages = {}
    for block in blocks:
        page = block.get("page_idx")
        if page is not None:
            pages.setdefault(page, []).append(block)
    return pages


def page_has_real_text(blocks, page):
    for block in blocks:
        if block.get("page_idx") == page and str(block.get("type", "")) not in {"image", "footer", "header", "page_number"}:
            if block_text(block):
                return True
    return False


def page_has_image(blocks, page):
    return any(block.get("page_idx") == page and block.get("type") == "image" for block in blocks)


def page_is_chapter_opener(page_blocks):
    headings = [
        clean_text(block.get("text"))
        for block in page_blocks
        if block.get("text_level") is not None and clean_text(block.get("text"))
    ]
    if not headings:
        return False
    has_number = any(re.match(r"^\d{1,2}(?:\s|$)", text) for text in headings)
    has_title = any(
        not re.fullmatch(r"\d{1,2}", text)
        and not re.match(r"^(Introduction|Contents|目录|How the book is structured|How to use this book|Organisation|Section\s+\d+:)", text, re.I)
        for text in headings
    )
    has_learning_signal = any(
        re.search(r"\b(in this chapter|you will learn|links to other chapters|chapter\s+\d+)", block_text(block), re.I)
        for block in page_blocks
    )
    substantive_body_count = sum(
        1
        for block in page_blocks
        if block.get("text_level") is None
        and str(block.get("type") or "") == "text"
        and len(block_text(block)) >= 30
    )
    return has_number and has_title and (
        has_learning_signal
        or len(headings) <= 3
        or substantive_body_count >= 3
    )


def page_is_toc_like(texts):
    cleaned = [clean_text(text) for text in texts if clean_text(text)]
    if len(cleaned) < 5:
        return False
    toc_rows = 0
    heading_rows = 0
    for text in cleaned:
        compact = re.sub(r"\s+", " ", text)
        if re.search(r"(?:\.{2,}|…+)\s*\d{1,4}\s*$", compact):
            toc_rows += 1
            continue
        if re.search(r"\s\d{1,4}\s*$", compact) and re.search(
            r"^(?:Unit|Chapter|第\s*\d+\s*章|\d+(?:\.\d+)+|习题|复习|挑战|名校|各区)",
            compact,
            re.I,
        ):
            toc_rows += 1
        if re.search(r"^(?:Unit|Chapter|第\s*\d+\s*章|\d+(?:\.\d+)+)", compact, re.I):
            heading_rows += 1
    return toc_rows >= 4 and toc_rows / max(len(cleaned), 1) >= 0.35 and heading_rows >= 2


def unit_top_heading_number(text):
    text = clean_text(text)
    match = UNIT_TOP_HEADING_RE.match(text)
    if not match:
        return None
    rest = match.group(2).strip()
    if not rest:
        return match.group(1).zfill(2)
    if re.search(r"\b(knowledge\s+test|unit\s+test|benchmark\s+test|review\s+test)\b", rest, re.I):
        return None
    if rest[0] in ":：-–—.":
        return match.group(1).zfill(2)
    if re.match(r"^[A-Z][A-Za-z0-9 ,;:'\"()/-]{2,}$", rest):
        return match.group(1).zfill(2)
    return None


def page_has_unit_top_heading(page_blocks, unit_number):
    if not unit_number:
        return False
    unit_number = str(unit_number).zfill(2)
    for block in page_blocks:
        text = clean_text(block.get("text"))
        if unit_top_heading_number(text) == unit_number:
            return True
    return False


def page_unit_top_heading_count(page_blocks):
    return sum(
        1 for block in page_blocks
        if block.get("text_level") is not None and unit_top_heading_number(clean_text(block.get("text")))
    )


def is_standalone_unit_title(text):
    text = clean_text(text)
    return bool(re.match(r"^[A-Z][A-Za-z0-9 ,;:'\"()/-]{2,}$", text) and not NON_UNIT_TITLE_RE.match(text))


def chapter_parent_heading(text):
    t = clean_text(text)
    match = re.match(r"^Chapter\s+([A-Z]?\d+)\.\s*Topic\s+\d+\b", t, re.I)
    if match:
        return f"Chapter {match.group(1).upper()}"
    match = re.match(r"^Exercise\s+([A-Z]?\d+)\.\d+\b", t, re.I)
    if match:
        return f"Chapter {match.group(1).upper()}"
    return None


def is_explicit_chapter_h1(text):
    t = clean_text(text)
    return bool(re.match(r"^Chapter\s+[A-Z]?\d+\b", t, re.I) and not re.match(r"^Chapter\s+[A-Z]?\d+\.\s*Topic\s+\d+\b", t, re.I))


def normalize_heading_key(text):
    text = clean_text(strip_inline_note_markers(text))
    text = re.sub(r"^\d+\s+", "", text)
    text = re.sub(r"^(Chapter|Unit)\s+[A-Z]?\d+\b[:.\s-]*", "", text, flags=re.I)
    text = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", " ", text).strip().lower()
    return re.sub(r"\s+", " ", text)


def normalized_heading_text(text):
    text = clean_text(strip_inline_note_markers(text)).lower()
    text = re.sub(r"^(chapter|unit)\s+[a-z]?\d+\b[:：.\s-]*", "", text, flags=re.I)
    text = re.sub(r"^part\s+\d+\b[:：.\s-]*", "", text, flags=re.I)
    text = re.sub(r"^第\s*[一二三四五六七八九十百千万0-9]+\s*[章节课单元篇]\s*", "", text)
    text = re.sub(r"^\d+(?:\.\d+)+\s*", "", text)
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text).strip()
    return re.sub(r"\s+", " ", text)


def heading_match_keys(text):
    text = clean_text(text)
    keys = set()
    normalized = normalized_heading_text(text)
    if normalized:
        keys.add(normalized)
    raw = text.lower()
    if raw:
        keys.add(raw)
    return keys


def popo_page_injections(popo_outline):
    by_page = {}
    allowed = {}
    if not popo_outline or not popo_outline.get("available"):
        return by_page, allowed
    for idx, entry in enumerate(popo_outline.get("outline") or []):
        page = entry.get("start_page_idx")
        level = entry.get("level")
        title = clean_text(entry.get("title"))
        if page is None or not title or not isinstance(level, int) or level > 3:
            continue
        item = {
            "id": idx,
            "entry": entry,
            "title": title,
            "level": max(1, min(3, level)),
            "source": entry.get("source"),
            "anchor_method": entry.get("anchor_method"),
            "order": idx,
            "keys": set(),
        }
        by_page.setdefault(page, []).append(item)
        match_titles = []
        if isinstance(entry.get("match_titles"), list):
            match_titles.extend(entry.get("match_titles") or [])
        parent_title = clean_text(entry.get("parent_title"))
        for text in (title, entry.get("anchor_title"), *match_titles):
            if parent_title and clean_text(text).lower() == parent_title.lower() and clean_text(text).lower() != title.lower():
                continue
            for key in heading_match_keys(text):
                item["keys"].add(key)
                allowed.setdefault(page, set()).add(key)
    for page in by_page:
        by_page[page] = sorted(by_page[page], key=lambda item: (item["level"], item["order"], item["title"]))
    return by_page, allowed


def same_page_parent_waits_for_block(item, page_items, page_heading_keys, emitted_popo_headings):
    parent_title = clean_text((item.get("entry") or {}).get("parent_title"))
    if not parent_title:
        return False
    if not (item.get("keys") and item["keys"] & page_heading_keys):
        return False
    parent_keys = heading_match_keys(parent_title)
    if not parent_keys or not (parent_keys & page_heading_keys):
        return False
    item_order = item.get("order", 10**9)
    for candidate in page_items:
        if candidate is item:
            continue
        if candidate.get("title") != parent_title:
            continue
        if candidate.get("order", 10**9) > item_order:
            continue
        if popo_item_key(candidate) in emitted_popo_headings:
            continue
        if candidate.get("keys") and candidate["keys"] & page_heading_keys:
            return True
    return False


def outline_from_decision(popo_outline, outline_decision):
    if not outline_decision or not outline_decision.get("final_outline"):
        return popo_outline
    output = dict(popo_outline or {})
    converted = []
    for item in outline_decision.get("final_outline") or []:
        title = clean_text(item.get("title"))
        if not title:
            continue
        page = item.get("start_page")
        page_idx = item.get("start_page_idx")
        if page_idx is None and isinstance(page, int):
            page_idx = page - 1
        entry = {
            "title": title,
            "level": max(1, min(3, int(item.get("level") or 3))),
            "parent_title": clean_text(item.get("parent_title")),
            "source": item.get("source") or "outline_decision_final",
            "start_page": page,
            "start_page_idx": page_idx,
            "anchor_title": ((item.get("evidence") or {}).get("anchor_title") or title),
            "anchor_method": ((item.get("evidence") or {}).get("anchor_method") or "outline_decision_final"),
            "match_titles": ((item.get("evidence") or {}).get("match_titles") or [title]),
            "block_ids": ((item.get("evidence") or {}).get("block_ids") or []),
            "candidate_ids": item.get("candidate_ids") or [],
            "outline_decision": item.get("decision"),
            "needs_visual": bool(item.get("needs_visual")),
        }
        converted.append(entry)
    if converted:
        output["available"] = True
        output["outline"] = converted
        output["outline_order"] = "outline_decision_final"
        output["outline_source"] = outline_decision.get("final_outline_source") or outline_decision.get("decision_method")
        output["outline_decision_method"] = outline_decision.get("decision_method")
    return output


def normalize_popo_outline_levels(popo_outline):
    if not popo_outline or not popo_outline.get("available"):
        return popo_outline, {"changed": False, "reason": "outline_unavailable", "adjustments": []}
    outline = popo_outline.get("outline") or []
    levels = [entry.get("level") for entry in outline if isinstance(entry.get("level"), int)]
    if not levels:
        return popo_outline, {"changed": False, "reason": "no_integer_levels", "adjustments": []}
    has_h2 = 2 in levels
    has_orphan_h3 = any(
        entry.get("level") == 3 and not clean_text(entry.get("parent_title"))
        for entry in outline
    )
    if has_h2 or not has_orphan_h3:
        return popo_outline, {"changed": False, "reason": "no_orphan_h3_without_h2", "adjustments": []}
    output = dict(popo_outline)
    converted = []
    adjustments = []
    for index, entry in enumerate(outline):
        item = dict(entry)
        if item.get("level") == 3 and not clean_text(item.get("parent_title")):
            item["level"] = 2
            item["level_normalization"] = "orphan_h3_promoted_to_h2"
            adjustments.append({"index": index, "title": clean_text(item.get("title")), "from": 3, "to": 2})
        converted.append(item)
    output["outline"] = converted
    output["level_normalization"] = {
        "changed": bool(adjustments),
        "reason": "orphan_h3_without_any_h2",
        "adjustments": adjustments,
    }
    return output, output["level_normalization"]


def apply_visual_decisions_to_outline(outline_decision, visual_decisions):
    if not outline_decision or not visual_decisions:
        return outline_decision
    visual_enabled = bool(visual_decisions.get("enabled"))
    by_candidate = {}
    for row in visual_decisions.get("results") or []:
        candidate_id = row.get("candidate_id")
        if not candidate_id:
            continue
        for cid in str(candidate_id).split(","):
            cid = cid.strip()
            if cid:
                by_candidate[cid] = row
    accepted = []
    conflicts = []
    revised = []
    removed = []
    pending = []
    kept_outline = []
    for item in outline_decision.get("final_outline") or []:
        if item.get("needs_visual") and not visual_enabled:
            item["removed_by_visual"] = True
            item["visual_decision"] = {
                "decision": "reject",
                "confidence": "medium",
                "reason": "Visual verification is disabled; needs_visual candidate is not promoted without positive evidence.",
            }
            removed.append(item.get("title"))
            continue
        rows = [by_candidate[cid] for cid in item.get("candidate_ids") or [] if cid in by_candidate]
        if not rows:
            if item.get("needs_visual"):
                pending.append(item.get("title"))
            kept_outline.append(item)
            continue
        row = rows[0]
        decision = row.get("decision")
        item["visual_decision"] = {
            key: row.get(key)
            for key in [
                "decision",
                "visible_title",
                "confidence",
                "reason",
                "page",
                "page_index",
                "page_image",
                "accepted_after_retry",
                "retry_label",
            ]
            if key in row
        }
        if decision == "accept":
            item["needs_visual"] = False
            accepted.append(item.get("title"))
            kept_outline.append(item)
        elif decision == "revise":
            item["needs_visual"] = False
            visible_title = clean_text(row.get("visible_title"))
            if visible_title:
                item["title"] = visible_title
                item["visual_revised_title"] = visible_title
            revised.append(item.get("title"))
            kept_outline.append(item)
        elif decision == "reject":
            item["removed_by_visual"] = True
            removed.append(item.get("title"))
            continue
        else:
            item["needs_visual"] = True
            pending.append(item.get("title"))
            kept_outline.append(item)
    for idx, item in enumerate(kept_outline):
        item["order"] = idx
    outline_decision["final_outline"] = kept_outline
    outline_decision["final_outline_count"] = len(kept_outline)
    outline_decision["visual_application"] = {
        "enabled": visual_decisions.get("enabled"),
        "provider": visual_decisions.get("provider"),
        "model": visual_decisions.get("model"),
        "accepted_final_nodes": accepted,
        "revised_final_nodes": revised,
        "removed_final_nodes": removed,
        "conflict_final_nodes": conflicts,
        "pending_final_nodes": pending,
        "accepted_count": len(accepted),
        "revised_count": len(revised),
        "removed_count": len(removed),
        "conflict_count": len(conflicts),
        "pending_count": len(pending),
        "usage": visual_decisions.get("usage"),
        "error_count": len(visual_decisions.get("errors") or []),
    }
    outline_decision["needs_visual_count"] = sum(1 for item in outline_decision.get("final_outline") or [] if item.get("needs_visual"))
    return outline_decision


def page_text_level_heading_keys(page_blocks):
    keys = set()
    for block in page_blocks:
        if block.get("text_level") is None:
            continue
        keys.update(heading_match_keys(block.get("text")))
    return keys


def popo_item_key(item):
    return item.get("id")


def popo_item_page_distance(item, page):
    if page is None:
        return 10**9
    entry = item.get("entry") or {}
    item_page = entry.get("start_page_idx")
    if item_page is None:
        item_page = item.get("page")
    if item_page is None:
        return 10**9
    return abs(int(item_page) - int(page))


def build_popo_items_by_key(popo_injections_by_page):
    by_key = {}
    for page_items in popo_injections_by_page.values():
        for item in page_items:
            for key in item.get("keys") or []:
                by_key.setdefault(key, []).append(item)
    return by_key


def matching_popo_item_for_header(text_heading_keys, page, popo_items_by_key, emitted_popo_headings, max_page_distance=3):
    candidates = []
    for key in text_heading_keys:
        for item in popo_items_by_key.get(key, []):
            if popo_item_key(item) in emitted_popo_headings:
                continue
            distance = popo_item_page_distance(item, page)
            if distance <= max_page_distance:
                candidates.append((distance, item.get("level", 9), item.get("order", 10**9), item))
    if not candidates:
        return None
    return sorted(candidates, key=lambda row: (row[0], row[1], row[2]))[0][3]


def matches_recently_emitted_popo_heading(
    text_heading_keys,
    page,
    popo_items_by_key,
    emitted_popo_headings,
    max_page_distance=1,
):
    if page is None:
        return False
    for key in text_heading_keys:
        for item in popo_items_by_key.get(key, []):
            if popo_item_key(item) not in emitted_popo_headings:
                continue
            if popo_item_page_distance(item, page) <= max_page_distance:
                return True
    return False


def collect_toc_chapters(blocks, last_front_page):
    chapters = []
    seen = set()
    for block in blocks:
        page = block.get("page_idx")
        if page is None or page > last_front_page:
            continue
        text = clean_text(block.get("text"))
        match = re.match(r"^(Chapter\s+[A-Z]?\d+)\s+(.+?)\s*$", text, re.I)
        if not match:
            continue
        title = re.sub(r"\s+\d{1,4}$", "", match.group(2)).strip()
        title = re.sub(r"(?:\.{2,}|…+)\s*\d{1,4}$", "", title).strip()
        if not title:
            continue
        label = f"{match.group(1)} {title}"
        key = normalize_heading_key(title)
        if key and key not in seen:
            chapters.append({"label": label, "title": title, "key": key})
            seen.add(key)
    return chapters


def page_header_texts(page_blocks):
    texts = []
    for block in page_blocks:
        if block.get("type") != "header":
            continue
        text = clean_text(block.get("text"))
        if text and not re.fullmatch(r"\d+", text) and text != "[No text]":
            texts.append(text)
    return texts


def canonical_chapter_for_page(page_blocks, toc_chapters):
    if not toc_chapters:
        return None
    for header in page_header_texts(page_blocks):
        header_key = normalize_heading_key(header)
        if not header_key:
            continue
        for chapter in toc_chapters:
            if header_key == chapter["key"]:
                return chapter["label"]
    return None


def is_canonical_chapter_heading(text, toc_chapters):
    if not toc_chapters:
        return False
    key = normalize_heading_key(text)
    label_key = normalize_heading_key(re.sub(r"^Chapter\s+[A-Z]?\d+\s+", "", clean_text(text), flags=re.I))
    return any(key == chapter["key"] or label_key == chapter["key"] for chapter in toc_chapters)


def normalized_page_signature(texts):
    joined = " ".join(clean_text(text) for text in texts if clean_text(text))
    joined = re.sub(r"\b(page|p)\.?\s*\d+\b", "", joined, flags=re.I)
    joined = re.sub(r"\d+", "", joined)
    joined = re.sub(r"[^A-Za-z\u4e00-\u9fff]+", " ", joined).strip().lower()
    words = joined.split()
    return " ".join(words[:16])


def title_like_front_signatures(pages, last_front_page):
    signatures = set()
    for page, texts in pages.items():
        if page > last_front_page:
            continue
        signature = normalized_page_signature(texts)
        if len(signature) >= 12:
            signatures.add(signature)
    return signatures


def signature_matches_front(signature, front_signatures):
    if not signature:
        return False
    signature_tokens = set(signature.split())
    for front in front_signatures:
        if signature == front or signature.startswith(front) or front.startswith(signature):
            return True
        front_tokens = set(front.split())
        if not signature_tokens or not front_tokens:
            continue
        overlap = signature_tokens & front_tokens
        smaller = min(len(signature_tokens), len(front_tokens))
        if len(overlap) >= 5 and len(overlap) / smaller >= 0.55:
            return True
    return False


def infer_body_range(blocks, *, preserve_answer_sections=False):
    pages = page_text_map(blocks)
    blocks_by_page = page_blocks_map(blocks)
    sorted_pages = sorted(pages)
    first_page = sorted_pages[0] if sorted_pages else 0
    front_scan_limit = first_page + 30
    front_pages = [
        page for page in sorted_pages
        if page <= front_scan_limit
        if any(FRONT_MATTER_RE.search(text) or text.strip().lower() == "contents" for text in pages[page])
        or page_is_toc_like(pages[page])
    ]
    last_front_page = max(front_pages) if front_pages else -1
    first_text_page = None
    for page in sorted_pages:
        if page <= last_front_page:
            continue
        if page <= front_scan_limit and page_is_toc_like(pages[page]):
            last_front_page = max(last_front_page, page)
            continue
        if any(BODY_START_RE.search(text) for text in pages[page]) or page_is_chapter_opener(blocks_by_page.get(page, [])):
            first_text_page = page
            break
    if first_text_page is None:
        for page in sorted_pages:
            if page > last_front_page and page_has_real_text(blocks, page):
                first_text_page = page
                break
    if first_text_page is None:
        first_text_page = sorted_pages[0] if sorted_pages else 0

    start_page = first_text_page
    probe = first_text_page - 1
    while probe >= 0 and page_has_image(blocks, probe) and not page_has_real_text(blocks, probe):
        start_page = probe
        probe -= 1

    end_page = max((block.get("page_idx") for block in blocks if block.get("page_idx") is not None), default=start_page)
    for page in sorted_pages:
        if page <= start_page:
            continue
        back_matter = [text for text in pages[page] if is_back_matter_text(text)]
        if back_matter and preserve_answer_sections and all(re.match(r"^(Answers|ANSWERS|答案)\b", clean_text(text)) for text in back_matter):
            continue
        if back_matter:
            end_page = page - 1
            break
    front_signatures = title_like_front_signatures(pages, last_front_page)
    while end_page > start_page:
        signature = normalized_page_signature(pages.get(end_page, []))
        if signature_matches_front(signature, front_signatures):
            end_page -= 1
            continue
        break
    return start_page, end_page, first_text_page, last_front_page


def apply_popo_body_bounds(start_page, end_page, first_body_text_page, popo_outline, *, scope, explicit_start=None, explicit_end=None):
    if scope != "body" or not popo_outline.get("available"):
        return start_page, end_page, first_body_text_page
    popo_start = popo_outline.get("body_start_page_idx")
    popo_end = popo_outline.get("body_end_page_idx")
    if explicit_start is None and isinstance(popo_start, int):
        start_page = min(start_page, max(0, popo_start))
        first_body_text_page = start_page
    if explicit_end is None and isinstance(popo_end, int):
        end_page = max(end_page, popo_end)
    elif explicit_end is None:
        outline_starts = [
            entry.get("start_page_idx")
            for entry in popo_outline.get("outline") or []
            if isinstance(entry.get("start_page_idx"), int)
        ]
        if outline_starts:
            end_page = max(end_page, max(outline_starts))
    return start_page, end_page, first_body_text_page


def unit_by_page(blocks):
    candidates_by_page = {}
    standalone_candidates_by_page = {}
    page_lines = {}
    for source_order, block in enumerate(blocks):
        source_ref = block_ref(block, source_order)
        btype = str(block.get("type", ""))
        if btype not in {"footer", "header", "text"}:
            continue
        page = block.get("page_idx")
        if page is None:
            continue
        text = block_text(block)
        if not text:
            continue
        page_lines.setdefault(page, []).append(text)
        unit_heading_number = unit_top_heading_number(text) if (block.get("text_level") is not None or btype == "header") else None
        if unit_heading_number is None and btype in {"header", "footer"}:
            header_unit_match = re.search(r"\bUnit\s+(\d{1,2})\b", text, re.I)
            if header_unit_match:
                unit_heading_number = header_unit_match.group(1).zfill(2)
        if unit_heading_number:
            candidates_by_page.setdefault(page, []).append(unit_heading_number)

    for page, lines in page_lines.items():
        for idx, text in enumerate(lines[:-1]):
            if re.fullmatch(r"Unit", text, re.I) and re.fullmatch(r"\d{1,2}", lines[idx + 1].strip()):
                candidates_by_page.setdefault(page, []).append(lines[idx + 1].strip().zfill(2))
            if re.fullmatch(r"\d{1,2}", text) and is_standalone_unit_title(lines[idx + 1]):
                standalone_candidates_by_page.setdefault(page, []).append(text.zfill(2))
            if re.fullmatch(r"\d{1,2}", lines[idx + 1].strip()) and is_standalone_unit_title(text):
                standalone_candidates_by_page.setdefault(page, []).append(lines[idx + 1].strip().zfill(2))
        page_numbers = [text.zfill(2) for text in lines if re.fullmatch(r"\d{1,2}", text)]
        page_titles = [text for text in lines if is_standalone_unit_title(text)]
        if page_numbers and page_titles:
            for number in page_numbers:
                standalone_candidates_by_page.setdefault(page, []).append(number)

    if candidates_by_page:
        first_explicit_unit_page = min(candidates_by_page)
        for page, candidates in standalone_candidates_by_page.items():
            if page >= first_explicit_unit_page:
                candidates_by_page.setdefault(page, candidates)

    units = {}
    current = None
    for page in sorted(candidates_by_page):
        page_candidates = candidates_by_page[page]
        candidate = page_candidates[0]
        if current is None:
            units[page] = candidate
            current = int(candidate)
            continue
        value = int(candidate)
        if value == current or value == current + 1:
            units[page] = candidate
            current = value
    return units


def safe_copy(src_root, rel_path, image_out, copy_images=True):
    if not rel_path:
        return None
    rel_path = rel_path.lstrip("/")
    if not rel_path.startswith("images/"):
        rel_path = f"images/{Path(rel_path).name}"
    src = src_root / rel_path
    if not src.exists():
        return rel_path
    if not copy_images:
        return rel_path
    image_out.mkdir(parents=True, exist_ok=True)
    dest = image_out / Path(rel_path).name
    if not dest.exists():
        shutil.copy2(src, dest)
    return f"images/{dest.name}"


def heading_level(text):
    t = text.strip()
    if unit_top_heading_number(t):
        return 1
    if re.match(r"^第\s*\d+\s*章\b", t):
        return 1
    if re.match(r"^Chapter\s+[A-Z]?\d+\.\s*Topic\s+\d+\b", t, re.I):
        return 2
    if re.match(r"^Exercise\s+[A-Z]?\d+\.\d+\b", t, re.I):
        return 2
    if re.match(r"^Table\s+\d+(?:\.\d+)?\b", t, re.I):
        return 3
    if re.match(r"^Chapter\s+[A-Z]?\d+\b", t, re.I):
        return 1
    if re.match(r"^\d+\.\d+\b", t):
        return 2
    if re.match(r"^(READING|GRAMMAR|SUMMARY OF UNIT|REVIEW|FROM GRAMMAR TO WRITING|APPENDIX|GLOSSARY|INDEX)\b", t, re.I):
        return 2
    if re.match(r"^(EXERCISE|COMPREHENSION|THINK ABOUT IT|ABOUT YOU|GRAMMAR IN USE|Note:|Notes:|Pronunciation Note:)", t, re.I):
        return 3
    return 2


def list_text(block):
    items = block.get("list_items")
    if isinstance(items, list):
        return "\n".join(clean_text(item) for item in items if clean_text(item))
    return clean_text(block.get("text"))


def render_inline(text):
    rendered = html.escape(text, quote=False)
    for tag in ("sup", "sub", "strong", "em", "b", "i"):
        rendered = rendered.replace(f"&lt;{tag}&gt;", f"<{tag}>")
        rendered = rendered.replace(f"&lt;/{tag}&gt;", f"</{tag}>")
    return rendered


def markdown_to_html(markdown, title="PDF Clean Markdown Preview"):
    html_lines = [
        "<!doctype html>",
        "<html lang=\"en\">",
        "<head>",
        "<meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
        f"<title>{html.escape(title)}</title>",
        "<script>",
        "window.MathJax={tex:{inlineMath:[['$','$'],['\\\\(','\\\\)']],displayMath:[['$$','$$'],['\\\\[','\\\\]']],processEscapes:true},options:{skipHtmlTags:['script','noscript','style','textarea','pre','code']}};",
        "</script>",
        "<script defer src=\"https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js\"></script>",
        "<style>",
        "body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;line-height:1.55;margin:0;background:#f7f7f5;color:#1f2933}",
        "main{max-width:920px;margin:0 auto;padding:32px 20px 72px;background:#fff}",
        "h1{font-size:2rem;margin:2.2rem 0 1rem;border-bottom:2px solid #222;padding-bottom:.35rem}",
        "h2{font-size:1.45rem;margin:1.8rem 0 .8rem}h3{font-size:1.15rem;margin:1.35rem 0 .5rem}",
        "p{margin:.65rem 0}.page{font-size:.78rem;color:#6b7280;margin:1.2rem 0 .4rem}",
        "img{max-width:100%;height:auto;display:block;margin:1rem 0}.caption{color:#4b5563;font-size:.92rem;margin-top:-.6rem}",
        ".table-wrap{overflow-x:auto;margin:1rem 0}table{border-collapse:collapse;width:100%;font-size:.92rem}td,th{border:1px solid #d0d7de;padding:.35rem .5rem;vertical-align:top}",
        "code{background:#f2f4f7;padding:.1rem .25rem;border-radius:3px}",
        "</style>",
        "</head><body><main>",
    ]
    in_paragraph = False

    def close_paragraph():
        nonlocal in_paragraph
        if in_paragraph:
            html_lines.append("</p>")
            in_paragraph = False

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            close_paragraph()
            continue
        page_match = re.match(r"<!--\s*page_idx:\s*([^>]+?)\s*-->", line)
        if page_match:
            close_paragraph()
            html_lines.append(f"<div class=\"page\">page_idx: {html.escape(page_match.group(1))}</div>")
            continue
        heading_match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading_match:
            close_paragraph()
            level = len(heading_match.group(1))
            html_lines.append(f"<h{level}>{render_inline(heading_match.group(2))}</h{level}>")
            continue
        image_match = re.match(r"^!\[(.*)\]\(([^)\r\n]+)\)$", line)
        if image_match:
            close_paragraph()
            alt, src = image_match.groups()
            html_lines.append(f"<img src=\"{html.escape(src, quote=True)}\" alt=\"{html.escape(alt, quote=True)}\">")
            continue
        if line.startswith("<table"):
            close_paragraph()
            html_lines.append(f"<div class=\"table-wrap\">{line}</div>")
            continue
        caption_match = re.match(r"^\*(.+)\*$", line)
        if caption_match:
            close_paragraph()
            html_lines.append(f"<p class=\"caption\">{render_inline(caption_match.group(1))}</p>")
            continue
        if not in_paragraph:
            html_lines.append("<p>")
            in_paragraph = True
        else:
            html_lines.append("<br>")
        html_lines.append(render_inline(line))

    close_paragraph()
    html_lines.append("</main></body></html>")
    return "\n".join(html_lines) + "\n"


def mark_empty_leaf_heading_chunks(markdown):
    lines = markdown.splitlines()
    headings = []
    for line_no, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if match:
            headings.append({"index": line_no, "level": len(match.group(1))})
    if not headings:
        return markdown, 0
    insert_after = set()
    for idx, heading in enumerate(headings):
        next_heading = headings[idx + 1] if idx + 1 < len(headings) else None
        is_parent = bool(next_heading and next_heading["level"] > heading["level"])
        if is_parent:
            continue
        start = heading["index"] + 1
        end = next_heading["index"] if next_heading else len(lines)
        meaningful = []
        for raw in lines[start:end]:
            text = raw.strip()
            if not text:
                continue
            if text.startswith("<!-- page_idx:"):
                continue
            if re.match(r"^#{1,6}\s+", text):
                continue
            meaningful.append(text)
        if not meaningful:
            insert_after.add(heading["index"])
    if not insert_after:
        return markdown, 0
    out = []
    marker = "<!-- source_empty_chunk: no OCR body content detected under this source-visible heading; preserve as blank/response-area section for review -->"
    for idx, line in enumerate(lines):
        out.append(line)
        if idx in insert_after:
            out.extend(["", marker])
    return "\n".join(out).strip() + "\n", len(insert_after)


BARE_NUMERIC_CODE_HEADING_RE = re.compile(r"^(#{2,3})\s+(\d{1,2}\s*[-–]\s*\d{1,2}[A-Za-z]?)\s*$")


def demote_bare_numeric_code_headings(markdown):
    """Keep bare workbook layout codes as provenance, not directory headings."""
    lines = markdown.splitlines()
    out = []
    demotions = []
    for line_no, line in enumerate(lines, start=1):
        match = BARE_NUMERIC_CODE_HEADING_RE.match(line.strip())
        if not match:
            out.append(line)
            continue
        title = re.sub(r"\s+", "", match.group(2).replace("–", "-"))
        out.append(f"<!-- outline_code_demoted: {title} -->")
        demotions.append({
            "line": line_no,
            "level": len(match.group(1)),
            "title": title,
            "reason": "bare_numeric_layout_code_not_semantic_heading",
        })
    report = {
        "schema": "luceon-outline-code-demotion/v1",
        "changed": bool(demotions),
        "demotion_count": len(demotions),
        "demotions": demotions,
    }
    return "\n".join(out).strip() + "\n", report


def repair_leading_orphan_child_headings(markdown):
    """Promote the first body heading to a root when it appears before any parent."""
    lines = markdown.splitlines()
    seen_root = False
    repairs = []
    out = []
    for line_no, line in enumerate(lines, start=1):
        match = re.match(r"^(#{1,6})(\s+.+?)\s*$", line)
        if not match:
            out.append(line)
            continue
        level = len(match.group(1))
        title = match.group(2).strip()
        if level == 1:
            seen_root = True
            out.append(line)
            continue
        if not seen_root:
            repaired = f"# {title}"
            out.append(repaired)
            seen_root = True
            repairs.append({
                "line": line_no,
                "before": line,
                "after": repaired,
                "reason": "leading_child_heading_without_parent_promoted_to_root",
            })
            continue
        out.append(line)
    report = {
        "schema": "luceon-leading-orphan-heading-repair/v1",
        "changed": bool(repairs),
        "repair_count": len(repairs),
        "repairs": repairs,
    }
    return "\n".join(out).strip() + "\n", report


def repair_heading_level_jumps(markdown):
    """Normalize heading jumps such as H1 -> H3 into adjacent levels."""
    lines = markdown.splitlines()
    previous_level = 0
    repairs = []
    out = []
    for line_no, line in enumerate(lines, start=1):
        match = re.match(r"^(#{1,6})(\s+.+?)\s*$", line)
        if not match:
            out.append(line)
            continue
        level = len(match.group(1))
        title = match.group(2).strip()
        repaired_level = level
        if previous_level and level > previous_level + 1:
            repaired_level = previous_level + 1
        if not previous_level and level > 1:
            repaired_level = 1
        if repaired_level != level:
            repaired = f"{'#' * repaired_level} {title}"
            out.append(repaired)
            repairs.append({
                "line": line_no,
                "title": title,
                "from": level,
                "to": repaired_level,
                "before": line,
                "after": repaired,
                "reason": "heading_level_jump_normalized",
            })
            previous_level = repaired_level
            continue
        out.append(line)
        previous_level = level
    report = {
        "schema": "luceon-heading-level-jump-repair/v1",
        "changed": bool(repairs),
        "repair_count": len(repairs),
        "repairs": repairs,
    }
    return "\n".join(out).strip() + "\n", report


def markdown_raw_units(markdown):
    lines = markdown.splitlines()
    headings = []
    page_by_line = {}
    current_page = None
    for idx, line in enumerate(lines, start=1):
        page_match = re.match(r"<!--\s*page_idx:\s*(\d+)\s*-->", line)
        if page_match:
            current_page = int(page_match.group(1))
        page_by_line[idx] = current_page
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            headings.append(
                {
                    "line": idx,
                    "level": len(match.group(1)),
                    "title": clean_text(match.group(2)),
                    "page": current_page,
                }
            )
    units = []
    for pos, heading in enumerate(headings):
        next_same_or_higher = len(lines) + 1
        for later in headings[pos + 1:]:
            if later["level"] <= heading["level"]:
                next_same_or_higher = later["line"]
                break
        child_headings = [
            later for later in headings[pos + 1:]
            if later["line"] < next_same_or_higher and later["level"] > heading["level"]
        ]
        body_start = heading["line"] + 1
        body_end = next_same_or_higher - 1
        body_lines = [
            line for line in lines[body_start - 1:body_end]
            if clean_text(line) and not re.match(r"^#{1,6}\s+", line)
        ]
        chunk_pages = [
            page_by_line.get(line_no)
            for line_no in range(heading["line"], body_end + 1)
            if page_by_line.get(line_no) is not None
        ]
        if not chunk_pages and heading.get("page") is None:
            for line_no in range(heading["line"] + 1, min(len(lines), heading["line"] + 8) + 1):
                if page_by_line.get(line_no) is not None:
                    chunk_pages.append(page_by_line[line_no])
                    break
        page_start = min(chunk_pages) if chunk_pages else heading.get("page")
        page_end = max(chunk_pages) if chunk_pages else heading.get("page")
        units.append(
            {
                "schema": "luceon-raw-unit/v1",
                "unit_id": f"unit-{pos + 1:04d}",
                "order": pos,
                "title": heading["title"],
                "level": heading["level"],
                "heading_line": heading["line"],
                "start_line": body_start,
                "end_line": body_end,
                "page_start": page_start,
                "page_end": page_end,
                "is_leaf": not child_headings,
                "child_heading_count": len(child_headings),
                "body_line_count": len(body_lines),
                "image_count": sum(1 for line in body_lines if re.search(r"!\[[^\]]*\]\([^)]+\)", line)),
                "table_count": sum(1 for line in body_lines if "<table" in line.lower() or re.match(r"^\s*\|.*\|\s*$", line)),
                "formula_signal_count": sum(1 for line in body_lines if "$" in line or "\\(" in line or "\\[" in line),
                "source_empty_chunk": any("source_empty_chunk" in line for line in body_lines),
                "empty_leaf": not child_headings and not body_lines,
            }
        )
    return units


def block_ref(block, order):
    for key in ("block_id", "id", "uuid"):
        if block.get(key):
            return str(block.get(key))
    return f"content-list-{order:06d}"


def block_bbox(block):
    for key in ("bbox", "poly", "box"):
        value = block.get(key)
        if isinstance(value, list) and value:
            return value
    return None


def normalize_image_ref(value):
    value = str(value or "").strip().strip("\"'")
    if not value:
        return ""
    value = value.split("#", 1)[0].split("?", 1)[0].lstrip("/")
    if not value.startswith("images/"):
        value = f"images/{Path(value).name}"
    return value


def source_image_refs_from_blocks(blocks, start_page=None, end_page=None):
    refs = set()
    for block in blocks:
        page = block.get("page_idx")
        if start_page is not None and page is not None and page < start_page:
            continue
        if end_page is not None and page is not None and page > end_page:
            continue
        for key in ("img_path", "image_path"):
            ref = normalize_image_ref(block.get(key))
            if ref:
                refs.add(ref)
        body = str(block.get("table_body") or "")
        for img_src in re.findall(r"<img[^>]+src=[\"']([^\"']+)[\"']", body, re.I):
            ref = normalize_image_ref(img_src)
            if ref:
                refs.add(ref)
    return refs


def markdown_image_refs(markdown):
    refs = set()
    for md_ref in re.findall(r"!\[.*?\]\(([^)\r\n]+)\)", markdown, re.S):
        ref = normalize_image_ref(md_ref)
        if ref:
            refs.add(ref)
    for html_ref in re.findall(r"<img[^>]+src=[\"']([^\"']+)[\"']", markdown, re.I):
        ref = normalize_image_ref(html_ref)
        if ref:
            refs.add(ref)
    return refs


def normalize_markdown_image_alt(value):
    return re.sub(r"\s+", " ", str(value or "image")).strip()


def eligible_block_for_assignment(block, start_page, end_page):
    page = block.get("page_idx")
    if page is None or page < start_page or page > end_page:
        return False, "out_of_scope"
    btype = str(block.get("type", ""))
    if is_noise_block(block):
        return False, "noise"
    if btype in IMAGE_BACKED_VISUAL_TYPES | {"table"}:
        return True, ""
    if block_text(block):
        return True, ""
    return False, "empty"


def unit_for_page(raw_units, page):
    if page is None:
        return None
    ranged = [
        unit for unit in raw_units
        if unit.get("page_start") is not None
        and unit.get("page_end") is not None
        and unit["page_start"] <= page <= unit["page_end"]
    ]
    if ranged:
        return sorted(ranged, key=lambda unit: (unit.get("level") or 0, unit.get("order") or 0), reverse=True)[0]
    before = [
        unit for unit in raw_units
        if unit.get("page_start") is not None and unit["page_start"] <= page
    ]
    if before:
        return sorted(before, key=lambda unit: (unit.get("page_start") or -1, unit.get("order") or 0))[-1]
    after = [
        unit for unit in raw_units
        if unit.get("page_start") is not None and unit["page_start"] > page
    ]
    if after:
        return sorted(after, key=lambda unit: (unit.get("page_start") or 10**9, unit.get("order") or 0))[0]
    return None


def heading_title_match_score(title, text):
    title_key = normalize_heading_key(title)
    text_key = normalize_heading_key(text)
    if not title_key or not text_key:
        return 0
    if title_key == text_key:
        return 100
    title_norm = normalized_heading_text(title)
    text_norm = normalized_heading_text(text)
    if title_norm and text_norm and title_norm == text_norm:
        return 95
    if len(title_key) >= 6 and title_key in text_key:
        return 50
    if len(text_key) >= 6 and text_key in title_key:
        return 45
    if title_norm and text_norm and len(title_norm) >= 6 and title_norm in text_norm:
        return 40
    return 0


def compute_unit_start_orders(raw_units, eligible_rows):
    starts = {}
    last_start = 0
    for unit in raw_units:
        page_start = unit.get("page_start")
        page_end = unit.get("page_end")
        title = unit.get("title") or ""
        candidates = []
        for row in eligible_rows:
            page = row.get("page_idx")
            if page is None:
                continue
            if page_start is not None and page < page_start:
                continue
            if page_end is not None and page > page_end:
                continue
            if row["source_order"] < last_start:
                continue
            score = heading_title_match_score(title, row.get("text_preview") or "")
            if score:
                scored = dict(row)
                scored["_heading_match_score"] = score
                candidates.append(scored)
        if candidates:
            best = sorted(candidates, key=lambda row: (-row["_heading_match_score"], row["source_order"]))[0]
            starts[unit.get("unit_id")] = best["source_order"]
            last_start = best["source_order"]
            continue
        page_candidates = [
            row for row in eligible_rows
            if row["source_order"] >= last_start
            and page_start is not None
            and row.get("page_idx") is not None
            and row["page_idx"] >= page_start
            and (page_end is None or row["page_idx"] <= page_end)
        ]
        if page_candidates:
            starts[unit.get("unit_id")] = page_candidates[0]["source_order"]
            last_start = page_candidates[0]["source_order"]
    return starts


def unit_for_source_order(raw_units, unit_start_orders, source_order):
    active = []
    for unit in raw_units:
        start = unit_start_orders.get(unit.get("unit_id"))
        if start is None or start > source_order:
            continue
        active.append((start, unit.get("order") or 0, unit))
    if not active:
        return None
    return sorted(active, key=lambda item: (item[0], item[1]))[-1][2]


def unit_covers_page(unit, page):
    if not unit or page is None:
        return True
    page_start = unit.get("page_start")
    page_end = unit.get("page_end")
    if page_start is not None and page < page_start:
        return False
    if page_end is not None and page > page_end:
        return False
    return True


SOURCE_LINEAGE_START_RE = re.compile(r"<!--\s*luceon-source-start:\s*([^\s>]+)\s*-->")
SOURCE_LINEAGE_END_RE = re.compile(r"<!--\s*luceon-source-end:\s*([^\s>]+)\s*-->")


def emit_source_bound_lines(lines, source_ref, rendered_lines):
    """Emit source-owned Markdown with temporary markers for exact output references."""
    lines.append(f"<!-- luceon-source-start: {source_ref} -->")
    lines.extend(rendered_lines)
    lines.append(f"<!-- luceon-source-end: {source_ref} -->")


def extract_direct_output_references(markdown):
    """Remove temporary markers and return exact final clean line references by source block."""
    rows = []
    active_ref = ""
    for raw in markdown.splitlines():
        start = SOURCE_LINEAGE_START_RE.fullmatch(raw.strip())
        if start:
            if active_ref:
                raise ValueError("nested Luceon source lineage markers are not allowed")
            active_ref = start.group(1)
            continue
        end = SOURCE_LINEAGE_END_RE.fullmatch(raw.strip())
        if end:
            if not active_ref or end.group(1) != active_ref:
                raise ValueError("unbalanced Luceon source lineage markers")
            active_ref = ""
            continue
        rows.append((raw, active_ref))
    if active_ref:
        raise ValueError("unclosed Luceon source lineage marker")
    while rows and not rows[0][0].strip():
        rows.pop(0)
    while rows and not rows[-1][0].strip():
        rows.pop()
    output_refs = {}
    for line_number, (raw, source_ref) in enumerate(rows, 1):
        if not source_ref or not raw.strip() or re.fullmatch(r"<!--.*?-->", raw.strip()):
            continue
        output_refs.setdefault(source_ref, []).append(f"clean:clean-line-{line_number:06d}")
    return "\n".join(raw for raw, _source_ref in rows) + "\n", output_refs


def build_block_assignment_reports(
    blocks,
    raw_units,
    clean_md,
    start_page,
    end_page,
    copied,
    missing,
    direct_output_refs=None,
):
    assignments = []
    unassigned = []
    eligible_rows = []
    unit_stats = {
        unit.get("unit_id"): {
            "block_count": 0,
            "text_block_count": 0,
            "image_block_count": 0,
            "table_block_count": 0,
            "formula_block_count": 0,
            "pages": set(),
            "block_ids": [],
        }
        for unit in raw_units
    }
    eligible_count = 0
    skipped_reasons = Counter()
    for order, block in enumerate(blocks):
        eligible, reason = eligible_block_for_assignment(block, start_page, end_page)
        if not eligible:
            skipped_reasons[reason] += 1
            continue
        eligible_count += 1
        page = block.get("page_idx")
        ref = block_ref(block, order)
        btype = str(block.get("type", ""))
        text = block_text(block)
        source_locator = {
            "block_id": ref,
            "source_order": order,
            "page_idx": page,
            "bbox": block_bbox(block),
        }
        source_locator["locator_sha256"] = hashlib.sha256(
            json.dumps(source_locator, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        row = {
            "schema": "luceon-raw-block-assignment/v1",
            "block_ref": ref,
            "source_order": order,
            "type": btype,
            "page_idx": page,
            "bbox": block_bbox(block),
            "text_preview": text[:240],
            "image_ref": normalize_image_ref(block.get("img_path") or block.get("image_path")),
            "formula_signal": "$" in text or "\\(" in text or "\\[" in text,
            "source_locator": source_locator,
            "output_refs": list((direct_output_refs or {}).get(ref) or []),
        }
        eligible_rows.append(row)

    unit_start_orders = compute_unit_start_orders(raw_units, eligible_rows)

    for row in eligible_rows:
        page = row.get("page_idx")
        ref = row["block_ref"]
        btype = row["type"]
        unit = unit_for_source_order(raw_units, unit_start_orders, row["source_order"])
        assignment_method = "source_order_heading_interval_flood" if unit_start_orders else "page_interval_flood"
        if unit and not unit_covers_page(unit, page):
            page_unit = unit_for_page(raw_units, page)
            if page_unit:
                unit = page_unit
                assignment_method = "page_boundary_override_after_source_order"
        if not unit:
            unit = unit_for_page(raw_units, page)
            assignment_method = "page_interval_flood"
        if unit:
            row.update(
                {
                    "unit_id": unit.get("unit_id"),
                    "unit_order": unit.get("order"),
                    "unit_title": unit.get("title"),
                    "unit_level": unit.get("level"),
                    "assignment_method": assignment_method,
                }
            )
            assignments.append(row)
            stats = unit_stats.get(unit.get("unit_id"))
            if stats is not None:
                stats["block_count"] += 1
                stats["block_ids"].append(ref)
                if page is not None:
                    stats["pages"].add(page)
                if btype in IMAGE_BACKED_VISUAL_TYPES:
                    stats["image_block_count"] += 1
                elif btype == "table":
                    stats["table_block_count"] += 1
                else:
                    stats["text_block_count"] += 1
                if row["formula_signal"]:
                    stats["formula_block_count"] += 1
        else:
            row["unassigned_reason"] = "no_directory_unit_for_page"
            unassigned.append(row)

    for unit in raw_units:
        stats = unit_stats.get(unit.get("unit_id")) or {}
        pages = sorted(stats.get("pages") or [])
        unit["block_count"] = stats.get("block_count", 0)
        unit["text_block_count"] = stats.get("text_block_count", 0)
        unit["image_block_count"] = stats.get("image_block_count", 0)
        unit["table_block_count"] = stats.get("table_block_count", 0)
        unit["formula_block_count"] = stats.get("formula_block_count", 0)
        unit["assigned_pages"] = pages
        unit["block_ids"] = stats.get("block_ids", [])[:200]

    source_refs = source_image_refs_from_blocks(blocks, start_page=start_page, end_page=end_page)
    markdown_refs = markdown_image_refs(clean_md)
    copied_refs = {normalize_image_ref(ref) for ref in copied if normalize_image_ref(ref)}
    missing_refs = {normalize_image_ref(ref) for ref in missing if normalize_image_ref(ref)}
    image_closure_report = {
        "schema": "luceon-raw-image-closure-report/v1",
        "source_image_ref_count": len(source_refs),
        "markdown_image_ref_count": len(markdown_refs),
        "copied_image_count": len(copied_refs),
        "missing_image_count": len(missing_refs),
        "markdown_refs_missing_from_source": sorted(markdown_refs - source_refs),
        "source_refs_not_in_markdown": sorted(source_refs - markdown_refs),
        "markdown_refs_not_copied": sorted(markdown_refs - copied_refs),
        "missing_images": sorted(missing_refs),
    }
    assignment_report = {
        "schema": "luceon-raw-outline-apply-report/v1",
        "method": "page_interval_flood_from_markdown_outline",
        "included_page_range": {"start_page": start_page, "end_page": end_page},
        "unit_count": len(raw_units),
        "eligible_block_count": eligible_count,
        "assigned_block_count": len(assignments),
        "unassigned_block_count": len(unassigned),
        "skipped_block_reasons": dict(sorted(skipped_reasons.items())),
        "leaf_units_without_blocks": [
            unit.get("unit_id")
            for unit in raw_units
            if unit.get("is_leaf") and not unit.get("block_count")
        ],
        "container_units_without_direct_blocks": [
            unit.get("unit_id")
            for unit in raw_units
            if not unit.get("is_leaf") and not unit.get("block_count")
        ],
        "assignment_limits": {
            "block_ids_per_unit_in_raw_units": 200,
            "text_preview_chars": 240,
        },
    }
    return assignments, unassigned, assignment_report, image_closure_report


def write_local_jsonl(path, rows):
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def recover_outline_free_table_headings(clean_md, blocks, start_page):
    """Recover source-visible hierarchy for a table document with no headings."""
    report = {
        "schema": "luceon-outline-free-table-heading-recovery/v1",
        "changed": False,
        "reason": "not_applicable",
        "root_title": "",
        "section_title": "",
    }
    if re.search(r"^#{1,6}\s+", clean_md, re.M):
        report["reason"] = "headings_already_present"
        return clean_md, report
    if not any(str(block.get("type") or "") == "table" for block in blocks):
        report["reason"] = "table_evidence_missing"
        return clean_md, report

    header_rows = []
    header_counts = Counter()
    for order, block in enumerate(blocks):
        if str(block.get("type") or "") != "header":
            continue
        title = clean_text(block.get("text") or block.get("content"))
        normalized = normalize_heading_key(title)
        if not normalized:
            continue
        header_counts[normalized] += 1
        header_rows.append((order, block, title, normalized))
    root = next(
        (
            (order, block, title, normalized)
            for order, block, title, normalized in header_rows
            if block.get("page_idx") == start_page
            and header_counts[normalized] == 1
            and 8 <= len(title) <= 180
            and len(re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?|[\u4e00-\u9fff]", title)) >= 3
        ),
        None,
    )
    if root is None:
        report["reason"] = "unique_first_page_title_missing"
        return clean_md, report

    root_title = root[2]
    section = next(
        (
            match.group(1).strip()
            for match in re.finditer(r"^\*\*(.+?)\*\*\s*$", clean_md, re.M)
            if normalize_heading_key(match.group(1)) != normalize_heading_key(root_title)
            and len(re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?|[\u4e00-\u9fff]", match.group(1))) >= 2
        ),
        "",
    )
    if not section:
        report["reason"] = "source_table_caption_missing"
        return clean_md, report

    marker = re.search(rf"^<!--\s*page_idx:\s*{int(start_page)}\s*-->\s*$", clean_md, re.M)
    if marker is None:
        report["reason"] = "first_page_marker_missing"
        return clean_md, report
    insertion = marker.end()
    recovered = clean_md[:insertion] + f"\n\n# {root_title}" + clean_md[insertion:]
    recovered = re.sub(
        rf"^\*\*{re.escape(section)}\*\*\s*$",
        f"## {section}",
        recovered,
        count=1,
        flags=re.M,
    )
    report.update(
        {
            "changed": True,
            "reason": "unique_first_page_title_plus_source_table_caption",
            "root_title": root_title,
            "root_page_idx": root[1].get("page_idx"),
            "root_source_order": root[0],
            "section_title": section,
        }
    )
    return recovered, report


def main():
    parser = argparse.ArgumentParser(description="Create a conservative clean Markdown draft from MinerU flat content.")
    parser.add_argument("root", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--scope", choices=["body", "all"], default="body", help="Use body to exclude front/back matter; use all to keep every page.")
    parser.add_argument("--start-page", type=int, help="Override inferred first included page_idx.")
    parser.add_argument("--end-page", type=int, help="Override inferred last included page_idx.")
    parser.add_argument("--link-images", action="store_true", help="Create output images as a symlink to the source images folder for fast local HTML preview.")
    parser.add_argument("--no-copy-images", action="store_true", help="Keep image links but do not copy image files; useful for fast dry runs on cloud-synced folders.")
    parser.add_argument(
        "--canonical-content-only",
        action="store_true",
        help="Preserve source content without applying or synthesizing an outline; the independent outline stage owns hierarchy.",
    )
    parser.add_argument("--with-llm-outline", action="store_true", help="Review the traceable outline decision with DeepSeek when DEEPSEEK_API_KEY is available.")
    parser.add_argument("--with-visual-outline", action="store_true", help="Verify needs-visual outline candidates with DashScope/Qwen vision or HY Vision when a supported vision API key is available.")
    parser.add_argument("--llm-outline-max-risk-candidates", type=int, default=80)
    parser.add_argument("--visual-outline-max-candidates", type=int, default=80)
    args = parser.parse_args()

    root = args.root.expanduser().resolve()
    out_dir = args.out_dir.expanduser().resolve()
    images_out = out_dir / "images"
    copy_images = not args.no_copy_images
    link_images = args.link_images and copy_images
    out_dir.mkdir(parents=True, exist_ok=True)
    if link_images:
        source_images = root / "images"
        if source_images.exists() and not images_out.exists():
            images_out.symlink_to(source_images, target_is_directory=True)
        copy_images = False
    elif copy_images:
        images_out.mkdir(parents=True, exist_ok=True)

    content_path, blocks = find_flat_content(root)
    inferred_start, inferred_end, first_body_text_page, last_front_page = infer_body_range(
        blocks,
        preserve_answer_sections=args.canonical_content_only,
    )
    popo_outline = build_popo_outline(root) if build_popo_outline else {"available": False, "reason": "popo_structure_import_failed"}
    start_page = args.start_page if args.start_page is not None else inferred_start
    end_page = args.end_page if args.end_page is not None else inferred_end
    start_page, end_page, first_body_text_page = apply_popo_body_bounds(
        start_page,
        end_page,
        first_body_text_page,
        popo_outline,
        scope=args.scope,
        explicit_start=args.start_page,
        explicit_end=args.end_page,
    )
    if args.scope == "all":
        start_page = min((block.get("page_idx") for block in blocks if block.get("page_idx") is not None), default=0)
        end_page = max((block.get("page_idx") for block in blocks if block.get("page_idx") is not None), default=start_page)

    outline_candidates_summary = {"available": False, "reason": "collect_outline_candidates_import_failed"}
    outline_decision_summary = {"available": False, "reason": "build_outline_decision_import_failed"}
    visual_decision_summary = {"available": False, "reason": "build_visual_decisions_import_failed"}
    outline_level_normalization_summary = {"changed": False, "reason": "not_run", "adjustments": []}
    outline_decision = None
    if collect_candidates and write_jsonl:
        candidate_result = collect_candidates(root)
        outline_candidates_summary = candidate_result.get("summary") or {}
        write_jsonl(out_dir / "outline_candidates.jsonl", candidate_result.get("candidates") or [])
        (out_dir / "outline_candidates_summary.json").write_text(
            json.dumps(outline_candidates_summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if build_decision:
            outline_decision = build_decision(candidate_result.get("candidates") or [], popo_outline)
            if maybe_review_with_llm:
                outline_decision = maybe_review_with_llm(
                    outline_decision,
                    candidate_result.get("candidates") or [],
                    enabled=args.with_llm_outline,
                    base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                    model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
                    timeout=90,
                    max_risk_candidates=args.llm_outline_max_risk_candidates,
                )
            outline_decision_summary = {
                "available": True,
                "decision_method": outline_decision.get("decision_method"),
                "selected_count": outline_decision.get("selected_count"),
                "final_outline_count": outline_decision.get("final_outline_count"),
                "final_outline_source": outline_decision.get("final_outline_source"),
                "rejected_count": outline_decision.get("rejected_count"),
                "needs_llm_count": outline_decision.get("needs_llm_count"),
                "needs_visual_count": outline_decision.get("needs_visual_count"),
                "llm": outline_decision.get("llm"),
            }
            (out_dir / "outline_decision.json").write_text(
                json.dumps(outline_decision, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            if build_visual_decisions:
                visual_decisions = build_visual_decisions(
                    root,
                    out_dir / "outline_decision.json",
                    out_dir,
                    enabled=args.with_visual_outline,
                    max_candidates=args.visual_outline_max_candidates,
                    timeout=90,
                )
                visual_decision_summary = {
                    "available": True,
                    "enabled": visual_decisions.get("enabled"),
                    "provider": visual_decisions.get("provider"),
                    "model": visual_decisions.get("model"),
                    "candidate_count": visual_decisions.get("candidate_count"),
                    "validated_count": visual_decisions.get("validated_count"),
                    "truncated": visual_decisions.get("truncated"),
                    "usage": visual_decisions.get("usage"),
                    "error_count": len(visual_decisions.get("errors") or []),
                }
                (out_dir / "visual_decisions.json").write_text(
                    json.dumps(visual_decisions, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                outline_decision = apply_visual_decisions_to_outline(outline_decision, visual_decisions)
                if close_numbered_outline_hierarchy:
                    outline_decision = close_numbered_outline_hierarchy(outline_decision, candidate_result.get("candidates") or [])
                outline_decision_summary["needs_visual_count"] = outline_decision.get("needs_visual_count")
                outline_decision_summary["visual_application"] = outline_decision.get("visual_application")
                (out_dir / "outline_decision.json").write_text(
                    json.dumps(outline_decision, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            popo_outline = outline_from_decision(popo_outline, outline_decision)

    popo_outline, outline_level_normalization_summary = normalize_popo_outline_levels(popo_outline)
    evidence_popo_outline = popo_outline
    if args.canonical_content_only:
        popo_outline = {
            "available": False,
            "reason": "canonical_content_only_defers_hierarchy_to_outline_reconstruction",
            "outline": [],
        }

    units = unit_by_page(blocks)
    first_unit = units.get(start_page) or units.get(first_body_text_page)
    uses_unit_structure = bool(units)
    blocks_by_page = page_blocks_map(blocks)
    toc_chapters = collect_toc_chapters(blocks, last_front_page) if args.scope == "body" else []
    chapter_opener_pages = {
        page for page, page_blocks in blocks_by_page.items()
        if start_page <= page <= end_page and page_is_chapter_opener(page_blocks)
    }
    chapter_title_emitted = set()
    popo_injections_by_page, popo_allowed_heading_by_page = popo_page_injections(popo_outline)
    popo_items_by_key = build_popo_items_by_key(popo_injections_by_page)
    popo_items_by_title = {}
    for page_items in popo_injections_by_page.values():
        for item in page_items:
            popo_items_by_title.setdefault(clean_text(item.get("title")), item)
    emitted_popo_headings = set()

    lines = []
    qa = {
        "source_root": str(root),
        "content_file": content_path.name,
        "block_count": len(blocks),
        "scope": args.scope,
        "inferred_body_range": {"start_page": inferred_start, "end_page": inferred_end, "first_body_text_page": first_body_text_page, "last_front_page": last_front_page},
        "included_page_range": {"start_page": start_page, "end_page": end_page},
        "canonical_content_only": bool(args.canonical_content_only),
        "canonical_outline_evidence": {
            "toc_chapters": toc_chapters,
            "popo_outline": evidence_popo_outline,
        },
        "type_counts": {},
        "included_type_counts": {},
        "dropped_noise_counts": {},
        "skipped_scope_counts": {},
        "missing_images": [],
        "copied_images": [],
        "table_source_images": [],
        "notes": [
            "This is a conservative draft. Review heading hierarchy, exercises, complex tables, formulas, and image quality before treating as final."
        ],
    }

    type_counts = Counter()
    included_type_counts = Counter()
    dropped = Counter()
    skipped_scope = Counter()
    copied = set()
    missing = set()
    table_sources = []
    footnotes_by_page = {}
    last_page = None
    current_unit = None
    current_chapter = None
    injected_initial_unit = False
    emitted_popo_outline = []

    def emit_popo_heading(item):
        parent_title = clean_text((item.get("entry") or {}).get("parent_title"))
        if parent_title:
            parent_item = popo_items_by_title.get(parent_title)
            if parent_item and popo_item_key(parent_item) not in emitted_popo_headings:
                emit_popo_heading(parent_item)
        key = popo_item_key(item)
        if key in emitted_popo_headings:
            return
        lines.extend(["", f"{'#' * item['level']} {item['title']}"])
        emitted_popo_headings.add(key)
        entry = dict(item.get("entry") or {})
        entry["title"] = item["title"]
        entry["level"] = item["level"]
        entry["emission_order"] = len(emitted_popo_outline)
        emitted_popo_outline.append(entry)

    for block in blocks:
        btype = str(block.get("type", ""))
        type_counts[btype] += 1
        page = block.get("page_idx")
        if page is not None and (page < start_page or page > end_page):
            skipped_scope[btype] += 1
            continue
        included_type_counts[btype] += 1
        if btype == "header" and SEMANTIC_HEADER_RE.match(clean_text(block.get("text"))):
            text = clean_text(block.get("text"))
            text_heading_keys = heading_match_keys(text)
            if args.scope == "body" and popo_outline.get("available"):
                global_item = matching_popo_item_for_header(
                    text_heading_keys,
                    page,
                    popo_items_by_key,
                    emitted_popo_headings,
                )
                if global_item:
                    emit_popo_heading(global_item)
                    dropped["popo_outline_semantic_header_anchor"] += 1
                    continue
            if args.scope == "body" and popo_outline.get("available") and text_heading_keys & popo_allowed_heading_by_page.get(page, set()):
                for item in popo_injections_by_page.get(page, []):
                    item_key = popo_item_key(item)
                    if item_key in emitted_popo_headings:
                        continue
                    if text_heading_keys & item.get("keys", set()):
                        emit_popo_heading(item)
                        break
                dropped["popo_outline_semantic_header_duplicate"] += 1
                continue
            level = 1 if re.match(r"^(Review\s+\d+|Mid-term Test|Final Test)\b", text, re.I) else 2
            emit_source_bound_lines(lines, source_ref, ["", f"{'#' * level} {text}"])
            continue
        if btype == "header" and args.scope == "body" and popo_outline.get("available"):
            text = clean_text(block.get("text"))
            text_heading_keys = heading_match_keys(text)
            if text_heading_keys & popo_allowed_heading_by_page.get(page, set()):
                for item in popo_injections_by_page.get(page, []):
                    item_key = popo_item_key(item)
                    if item_key in emitted_popo_headings:
                        continue
                    if text_heading_keys & item.get("keys", set()):
                        emit_popo_heading(item)
                        break
                dropped["popo_outline_header_duplicate"] += 1
                continue
        if is_noise_block(block):
            dropped[btype] += 1
            continue

        if page != last_page:
            if lines and lines[-1] != "":
                lines.append("")
            page_blocks = blocks_by_page.get(page, [])
            if args.scope == "body" and popo_outline.get("available"):
                page_heading_keys = page_text_level_heading_keys(page_blocks)
                page_injections = popo_injections_by_page.get(page, [])
                for item in page_injections:
                    if item.get("keys") and item["keys"] & page_heading_keys:
                        continue
                    if same_page_parent_waits_for_block(item, page_injections, page_heading_keys, emitted_popo_headings):
                        continue
                    key = popo_item_key(item)
                    if key in emitted_popo_headings:
                        continue
                    emit_popo_heading(item)
            page_canonical_chapter = canonical_chapter_for_page(page_blocks, toc_chapters)
            if args.scope == "body" and not args.canonical_content_only and not popo_outline.get("available") and page_canonical_chapter and page_canonical_chapter != current_chapter:
                lines.extend(["", f"# {page_canonical_chapter}"])
                current_chapter = page_canonical_chapter
            if args.scope == "body" and not args.canonical_content_only and not popo_outline.get("available") and first_unit and not injected_initial_unit:
                has_real_unit_heading = page_has_unit_top_heading(page_blocks, first_unit)
                if not has_real_unit_heading:
                    lines.extend(["", f"# Unit {first_unit}"])
                current_unit = first_unit
                injected_initial_unit = True
            page_unit = units.get(page)
            if args.scope == "body" and not args.canonical_content_only and not popo_outline.get("available") and page_unit and page_unit != current_unit:
                has_real_unit_heading = page_has_unit_top_heading(page_blocks, page_unit)
                if not has_real_unit_heading:
                    lines.extend(["", f"# Unit {page_unit}"])
                current_unit = page_unit
            lines.append(f"<!-- page_idx: {page} -->")
            last_page = page

        if btype == "page_footnote":
            text = clean_text(block.get("text"))
            if text:
                emit_source_bound_lines(lines, source_ref, ["", text])
            continue

        if btype in IMAGE_BACKED_VISUAL_TYPES and (block.get("img_path") or block.get("image_path")):
            rel = safe_copy(root, block.get("img_path") or block.get("image_path"), images_out, copy_images=copy_images)
            caption = " ".join(clean_text(x) for x in block.get("image_caption", []) if clean_text(x))
            alt = normalize_markdown_image_alt(caption or "image")
            if rel:
                if copy_images and (root / rel).exists():
                    copied.add(rel)
                elif not (root / rel).exists():
                    missing.add(rel)
                rendered = ["", f"![{alt}]({rel})"]
                if caption:
                    rendered.append(f"*{caption}*")
                emit_source_bound_lines(lines, source_ref, rendered)
            continue

        if btype == "table":
            caption = " ".join(clean_text(x) for x in block.get("table_caption", []) if clean_text(x))
            body = clean_text(block.get("table_body"))
            source_img = block.get("img_path")
            if source_img:
                source_img = source_img.lstrip("/")
                if not source_img.startswith("images/"):
                    source_img = f"images/{Path(source_img).name}"
                table_sources.append(source_img)
            rendered = []
            if caption:
                rendered.extend(["", f"**{caption}**"])
            if body:
                for img_src in re.findall(r"<img[^>]+src=[\"']([^\"']+)[\"']", body, re.I):
                    img_rel = safe_copy(root, img_src, images_out, copy_images=copy_images)
                    if img_rel:
                        if copy_images and (root / img_rel).exists():
                            copied.add(img_rel)
                        elif not (root / img_rel).exists():
                            missing.add(img_rel)
                rendered.extend(["", body])
            elif source_img:
                img_rel = safe_copy(root, source_img, images_out, copy_images=copy_images)
                if img_rel:
                    if copy_images and (root / img_rel).exists():
                        copied.add(img_rel)
                    elif not (root / img_rel).exists():
                        missing.add(img_rel)
                    alt = normalize_markdown_image_alt(caption or "table")
                    rendered.extend(["", f"![{alt}]({img_rel})"])
            if rendered:
                emit_source_bound_lines(lines, source_ref, rendered)
            continue

        text = list_text(block) if btype == "list" else clean_text(block.get("text"))
        if not text:
            continue
        text_heading_keys = heading_match_keys(text)
        if (
            args.scope == "body"
            and not args.canonical_content_only
            and re.fullmatch(r"Unit\s+\d+", text, re.I)
            and not (popo_outline.get("available") and text_heading_keys & popo_allowed_heading_by_page.get(page, set()))
        ):
            dropped["unit_label"] += 1
            continue
        standalone_unit_match = STANDALONE_UNIT_NUMBER_RE.fullmatch(text)
        if (
            args.scope == "body"
            and block.get("text_level") is not None
            and standalone_unit_match
            and not (popo_outline.get("available") and text_heading_keys & popo_allowed_heading_by_page.get(page, set()))
        ):
            emit_source_bound_lines(lines, source_ref, ["", text])
            continue

        if block.get("text_level") is not None and args.scope == "body" and popo_outline.get("available"):
            block_heading_keys = text_heading_keys
            if block_heading_keys & popo_allowed_heading_by_page.get(page, set()):
                for item in popo_injections_by_page.get(page, []):
                    item_key = popo_item_key(item)
                    if item_key in emitted_popo_headings:
                        continue
                    if block_heading_keys & item.get("keys", set()):
                        emit_popo_heading(item)
                        break
                dropped["popo_outline_heading_duplicate"] += 1
                continue
            if matches_recently_emitted_popo_heading(
                block_heading_keys,
                page,
                popo_items_by_key,
                emitted_popo_headings,
            ):
                dropped["popo_outline_adjacent_page_heading_duplicate"] += 1
                continue
            emit_source_bound_lines(lines, source_ref, ["", text])
        elif block.get("text_level") is not None:
            if args.canonical_content_only:
                emit_source_bound_lines(lines, source_ref, ["", f"{'#' * heading_level(text)} {text}"])
                continue
            parent_chapter = None if uses_unit_structure else chapter_parent_heading(text)
            level = heading_level(text)
            if uses_unit_structure and level == 1 and page_unit_top_heading_count(blocks_by_page.get(page, [])) > 2:
                level = 2
            if toc_chapters and level == 1 and not is_canonical_chapter_heading(text, toc_chapters):
                level = 3
            if parent_chapter and parent_chapter != current_chapter:
                lines.extend(["", f"# {parent_chapter}"])
                current_chapter = parent_chapter
            elif (
                not uses_unit_structure
                and is_explicit_chapter_h1(text)
                and (not toc_chapters or is_canonical_chapter_heading(text, toc_chapters))
            ):
                match = re.match(r"^Chapter\s+([A-Z]?\d+)\b", text, re.I)
                chapter_label = f"Chapter {match.group(1).upper()}" if match else current_chapter
                if chapter_label == current_chapter:
                    level = 2
                current_chapter = chapter_label
            emit_source_bound_lines(lines, source_ref, ["", f"{'#' * level} {text}"])
        else:
            emit_source_bound_lines(lines, source_ref, ["", text])

    clean_md = "\n".join(lines).strip() + "\n"
    clean_md, outline_code_demotion_report = demote_bare_numeric_code_headings(clean_md)
    clean_md, leading_orphan_heading_repair_report = repair_leading_orphan_child_headings(clean_md)
    clean_md, heading_level_jump_repair_report = repair_heading_level_jumps(clean_md)
    outline_free_table_recovery_report = {
        "schema": "luceon-outline-free-table-heading-recovery/v1",
        "changed": False,
        "reason": "canonical_content_only_disabled",
        "root_title": "",
        "section_title": "",
    }
    if args.canonical_content_only:
        clean_md, outline_free_table_recovery_report = recover_outline_free_table_headings(
            clean_md,
            blocks,
            start_page,
        )
    clean_md, empty_leaf_marker_count = mark_empty_leaf_heading_chunks(clean_md)
    clean_md, math_segment_repair_report = normalize_math_digit_spacing(clean_md)
    clean_md, arithmetic_line_repair_report = normalize_arithmetic_line_digit_spacing(clean_md)
    math_ocr_repair_report = combine_math_ocr_repair_reports(math_segment_repair_report, arithmetic_line_repair_report)
    clean_md, direct_output_refs = extract_direct_output_references(clean_md)
    raw_units = markdown_raw_units(clean_md)
    block_assignments, unassigned_blocks, outline_apply_report, image_closure_report = build_block_assignment_reports(
        blocks,
        raw_units,
        clean_md,
        start_page,
        end_page,
        copied,
        missing,
        direct_output_refs=direct_output_refs,
    )
    (out_dir / "clean.md").write_text(clean_md, encoding="utf-8")
    (out_dir / "math_ocr_repair_report.json").write_text(
        json.dumps(math_ocr_repair_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "outline_code_demotion_report.json").write_text(
        json.dumps(outline_code_demotion_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "leading_orphan_heading_repair_report.json").write_text(
        json.dumps(leading_orphan_heading_repair_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "heading_level_jump_repair_report.json").write_text(
        json.dumps(heading_level_jump_repair_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "outline_free_table_recovery_report.json").write_text(
        json.dumps(outline_free_table_recovery_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_local_jsonl(out_dir / "raw_units.jsonl", raw_units)
    write_local_jsonl(out_dir / "raw_block_assignments.jsonl", block_assignments)
    write_local_jsonl(out_dir / "unassigned_blocks.jsonl", unassigned_blocks)
    (out_dir / "outline_apply_report.json").write_text(
        json.dumps(outline_apply_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "image_closure_report.json").write_text(
        json.dumps(image_closure_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    chunk_boundary_report = {
        "schema": "luceon-raw-chunk-boundary-report/v1",
        "unit_count": len(raw_units),
        "leaf_count": sum(1 for unit in raw_units if unit.get("is_leaf")),
        "empty_leaf_count": sum(1 for unit in raw_units if unit.get("empty_leaf")),
        "source_empty_chunk_count": sum(1 for unit in raw_units if unit.get("source_empty_chunk")),
        "max_heading_level": max((unit.get("level") or 0 for unit in raw_units), default=0),
        "units_with_images": sum(1 for unit in raw_units if unit.get("image_count")),
        "units_with_tables": sum(1 for unit in raw_units if unit.get("table_count")),
        "units_with_formula_signals": sum(1 for unit in raw_units if unit.get("formula_signal_count")),
        "assigned_block_count": outline_apply_report["assigned_block_count"],
        "unassigned_block_count": outline_apply_report["unassigned_block_count"],
        "leaf_units_without_blocks": outline_apply_report["leaf_units_without_blocks"],
        "container_units_without_direct_blocks": outline_apply_report["container_units_without_direct_blocks"],
        "source_image_ref_count": image_closure_report["source_image_ref_count"],
        "markdown_image_ref_count": image_closure_report["markdown_image_ref_count"],
        "missing_image_count": image_closure_report["missing_image_count"],
    }
    (out_dir / "chunk_boundary_report.json").write_text(
        json.dumps(chunk_boundary_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "preview.html").write_text(markdown_to_html(clean_md, title=root.name), encoding="utf-8")
    if write_outline_anchor_check:
        write_outline_anchor_check(clean_md, out_dir, title="Outline Anchor Check")
    output_popo_outline = dict(evidence_popo_outline)
    if emitted_popo_outline:
        output_popo_outline["outline"] = emitted_popo_outline
        output_popo_outline["outline_order"] = "markdown_emission_order"
    (out_dir / "popo_outline.json").write_text(json.dumps(output_popo_outline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    qa["type_counts"] = dict(sorted(type_counts.items()))
    qa["included_type_counts"] = dict(sorted(included_type_counts.items()))
    qa["dropped_noise_counts"] = dict(sorted(dropped.items()))
    qa["skipped_scope_counts"] = dict(sorted(skipped_scope.items()))
    qa["copied_images"] = sorted(copied)
    qa["missing_images"] = sorted(missing)
    qa["table_source_images"] = table_sources
    qa["empty_leaf_heading_markers"] = empty_leaf_marker_count
    qa["leading_orphan_heading_repair"] = leading_orphan_heading_repair_report
    qa["heading_level_jump_repair"] = heading_level_jump_repair_report
    qa["outline_free_table_recovery"] = outline_free_table_recovery_report
    qa["raw_units"] = chunk_boundary_report
    qa["outline_apply_report"] = outline_apply_report
    qa["image_closure_report"] = image_closure_report
    qa["outline_candidates"] = outline_candidates_summary
    qa["outline_decision"] = outline_decision_summary
    qa["visual_decisions"] = visual_decision_summary
    qa["outline_level_normalization"] = outline_level_normalization_summary
    qa["math_ocr_repair"] = math_ocr_repair_report
    qa["image_copy_mode"] = "linked" if link_images else ("copied" if copy_images else "links_only_no_copy")
    if link_images:
        qa["notes"].append("Images were linked with --link-images for local preview; final delivery should copy or re-crop linked images if the output folder must be portable.")
    elif not copy_images:
        qa["notes"].append("Images were not copied because --no-copy-images was used; final delivery must copy or re-crop linked images.")
    (out_dir / "manifest.json").write_text(json.dumps(qa, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report_lines = [
        "# QA Report",
        "",
        f"- Source: `{root}`",
        f"- Content file: `{content_path.name}`",
        f"- Scope: `{args.scope}`",
        f"- Inferred body range: `{inferred_start}-{inferred_end}`; first body text page: `{first_body_text_page}`; last front page: `{last_front_page}`",
        f"- Included page range: `{start_page}-{end_page}`",
        f"- Canonical content only: `{bool(args.canonical_content_only)}`",
        f"- Popo outline evidence available: `{bool(evidence_popo_outline.get('available'))}`; outline entries: `{len(evidence_popo_outline.get('outline') or [])}`",
        f"- Outline candidates: `{outline_candidates_summary.get('candidate_count', 0)}`; needs LLM: `{outline_candidates_summary.get('needs_llm_count', 0)}`; needs visual: `{outline_candidates_summary.get('needs_visual_count', 0)}`",
        f"- Outline decision: `{outline_decision_summary.get('decision_method', 'unavailable')}`; selected: `{outline_decision_summary.get('selected_count', 0)}`",
        f"- Visual decisions: available `{visual_decision_summary.get('available', False)}`; candidates: `{visual_decision_summary.get('candidate_count', 0)}`; enabled: `{visual_decision_summary.get('enabled', False)}`",
        f"- Leading orphan heading repairs: `{leading_orphan_heading_repair_report['repair_count']}`",
        f"- Heading level jump repairs: `{heading_level_jump_repair_report['repair_count']}`",
        f"- Outline level normalization: changed `{outline_level_normalization_summary.get('changed', False)}`; adjustments `{len(outline_level_normalization_summary.get('adjustments') or [])}`",
        f"- Math OCR repair: changed `{math_ocr_repair_report.get('changed', False)}`; segments `{math_ocr_repair_report.get('segment_count', 0)}`; substitutions `{math_ocr_repair_report.get('substitution_count', 0)}`",
        f"- Raw units: `{chunk_boundary_report['unit_count']}`; leaf: `{chunk_boundary_report['leaf_count']}`; empty leaf: `{chunk_boundary_report['empty_leaf_count']}`; max heading level: `{chunk_boundary_report['max_heading_level']}`",
        f"- Block flooding: assigned `{outline_apply_report['assigned_block_count']}` / `{outline_apply_report['eligible_block_count']}` eligible blocks; unassigned `{outline_apply_report['unassigned_block_count']}`",
        f"- Blocks: {len(blocks)}",
        f"- Included blocks by type: {dict(sorted(included_type_counts.items()))}",
        f"- Skipped by scope: {dict(sorted(skipped_scope.items()))}",
        f"- Dropped noise: {dict(sorted(dropped.items()))}",
        f"- Image copy mode: {'linked' if link_images else ('copied' if copy_images else 'links only; images not copied')}",
        f"- Copied images: {len(copied)}",
        f"- Missing images: {len(missing)}",
        f"- Image closure: source refs `{image_closure_report['source_image_ref_count']}`; markdown refs `{image_closure_report['markdown_image_ref_count']}`; refs not copied `{len(image_closure_report['markdown_refs_not_copied'])}`",
        f"- Table source images recorded: {len(table_sources)}",
        "",
        "Review this draft before final use. Pay special attention to heading hierarchy, exercise blanks, table OCR, formulas, and image quality.",
    ]
    (out_dir / "qa_report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    print(f"Wrote {out_dir / 'clean.md'}")
    print(f"Wrote {out_dir / 'preview.html'}")
    if write_outline_anchor_check:
        print(f"Wrote {out_dir / 'outline-view.html'}")
        print(f"Wrote {out_dir / 'outline-anchor-check.html'}")
    if (out_dir / "outline_candidates.jsonl").exists():
        print(f"Wrote {out_dir / 'outline_candidates.jsonl'}")
    if (out_dir / "outline_decision.json").exists():
        print(f"Wrote {out_dir / 'outline_decision.json'}")
    if (out_dir / "visual_decisions.json").exists():
        print(f"Wrote {out_dir / 'visual_decisions.json'}")
    print(f"Wrote {out_dir / 'raw_units.jsonl'}")
    print(f"Wrote {out_dir / 'raw_block_assignments.jsonl'}")
    print(f"Wrote {out_dir / 'unassigned_blocks.jsonl'}")
    print(f"Wrote {out_dir / 'outline_apply_report.json'}")
    print(f"Wrote {out_dir / 'image_closure_report.json'}")
    print(f"Wrote {out_dir / 'chunk_boundary_report.json'}")
    print(f"Wrote {out_dir / 'manifest.json'}")
    print(f"Wrote {out_dir / 'qa_report.md'}")


if __name__ == "__main__":
    main()
