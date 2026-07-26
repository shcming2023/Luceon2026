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
from app.workflow_v3.evaluator import (  # noqa: E402
    WorkflowV3Evaluator,
    WorkflowV3PromotionController,
)
from app.workflow_v3.executor import DirectoryReleaseResolver  # noqa: E402
from app.workflow_v3.queue import (  # noqa: E402
    claim_next_evaluation_item,
    claim_next_promotion_item,
    recover_stale_operations,
)
from app.workflow_v3.runtime_factory import (  # noqa: E402
    RuntimeBindingGuard,
    WorkerHeartbeatLoop,
    load_runtime_binding_guard,
    readonly_artifact_store,
)


def _required_path(name: str) -> Path:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return Path(value)


def _dependencies(role: str):
    session_factory = workflow_v3_session_factory()
    resolver = DirectoryReleaseResolver(_required_path("WORKFLOW_V3_RELEASES_ROOT"))
    store = readonly_artifact_store(
        "evaluator" if role == "evaluate" else "promoter"
    )
    return session_factory, resolver, store


def _next(
    role: str,
    *,
    identity: str,
    lease_seconds: int,
    max_attempts: int,
    runtime_identity_sha256: str,
):
    db = workflow_v3_session_factory()()
    try:
        item = (
            claim_next_evaluation_item(
                db,
                owner_identity=identity,
                lease_seconds=lease_seconds,
                max_attempts=max_attempts,
                runtime_identity_sha256=runtime_identity_sha256,
            )
            if role == "evaluate"
            else claim_next_promotion_item(
                db,
                owner_identity=identity,
                lease_seconds=lease_seconds,
                max_attempts=max_attempts,
                runtime_identity_sha256=runtime_identity_sha256,
            )
        )
        db.commit()
        return item
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main(
    argv: list[str] | None = None,
    *,
    forced_role: str | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        description="Run Worker V3 independent evaluation or promotion control."
    )
    parser.add_argument(
        "--role",
        choices=("evaluate", "promote"),
        required=forced_role is None,
        default=forced_role,
    )
    parser.add_argument("--job-id")
    parser.add_argument("--candidate-id", type=int)
    parser.add_argument("--evaluation-id", type=int)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--identity", default="")
    parser.add_argument(
        "--lease-seconds",
        type=int,
        default=max(
            30,
            int(os.getenv("WORKFLOW_V3_OPERATION_LEASE_SECONDS", "300")),
        ),
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=max(
            1,
            int(os.getenv("WORKFLOW_V3_OPERATION_MAX_ATTEMPTS", "3")),
        ),
    )
    args = parser.parse_args(argv)
    if forced_role is not None and args.role != forced_role:
        parser.error(f"this process is restricted to role {forced_role}")
    if not (args.job_id or args.once or args.loop):
        parser.error("use --job-id, --once, or --loop")
    ready = initialize_workflow_v3_database()
    if not ready.get("ready"):
        print(json.dumps({"ok": False, "database": ready}, ensure_ascii=False), flush=True)
        return 2
    session_factory, resolver, store = _dependencies(args.role)
    runtime_guard: RuntimeBindingGuard = load_runtime_binding_guard()
    default_identity = f"worker-v3-{args.role}-{socket.gethostname()}"
    identity = args.identity or default_identity
    runtime = (
        WorkflowV3Evaluator(
            session_factory=session_factory,
            release_resolver=resolver,
            artifact_store=store,
            work_root=_required_path("WORKFLOW_V3_EVALUATION_WORK_ROOT"),
            evaluator_identity=identity,
            runtime_guard=runtime_guard,
        )
        if args.role == "evaluate"
        else WorkflowV3PromotionController(
            session_factory=session_factory,
            release_resolver=resolver,
            artifact_store=store,
            promoter_identity=identity,
            runtime_guard=runtime_guard,
        )
    )
    operation = "evaluation" if args.role == "evaluate" else "promotion"
    heartbeat = WorkerHeartbeatLoop(
        session_factory=session_factory,
        worker_id=identity,
        role="evaluator" if args.role == "evaluate" else "promoter",
        interval_seconds=float(
            os.getenv("WORKFLOW_V3_HEARTBEAT_SECONDS", "5")
        ),
        runtime_identity_sha256=runtime_guard.runtime_identity_sha256,
    )
    heartbeat.start()
    heartbeat.update(status="idle")
    next_recovery_at = 0.0
    try:
        while True:
            now = time.monotonic()
            if now >= next_recovery_at:
                recovered = recover_stale_operations(
                    session_factory,
                    operation=operation,
                )
                if recovered:
                    print(
                        json.dumps(
                            {
                                "recovered_operation_attempts": recovered,
                                "operation": operation,
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        flush=True,
                    )
                next_recovery_at = now + 30
            if args.job_id:
                if args.role == "evaluate" and args.candidate_id is None:
                    parser.error("--candidate-id is required for direct evaluation")
                if args.role == "promote" and args.evaluation_id is None:
                    parser.error("--evaluation-id is required for direct promotion")
                item = None
                job_id = args.job_id
                item_id = args.candidate_id if args.role == "evaluate" else args.evaluation_id
            else:
                item = _next(
                    args.role,
                    identity=identity,
                    lease_seconds=args.lease_seconds,
                    max_attempts=args.max_attempts,
                    runtime_identity_sha256=(
                        runtime_guard.runtime_identity_sha256
                    ),
                )
                if item is None:
                    heartbeat.update(status="idle", write_now=False)
                    if args.once:
                        print(json.dumps({"ok": True, "status": "idle"}, ensure_ascii=False), flush=True)
                        return 0
                    time.sleep(1)
                    continue
                job_id = item.public_id
                item_id = item.candidate_id if args.role == "evaluate" else item.evaluation_id
            heartbeat.update(
                status="busy",
                current_job_id=job_id,
                write_now=True,
            )
            try:
                operation_kwargs = (
                    {
                        "operation_attempt_id": item.operation_attempt_id,
                        "owner_token": item.owner_token,
                    }
                    if item is not None
                    else {
                        "lease_seconds": args.lease_seconds,
                        "max_attempts": args.max_attempts,
                    }
                )
                result = (
                    runtime.evaluate(job_id, int(item_id), **operation_kwargs)
                    if args.role == "evaluate"
                    else runtime.promote(job_id, int(item_id), **operation_kwargs)
                )
            except Exception as exc:
                result = {
                    "ok": False,
                    "job_id": job_id,
                    "role": args.role,
                    "error_code": getattr(exc, "code", type(exc).__name__),
                    "error": str(exc)[:2000],
                }
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
