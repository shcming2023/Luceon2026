#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

WORKER_V3_ENTRYPOINT_PROTOCOL = "luceon.worker-v3-stage-entrypoint/v1"
WORKER_V3_ENTRYPOINT_ROLE = "evaluator"
WORKER_V3_STAGE = "deterministic_elegantbook"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()
    release_root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(release_root / "scripts" / "worker-v3"))
    from stage_evaluation_entrypoint import run_stage_evaluation_entrypoint
    from stage_evaluators import evaluate_stage

    return run_stage_evaluation_entrypoint(
        stage_key=WORKER_V3_STAGE,
        request_path=args.request,
        result_path=args.result,
        evaluator=evaluate_stage,
        release_root=release_root,
    )


if __name__ == "__main__":
    raise SystemExit(main())
