#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.workflow_v3.release_recipe import (
    ReleaseRecipeError,
    assemble_release_source,
    verify_release_recipe,
)


def _root_overrides(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        name, separator, raw_path = value.partition("=")
        if not separator or not name or not raw_path or name in result:
            raise argparse.ArgumentTypeError(
                "--root values must be unique NAME=/absolute/path assignments"
            )
        result[name] = Path(raw_path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify hash-bound Worker V3 release inputs and optionally assemble an "
            "immutable release source directory. This command never promotes a release."
        )
    )
    parser.add_argument("--recipe", required=True, type=Path)
    parser.add_argument(
        "--root",
        action="append",
        default=[],
        metavar="NAME=/ABSOLUTE/PATH",
        help="Override one named build-time source root from the recipe.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--verify-only",
        action="store_true",
        help="Audit source hashes and entrypoint eligibility without writing output.",
    )
    mode.add_argument(
        "--output",
        type=Path,
        help="Assemble into a new directory. Existing destinations are refused.",
    )
    args = parser.parse_args()

    try:
        overrides = _root_overrides(args.root)
        audit = verify_release_recipe(args.recipe, root_overrides=overrides)
        if args.verify_only:
            result = {
                "release_id": audit.recipe["release"]["release_id"],
                "status": audit.status,
                "source_count": len(audit.source_evidence),
                "planned_file_count": len(audit.planned_files),
                "known_gap_count": len(audit.known_gaps),
                "known_gaps": list(audit.known_gaps),
                "entrypoints": list(audit.entrypoint_evidence),
                "output_written": False,
            }
        else:
            result = assemble_release_source(audit, args.output)
            result["output_written"] = True
    except (ReleaseRecipeError, argparse.ArgumentTypeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
