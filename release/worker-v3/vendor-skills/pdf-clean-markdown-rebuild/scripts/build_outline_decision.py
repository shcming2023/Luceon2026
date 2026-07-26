#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import popo_structure as ps

SYSTEM_PROMPT = """You rebuild an auditable textbook outline from candidates.
Return strict JSON only.

You are not writing Markdown and you are not publishing Raw.
Your job is to infer the final max-3-level outline from all provided candidates and context.
The current Popo outline is only a proposal/candidate source, not source truth.
You must select, merge, reject, promote, demote, and parent candidates globally.
Keep final_outline compact enough to be useful for downstream chunking, but complete enough to preserve the real source hierarchy.
review_notes must contain at most 5 concise notes.

Rules:
- Keep a max-3-level outline.
- Select only candidates supported by TOC, body heading, document tree, image OCR, page, bbox, or block evidence.
- Every final_outline item must include at least one candidate_id from the provided candidate_pool.
- Merge split candidates when they represent one source-visible title.
- Prefer real TOC and repeated source-visible numbering/page-order patterns over Popo OCR heading levels.
- Do not trust Popo structural heading levels blindly; MinerU/Popo often marks local labels as level 1.
- Treat candidate_type=selected_outline_entry with source=popo_structural_heading as weak evidence. Select it only when it is corroborated by TOC/body/document-tree patterns or when no stronger source-visible hierarchy exists.
- Remove front matter, online resource pages, standards pages, ads, running headers, local labels, body prompts, and noise from the directory layer unless they are true source-visible standalone material sections.
- If real Unit/Chapter/Topic candidates exist, do not include the book title, cover/title-page text, online resource pages, standards pages, credits, or contents/front matter as final_outline nodes.
- Top-level headings are not limited to Unit/Chapter labels. Review sections, vocabulary-building modules, world-heritage/spotlight readings, inter-unit modules, and body-visible article titles may be level-1 when the source TOC or body heading supports them as standalone material sections.
- For textbooks with stable Topic/Lesson patterns, use Topic/Unit/Chapter as parents and put real lesson/section entries below them.
- Review, practice, skills-review, 3-act, STEM project, and assessment sections that occur inside a numbered Topic/Unit/Chapter page range should be children or body content under that Topic/Unit/Chapter, not top-level peers, unless the source TOC clearly lists them as peer modules.
- Do not remove a source-supported heading merely because it is a special section rather than a Unit.
- Reject local labels, page headers/footers, exercise prompts, body text, and noise.
- Reject pure formulas, single-letter answer choices, answer labels, point marks such as [3], option fragments such as a/b/c/d/e/f, examples, practice prompts, and exercise labels unless source evidence proves they are real outline nodes.
- Mark a final item with needs_visual=true only when the candidate is valuable but PDF page-image verification is still required.
- Do not invent textbook content.

Schema:
{
  "verdict": "ok|needs_fix|needs_visual_review",
  "review_notes": ["short notes"],
  "final_outline": [
    {
      "title": "source visible heading",
      "level": 1,
      "parent_title": "",
      "page": 12,
      "candidate_ids": ["..."],
      "reason": "evidence-based reason",
      "confidence": "high|medium|low",
      "needs_visual": false
    }
  ],
  "rejected_candidate_ids": ["..."]
}
"""


