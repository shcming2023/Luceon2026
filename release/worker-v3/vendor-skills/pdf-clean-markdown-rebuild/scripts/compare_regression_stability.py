#!/usr/bin/env python3
import argparse
import json
from pathlib import Path


APPROVED = {"approved", "pass", "passed"}


def snapshot_path(path):
    path = Path(path)
    if path.is_dir():
        return path / "regression_stability_snapshot.json"
    return path


def load_snapshot(path):
    path = snapshot_path(path)
    if not path.exists():
        raise SystemExit(f"Missing stability snapshot: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def sample_key(sample):
    return sample.get("pdf_id") or sample.get("job_id") or sample.get("pdf_name")


def compare_snapshots(baseline, current):
    base_samples = {sample_key(sample): sample for sample in baseline.get("samples") or []}
    current_samples = {sample_key(sample): sample for sample in current.get("samples") or []}
    failures = []
    warnings = []

    for key, base in base_samples.items():
        cur = current_samples.get(key)
        label = base.get("pdf_name") or key
        if not cur:
            failures.append({
                "pdf": label,
                "check": "sample_missing",
                "detail": "Sample existed in baseline but is absent from current snapshot.",
            })
            continue

        if base.get("qa_ok") and not cur.get("qa_ok"):
            failures.append({
                "pdf": label,
                "check": "mechanical_qa_regressed",
                "detail": "Baseline QA passed, current QA does not pass.",
            })
        if not base.get("false_gates") and cur.get("false_gates"):
            failures.append({
                "pdf": label,
                "check": "false_gates_added",
                "detail": ", ".join(cur.get("false_gates") or []),
            })
        if base.get("anchor_ok") and not cur.get("anchor_ok"):
            failures.append({
                "pdf": label,
                "check": "anchor_integrity_regressed",
                "detail": "Baseline outline anchors passed, current outline anchors do not pass.",
            })
        if int(cur.get("anchor_count") or 0) < int(base.get("anchor_count") or 0):
            warnings.append({
                "pdf": label,
                "check": "anchor_count_decreased",
                "detail": f"{base.get('anchor_count')} -> {cur.get('anchor_count')}",
            })
        if int(cur.get("image_refs") or 0) < int(base.get("image_refs") or 0):
            failures.append({
                "pdf": label,
                "check": "image_refs_decreased",
                "detail": f"{base.get('image_refs')} -> {cur.get('image_refs')}",
            })
        if not base.get("missing_images") and cur.get("missing_images"):
            failures.append({
                "pdf": label,
                "check": "missing_images_added",
                "detail": ", ".join(cur.get("missing_images") or []),
            })

        base_heading_sig = base.get("heading_signature_sha256")
        cur_heading_sig = cur.get("heading_signature_sha256")
        if base_heading_sig and cur_heading_sig and base_heading_sig != cur_heading_sig:
            row = {
                "pdf": label,
                "check": "heading_signature_changed",
                "detail": f"heading_count {base.get('heading_count')} -> {cur.get('heading_count')}",
            }
            if (base.get("manual_status") or "").lower() in APPROVED:
                failures.append(row)
            else:
                warnings.append(row)

        if base.get("h1_signature_sha256") != cur.get("h1_signature_sha256"):
            row = {
                "pdf": label,
                "check": "h1_signature_changed",
                "detail": "Top-level structure changed.",
            }
            if (base.get("manual_status") or "").lower() in APPROVED:
                failures.append(row)
            else:
                warnings.append(row)

    for key, cur in current_samples.items():
        if key not in base_samples:
            warnings.append({
                "pdf": cur.get("pdf_name") or key,
                "check": "sample_added",
                "detail": "Sample is present in current snapshot but absent from baseline.",
            })

    return {
        "ok": not failures,
        "baseline_sample_count": len(base_samples),
        "current_sample_count": len(current_samples),
        "failure_count": len(failures),
        "warning_count": len(warnings),
        "failures": failures,
        "warnings": warnings,
    }


def write_markdown(path, result, baseline_path, current_path):
    lines = [
        "# Regression Stability Comparison",
        "",
        f"Status: **{'PASS' if result['ok'] else 'FAIL'}**",
        "",
        f"- Baseline: `{baseline_path}`",
        f"- Current: `{current_path}`",
        f"- Baseline samples: {result['baseline_sample_count']}",
        f"- Current samples: {result['current_sample_count']}",
        f"- Failures: {result['failure_count']}",
        f"- Warnings: {result['warning_count']}",
        "",
        "## Failures",
        "",
    ]
    if result["failures"]:
        lines.extend(["| PDF | Check | Detail |", "|---|---|---|"])
        for row in result["failures"]:
            lines.append(f"| {row['pdf']} | {row['check']} | {row['detail']} |")
    else:
        lines.append("No blocking stability regressions detected.")

    lines.extend(["", "## Warnings", ""])
    if result["warnings"]:
        lines.extend(["| PDF | Check | Detail |", "|---|---|---|"])
        for row in result["warnings"]:
            lines.append(f"| {row['pdf']} | {row['check']} | {row['detail']} |")
    else:
        lines.append("No warnings.")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Compare pdf-clean-markdown-rebuild regression stability snapshots.")
    parser.add_argument("baseline", type=Path, help="Baseline regression root or regression_stability_snapshot.json.")
    parser.add_argument("current", type=Path, help="Current regression root or regression_stability_snapshot.json.")
    parser.add_argument("--out-json", type=Path)
    parser.add_argument("--out-md", type=Path)
    args = parser.parse_args()

    baseline_path = snapshot_path(args.baseline)
    current_path = snapshot_path(args.current)
    result = compare_snapshots(load_snapshot(baseline_path), load_snapshot(current_path))
    if args.out_json:
        args.out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.out_md:
        write_markdown(args.out_md, result, baseline_path, current_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
