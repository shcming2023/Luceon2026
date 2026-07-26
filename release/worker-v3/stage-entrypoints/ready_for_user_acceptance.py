#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

WORKER_V3_ENTRYPOINT_PROTOCOL = "luceon.worker-v3-stage-entrypoint/v1"
WORKER_V3_ENTRYPOINT_ROLE = "producer"
WORKER_V3_STAGE = "ready_for_user_acceptance"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args()
    release_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(release_root / "scripts" / "worker-v3"))
    from spec05_06_stage_adapters import produce_stage
    from stage_entrypoint import run_stage_entrypoint

    return run_stage_entrypoint(
        stage_key=WORKER_V3_STAGE,
        request_path=args.request,
        result_path=args.result,
        producer=produce_stage,
        release_root=release_root,
    )


if __name__ == "__main__":
    raise SystemExit(main())
