#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path


SYSTEM_PROMPT = """You validate inferred textbook outline candidates.
Return strict JSON only. Do not rewrite source content.

Task:
- Some headings were inferred by rules because MinerU/MinerU-Popo did not expose a clean TOC entry.
- Decide whether each candidate is supported by the provided evidence.
- Accept only when the candidate is source-visible or strongly supported by nearby document-tree evidence.
- Reject when it looks like an article title, exercise prompt, local label, page noise, back matter, or a rule hallucination.
- Use revise only when the candidate should be accepted with a small title correction from evidence.

Important:
- Rules may propose plausible missing headings. Plausible is not enough; require evidence.
- Preserve a max-3-level outline.
- Do not invent missing chapters/units from general knowledge.
- If MinerU/Popo evidence is insufficient, reject and set needs_visual_review=true.

Schema:
{
  "verdict": "ok|needs_fix|needs_visual_review",
  "decisions": [
    {
      "candidate_id": "string",
      "decision": "accept|reject|revise",
      "title": "current title",
      "revised_title": "only for revise, else empty string",
      "confidence": "high|medium|low",
      "reason": "short evidence-based reason",
      "needs_visual_review": true
    }
  ],
  "notes": []
}
"""


def load_json(path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def clean_text(value):
    value = str(value or "").replace("<|txt_split|>", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def call_deepseek(api_key, base_url, model, payload, timeout):
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
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


def tree_text(root):
    for name in ("popo_document_tree.txt", "document_tree.txt"):
        path = root / name
        if path.exists():
            return path.read_text(encoding="utf-8", errors="replace")
    return ""


def window(text, needle, radius=1800):
    if not text or not needle:
        return ""
    pos = text.lower().find(needle.lower())
    if pos < 0:
        return ""
    start = max(0, pos - radius)
    end = min(len(text), pos + len(needle) + radius)
    return text[start:end]


def compact_outline(outline, candidate_indexes, radius=3):
    keep = set()
    for idx in candidate_indexes:
        for j in range(max(0, idx - radius), min(len(outline), idx + radius + 1)):
            keep.add(j)
    rows = []
    for idx in sorted(keep):
        entry = outline[idx]
        rows.append({
            "index": idx,
            "level": entry.get("level"),
            "title": entry.get("title"),
            "start_page": entry.get("start_page"),
            "source": entry.get("source"),
            "validation_required": entry.get("validation_required"),
        })
    return rows


def candidate_id(entry, index):
    return f"{index}:{entry.get('start_page')}:{entry.get('level')}:{clean_text(entry.get('title'))}"


def build_candidates(outline):
    candidates = []
    indexes = []
    for idx, entry in enumerate(outline):
        if not entry.get("validation_required"):
            continue
        cid = candidate_id(entry, idx)
        item = {
            "candidate_id": cid,
            "index": idx,
            "title": entry.get("title"),
            "level": entry.get("level"),
            "kind": entry.get("kind"),
            "start_page": entry.get("start_page"),
            "anchor_title": entry.get("anchor_title"),
            "source": entry.get("source"),
            "validation_required": entry.get("validation_required"),
            "children_count": entry.get("children_count"),
            "parent_title": entry.get("parent_title"),
        }
        candidates.append(item)
        indexes.append(idx)
    return candidates, indexes


def normalize_decisions(response, candidates):
    known = {item["candidate_id"]: item for item in candidates}
    decisions = []
    for raw in response.get("decisions") or []:
        cid = str(raw.get("candidate_id") or "")
        if cid not in known:
            continue
        decision = str(raw.get("decision") or "").lower()
        if decision not in {"accept", "reject", "revise"}:
            decision = "reject"
        decisions.append({
            "candidate_id": cid,
            "decision": decision,
            "title": raw.get("title") or known[cid].get("title"),
            "revised_title": raw.get("revised_title") or "",
            "confidence": raw.get("confidence") or "low",
            "reason": raw.get("reason") or "",
            "needs_visual_review": bool(raw.get("needs_visual_review")),
        })
    decided = {item["candidate_id"] for item in decisions}
    for cid, candidate in known.items():
        if cid not in decided:
            decisions.append({
                "candidate_id": cid,
                "decision": "reject",
                "title": candidate.get("title"),
                "revised_title": "",
                "confidence": "low",
                "reason": "No explicit LLM decision returned for this candidate.",
                "needs_visual_review": True,
            })
    return decisions


def main():
    parser = argparse.ArgumentParser(description="Validate inferred Popo outline candidates with DeepSeek evidence review.")
    parser.add_argument("rebuild_input", type=Path)
    parser.add_argument("popo_outline", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--base-url", default=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
    parser.add_argument("--model", default=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"))
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--max-candidates", type=int, default=80)
    args = parser.parse_args()

    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("DEEPSEEK_API_KEY is not set.", file=sys.stderr)
        return 2

    root = args.rebuild_input.expanduser().resolve()
    outline_doc = load_json(args.popo_outline.expanduser().resolve())
    outline = outline_doc.get("outline") or []
    candidates, indexes = build_candidates(outline)
    limited = candidates[: args.max_candidates]
    limited_indexes = indexes[: args.max_candidates]
    text = tree_text(root)
    snippets = []
    for item in limited[:30]:
        snippet = window(text, item.get("title") or item.get("anchor_title") or "")
        if snippet:
            snippets.append({
                "candidate_id": item["candidate_id"],
                "tree_text_window": snippet[:3000],
            })

    if not limited:
        result = {
            "verdict": "ok",
            "model": args.model,
            "candidate_count": 0,
            "decisions": [],
            "errors": [],
            "notes": ["No inferred candidates require validation."],
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {args.out}")
        return 0

    payload = {
        "task": "validate_inferred_outline_candidates",
        "candidate_count": len(limited),
        "candidates": limited,
        "neighbor_outline": compact_outline(outline, limited_indexes),
        "tree_text_snippets": snippets,
        "input_note": "Use the candidates, neighboring outline, and document-tree snippets as evidence. Reject candidates that need PDF visual evidence.",
    }
    errors = []
    try:
        response = call_deepseek(api_key, args.base_url, args.model, payload, args.timeout)
        decisions = normalize_decisions(response, limited)
        verdict = response.get("verdict") or "ok"
        notes = response.get("notes") or []
    except Exception as exc:
        errors.append(str(exc))
        decisions = [
            {
                "candidate_id": item["candidate_id"],
                "decision": "reject",
                "title": item.get("title"),
                "revised_title": "",
                "confidence": "low",
                "reason": f"LLM validation failed: {exc}",
                "needs_visual_review": True,
            }
            for item in limited
        ]
        verdict = "needs_visual_review"
        notes = []

    result = {
        "verdict": "ok" if decisions and all(item.get("decision") in {"accept", "revise"} and not item.get("needs_visual_review") for item in decisions) and not errors else verdict,
        "model": args.model,
        "base_url": args.base_url,
        "candidate_count": len(candidates),
        "validated_candidate_count": len(limited),
        "truncated": len(candidates) > len(limited),
        "errors": errors,
        "decisions": decisions,
        "notes": notes,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.out}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
