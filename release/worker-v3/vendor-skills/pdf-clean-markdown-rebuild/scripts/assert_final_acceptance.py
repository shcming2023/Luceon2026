#!/usr/bin/env python3
import argparse
import json
from pathlib import Path


def gate_path(path):
    path = Path(path)
    if path.is_dir():
        return path / "final_acceptance_gate.json"
    return path


def load_gate(path):
    path = gate_path(path)
    if not path.exists():
        raise SystemExit(f"Missing final acceptance gate: {path}")
    return path, json.loads(path.read_text(encoding="utf-8"))


def suggestion_for(check_name, gate_file):
    root = gate_file.parent
    if check_name == "human_review_all_approved":
        return f"Review pending samples, then update statuses using {root / 'manual_review_command_sheet.md'}."
    if check_name == "regression_stability_comparison_passed":
        return "Run finalize_regression_review.py with --baseline-root, or run compare_regression_stability.py before refreshing final artifacts."
    if check_name == "outline_inventory_links_all_valid":
        return f"Inspect {root / 'outline_inventory_link_audit.md'} and regenerate outline-view artifacts."
    if check_name == "manual_anchor_spotcheck_links_all_valid":
        return f"Inspect {root / 'manual_anchor_link_audit.md'} and regenerate manual_anchor_spotcheck.html / outline-view artifacts."
    if check_name == "outline_anchor_integrity_all_passed":
        return f"Inspect {root / 'anchor_integrity_audit.md'} and the affected outline-view.html files."
    if check_name == "no_false_gates":
        return f"Inspect {root / 'regression_report.md'} and repair the failing QA gates."
    if check_name == "mechanical_qa_all_passed":
        return f"Inspect {root / 'regression_report.md'} and rerun regression after fixing failed samples."
    return "Inspect final_acceptance_gate.md and related review artifacts."


def main():
    parser = argparse.ArgumentParser(
        description="Assert that a pdf-clean-markdown-rebuild regression root has final_acceptance_gate.json accepted=true."
    )
    parser.add_argument("path", type=Path, help="Regression root or final_acceptance_gate.json.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable assertion result.")
    args = parser.parse_args()

    path, gate = load_gate(args.path)
    failed = []
    for check in gate.get("checks") or []:
        if check.get("ok"):
            continue
        enriched = dict(check)
        enriched["suggestion"] = suggestion_for(check.get("name"), path)
        failed.append(enriched)
    result = {
        "ok": bool(gate.get("accepted")) and not failed,
        "path": str(path),
        "status": gate.get("status"),
        "accepted": bool(gate.get("accepted")),
        "failed_checks": failed,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"{'PASS' if result['ok'] else 'FAIL'} {path}")
        print(f"status={result['status']} accepted={result['accepted']}")
        if failed:
            print("failed checks:")
            for check in failed:
                print(f"- {check.get('name')}: {check.get('detail')}")
                print(f"  next: {check.get('suggestion')}")
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
