#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path

try:
    from bootstrap_clean_markdown import markdown_to_html
except Exception:
    markdown_to_html = None

try:
    from outline_anchor_check import write_outline_anchor_check
except Exception:
    write_outline_anchor_check = None


def load_json(path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def normalize_space(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def candidate_id(entry, index):
    return f"{index}:{entry.get('start_page')}:{entry.get('level')}:{normalize_space(entry.get('title'))}"


def apply_outline(outline_doc, validation):
    decisions = {item.get("candidate_id"): item for item in validation.get("decisions") or []}
    kept = []
    removed = []
    revised = []
    for idx, entry in enumerate(outline_doc.get("outline") or []):
        cid = candidate_id(entry, idx)
        decision = decisions.get(cid)
        if entry.get("validation_required") and decision:
            if decision.get("needs_visual_review") or decision.get("decision") == "reject":
                removed.append({"candidate_id": cid, "entry": entry, "decision": decision})
                continue
            if decision.get("decision") == "revise" and decision.get("revised_title"):
                old = entry.get("title")
                entry = dict(entry)
                entry["title"] = decision["revised_title"]
                entry["validation_status"] = "revised_by_llm"
                entry["validation_reason"] = decision.get("reason")
                entry.pop("validation_required", None)
                revised.append({"candidate_id": cid, "old_title": old, "new_title": entry["title"]})
            else:
                entry = dict(entry)
                entry["validation_status"] = "accepted_by_llm"
                entry["validation_reason"] = decision.get("reason")
                entry.pop("validation_required", None)
        kept.append(entry)
    updated = dict(outline_doc)
    updated["outline"] = kept
    updated["candidate_validation_apply"] = {
        "removed_count": len(removed),
        "revised_count": len(revised),
    }
    return updated, removed, revised


def rewrite_markdown(markdown, removed, revised):
    remove_by_heading = {}
    for item in removed:
        entry = item["entry"]
        key = (int(entry.get("level") or 0), normalize_space(entry.get("title")))
        remove_by_heading[key] = entry.get("validation_required")
    revise_by_heading = {}
    for item in revised:
        revise_by_heading[normalize_space(item["old_title"])] = item["new_title"]

    out = []
    removed_lines = []
    revised_lines = []
    for line_no, line in enumerate(markdown.splitlines(), 1):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            level = len(match.group(1))
            title = normalize_space(match.group(2))
            reason = remove_by_heading.get((level, title))
            if reason:
                if reason == "generic_topic_title_unresolved":
                    removed_lines.append({"line": line_no, "title": title, "mode": "delete"})
                    continue
                removed_lines.append({"line": line_no, "title": title, "mode": "demote"})
                out.append(title)
                continue
            if title in revise_by_heading:
                new_title = revise_by_heading[title]
                revised_lines.append({"line": line_no, "old_title": title, "new_title": new_title})
                out.append(f"{match.group(1)} {new_title}")
                continue
        out.append(line)
    return "\n".join(out).strip() + "\n", removed_lines, revised_lines


def main():
    parser = argparse.ArgumentParser(description="Apply DeepSeek inferred-outline candidate validation to clean.md and popo_outline.json.")
    parser.add_argument("body_dir", type=Path)
    parser.add_argument("--title", default="")
    args = parser.parse_args()

    body_dir = args.body_dir.expanduser().resolve()
    outline_path = body_dir / "popo_outline.json"
    validation_path = body_dir / "outline_candidate_validation.json"
    clean_path = body_dir / "clean.md"
    if not outline_path.exists() or not validation_path.exists() or not clean_path.exists():
        raise SystemExit("Missing popo_outline.json, outline_candidate_validation.json, or clean.md")

    outline_doc = load_json(outline_path)
    validation = load_json(validation_path)
    updated_outline, removed, revised = apply_outline(outline_doc, validation)
    markdown = clean_path.read_text(encoding="utf-8")
    updated_md, removed_lines, revised_lines = rewrite_markdown(markdown, removed, revised)

    outline_path.write_text(json.dumps(updated_outline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    clean_path.write_text(updated_md, encoding="utf-8")
    if markdown_to_html:
        (body_dir / "preview.html").write_text(markdown_to_html(updated_md, title=args.title or body_dir.name), encoding="utf-8")
    if write_outline_anchor_check:
        write_outline_anchor_check(updated_md, body_dir, title=args.title or "Outline Anchor Check")
    report = {
        "removed_outline_entries": [
            {
                "candidate_id": item["candidate_id"],
                "title": item["entry"].get("title"),
                "level": item["entry"].get("level"),
                "reason": item["decision"].get("reason"),
            }
            for item in removed
        ],
        "revised_outline_entries": revised,
        "removed_markdown_lines": removed_lines,
        "revised_markdown_lines": revised_lines,
    }
    (body_dir / "outline_candidate_apply_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "removed": len(removed),
        "revised": len(revised),
        "report": str(body_dir / "outline_candidate_apply_report.json"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
