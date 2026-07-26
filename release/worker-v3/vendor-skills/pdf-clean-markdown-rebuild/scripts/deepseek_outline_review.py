#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path


SYSTEM_PROMPT = """You review a textbook or teaching handout Markdown heading tree for semantic structure.
Return strict JSON only. Do not rewrite source content. Do not invent missing content.
Use only line numbers and heading text provided by the user.

Target hierarchy:
- For Section-based textbooks:
  # Section N
  ## Unit N title, Section N Review, or other source-visible section-level review
  ### Only real source-visible subsection headings; do not invent deeper headings from repeated local labels
- For Unit textbooks:
  # Unit N
  ## Unit theme/title or major reading/article/grammar numbered section/summary/review/from grammar to writing
  ### Exercise, comprehension, think about it, notes, grammar in use, form, use, examples, explanation, part, writing tip
- For Unit-based grammar workbooks:
  # Unit NN
  ## Grammar Point 1/2, Grammar Practice 1/2, Grammar Extension, Grammar in Passage, Grammar in Use, Self Check
  ## Review N, Mid-term Test, Final Test when they are source-visible main review/test sections
  ### Grammar topic titles, exercise prompts A/B/C, and local labels under the H2 section
  #### Small form labels such as Regular/Irregular, Subject/Verb/Object, Time/Place when nested under a grammar topic
- For non-Unit handouts or packets:
  # Main topic or top-level module in source order
  ## Subtopic, model variant, question type, or major task under the current main topic
  ### Local teaching labels such as 思路点拨, 变式拓展, 构建联系, 深入探究, 例题解析, 方法总结
  If the provided non-Unit heading list has no H1 headings, promote source-visible main topic/module headings to H1 instead of leaving the document with a flat H2-only top level.
- For Chinese math workbook or教辅 chapter structures:
  # 第N章 chapter title
  ## Numbered lesson/section such as 20.1, 20.2(1), 阶段训练N, 本章复习题
  ### Local lesson columns and exercise categories such as 要点归纳, 疑难分析, 基础训练, 拓展训练, 一、选择题, 二、填空题, 三、解答题, 四、作图题与解答题
  Captions such as （第4题） usually label a nearby figure or exercise and should not be deleted as content. If they are headings only because of OCR, demote them to a low heading level only when necessary instead of treating them as removable noise.

Flag:
- unit headings inserted too late or too early
- headings that should be demoted/promoted
- split headings that should be merged
- page labels/noise that should not be headings
- OCR spelling corrections only when obvious from adjacent heading fragments

Important:
- Do not merge generic labels such as FORM, USE, Notes, GRAMMAR IN USE, EXAMPLES, EXPLANATION, PART 1, or WRITING TIP into the preceding numbered section. Demote them instead.
- Only use merge_candidates for visual title fragments that together form one title, such as "The" + "CUBAN MISSILE CRISIS" or "TRAVEL BY AIR: The" + "DC-3".
- If headings clearly belong to the next unit because a standalone number/theme appears, report a missing_expected Unit heading before that line.
- For workbook heading trees, report every repeated heading pattern that needs level correction; do not leave local columns such as 要点归纳/疑难分析/基础训练/拓展训练 at the same level as numbered lessons.

Schema:
{
  "unit": "Unit N or Document",
  "verdict": "ok|needs_fix",
  "unit_heading_move": {"from_line": 0, "to_before_line": 0, "reason": ""} or null,
  "level_fixes": [{"line": 0, "from_level": 0, "to_level": 0, "reason": ""}],
  "merge_candidates": [{"lines": [0, 0], "merged_text": "", "reason": ""}],
  "noise_headings": [{"line": 0, "reason": ""}],
  "ocr_corrections": [{"line": 0, "current_text": "", "suggested_text": "", "reason": ""}],
  "missing_expected": [{"after_line": 0, "expected": "", "reason": ""}],
  "notes": []
}
"""


def is_unit_h1(text):
    return bool(re.match(r"^Unit\s+\d+\b", text, re.I))


def is_chapter_h1(text):
    return bool(re.match(r"^(Chapter\s+[A-Z]?\d+\b|第\s*\d+\s*章\b)", text, re.I))


