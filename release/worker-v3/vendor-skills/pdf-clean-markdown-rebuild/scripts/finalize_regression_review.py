#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent


def run_step(cmd, *, allow_failure=False):
    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    step = {
        "cmd": cmd,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }
    if proc.returncode != 0 and not allow_failure:
        raise SystemExit(json.dumps({
            "ok": False,
            "failed_step": step,
        }, ensure_ascii=False, indent=2))
    return step


def main():
    parser = argparse.ArgumentParser(
        description="Refresh review artifacts, optionally compare stability against a baseline, then assert final acceptance."
    )
    parser.add_argument("out_root", type=Path, help="Regression output root.")
    parser.add_argument("--manual-review-status", type=Path, help="Optional manual review ledger JSON.")
    parser.add_argument("--baseline-root", type=Path, help="Optional baseline regression root or stability snapshot for anti-regression comparison.")
    parser.add_argument("--allow-not-accepted", action="store_true", help="Return 0 even if final acceptance assertion fails; useful for status refreshes during active review.")
    args = parser.parse_args()

    refresh_cmd = [sys.executable, str(SCRIPT_DIR / "refresh_regression_review_artifacts.py"), str(args.out_root)]
    if args.manual_review_status:
        refresh_cmd.extend(["--manual-review-status", str(args.manual_review_status)])
    steps = [run_step(refresh_cmd)]

    if args.baseline_root:
        compare_cmd = [
            sys.executable,
            str(SCRIPT_DIR / "compare_regression_stability.py"),
            str(args.baseline_root),
            str(args.out_root),
            "--out-md",
            str(args.out_root / "regression_stability_comparison.md"),
            "--out-json",
            str(args.out_root / "regression_stability_comparison.json"),
        ]
        steps.append(run_step(compare_cmd))
        steps.append(run_step(refresh_cmd))

    assert_cmd = [sys.executable, str(SCRIPT_DIR / "assert_final_acceptance.py"), str(args.out_root), "--json"]
    assert_step = run_step(assert_cmd, allow_failure=True)
    steps.append(assert_step)
    assert_result = {}
    if assert_step["stdout"].strip():
        try:
            assert_result = json.loads(assert_step["stdout"])
        except json.JSONDecodeError:
            assert_result = {"parse_error": assert_step["stdout"]}

    result = {
        "ok": assert_step["returncode"] == 0,
        "out_root": str(args.out_root),
        "assertion": assert_result,
        "steps": steps,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["ok"] or args.allow_not_accepted:
        raise SystemExit(0)
    raise SystemExit(assert_step["returncode"] or 1)


if __name__ == "__main__":
    main()
