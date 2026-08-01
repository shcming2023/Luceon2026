#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.workflow_v3.qualification import (
    QUALIFICATION_STOP_STAGES,
    QualificationConfig,
    QualificationError,
    run_qualification,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run an incomplete Worker V3 release in a fresh, isolated "
            "qualification control plane. This command never uses the API, "
            "normal worker database, MinIO, or a live model provider."
        )
    )
    release = parser.add_mutually_exclusive_group(required=True)
    release.add_argument(
        "--release-root",
        type=Path,
        help="Read-only pre-materialized incomplete release directory.",
    )
    release.add_argument(
        "--release-archive",
        type=Path,
        help=(
            "Read-only incomplete archive emitted by the release builder. "
            "It is materialized only inside the fresh qualification run root."
        ),
    )
    parser.add_argument(
        "--release-archive-sha256",
        default="",
        help="Required external SHA-256 binding for --release-archive.",
    )
    parser.add_argument("--source-package-root", type=Path, required=True)
    parser.add_argument("--source-evidence-json", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument(
        "--stop-after",
        choices=QUALIFICATION_STOP_STAGES,
        default="deterministic_elegantbook",
    )
    parser.add_argument(
        "--fixture-responses-json",
        type=Path,
        help=(
            "Read-only exact request-hash to response replay bundle. "
            "Required when the release invokes bounded LLM/vision stages."
        ),
    )
    parser.add_argument(
        "--spec05-warning-review-json",
        type=Path,
        help=(
            "Optional read-only, exact-fingerprint Spec 05 warning review. "
            "It is consumed only after a real deterministic_elegantbook "
            "needs_review evaluation and resumes that stage once."
        ),
    )
    args = parser.parse_args()
    if args.release_archive and not args.release_archive_sha256:
        parser.error(
            "--release-archive-sha256 is required with --release-archive"
        )
    if args.release_root and args.release_archive_sha256:
        parser.error(
            "--release-archive-sha256 is only valid with --release-archive"
        )
    try:
        result = run_qualification(
            QualificationConfig(
                release_root=args.release_root,
                release_archive=args.release_archive,
                release_archive_sha256=args.release_archive_sha256,
                source_package_root=args.source_package_root,
                source_evidence_json=args.source_evidence_json,
                run_root=args.run_root,
                stop_after=args.stop_after,
                fixture_responses_json=args.fixture_responses_json,
                spec05_warning_review_json=(
                    args.spec05_warning_review_json
                ),
            )
        )
    except QualificationError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_code": exc.code,
                    "error": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(
            {
                "ok": True,
                "job_id": result.job_id,
                "stop_after": result.stop_after,
                "report": str(result.report_path),
                "report_sha256": result.report_sha256,
                "payload_sha256": result.payload_sha256,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