def extract_heading_units(markdown):
    headings = []
    page = None
    for lineno, line in enumerate(markdown.splitlines(), 1):
        page_match = re.match(r"<!--\s*page_idx:\s*([^>]+?)\s*-->", line.strip())
        if page_match:
            page = page_match.group(1)
            continue
        heading_match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if heading_match:
            headings.append({
                "line": lineno,
                "level": len(heading_match.group(1)),
                "text": heading_match.group(2),
                "page_idx": page,
            })

    units = []
    current = None
    preface = []
    for heading in headings:
        if heading["level"] == 1 and is_unit_h1(heading["text"]):
            if current:
                units.append(current)
            current = {"unit": heading["text"], "headings": [heading]}
        elif current:
            current["headings"].append(heading)
        else:
            preface.append(heading)
    if current:
        units.append(current)
    if not units and headings:
        chapter_units = []
        current_chapter = None
        chapter_preface = []
        for heading in headings:
            if heading["level"] == 1 and is_chapter_h1(heading["text"]):
                if current_chapter:
                    chapter_units.append(current_chapter)
                current_chapter = {"unit": heading["text"], "headings": [heading]}
            elif current_chapter:
                current_chapter["headings"].append(heading)
            else:
                chapter_preface.append(heading)
        if current_chapter:
            chapter_units.append(current_chapter)
        if chapter_units:
            return chapter_preface, chapter_units
        preface = []
        units = [{"unit": "Document", "headings": headings}]
    return preface, units


def call_deepseek(api_key, base_url, model, unit_payload, timeout):
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(unit_payload, ensure_ascii=False)},
        ],
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"DeepSeek HTTP {exc.code}: {detail[:1000]}") from exc
    content = data["choices"][0]["message"]["content"]
    return json.loads(content)


def normalize_review_response(response):
    if isinstance(response, dict) and isinstance(response.get("reviews"), list):
        return [normalize_single_review(item) for item in response["reviews"] if isinstance(item, dict)]
    if isinstance(response, dict):
        return [normalize_single_review(response)]
    return []


def review_has_actionable_items(review):
    if review.get("unit_heading_move"):
        return True
    for key in ("level_fixes", "merge_candidates", "noise_headings", "ocr_corrections", "missing_expected"):
        if review.get(key):
            return True
    return False


def normalize_single_review(review):
    normalized = dict(review)
    for key in ("level_fixes", "merge_candidates", "noise_headings", "ocr_corrections", "missing_expected", "notes"):
        value = normalized.get(key)
        if not isinstance(value, list):
            normalized[key] = []
    if "unit_heading_move" not in normalized:
        normalized["unit_heading_move"] = None
    has_actions = review_has_actionable_items(normalized)
    verdict = str(normalized.get("verdict") or "").strip().lower()
    if not has_actions and verdict == "needs_fix":
        normalized["verdict"] = "ok"
        normalized["notes"].append("verdict_normalized_to_ok_no_actionable_items")
    elif has_actions and verdict != "needs_fix":
        normalized["verdict"] = "needs_fix"
        normalized["notes"].append("verdict_normalized_to_needs_fix_actionable_items")
    elif verdict not in {"ok", "needs_fix"}:
        normalized["verdict"] = "needs_fix" if has_actions else "ok"
        normalized["notes"].append("verdict_normalized_from_invalid_value")
    return normalized


def heading_is_review_worthy(heading):
    text = heading.get("text", "")
    if heading.get("level", 0) <= 2:
        return True
    return bool(re.search(
        r"\b(Unit|Chapter|Exercise|Review|Test|Summary|Grammar|Reading|Writing|Topic|Contents|Appendix|Glossary|Index)\b|第\s*\d+\s*章|复习|练习|测试|目录",
        text,
        re.I,
    ))


