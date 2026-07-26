#!/usr/bin/env python3
import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

import fitz


SYSTEM_PROMPT = """You verify textbook outline candidates from PDF page images.
Return strict JSON only.

Task:
- Look at the PDF page image.
- Decide whether the candidate title is visibly supported as a source-book outline heading.
- Accept/revise only when source-visible heading evidence exists.
- Reject page headers, footers, local labels, exercise prompts, captions, or body text.
- Do not invent textbook content.

Schema:
{
  "decision": "accept|reject|revise|uncertain",
  "visible_title": "exact visible title if present",
  "confidence": "high|medium|low",
  "reason": "short visual-evidence reason"
}
"""


def load_json(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


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


def outline_item_page(item: dict[str, Any]) -> int | None:
    page = parse_page_value(item.get("page") or item.get("start_page"))
    if page is not None:
        return page
    evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
    candidate_pages = evidence.get("candidate_pages") if isinstance(evidence.get("candidate_pages"), list) else []
    for value in candidate_pages:
        page = parse_page_value(value)
        if page is not None:
            return page
    return parse_page_value(evidence.get("printed_page") or evidence.get("page") or evidence.get("start_page"))


def should_reject_on_vision_failure(item: dict[str, Any], error: Exception) -> bool:
    candidate_id = str(item.get("candidate_id") or "")
    reason = str(item.get("reason") or "").lower()
    title = clean_text(item.get("title"))
    source = str(item.get("source") or "")
    error_text = str(error).lower()
    sentence_like = bool(re.search(r"[.!?。！？]$", title)) or len(title.split()) >= 9
    image_candidate = candidate_id.startswith("imag-")
    caption_reason = "caption" in reason or "image" in reason or "unlikely heading" in reason
    provider_refused = "data_inspection_failed" in error_text or "internalerror.algo.datainspectionfailed" in error_text
    return source == "final_outline" and image_candidate and (caption_reason or sentence_like or provider_refused)


def find_pdf(root: Path) -> Path | None:
    matches = sorted(root.glob("*_origin.pdf"))
    if matches:
        return matches[0]
    matches = sorted(root.rglob("*.pdf"))
    return matches[0] if matches else None


def render_page(pdf_path: Path, page_index: int, out_path: Path, zoom: float = 1.5) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    if page_index < 0 or page_index >= doc.page_count:
        raise IndexError(f"page_index out of range: {page_index} / {doc.page_count}")
    page = doc.load_page(page_index)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    pix.save(out_path)


def image_data_url(path: Path) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def parse_json_object(content: str) -> dict[str, Any]:
    content = str(content or "").strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.I)
        content = re.sub(r"\s*```$", "", content)
    try:
        parsed = json.loads(content)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for match in re.finditer(r"\{", content):
            try:
                obj, _ = decoder.raw_decode(content[match.start():])
                return obj if isinstance(obj, dict) else {}
            except json.JSONDecodeError:
                continue
        raise


def call_vision(api_key: str, base_url: str, model: str, payload: dict[str, Any], image_url: str, timeout: int) -> tuple[dict[str, Any], dict[str, Any]]:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": json.dumps(payload, ensure_ascii=False)},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            },
        ],
        "stream": False,
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
        raise RuntimeError(f"Vision HTTP {exc.code}: {detail[:1000]}") from exc
    content = data["choices"][0]["message"]["content"]
    return parse_json_object(content), data.get("usage") or {}


