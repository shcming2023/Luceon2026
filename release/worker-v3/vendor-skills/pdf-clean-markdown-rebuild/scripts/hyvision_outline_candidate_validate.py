#!/usr/bin/env python3
import argparse
import base64
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import fitz


SYSTEM_PROMPT = """You validate textbook outline candidates from PDF page images.
Return strict JSON only. Do not rewrite source content.

Task:
- Look at the provided PDF page image.
- Decide whether the page visibly contains a real source-book outline heading for the candidate location.
- Accept/revise only when a chapter/unit/section/topic heading is visible on the page and suitable for a max-3-level textbook outline.
- Reject when the candidate is only a generated placeholder, local label, exercise prompt, body paragraph, page header/footer, or not visibly supported.

Schema:
{
  "decision": "accept|reject|revise",
  "visible_title": "exact visible title if accepted or revised, else empty string",
  "confidence": "high|medium|low",
  "reason": "short visual-evidence reason",
  "needs_manual_review": false
}
"""


def load_json(path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def candidate_id(entry, index):
    return f"{index}:{entry.get('start_page')}:{entry.get('level')}:{clean_text(entry.get('title'))}"


def is_generic_topic_marker(text):
    return bool(re.fullmatch(r"Chapter\s+\d+\s*\.\s*Topic\s+\d+", clean_text(text), re.I))


def find_pdf(root):
    matches = sorted(root.glob("*_origin.pdf"))
    if matches:
        return matches[0]
    matches = sorted(root.rglob("*.pdf"))
    return matches[0] if matches else None


def render_page(pdf_path, page_index, out_path, zoom=2.0):
    doc = fitz.open(pdf_path)
    if page_index < 0 or page_index >= doc.page_count:
        raise IndexError(f"page_index out of range: {page_index} / {doc.page_count}")
    page = doc.load_page(page_index)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    pix.save(out_path)


def image_data_url(path):
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def parse_json_object(content):
    content = str(content or "").strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.I)
        content = re.sub(r"\s*```$", "", content)
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for match in re.finditer(r"\{", content):
            try:
                obj, _ = decoder.raw_decode(content[match.start():])
                return obj
            except json.JSONDecodeError:
                continue
        raise


def call_hyvision(api_key, base_url, model, payload, image_url, timeout):
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(payload, ensure_ascii=False),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": image_url},
                    },
                ],
            },
        ],
        "stream": False,
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
        raise RuntimeError(f"HY Vision HTTP {exc.code}: {detail[:1000]}") from exc
    content = data["choices"][0]["message"]["content"]
    return parse_json_object(content)


