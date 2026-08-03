#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import regression_minio_tasks as regression


def load_report(out_root):
    path = out_root / "regression_report.json"
    if not path.exists():
        raise SystemExit(f"Missing regression report: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(
        description="Refresh review/acceptance artifacts from an existing regression_report.json without rebuilding samples."
    )
    parser.add_argument("out_root", type=Path, help="Existing regression output root.")
    parser.add_argument("--manual-review-status", type=Path, help="Optional updated manual review ledger JSON.")
    args = parser.parse_args()

    report = load_report(args.out_root)
    results = report.get("results") or []
    if args.manual_review_status:
        status_map = regression.load_manual_review_status(args.manual_review_status)
        for item in results:
            item["manual_review"] = regression.manual_review_for(item, status_map)

    discovery = {
        "buckets": report.get("buckets") or {},
        "total_task_count": report.get("total_task_count") or len(results),
    }
    refreshed = regression.write_reports(args.out_root, discovery, results)
    print(json.dumps({
        "out_root": str(args.out_root),
        "tested_count": refreshed.get("tested_count"),
        "passed_count": refreshed.get("passed_count"),
        "failed_count": refreshed.get("failed_count"),
        "accepted_count": refreshed.get("accepted_count"),
        "pending_manual_review_count": refreshed.get("pending_manual_review_count"),
        "needs_fix_count": refreshed.get("needs_fix_count"),
        "fully_accepted": refreshed.get("fully_accepted"),
        "review_index": str(args.out_root / "review-index.html"),
        "final_acceptance_gate": str(args.out_root / "final_acceptance_gate.md"),
        "final_acceptance_gate_json": str(args.out_root / "final_acceptance_gate.json"),
        "objective_completion_audit": str(args.out_root / "objective_completion_audit.md"),
        "objective_completion_audit_html": str(args.out_root / "objective_completion_audit.html"),
        "regression_stability_snapshot": str(args.out_root / "regression_stability_snapshot.json"),
        "outline_inventory": str(args.out_root / "outline_inventory.md"),
        "outline_inventory_html": str(args.out_root / "outline_inventory.html"),
        "outline_inventory_link_audit": str(args.out_root / "outline_inventory_link_audit.md"),
        "manual_anchor_spotcheck": str(args.out_root / "manual_anchor_spotcheck.md"),
        "manual_anchor_spotcheck_html": str(args.out_root / "manual_anchor_spotcheck.html"),
        "manual_anchor_link_audit": str(args.out_root / "manual_anchor_link_audit.md"),
        "manual_review_decision_sheet": str(args.out_root / "manual_review_decision_sheet.md"),
        "manual_review_decision_sheet_html": str(args.out_root / "manual_review_decision_sheet.html"),
        "manual_review_command_sheet": str(args.out_root / "manual_review_command_sheet.md"),
        "pending_manual_fact_review_summary": str(args.out_root / "pending_manual_fact_review_summary.md"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
