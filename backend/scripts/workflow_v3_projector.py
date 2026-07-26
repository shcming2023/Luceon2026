#!/usr/bin/env python3
"""Project immutable Worker V3 outbox events into formal material outputs."""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal  # noqa: E402
from app.workflow_v3.database import (  # noqa: E402
    initialize_workflow_v3_database,
    workflow_v3_session_factory,
)
from app.workflow_v3.executor import DirectoryReleaseResolver  # noqa: E402
from app.workflow_v3.projection import WorkflowV3ProjectionProcessor  # noqa: E402
from app.workflow_v3.runtime_factory import (  # noqa: E402
    RuntimeBindingGuard,
    WorkerHeartbeatLoop,
    load_runtime_binding_guard,
    projector_artifact_stores,
)


def _required_path(name: str) -> Path:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return Path(value)


def _processor(
    worker_id: str,
    lease_seconds: int,
    runtime_guard: RuntimeBindingGuard,
) -> WorkflowV3ProjectionProcessor:
    stores = projector_artifact_stores()
    return WorkflowV3ProjectionProcessor(
        workflow_session_factory=workflow_v3_session_factory(),
        material_session_factory=SessionLocal,
        candidate_store=stores.candidate_reader,
        formal_store=stores.formal_writer,
        work_root=_required_path("WORKFLOW_V3_PROJECTION_WORK_ROOT"),
        worker_id=worker_id,
        formal_bucket=stores.formal_bucket,
        formal_prefix=stores.formal_prefix,
        lease_seconds=lease_seconds,
        release_resolver=DirectoryReleaseResolver(
            _required_path("WORKFLOW_V3_RELEASES_ROOT")
        ),
        runtime_guard=runtime_guard,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Lease and replay Worker V3 final-ready/human-acceptance "
            "projections without mutating candidate artifacts."
        )
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--outbox-id", type=int)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument(
        "--worker-id",
        default=f"worker-v3-projector-{socket.gethostname()}",
    )
    parser.add_argument(
        "--lease-seconds",
        type=int,
        default=max(
            30,
            int(os.getenv("WORKFLOW_V3_PROJECTION_LEASE_SECONDS", "900")),
        ),
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=max(
            1,
            int(os.getenv("WORKFLOW_V3_PROJECTION_MAX_ATTEMPTS", "5")),
        ),
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=max(
            0.2,
            float(os.getenv("WORKFLOW_V3_PROJECTION_POLL_SECONDS", "1")),
        ),
    )
    args = parser.parse_args()
    if not (args.once or args.loop or args.outbox_id):
        parser.error("use --once, --loop, or --outbox-id")
    ready = initialize_workflow_v3_database()
    if not ready.get("ready"):
        print(
            json.dumps(
                {"ok": False, "database": ready},
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        return 2
    try:
        runtime_guard = load_runtime_binding_guard()
        processor = _processor(
            args.worker_id,
            args.lease_seconds,
            runtime_guard,
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "status": "configuration_error",
                    "error": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        return 2

    heartbeat = WorkerHeartbeatLoop(
        session_factory=workflow_v3_session_factory(),
        worker_id=args.worker_id,
        role="projector",
        interval_seconds=float(
            os.getenv("WORKFLOW_V3_HEARTBEAT_SECONDS", "5")
        ),
        runtime_identity_sha256=runtime_guard.runtime_identity_sha256,
    )
    heartbeat.start()
    heartbeat.update(status="idle")
    try:
        while True:
            heartbeat.update(status="busy", write_now=False)
            result = processor.process_one(
                include_failed=args.retry_failed,
                max_attempts=args.max_attempts,
                outbox_id=args.outbox_id,
            )
            heartbeat.update(
                status=(
                    "idle"
                    if result.get("ok") or result.get("status") == "idle"
                    else "degraded"
                ),
                last_error="" if result.get("ok") else str(result.get("error") or ""),
            )
            print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
            if args.outbox_id or args.once:
                return 0 if result.get("ok") else 2
            if result.get("status") == "idle":
                time.sleep(args.poll_seconds)
    finally:
        heartbeat.stop()


if __name__ == "__main__":
    raise SystemExit(main())