def visual_candidates(decision: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in decision.get("final_outline") or []:
        if item.get("needs_visual"):
            rows.append(
                {
                    "candidate_id": ",".join(item.get("candidate_ids") or []) or f"final:{item.get('order')}",
                    "title": item.get("title"),
                    "page": outline_item_page(item),
                    "level": item.get("level"),
                    "parent_title": item.get("parent_title"),
                    "source": "final_outline",
                    "reason": ((item.get("llm_decision") or {}).get("reason")
                               or (item.get("llm_reconsideration") or {}).get("reason")
                               or "final outline node flagged needs_visual"),
                }
            )
    for item in decision.get("selected_outline") or []:
        if item.get("needs_visual") or (item.get("llm_decision") or {}).get("decision") == "needs_visual":
            rows.append(
                {
                    "candidate_id": ",".join(item.get("candidate_ids") or []) or f"selected:{item.get('order')}",
                    "title": item.get("title"),
                    "page": outline_item_page(item),
                    "level": item.get("level"),
                    "parent_title": item.get("parent_title"),
                    "source": "selected_outline",
                    "reason": (item.get("llm_decision") or {}).get("reason") or "candidate flagged needs_visual",
                }
            )
    for item in decision.get("rejected_candidates") or []:
        reconsider = item.get("llm_reconsideration") or {}
        if reconsider.get("decision") == "needs_visual":
            rows.append(
                {
                    "candidate_id": item.get("candidate_id"),
                    "title": item.get("title"),
                    "page": outline_item_page(item),
                    "level": None,
                    "parent_title": item.get("parent_hint"),
                    "source": "rejected_candidate",
                    "reason": reconsider.get("reason") or "candidate flagged needs_visual",
                }
            )
    seen = set()
    out = []
    for row in rows:
        key = (row.get("candidate_id"), row.get("page"))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    priority = {"final_outline": 0, "selected_outline": 1, "rejected_candidate": 2}
    return sorted(out, key=lambda row: (priority.get(row.get("source"), 9), row.get("page") or 999999, row.get("title") or ""))


def build_visual_decisions(rebuild_input: Path, outline_decision: Path, out_dir: Path, *, enabled: bool, max_candidates: int, timeout: int) -> dict[str, Any]:
    root = rebuild_input.expanduser().resolve()
    decision = load_json(outline_decision.expanduser().resolve())
    pdf_path = find_pdf(root)
    evidence_dir = out_dir / "visual_evidence"
    candidates = visual_candidates(decision)
    limited = candidates[:max_candidates]
    provider = "none"
    api_key = os.getenv("HY_VISION_API_KEY") or os.getenv("TENCENTMAAS_API_KEY")
    base_url = os.getenv("HY_VISION_BASE_URL", "https://tokenhub.tencentmaas.com/v1")
    model = os.getenv("HY_VISION_MODEL", "hy-vision-2.0-instruct")
    if api_key:
        provider = "hyvision"
    else:
        api_key = os.getenv("DASHSCOPE_API_KEY")
        base_url = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        model = os.getenv("VISION_MODEL") or os.getenv("DASHSCOPE_VISION_MODEL") or "qwen3.7-plus"
        if api_key:
            provider = "dashscope"
    results = []
    usage_totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    errors = []
    if not pdf_path:
        errors.append("No source PDF found in rebuild_input.")

    def verify_on_page(item: dict[str, Any], page_index: int, *, retry_label: str = "") -> dict[str, Any]:
        page_number = page_index + 1
        image_rel = ""
        if pdf_path:
            image_path = evidence_dir / f"page-{page_number:04d}.png"
            try:
                render_page(pdf_path, page_index, image_path)
                image_rel = str(image_path.relative_to(out_dir))
            except Exception as exc:
                errors.append(f"{item.get('candidate_id')}: render failed: {exc}")
        visual_result = {
            "candidate_id": item.get("candidate_id"),
            "title": item.get("title"),
            "page": page_number,
            "page_index": page_index,
            "parent_title": item.get("parent_title"),
            "source": item.get("source"),
            "input_reason": item.get("reason"),
            "page_image": image_rel,
            "decision": "pending_visual_review",
            "visible_title": "",
            "confidence": "low",
            "reason": "Vision verification not enabled or API key missing.",
        }
        if retry_label:
            visual_result["retry_label"] = retry_label
        if enabled and api_key and image_rel:
            try:
                raw, usage = call_vision(
                    api_key,
                    base_url,
                    model,
                    {
                        "candidate_id": item.get("candidate_id"),
                        "candidate_title": item.get("title"),
                        "candidate_level": item.get("level"),
                        "candidate_page": page_number,
                        "parent_title": item.get("parent_title"),
                        "reason_for_visual_check": item.get("reason"),
                    },
                    image_data_url(out_dir / image_rel),
                    timeout,
                )
                visual_result.update(
                    {
                        "decision": raw.get("decision") or "uncertain",
                        "visible_title": clean_text(raw.get("visible_title")),
                        "confidence": raw.get("confidence") or "low",
                        "reason": raw.get("reason") or "Visual model returned no reason.",
                    }
                )
                usage_totals["prompt_tokens"] += int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
                usage_totals["completion_tokens"] += int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
                usage_totals["total_tokens"] += int(usage.get("total_tokens") or 0)
            except Exception as exc:
                if should_reject_on_vision_failure(item, exc):
                    visual_result.update(
                        {
                            "decision": "reject",
                            "confidence": "low",
                            "reason": (
                                "Vision verification failed and the candidate is image/caption-like; "
                                f"rejecting outline promotion without positive visual support. Error: {exc}"
                            ),
                        }
                    )
                else:
                    visual_result["reason"] = f"Vision verification failed: {exc}"
                    errors.append(f"{item.get('candidate_id')}: {exc}")
        return visual_result

    for item in limited:
        page = parse_page_value(item.get("page"))
        page_index = page - 1 if page is not None else None
        if page_index is None:
            visual_result = verify_on_page(item, -1)
        else:
            visual_result = verify_on_page(item, page_index)
            if (
                enabled
                and api_key
                and item.get("source") == "selected_outline"
                and visual_result.get("decision") in {"reject", "uncertain"}
            ):
                original_result = dict(visual_result)
                retries = []
                for retry_index, retry_label in ((page_index - 1, "previous_page"), (page_index + 1, "next_page")):
                    if retry_index < 0:
                        continue
                    retry_result = verify_on_page(item, retry_index, retry_label=retry_label)
                    retries.append(retry_result)
                    if retry_result.get("decision") == "accept":
                        visual_result = dict(retry_result)
                        visual_result["accepted_after_retry"] = True
                        break
                if retries:
                    visual_result["original_result"] = original_result
                    visual_result["retry_results"] = [dict(row) for row in retries]
        results.append(visual_result)
    return {
        "schema": "luceon-visual-decisions/v1",
        "created_at": datetime.utcnow().isoformat() + "Z",
        "enabled": bool(enabled and api_key),
        "provider": provider if enabled and api_key else None,
        "model": model if enabled and api_key else None,
        "candidate_count": len(candidates),
        "validated_count": len(limited),
        "truncated": len(candidates) > len(limited),
        "usage": usage_totals,
        "errors": errors,
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build visual verification decisions and PDF page evidence for outline candidates.")
    parser.add_argument("rebuild_input", type=Path)
    parser.add_argument("outline_decision", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--with-vision", action="store_true")
    parser.add_argument("--max-candidates", type=int, default=40)
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()
    out_path = args.out.expanduser().resolve()
    result = build_visual_decisions(
        args.rebuild_input,
        args.outline_decision,
        out_path.parent,
        enabled=args.with_vision,
        max_candidates=args.max_candidates,
        timeout=args.timeout,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