def normalize_visual_decision(raw, candidate, error=None):
    if error:
        return {
            "candidate_id": candidate["candidate_id"],
            "decision": "reject",
            "title": candidate.get("title"),
            "revised_title": "",
            "confidence": "low",
            "reason": f"HY Vision validation failed: {error}",
            "needs_visual_review": True,
            "visual_evidence": {"error": str(error)},
        }
    decision = str(raw.get("decision") or "").lower()
    if decision not in {"accept", "reject", "revise"}:
        decision = "reject"
    visible_title = clean_text(raw.get("visible_title"))
    if decision in {"accept", "revise"} and not visible_title:
        decision = "reject"
    generic_topic_marker = is_generic_topic_marker(visible_title)
    if generic_topic_marker:
        decision = "reject"
    if decision in {"accept", "revise"} and visible_title and visible_title != clean_text(candidate.get("title")):
        decision = "revise"
    revised_title = visible_title if decision == "revise" else ""
    reason = raw.get("reason") or "Visual page evidence reviewed."
    if generic_topic_marker:
        reason = "Visual page shows only a generic Chapter/Topic marker, not a meaningful new outline title."
    return {
        "candidate_id": candidate["candidate_id"],
        "decision": decision,
        "title": candidate.get("title"),
        "revised_title": revised_title,
        "confidence": raw.get("confidence") or "low",
        "reason": reason,
        "needs_visual_review": bool(raw.get("needs_manual_review")),
        "visual_evidence": {
            "model": candidate.get("model"),
            "page_index": candidate.get("page_index"),
            "visible_title": visible_title,
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Resolve needs_visual_review outline candidates with HY Vision page-image evidence.")
    parser.add_argument("rebuild_input", type=Path)
    parser.add_argument("popo_outline", type=Path)
    parser.add_argument("validation_json", type=Path)
    parser.add_argument("--out", type=Path, help="Defaults to overwriting validation_json.")
    parser.add_argument("--base-url", default=os.environ.get("HY_VISION_BASE_URL", "https://tokenhub.tencentmaas.com/v1"))
    parser.add_argument("--model", default=os.environ.get("HY_VISION_MODEL", "hy-vision-2.0-instruct"))
    parser.add_argument("--api-key-env", default="HY_VISION_API_KEY")
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--max-candidates", type=int, default=40)
    args = parser.parse_args()

    api_key = os.environ.get(args.api_key_env) or os.environ.get("TENCENTMAAS_API_KEY")
    if not api_key:
        print(f"{args.api_key_env} is not set.", file=sys.stderr)
        return 2

    root = args.rebuild_input.expanduser().resolve()
    outline_doc = load_json(args.popo_outline.expanduser().resolve())
    validation = load_json(args.validation_json.expanduser().resolve())
    out_path = args.out.expanduser().resolve() if args.out else args.validation_json.expanduser().resolve()
    pdf_path = find_pdf(root)
    if not pdf_path:
        print("No source PDF found in rebuild_input.", file=sys.stderr)
        return 2

    decisions = {item.get("candidate_id"): dict(item) for item in validation.get("decisions") or []}
    candidates = []
    for idx, entry in enumerate(outline_doc.get("outline") or []):
        cid = candidate_id(entry, idx)
        decision = decisions.get(cid)
        if not entry.get("validation_required") or not decision or not decision.get("needs_visual_review"):
            continue
        page_index = entry.get("start_page_idx")
        if page_index is None:
            start_page = entry.get("start_page")
            page_index = int(start_page) - 1 if isinstance(start_page, int) else None
        if page_index is None:
            continue
        item = dict(entry)
        item["candidate_id"] = cid
        item["page_index"] = int(page_index)
        item["model"] = args.model
        candidates.append(item)

    limited = candidates[: args.max_candidates]
    errors = []
    visual_results = []
    with tempfile.TemporaryDirectory(prefix="hyvision-outline-") as tmp:
        tmp_dir = Path(tmp)
        for candidate in limited:
            image_path = tmp_dir / f"page-{candidate['page_index']}.png"
            try:
                render_page(pdf_path, candidate["page_index"], image_path)
                payload = {
                    "task": "validate_outline_candidate_from_pdf_image",
                    "candidate_id": candidate["candidate_id"],
                    "candidate_title": candidate.get("title"),
                    "candidate_level": candidate.get("level"),
                    "candidate_start_page": candidate.get("start_page"),
                    "candidate_page_index": candidate.get("page_index"),
                    "candidate_reason": decisions[candidate["candidate_id"]].get("reason"),
                    "instruction": "If a real source-visible outline heading is present, return the exact visible title. If not, reject.",
                }
                raw = call_hyvision(api_key, args.base_url, args.model, payload, image_data_url(image_path), args.timeout)
                visual_decision = normalize_visual_decision(raw, candidate)
            except Exception as exc:
                if is_generic_topic_marker(candidate.get("title")):
                    visual_decision = {
                        "candidate_id": candidate["candidate_id"],
                        "decision": "reject",
                        "title": candidate.get("title"),
                        "revised_title": "",
                        "confidence": "low",
                        "reason": f"Visual validation was inconclusive, and the candidate is only a generic Chapter/Topic marker: {exc}",
                        "needs_visual_review": False,
                        "visual_evidence": {
                            "model": args.model,
                            "page_index": candidate.get("page_index"),
                            "error": str(exc),
                        },
                    }
                else:
                    errors.append({"candidate_id": candidate["candidate_id"], "error": str(exc)})
                    visual_decision = normalize_visual_decision({}, candidate, error=exc)
            decisions[candidate["candidate_id"]] = visual_decision
            visual_results.append(visual_decision)

    validation["decisions"] = list(decisions.values())
    validation["visual_validation"] = {
        "model": args.model,
        "base_url": args.base_url,
        "candidate_count": len(candidates),
        "validated_candidate_count": len(limited),
        "truncated": len(candidates) > len(limited),
        "errors": errors,
        "results": visual_results,
    }
    validation["truncated"] = bool(validation.get("truncated")) or len(candidates) > len(limited)
    if errors:
        validation["errors"] = (validation.get("errors") or []) + [item["error"] for item in errors]
    unresolved = [item for item in validation["decisions"] if item.get("needs_visual_review")]
    validation["verdict"] = "needs_visual_review" if unresolved or errors else "ok"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "out": str(out_path),
        "candidate_count": len(candidates),
        "validated_candidate_count": len(limited),
        "unresolved_count": len(unresolved),
        "error_count": len(errors),
    }, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
