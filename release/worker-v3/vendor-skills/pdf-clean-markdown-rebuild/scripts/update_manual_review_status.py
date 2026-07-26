#!/usr/bin/env python3
import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path


APPROVED = {"approved", "pass", "passed"}
NEEDS_FIX = {"needs_fix", "failed", "fail"}
PENDING = {"pending"}
VALID_STATUS = APPROVED | NEEDS_FIX | PENDING
SCRIPT_DIR = Path(__file__).resolve().parent


def normalize(text):
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def entry_tokens(entry, index):
    return [
        str(index),
        normalize(entry.get("pdf_id")),
        normalize(entry.get("job_id")),
        normalize(entry.get("pdf_name")),
    ]


def match_entries(reviews, selector):
    selector_norm = normalize(selector)
    if not selector_norm:
        return []
    exact = []
    partial = []
    for index, entry in enumerate(reviews, 1):
        tokens = entry_tokens(entry, index)
        if selector_norm in tokens:
            exact.append((index, entry))
            continue
        name = normalize(entry.get("pdf_name"))
        if selector_norm in name:
            partial.append((index, entry))
    return exact or partial


def expand_selector(selector):
    text = str(selector or "").strip()
    if not text:
        return []
    compact = re.sub(r"\s+", "", text)
    numeric_probe = re.sub(r"[,，、；;~\-—–至到]", "", compact)
    if not numeric_probe or not numeric_probe.isdigit():
        return [text]

    parts = [part.strip() for part in re.split(r"[,，、；;]+", text) if part.strip()]
    expanded = []
    for part in parts:
        part_compact = re.sub(r"\s+", "", part)
        range_match = re.fullmatch(r"(\d+)(?:-|~|—|–|至|到)(\d+)", part_compact)
        if range_match:
            start = int(range_match.group(1))
            end = int(range_match.group(2))
            step = 1 if start <= end else -1
            expanded.extend(str(value) for value in range(start, end + step, step))
        else:
            expanded.append(part_compact)
    return expanded


def review_counts(reviews):
    approved = 0
    needs_fix = 0
    for entry in reviews:
        status = normalize(entry.get("status") or "pending")
        if status in APPROVED:
            approved += 1
        elif status in NEEDS_FIX:
            needs_fix += 1
    pending = len(reviews) - approved - needs_fix
    return {
        "total": len(reviews),
        "approved": approved,
        "pending": pending,
        "needs_fix": needs_fix,
        "fully_accepted": pending == 0 and needs_fix == 0,
    }


def pending_review_row_map(regression_root):
    if str(SCRIPT_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPT_DIR))
    import regression_minio_tasks as regression

    report_path = regression_root / "regression_report.json"
    if not report_path.exists():
        raise SystemExit(f"Missing regression report for pending row selection: {report_path}")
    report = load_json(report_path)
    pending_pairs = []
    for item in report.get("results") or []:
        status = normalize((item.get("manual_review") or {}).get("status") or "pending")
        if status in APPROVED:
            continue
        pending_pairs.append((regression.review_queue_item(item, regression_root), item))
    pending_pairs.sort(key=lambda pair: (-pair[0]["score"], pair[0]["pdf"].lower()))
    return {
        str(index): {
            "pdf_id": item.get("pdf_id"),
            "job_id": item.get("job_id"),
            "pdf_name": item.get("pdf_name"),
        }
        for index, (_, item) in enumerate(pending_pairs, 1)
    }


def main():
    parser = argparse.ArgumentParser(
        description="Update a pdf-clean-markdown-rebuild manual review status JSON ledger."
    )
    parser.add_argument("ledger", type=Path, help="manual_review_status_template.json or compatible review ledger.")
    parser.add_argument("--status", required=True, choices=sorted(VALID_STATUS))
    parser.add_argument("--select", action="append", default=[], help="1-based row number, pdf_id, job_id, unique PDF name fragment, or numeric list/range such as 1,2,3 / 1-3 / 1、2、3. May be repeated.")
    parser.add_argument("--row-scope", choices=["ledger", "pending"], default="ledger", help="Interpret numeric --select values as ledger row numbers or pending-review queue row numbers.")
    parser.add_argument("--regression-root", type=Path, help="Regression output root required when --row-scope pending is used.")
    parser.add_argument("--reviewer", default="user")
    parser.add_argument("--reviewed-at", default=date.today().isoformat())
    parser.add_argument("--notes", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.select:
        raise SystemExit("At least one --select value is required.")
    if args.row_scope == "pending" and not args.regression_root:
        raise SystemExit("--regression-root is required when --row-scope pending is used.")

    data = load_json(args.ledger)
    reviews = data.get("reviews") if isinstance(data, dict) else data
    if not isinstance(reviews, list):
        raise SystemExit("Ledger must be a JSON object with reviews[] or a reviews array.")

    selected = {}
    errors = []
    pending_rows = pending_review_row_map(args.regression_root) if args.row_scope == "pending" else {}
    selectors = []
    for raw_selector in args.select:
        selectors.extend(expand_selector(raw_selector))

    for selector in selectors:
        lookup_selector = selector
        if args.row_scope == "pending" and re.fullmatch(r"\d+", selector.strip()):
            pending_entry = pending_rows.get(selector.strip())
            if not pending_entry:
                errors.append(f"No pending-review row for selector: {selector}")
                continue
            lookup_selector = pending_entry.get("pdf_id") or pending_entry.get("job_id") or pending_entry.get("pdf_name") or selector
        matches = match_entries(reviews, selector)
        if args.row_scope == "pending" and lookup_selector != selector:
            matches = match_entries(reviews, lookup_selector)
        if not matches:
            errors.append(f"No match for selector: {selector}")
            continue
        if len(matches) > 1:
            labels = ", ".join(f"{idx}:{entry.get('pdf_name') or entry.get('pdf_id')}" for idx, entry in matches[:10])
            errors.append(f"Ambiguous selector: {selector} -> {labels}")
            continue
        index, entry = matches[0]
        selected[index] = entry
    if errors:
        raise SystemExit("\n".join(errors))

    changed = []
    for index, entry in sorted(selected.items()):
        before = {
            "status": entry.get("status") or "pending",
            "reviewer": entry.get("reviewer") or "",
            "reviewed_at": entry.get("reviewed_at") or "",
            "notes": entry.get("notes") or "",
        }
        entry["status"] = args.status
        entry["reviewer"] = args.reviewer
        entry["reviewed_at"] = args.reviewed_at
        if args.notes:
            entry["notes"] = args.notes
        elif args.status in APPROVED and not entry.get("notes"):
            entry["notes"] = "Manual outline tree and clean.md anchor placement spot-checked by user."
        elif args.status in NEEDS_FIX and not entry.get("notes"):
            entry["notes"] = "Manual review found issues; see user notes."
        after = {
            "status": entry.get("status") or "pending",
            "reviewer": entry.get("reviewer") or "",
            "reviewed_at": entry.get("reviewed_at") or "",
            "notes": entry.get("notes") or "",
        }
        changed.append({
            "index": index,
            "pdf_id": entry.get("pdf_id"),
            "job_id": entry.get("job_id"),
            "pdf_name": entry.get("pdf_name"),
            "before": before,
            "after": after,
        })

    if not args.dry_run:
        write_json(args.ledger, data)

    print(json.dumps({
        "ledger": str(args.ledger),
        "dry_run": args.dry_run,
        "changed": changed,
        "counts": review_counts(reviews),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