def load_json(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def parse_page_value(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    text = str(value or "").strip()
    if not text:
        return None
    match = re.search(r"\d+", text)
    if not match:
        return None
    page = int(match.group(0))
    return page if page > 0 else None


def candidate_page(item: dict[str, Any]) -> int | None:
    page = parse_page_value(item.get("page"))
    if page is not None:
        return page
    evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
    return parse_page_value(evidence.get("printed_page") or evidence.get("page") or evidence.get("start_page"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def call_deepseek(api_key: str, base_url: str, model: str, payload: dict[str, Any], timeout: int) -> tuple[dict[str, Any], dict[str, Any]]:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "max_tokens": int(os.getenv("DEEPSEEK_OUTLINE_DECISION_MAX_TOKENS", "16000")),
    }
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"DeepSeek HTTP {exc.code}: {detail[:1000]}") from exc
    content = data["choices"][0]["message"]["content"]
    try:
        return parse_json_object(content), data.get("usage") or {}
    except json.JSONDecodeError as exc:
        sample = str(content or "")
        if len(sample) > 1200:
            sample = sample[:700] + "\n...[truncated]...\n" + sample[-300:]
        raise RuntimeError(f"DeepSeek response was not valid JSON: {exc}. response_sample={sample!r}") from exc


def parse_json_object(content: str) -> dict[str, Any]:
    content = str(content or "").strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.I)
        content = re.sub(r"\s*```$", "", content)
    try:
        parsed = json.loads(content)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", content):
        try:
            obj, _ = decoder.raw_decode(content[match.start():])
            return obj if isinstance(obj, dict) else {}
        except json.JSONDecodeError:
            continue
    raise json.JSONDecodeError("No valid JSON object found in model response", content, 0)


def estimate_cost(provider: str, model: str, prompt_tokens: int, completion_tokens: int) -> dict[str, Any]:
    input_per_million = float(os.getenv("DEEPSEEK_INPUT_PRICE_PER_MILLION", "0.27"))
    output_per_million = float(os.getenv("DEEPSEEK_OUTPUT_PRICE_PER_MILLION", "1.10"))
    estimated = (prompt_tokens / 1_000_000) * input_per_million + (completion_tokens / 1_000_000) * output_per_million
    return {
        "provider": provider,
        "model": model,
        "input_price_per_million": input_per_million,
        "output_price_per_million": output_per_million,
        "currency": "USD",
        "estimated_cost": round(estimated, 6),
    }


def candidate_key(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        item.get("normalized_title"),
        item.get("page"),
        item.get("level_hint"),
        item.get("parent_hint") or "",
    )


def loose_candidate_key(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        item.get("normalized_title"),
        item.get("page"),
        item.get("parent_hint") or "",
    )


def outline_key(item: dict[str, Any]) -> tuple[Any, ...]:
    normalized = item.get("normalized_title")
    if not normalized:
        normalized = ps.normalize(item.get("title") or "")
    return (
        normalized,
        item.get("start_page"),
        item.get("level"),
        item.get("parent_title") or "",
    )


def outline_loose_key(item: dict[str, Any]) -> tuple[Any, ...]:
    normalized = item.get("normalized_title")
    if not normalized:
        normalized = ps.normalize(item.get("title") or "")
    return (
        normalized,
        item.get("start_page"),
        item.get("parent_title") or "",
    )


def numbered_series_heading(title: str) -> bool:
    text = str(title or "").strip()
    return bool(re.match(r"^(chapter|unit|module|part|book)\s+\d+\b", text, flags=re.I))


def obvious_local_label_title(title: str) -> bool:
    text = str(title or "").strip()
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return False
    if re.fullmatch(r"[a-fA-F]", compact):
        return True
    if re.fullmatch(r"\[?\d{1,2}\]?", compact):
        return True
    if re.fullmatch(r"answer|answers", text, flags=re.I):
        return True
    if re.fullmatch(r"\\\(.{1,20}\\\)", compact) and re.search(r"[=+\-×÷<>]", compact):
        return True
    if re.fullmatch(r"[a-zA-Z]\s*=\s*[a-zA-Z]", text):
        return True
    if re.fullmatch(r"(?:[a-fA-F]\s*\.?\s*){2,6}", text):
        return True
    return False


def add_selected_risk_flags(selected: list[dict[str, Any]]) -> None:
    h1_positions = [idx for idx, item in enumerate(selected) if int(item.get("level") or 0) == 1]
    for pos, idx in enumerate(h1_positions):
        item = selected[idx]
        title = item.get("title") or ""
        flags = []
        prev_h1 = selected[h1_positions[pos - 1]] if pos > 0 else {}
        next_h1 = selected[h1_positions[pos + 1]] if pos + 1 < len(h1_positions) else {}
        if (
            prev_h1
            and next_h1
            and numbered_series_heading(prev_h1.get("title") or "")
            and numbered_series_heading(next_h1.get("title") or "")
            and not numbered_series_heading(title)
        ):
            flags.append("interstitial_title_between_numbered_series")
        if flags:
            item["risk_flags"] = flags


def build_decision(candidates: list[dict[str, Any]], outline: dict[str, Any]) -> dict[str, Any]:
    by_key = {candidate_key(item): item for item in candidates}
    by_loose = {loose_candidate_key(item): item for item in candidates}
    selected = []
    selected_ids = set()
    for order, entry in enumerate(outline.get("outline") or []):
        probe = {
            "normalized_title": ps.normalize(entry.get("title") or ""),
            "page": entry.get("start_page"),
            "level_hint": entry.get("level"),
            "parent_hint": entry.get("parent_title") or "",
        }
        candidate = by_key.get(candidate_key(probe)) or by_loose.get(loose_candidate_key(probe))
        candidate_id = candidate.get("candidate_id") if candidate else None
        if candidate_id:
            selected_ids.add(candidate_id)
        selected.append(
            {
                "order": order,
                "title": entry.get("title"),
                "level": entry.get("level"),
                "parent_title": entry.get("parent_title") or "",
                "page": entry.get("start_page"),
                "source": entry.get("source"),
                "candidate_ids": [candidate_id] if candidate_id else [],
                "evidence": {
                    "anchor_title": entry.get("anchor_title"),
                    "anchor_method": entry.get("anchor_method"),
                    "block_ids": entry.get("block_ids") or [],
                    "match_titles": entry.get("match_titles") or [],
                },
                "confidence": candidate.get("confidence") if candidate else 0.64,
                "needs_llm": bool(candidate.get("needs_llm")) if candidate else True,
                "needs_visual": bool(candidate.get("needs_visual")) if candidate else False,
                "decision": "selected",
            }
        )
    add_selected_risk_flags(selected)

    rejected = []
    for item in candidates:
        candidate_id = item.get("candidate_id")
        if candidate_id in selected_ids:
            continue
        rejected.append(
            {
                "candidate_id": candidate_id,
                "title": item.get("title_text"),
                "candidate_type": item.get("candidate_type"),
                "page": item.get("page"),
                "parent_hint": item.get("parent_hint"),
                "reason": "not_selected_by_current_deterministic_outline",
                "needs_llm": bool(item.get("needs_llm")),
                "needs_visual": bool(item.get("needs_visual")),
            }
        )

    decision = {
        "schema": "luceon-outline-decision/v1",
        "created_at": datetime.utcnow().isoformat() + "Z",
        "decision_method": "deterministic_bootstrap_selected_outline",
        "llm": {
            "enabled": False,
            "provider": None,
            "model": None,
            "call_count": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "estimated_cost": 0,
            "note": "This is the traceable deterministic decision artifact. LLM reasoning will replace this method when enabled.",
        },
        "selected_count": len(selected),
        "rejected_count": len(rejected),
        "needs_llm_count": sum(1 for item in candidates if item.get("needs_llm")),
        "needs_visual_count": sum(1 for item in candidates if item.get("needs_visual")),
        "selected_outline": selected,
        "rejected_candidates": rejected,
    }
    return rebuild_final_outline(decision, candidates)


def candidate_to_final_node(candidate: dict[str, Any], order: int) -> dict[str, Any]:
    title = candidate.get("title_text") or ""
    level = candidate.get("level_hint")
    try:
        level = int(level)
    except Exception:
        level = 3
    level = max(1, min(3, level))
    page = candidate.get("page")
    return {
        "order": order,
        "title": title,
        "level": level,
        "parent_title": candidate.get("parent_hint") or "",
        "page": page,
        "start_page": page,
        "start_page_idx": page - 1 if isinstance(page, int) else None,
        "source": candidate.get("source") or candidate.get("candidate_type"),
        "candidate_ids": [candidate.get("candidate_id")] if candidate.get("candidate_id") else [],
        "evidence": candidate.get("evidence") or {},
        "confidence": candidate.get("confidence"),
        "needs_visual": bool(candidate.get("needs_visual")),
        "decision": "selected_by_llm_reconsideration",
    }


def selected_to_final_node(item: dict[str, Any], order: int) -> dict[str, Any] | None:
    llm_decision = item.get("llm_decision") or {}
    action = llm_decision.get("decision") or "keep"
    if action == "remove":
        return None
    title = llm_decision.get("revised_title") if action == "revise" and llm_decision.get("revised_title") else item.get("title")
    page = item.get("page")
    node = {
        "order": order,
        "title": title,
        "level": max(1, min(3, int(item.get("level") or 3))),
        "parent_title": item.get("parent_title") or "",
        "page": page,
        "start_page": page,
        "start_page_idx": page - 1 if isinstance(page, int) else None,
        "source": item.get("source"),
        "candidate_ids": item.get("candidate_ids") or [],
        "evidence": item.get("evidence") or {},
        "confidence": item.get("confidence"),
        "needs_visual": bool(item.get("needs_visual") or action == "needs_visual"),
        "decision": "selected",
    }
    if llm_decision:
        node["llm_decision"] = llm_decision
        node["decision"] = f"llm_{action}"
    return node


def rebuild_final_outline(decision: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {item.get("candidate_id"): item for item in candidates if item.get("candidate_id")}
    selected = decision.get("selected_outline") or []
    child_parent_titles = {
        ps.clean_text(str(item.get("parent_title") or ""))
        for item in selected
        if ps.clean_text(str(item.get("parent_title") or ""))
    }
    final = []
    for item in selected:
        title_key = ps.clean_text(str(item.get("title") or ""))
        llm_decision = item.get("llm_decision") or {}
        if title_key in child_parent_titles and llm_decision.get("decision") == "remove":
            item = dict(item)
            item["llm_decision"] = {
                **llm_decision,
                "decision": "keep",
                "reason": (
                    "Removal blocked because this selected heading is still referenced "
                    "as a parent by child outline entries."
                ),
                "blocked_remove": True,
                "original_decision": llm_decision,
            }
        node = selected_to_final_node(item, len(final))
        if node:
            final.append(node)
    for item in decision.get("rejected_candidates") or []:
        reconsider = item.get("llm_reconsideration") or {}
        action = reconsider.get("decision")
        if action not in {"select", "needs_visual"}:
            continue
        candidate = by_id.get(item.get("candidate_id"))
        if not candidate:
            continue
        node = candidate_to_final_node(candidate, len(final))
        node["llm_reconsideration"] = reconsider
        node["needs_visual"] = bool(node.get("needs_visual") or action == "needs_visual")
        final.append(node)

    def sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
        page = item.get("start_page")
        if item.get("source") in {"contents", "contents_detail", "contents_category"}:
            return (0, item.get("order") or 0)
        return (
            1,
            999999 if page is None else page,
            item.get("level") or 9,
            item.get("order") or 0,
            item.get("title") or "",
        )

    final = sorted(final, key=sort_key)

    for original_order, item in enumerate(final):
        item["_outline_original_order"] = original_order
    by_title = {ps.clean_text(str(item.get("title") or "")): item for item in final if ps.clean_text(str(item.get("title") or ""))}
    children_by_parent: dict[str, list[dict[str, Any]]] = {}
    roots: list[dict[str, Any]] = []
    for item in final:
        parent_key = ps.clean_text(str(item.get("parent_title") or ""))
        parent = by_title.get(parent_key) if parent_key else None
        if parent is not None and parent is not item:
            parent_level = int(parent.get("level") or 1)
            item["level"] = max(int(item.get("level") or 3), min(3, parent_level + 1))
            if item["level"] <= parent_level:
                item["level"] = min(3, parent_level + 1)
            children_by_parent.setdefault(parent_key, []).append(item)
        else:
            if parent_key:
                item["level"] = max(2, int(item.get("level") or 2))
            roots.append(item)

    def tree_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
        page = item.get("start_page")
        return (
            999999 if page is None else page,
            item.get("level") or 9,
            item.get("_outline_original_order") or 0,
            item.get("title") or "",
        )

    ordered: list[dict[str, Any]] = []
    emitted: set[int] = set()

    def emit_tree(item: dict[str, Any], stack: set[int] | None = None) -> None:
        stack = stack or set()
        item_id = id(item)
        if item_id in emitted:
            return
        if item_id in stack:
            return
        ordered.append(item)
        emitted.add(item_id)
        title_key = ps.clean_text(str(item.get("title") or ""))
        for child in sorted(children_by_parent.get(title_key, []), key=tree_sort_key):
            emit_tree(child, stack | {item_id})

    for item in sorted(roots, key=tree_sort_key):
        emit_tree(item)
    for item in sorted(final, key=tree_sort_key):
        emit_tree(item)
    final = ordered

    for idx, item in enumerate(final):
        item["order"] = idx
        item.pop("_outline_original_order", None)
    selected_ids = {cid for item in final for cid in (item.get("candidate_ids") or []) if cid}
    for item in decision.get("rejected_candidates") or []:
        cid = item.get("candidate_id")
        reconsider = item.get("llm_reconsideration") or {}
        if cid in selected_ids:
            item["reason"] = reconsider.get("reason") or "selected_into_final_outline_by_llm"
        elif reconsider.get("decision") == "keep_rejected":
            item["reason"] = reconsider.get("reason") or item.get("reason")
    decision["final_outline"] = final
    decision["final_outline_count"] = len(final)
    decision["final_outline_source"] = decision.get("decision_method")
    decision["selected_count"] = len(final)
    return decision


def compact_selected(selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "order": item.get("order"),
            "title": item.get("title"),
            "level": item.get("level"),
            "parent_title": item.get("parent_title"),
            "page": item.get("page"),
            "source": item.get("source"),
            "candidate_ids": item.get("candidate_ids"),
            "evidence": item.get("evidence"),
            "confidence": item.get("confidence"),
            "risk_flags": item.get("risk_flags") or [],
        }
        for item in selected
    ]


def compact_risk_candidates(candidates: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    risk = [item for item in candidates if item.get("needs_llm") or item.get("needs_visual")]
    risk = sorted(risk, key=lambda item: (not item.get("needs_visual"), not item.get("needs_llm"), item.get("page") or 99999))
    compact = []
    for item in risk[:limit]:
        compact.append(
            {
                "candidate_id": item.get("candidate_id"),
                "candidate_type": item.get("candidate_type"),
                "title_text": item.get("title_text"),
                "page": item.get("page"),
                "parent_hint": item.get("parent_hint"),
                "level_hint": item.get("level_hint"),
                "confidence": item.get("confidence"),
                "needs_llm": item.get("needs_llm"),
                "needs_visual": item.get("needs_visual"),
                "source": item.get("source"),
                "source_path": item.get("source_path"),
                "evidence": item.get("evidence"),
            }
        )
    return compact


def compact_candidate_pool(candidates: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    configured_limit = int(os.getenv("DEEPSEEK_OUTLINE_GLOBAL_MAX_CANDIDATES", "500"))
    limit = max(int(limit or 0), configured_limit)
    if limit <= 0:
        limit = len(candidates)

    grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for item in candidates:
        cid = item.get("candidate_id")
        norm = item.get("normalized_title") or ps.normalize(item.get("title_text") or "")
        title_key = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", ps.clean_display_title(item.get("title_text") or "").lower()).strip()
        title_key = re.sub(r"\s+", " ", title_key)
        if not cid or not norm or not title_key:
            continue
        key = (
            str(item.get("candidate_type") or ""),
            title_key,
            str(item.get("source") or ""),
            str(item.get("parent_hint") or "") if item.get("candidate_type") != "body_lesson_heading" else "",
        )
        existing = grouped.get(key)
        page = item.get("page")
        if existing is None:
            existing = dict(item)
            existing["_candidate_ids"] = [cid]
            existing["_pages"] = [page] if isinstance(page, int) else []
            grouped[key] = existing
        else:
            existing["_candidate_ids"].append(cid)
            if isinstance(page, int):
                existing["_pages"].append(page)
            current_page = existing.get("page")
            if isinstance(page, int) and (not isinstance(current_page, int) or page < current_page):
                for field in [
                    "candidate_id",
                    "title_text",
                    "normalized_title",
                    "page",
                    "parent_hint",
                    "level_hint",
                    "confidence",
                    "needs_llm",
                    "needs_visual",
                    "source",
                    "source_path",
                    "block_ids",
                    "evidence",
                ]:
                    existing[field] = item.get(field)

    compact_source = list(grouped.values())

    def priority(item: dict[str, Any]) -> tuple[Any, ...]:
        ctype = item.get("candidate_type")
        source = item.get("source")
        source_rank = {
            "toc_outline_entry": 0,
            "body_structural_heading": 1,
            "body_lesson_heading": 2,
            "body_inter_unit_module": 3,
            "selected_outline_entry": 4,
            "image_ocr_title_or_caption": 5,
        }.get(str(ctype), 9)
        if ctype == "selected_outline_entry" and source == "popo_structural_heading":
            source_rank = 6
        if source in {"contents", "contents_detail", "contents_category"}:
            source_rank = min(source_rank, 0)
        return (
            source_rank,
            999999 if item.get("page") is None else item.get("page"),
            item.get("level_hint") if item.get("level_hint") is not None else 9,
            item.get("title_text") or "",
        )

    compact = []
    for item in sorted(compact_source, key=priority)[:limit]:
        block_ids = item.get("block_ids") or []
        if not isinstance(block_ids, list):
            block_ids = []
        pages = sorted({page for page in item.get("_pages", []) if isinstance(page, int)})
        related_ids = item.get("_candidate_ids") or []
        compact.append(
            {
                "candidate_id": item.get("candidate_id"),
                "related_candidate_count": len(related_ids),
                "candidate_type": item.get("candidate_type"),
                "title_text": item.get("title_text"),
                "page": item.get("page"),
                "page_range": [pages[0], pages[-1]] if pages else None,
                "page_count": len(pages),
                "parent_hint": item.get("parent_hint"),
                "level_hint": item.get("level_hint"),
                "confidence": item.get("confidence"),
                "needs_visual": item.get("needs_visual"),
                "source": item.get("source"),
                "evidence_grade": (
                    "weak_popo_structural"
                    if item.get("candidate_type") == "selected_outline_entry" and item.get("source") == "popo_structural_heading"
                    else "candidate"
                ),
                "block_id_count": len(block_ids),
            }
        )
    return compact


def outline_context(decision: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    source_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    for item in candidates:
        source_counts[str(item.get("source") or "")] = source_counts.get(str(item.get("source") or ""), 0) + 1
        type_counts[str(item.get("candidate_type") or "")] = type_counts.get(str(item.get("candidate_type") or ""), 0) + 1
    selected = decision.get("selected_outline") or []
    popo_selected = [item for item in selected if item.get("source") == "popo_structural_heading"]
    return {
        "candidate_count": len(candidates),
        "candidate_type_counts": type_counts,
        "candidate_source_counts": source_counts,
        "bootstrap_selected_count": len(selected),
        "bootstrap_popo_structural_selected_count": len(popo_selected),
        "bootstrap_risk_flag_count": sum(1 for item in selected if item.get("risk_flags")),
        "policy": "Always perform global LLM outline reasoning when LLM is enabled. Quality signals only guide evidence weighting and QA.",
        "source_guidance": [
            "Do not treat Popo structural headings as locked outline rows.",
            "Prefer repeated Topic/Unit/Chapter body candidates and TOC-like page-order patterns.",
            "Weak Popo structural rows such as examples, practice prompts, online resources, standards pages, and local labels should usually be rejected or demoted to body text.",
        ],
    }


def find_candidate_for_global_node(raw: dict[str, Any], by_id: dict[str, dict[str, Any]], candidates: list[dict[str, Any]]) -> list[str]:
    raw_ids = raw.get("candidate_ids") or raw.get("candidate_id") or []
    if isinstance(raw_ids, str):
        raw_ids = [raw_ids]
    ids = [cid for cid in raw_ids if cid in by_id]
    if ids:
        return ids

    title = ps.normalize(raw.get("title") or "")
    page = raw.get("page")
    if not title:
        return []
    matches = []
    for item in candidates:
        if item.get("normalized_title") != title:
            continue
        if page is not None and item.get("page") != page:
            continue
        cid = item.get("candidate_id")
        if cid:
            matches.append(cid)
    return matches[:3]


def candidate_has_root_toc_evidence(candidate: dict[str, Any]) -> bool:
    source = str(candidate.get("source") or "")
    evidence = candidate.get("evidence") if isinstance(candidate.get("evidence"), dict) else {}
    kind = str(evidence.get("kind") or "")
    parent_hint = ps.clean_text(str(candidate.get("parent_hint") or ""))
    return source in {"contents", "contents_detail", "contents_category"} and kind in {"primary", "unit"} and not parent_hint


def candidates_force_root(candidate_refs: list[dict[str, Any]]) -> bool:
    return any(candidate_has_root_toc_evidence(candidate) for candidate in candidate_refs)


def apply_global_outline_review(decision: dict[str, Any], candidates: list[dict[str, Any]], review: dict[str, Any], usage: dict[str, Any], provider: str, model: str) -> dict[str, Any]:
    prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
    cost = estimate_cost(provider, model, prompt_tokens, completion_tokens)
    by_id = {item.get("candidate_id"): item for item in candidates if item.get("candidate_id")}
    final: list[dict[str, Any]] = []
    seen = set()
    level_stack: dict[int, str] = {}
    parent_levels: dict[str, int] = {}
    skipped = []

    for raw in review.get("final_outline") or []:
        if not isinstance(raw, dict):
            continue
        candidate_ids = find_candidate_for_global_node(raw, by_id, candidates)
        if not candidate_ids:
            skipped.append({"title": raw.get("title"), "page": raw.get("page"), "reason": "missing_candidate_id"})
            continue
        candidate_refs = [by_id[cid] for cid in candidate_ids if cid in by_id]
        first_candidate = candidate_refs[0] if candidate_refs else {}
        title = ps.clean_display_title(raw.get("title") or first_candidate.get("title_text") or "")
        if not title:
            continue
        try:
            level = int(raw.get("level"))
        except Exception:
            try:
                level = int(first_candidate.get("level_hint") or 1)
            except Exception:
                level = 1
        level = max(1, min(3, level))
        page = parse_page_value(raw.get("page")) or candidate_page(first_candidate)
        parent_title = ps.clean_display_title(raw.get("parent_title") or "")
        if candidates_force_root(candidate_refs):
            level = 1
            parent_title = ""
        if level == 1:
            parent_title = ""
        elif not parent_title:
            parent_title = level_stack.get(level - 1, "")
        if parent_title:
            parent_level = parent_levels.get(ps.normalize(parent_title))
            if parent_level is not None:
                level = min(3, parent_level + 1)
        key = (ps.normalize(title), page, level, ps.normalize(parent_title))
        if key in seen:
            continue
        seen.add(key)
        source = raw.get("source") or first_candidate.get("source") or first_candidate.get("candidate_type")
        evidence = {
            "llm_reason": raw.get("reason") or "",
            "candidate_ids": candidate_ids,
            "candidate_titles": [item.get("title_text") for item in candidate_refs],
            "candidate_sources": [item.get("source") for item in candidate_refs],
            "candidate_pages": [candidate_page(item) for item in candidate_refs],
        }
        node = {
            "order": len(final),
            "title": title,
            "level": level,
            "parent_title": parent_title,
            "page": page,
            "start_page": page,
            "start_page_idx": page - 1 if isinstance(page, int) else None,
            "source": source,
            "candidate_ids": candidate_ids,
            "evidence": evidence,
            "confidence": raw.get("confidence") or first_candidate.get("confidence"),
            "needs_visual": bool(raw.get("needs_visual")),
            "decision": "llm_global_selected",
        }
        final.append(node)
        parent_levels[ps.normalize(title)] = level
        level_stack[level] = title
        for deeper in [depth for depth in list(level_stack) if depth > level]:
            level_stack.pop(deeper, None)

    if not final:
        decision["llm"] = {
            "enabled": True,
            "provider": provider,
            "model": model,
            "call_count": 1,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "estimated_cost": cost["estimated_cost"],
            "pricing": cost,
            "raw_usage": usage,
            "verdict": review.get("verdict") or "needs_fix",
            "review_notes": (review.get("review_notes") or []) + ["Global outline response produced no usable candidate-backed nodes."],
            "skipped_global_nodes": skipped[:20],
        }
        return decision

    final = sorted(
        final,
        key=lambda item: (
            999999 if item.get("start_page") is None else item.get("start_page"),
            item.get("level") or 9,
            item.get("order") or 0,
            item.get("title") or "",
        ),
    )
    for idx, item in enumerate(final):
        item["order"] = idx

    selected_ids = {cid for item in final for cid in (item.get("candidate_ids") or [])}
    rejected = []
    rejected_by_model = set(review.get("rejected_candidate_ids") or [])
    for item in candidates:
        cid = item.get("candidate_id")
        if cid in selected_ids:
            continue
        reason = "rejected_by_llm_global_outline" if cid in rejected_by_model else "not_selected_by_llm_global_outline"
        rejected.append(
            {
                "candidate_id": cid,
                "title": item.get("title_text"),
                "candidate_type": item.get("candidate_type"),
                "page": item.get("page"),
                "parent_hint": item.get("parent_hint"),
                "reason": reason,
                "needs_llm": bool(item.get("needs_llm")),
                "needs_visual": bool(item.get("needs_visual")),
            }
        )

    decision["decision_method"] = "llm_global_candidate_outline"
    decision["selected_outline"] = final
    decision["rejected_candidates"] = rejected
    decision["selected_count"] = len(final)
    decision["rejected_count"] = len(rejected)
    decision["final_outline"] = final
    decision["final_outline_count"] = len(final)
    decision["final_outline_source"] = "llm_global_candidate_outline"
    decision["needs_visual_count"] = sum(1 for item in final if item.get("needs_visual"))
    decision["llm"] = {
        "enabled": True,
        "provider": provider,
        "model": model,
        "call_count": 1,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "estimated_cost": cost["estimated_cost"],
        "pricing": cost,
        "raw_usage": usage,
        "verdict": review.get("verdict") or "ok",
        "review_notes": review.get("review_notes") or [],
        "skipped_global_nodes": skipped[:20],
    }
    return decision


def apply_llm_review(decision: dict[str, Any], candidates: list[dict[str, Any]], review: dict[str, Any], usage: dict[str, Any], provider: str, model: str) -> dict[str, Any]:
    if review.get("final_outline"):
        return apply_global_outline_review(decision, candidates, review, usage, provider, model)

    prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
    cost = estimate_cost(provider, model, prompt_tokens, completion_tokens)
    decision["llm"] = {
        "enabled": True,
        "provider": provider,
        "model": model,
        "call_count": 1,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "estimated_cost": cost["estimated_cost"],
        "pricing": cost,
        "raw_usage": usage,
        "verdict": "needs_fix",
        "error": "LLM response missing required final_outline; global outline rebuild was not applied.",
        "review_notes": review.get("review_notes") or [],
    }
    return decision

    prompt_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
    cost = estimate_cost(provider, model, prompt_tokens, completion_tokens)
    by_order = {int(item.get("order")): item for item in decision.get("selected_outline") or [] if item.get("order") is not None}
    for raw in review.get("selected_overrides") or []:
        try:
            order = int(raw.get("order"))
        except Exception:
            continue
        item = by_order.get(order)
        if not item:
            continue
        item["llm_decision"] = {
            "decision": raw.get("decision") or "keep",
            "revised_title": raw.get("revised_title") or "",
            "reason": raw.get("reason") or "",
            "confidence": raw.get("confidence") or "low",
        }
        if raw.get("decision") == "needs_visual":
            item["needs_visual"] = True
        if raw.get("decision") == "revise" and raw.get("revised_title"):
            item["llm_revised_title"] = raw.get("revised_title")
    reconsider_by_id = {}
    candidate_by_id = {item.get("candidate_id"): item for item in candidates if item.get("candidate_id")}
    for raw in review.get("candidate_reconsiderations") or []:
        cid = raw.get("candidate_id")
        if cid:
            raw_decision = raw.get("decision") or "keep_rejected"
            raw_reason = raw.get("reason") or ""
            candidate = candidate_by_id.get(cid) or {}
            if raw_decision in {"select", "needs_visual"} and (
                obvious_local_label_title(candidate.get("title_text") or "") or is_low_value_visual_reconsideration(raw_reason)
            ):
                raw_decision = "keep_rejected"
                raw_reason = (
                    f"{raw_reason} Filtered: obvious local label, formula, answer choice, or answer-key marker; "
                    "not eligible for outline promotion."
                ).strip()
            reconsider_by_id[cid] = {
                "decision": raw_decision,
                "reason": raw_reason,
                "confidence": raw.get("confidence") or "low",
            }
    for item in decision.get("rejected_candidates") or []:
        if item.get("candidate_id") in reconsider_by_id:
            item["llm_reconsideration"] = reconsider_by_id[item.get("candidate_id")]
            if reconsider_by_id[item.get("candidate_id")]["decision"] == "needs_visual":
                item["needs_visual"] = True
    decision["decision_method"] = "llm_reviewed_candidate_outline"
    decision["llm"] = {
        "enabled": True,
        "provider": provider,
        "model": model,
        "call_count": 1,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "estimated_cost": cost["estimated_cost"],
        "pricing": cost,
        "raw_usage": usage,
        "verdict": review.get("verdict") or "ok",
        "review_notes": review.get("review_notes") or [],
    }
    decision["needs_visual_count"] = sum(1 for item in decision.get("selected_outline", []) if item.get("needs_visual")) + sum(
        1 for item in decision.get("rejected_candidates", []) if item.get("needs_visual")
    )
    return rebuild_final_outline(decision, candidates)


def is_low_value_visual_reconsideration(reason: str) -> bool:
    text = (reason or "").lower()
    low_value_signals = [
        "page header",
        "running head",
        "exercise label",
        "rule label",
        "grammar rule label",
        "caption/diagram label",
        "caption or diagram label",
        "answer label",
        "not a heading",
        "not an outline heading",
        "depth is 4",
        "deep-level heading",
    ]
    return any(signal in text for signal in low_value_signals)


def maybe_review_with_llm(decision: dict[str, Any], candidates: list[dict[str, Any]], *, enabled: bool, base_url: str, model: str, timeout: int, max_risk_candidates: int) -> dict[str, Any]:
    if not enabled:
        return decision
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        decision["llm"]["note"] = "LLM review requested but DEEPSEEK_API_KEY is not set."
        decision["llm"]["requested"] = True
        return decision
    provider = "deepseek"
    payload = {
        "task": "global_outline_rebuild",
        "review_scope": "all_candidates_and_context",
        "outline_context": outline_context(decision, candidates),
        "unsafe_popo_outline_proposal": compact_selected(decision.get("selected_outline") or []),
        "candidate_pool": compact_candidate_pool(candidates, max_risk_candidates),
        "constraints": [
            "max heading level is 3",
            "do not invent textbook content",
            "LLM decides the full outline structure only; Markdown publishing is deterministic",
            "Popo outline and heading levels are candidates, not source truth",
            "Every final_outline row must cite one or more candidate_ids from candidate_pool",
            "Use source order and parent-child page ranges; reject local labels and prompts from the directory layer",
            "review_notes max 5",
        ],
    }
    try:
        review, usage = call_deepseek(api_key, base_url, model, payload, timeout)
    except Exception as exc:
        decision["llm"] = {
            "enabled": False,
            "requested": True,
            "provider": provider,
            "model": model,
            "call_count": 1,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "estimated_cost": 0,
            "error": str(exc),
        }
        return decision
    return apply_llm_review(decision, candidates, review, usage, provider, model)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a traceable outline decision artifact from candidates and current outline.")
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--outline", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--with-llm", action="store_true")
    parser.add_argument("--base-url", default=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
    parser.add_argument("--model", default=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"))
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--max-risk-candidates", type=int, default=80)
    args = parser.parse_args()
    candidates = load_jsonl(args.candidates)
    decision = build_decision(candidates, load_json(args.outline))
    decision = maybe_review_with_llm(
        decision,
        candidates,
        enabled=args.with_llm,
        base_url=args.base_url,
        model=args.model,
        timeout=args.timeout,
        max_risk_candidates=args.max_risk_candidates,
    )
    args.out.expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    args.out.expanduser().resolve().write_text(json.dumps(decision, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