def compact_headings(headings, max_count):
    if max_count <= 0 or len(headings) <= max_count:
        return headings, False
    selected = []
    seen = set()

    def add(items):
        for item in items:
            key = item["line"]
            if key not in seen:
                selected.append(item)
                seen.add(key)

    add(headings[: max_count // 3])
    add([heading for heading in headings if heading_is_review_worthy(heading)])
    add(headings[- max(10, max_count // 4):])
    selected = sorted(selected, key=lambda item: item["line"])
    if len(selected) > max_count:
        keep = max_count
        head = keep // 2
        tail = keep - head
        selected = selected[:head] + selected[-tail:]
    return selected, True


def chunks(items, size):
    if size <= 0:
        yield items
        return
    for idx in range(0, len(items), size):
        yield items[idx: idx + size]


def main():
    parser = argparse.ArgumentParser(description="Use DeepSeek to review a clean.md heading tree and emit structured JSON suggestions.")
    parser.add_argument("markdown", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--units", help="Comma-separated unit numbers to review, e.g. 1,2,11. Default: all units.")
    parser.add_argument("--base-url", default=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
    parser.add_argument("--model", default=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"))
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--max-headings-per-unit", type=int, default=80)
    parser.add_argument("--batch-document", action="store_true", help="Send one compacted document-level request instead of one request per Unit/Chapter.")
    parser.add_argument("--batch-size", type=int, default=4, help="Maximum Unit/Chapter payloads per batch-document DeepSeek request.")
    args = parser.parse_args()

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("DEEPSEEK_API_KEY is not set.", file=sys.stderr)
        return 2

    markdown = args.markdown.read_text(encoding="utf-8")
    preface, units = extract_heading_units(markdown)
    wanted = None
    if args.units:
        wanted = {item.strip().lower() for item in args.units.split(",") if item.strip()}
        units = [
            unit for unit in units
            if unit["unit"].lower() in wanted
            or any(re.search(rf"\bunit\s+{re.escape(item)}\b", unit["unit"], re.I) for item in wanted)
        ]

    reviews = []
    errors = []
    compaction = []
    compacted_units = []
    for unit in units:
        compacted_headings, was_compacted = compact_headings(unit["headings"], args.max_headings_per_unit)
        compaction.append({
            "unit": unit["unit"],
            "original_heading_count": len(unit["headings"]),
            "sent_heading_count": len(compacted_headings),
            "compacted": was_compacted,
        })
        compacted_units.append({
            "unit": unit["unit"],
            "headings": compacted_headings,
            "original_heading_count": len(unit["headings"]),
        })

    if args.batch_document and compacted_units:
        for batch_index, unit_batch in enumerate(chunks(compacted_units, args.batch_size), 1):
            payload = {
                "task": "review_heading_tree_batch",
                "instructions": "Review each compacted Unit/Chapter heading tree. Return a JSON object with a reviews array; each item must follow the single-unit schema from the system prompt. Use only provided line numbers.",
                "units": unit_batch,
                "input_note": "Each Unit/Chapter may be compacted for long documents; line numbers still refer to source Markdown.",
            }
            try:
                reviews.extend(normalize_review_response(call_deepseek(os.environ["DEEPSEEK_API_KEY"], args.base_url, args.model, payload, args.timeout)))
            except Exception as exc:
                for unit in unit_batch:
                    single_payload = {
                        "task": "review_heading_tree",
                        "unit": unit["unit"],
                        "headings": unit["headings"],
                        "original_heading_count": unit["original_heading_count"],
                        "input_note": f"Fallback single-unit review after batch {batch_index} failed; line numbers still refer to source Markdown.",
                    }
                    try:
                        reviews.extend(normalize_review_response(call_deepseek(os.environ["DEEPSEEK_API_KEY"], args.base_url, args.model, single_payload, args.timeout)))
                    except Exception as single_exc:
                        errors.append({"unit": unit["unit"], "error": str(single_exc)})
    elif compacted_units:
        for unit in compacted_units:
            payload = {
                "task": "review_heading_tree",
                "unit": unit["unit"],
                "headings": unit["headings"],
                "original_heading_count": unit["original_heading_count"],
                "input_note": "Headings may be compacted for long units; line numbers still refer to source Markdown.",
            }
            try:
                reviews.extend(normalize_review_response(call_deepseek(os.environ["DEEPSEEK_API_KEY"], args.base_url, args.model, payload, args.timeout)))
            except Exception as exc:
                errors.append({"unit": unit["unit"], "error": str(exc)})

    result = {
        "source_markdown": str(args.markdown),
        "model": args.model,
        "base_url": args.base_url,
        "preface_heading_count": len(preface),
        "reviewed_unit_count": len(reviews),
        "compaction": compaction,
        "errors": errors,
        "reviews": reviews,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.out}")
    if errors:
        print(f"Completed with {len(errors)} unit errors.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
