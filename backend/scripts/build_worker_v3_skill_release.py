#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.workflow_v3.release import ReleaseValidationError, build_release_archive


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a deterministic Worker V3 skill release archive.")
    parser.add_argument("--source", required=True, type=Path, help="Release source directory.")
    parser.add_argument("--output", required=True, type=Path, help="Output .tar or deterministic .tar.gz.")
    args = parser.parse_args()
    try:
        result = build_release_archive(args.source, args.output)
    except ReleaseValidationError as exc:
        parser.error(str(exc))
    print(json.dumps({**result, "archive": str(args.output.resolve())}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
