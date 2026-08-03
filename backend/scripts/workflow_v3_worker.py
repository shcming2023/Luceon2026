#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.workflow_v3.database import (  # noqa: E402
    initialize_workflow_v3_database,
    workflow_v3_session_factory,
)
from app.workflow_v3.executor import (  # noqa: E402
    DirectoryReleaseResolver,
    WorkflowV3Executor,
)
from app.workflow_v3.queue import cancel, next_producer_item, recover_stale  # noqa: E402
from app.workflow_v3.runtime_factory import (  # noqa: E402
    RuntimeBindingGuard,
    WorkerHeartbeatLoop,
    load_runtime_binding_guard,
    producer_artifact_store,
)


def _required_path(name: str) -> Path:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return Path(value)


def _executor(
    worker_id: str,
    heartbeat: WorkerHeartbeatLoop,
    runtime_guard: RuntimeBindingGuard,
) -> WorkflowV3Executor:
    return WorkflowV3Executor(
        session_factory=workflow_v3_session_factory(),
        release_resolver=DirectoryReleaseResolver(_required_path("WORKFLOW_V3_RELEASES_ROOT")),
        artifact_store=producer_artifact_store(),
        work_root=_required_path("WORKFLOW_V3_WORK_ROOT"),
        producer_identity=worker_id,
        candidate_bucket=os.getenv("WORKFLOW_V3_CANDIDATE_BUCKET", "worker-v3-candidates"),
        candidate_prefix=os.getenv(
            "WORKFLOW_V3_CANDIDATE_PREFIX",
            "v3/candidates",
        ),
        operational_heartbeat=lambda job_id, stage_key, runtime_sha: heartbeat.update(
            status="busy",
            runtime_identity_sha256=runtime_sha,
            current_job_id=job_id,
            current_stage_key=stage_key,
            write_now=False,
        ),
        runtime_guard=runtime_guard,
    )


def _next_job_id(runtime_identity_sha256: str) -> str | None:
    db = workflow_v3_session_factory()()
    try:
        item = next_producer_item(
            db,
            runtime_identity_sha256=runtime_identity_sha256,
        )
        db.commit()
        return item.public_id if item else None
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run immutable-release Worker V3 producer entrypoints."
    )
    parser.add_argument("--job-id")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--worker-id", default=f"worker-v3-{socket.gethostname()}")
    parser.add_argument("--cancel")
    parser.add_argument("--reason", default="")
    parser.add_argument(
        "--stale-after-seconds",
        type=int,
        default=max(30, int(os.getenv("WORKFLOW_V3_STALE_AFTER_SECONDS", "300"))),
    )
    args = parser.parse_args()
    ready = initialize_workflow_v3_database()
    if not ready.get("ready"):
        print(json.dumps({"ok": False, "database": ready}, ensure_ascii=False), flush=True)
        return 2
    if args.cancel:
        result = cancel(
            workflow_v3_session_factory(),
            args.cancel,
            cancelled_by=args.worker_id,
            reason=args.reason,
        )
        print(json.dumps({"ok": True, **result}, ensure_ascii=False), flush=True)
        return 0
    if not (args.job_id or args.once or args.loop):
        parser.error("use --job-id, --once, --loop, or --cancel")

    runtime_guard = load_runtime_binding_guard()
    heartbeat = WorkerHeartbeatLoop(
        session_factory=workflow_v3_session_factory(),
        worker_id=args.worker_id,
        role="producer",
        interval_seconds=float(
            os.getenv("WORKFLOW_V3_HEARTBEAT_SECONDS", "5")
        ),
        runtime_identity_sha256=runtime_guard.runtime_identity_sha256,
    )
    executor = _executor(args.worker_id, heartbeat, runtime_guard)
    heartbeat.start()
    heartbeat.update(status="idle")
    next_recovery_at = 0.0
    try:
        while True:
            now = time.monotonic()
            if now >= next_recovery_at:
                recovered = recover_stale(
                    workflow_v3_session_factory(),
                    stale_after_seconds=args.stale_after_seconds,
                )
                if recovered:
                    print(json.dumps({"recovered_jobs": recovered}, ensure_ascii=False), flush=True)
                next_recovery_at = now + 30
            job_id = args.job_id or _next_job_id(
                runtime_guard.runtime_identity_sha256
            )
            if not job_id:
                heartbeat.update(status="idle", write_now=False)
                if args.once:
                    print(json.dumps({"ok": True, "status": "idle"}, ensure_ascii=False), flush=True)
                    return 0
                time.sleep(1)
                continue
            result = executor.run_one_stage(job_id)
            heartbeat.update(
                status="idle" if result.get("ok") else "degraded",
                current_job_id="",
                current_stage_key="",
                last_error="" if result.get("ok") else str(result.get("error") or ""),
            )
            print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
            if args.job_id or args.once:
                return 0 if result.get("ok") else 2
            time.sleep(0.2)
    finally:
        heartbeat.stop()


if __name__ == "__main__":
    raise SystemExit(main())
