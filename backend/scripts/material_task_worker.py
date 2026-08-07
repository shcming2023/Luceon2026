#!/usr/bin/env python3
import argparse
import json
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal
from app.services.material_inventory import pipeline_wait_timeout_seconds, popo_resume_command, run_pipeline_subprocess
from app.services.gpu_pipeline_lifecycle import (
    acquire_gpu_for_pipeline,
    mark_gpu_offline_before_submit_if_applicable,
    mark_lifecycle_failure,
    mark_transport_pre_submit_failure,
    qualify_pipeline_transport_for_submission,
    reconcile_stale_lifecycle_leases,
    release_gpu_after_pipeline,
)
from app.services.material_task_queue import (
    claim_next_metadata_job,
    claim_next_pipeline_run,
    execute_metadata_job,
    recover_stale_tasks,
)
from app.services.runtime_health import record_runtime_worker_heartbeat


def consume_once(worker_id: str) -> dict | None:
    db = SessionLocal()
    try:
        pipeline_run = claim_next_pipeline_run(db, worker_id)
        if pipeline_run:
            request = pipeline_run.request()
            lifecycle_lease = None
            if bool(request.get("apply")):
                try:
                    lifecycle_lease = acquire_gpu_for_pipeline(db, pipeline_run)
                except Exception as exc:
                    mark_lifecycle_failure(db, pipeline_run, exc)
                    # A failed post-Start readiness gate (including disk) leaves
                    # a durable owned lease; immediately reuse the same safe
                    # stop authority instead of waiting for a process restart.
                    reconcile_stale_lifecycle_leases(db)
                    return {"kind": "pipeline_run", "id": str(pipeline_run.id), "status": "gpu_lifecycle_failed"}
            snapshot = request.get("snapshot") if isinstance(request.get("snapshot"), list) else []
            material_ids = [str(row.get("material_id") or "") for row in snapshot if isinstance(row, dict)]
            input_objects = [str(row.get("input_object") or "") for row in snapshot if isinstance(row, dict)]
            command_override = None
            start_message = "开始执行现有 Luceon first-stage 调度脚本"
            reprocess_completed = bool(request.get("reprocess_completed"))
            if reprocess_completed:
                start_message = "开始为已完成资产创建新的不可变 MinerU/Popo 版本"
            if pipeline_run.mode == "resume_popo":
                context = request.get("resume_context") if isinstance(request.get("resume_context"), dict) else {}
                command_override = popo_resume_command(
                    str(context.get("mineru_batch_id") or ""),
                    str(context.get("material_id") or ""),
                    str(context.get("input_object") or ""),
                    apply=True,
                    existing_popo_batch_id=str(request.get("existing_popo_batch_id") or ""),
                    timeout_seconds=pipeline_wait_timeout_seconds(snapshot),
                )
                start_message = "开始从冻结 MinerU 恢复 Popo"
            subprocess_env = None
            if lifecycle_lease is not None:
                try:
                    transport = qualify_pipeline_transport_for_submission(db, pipeline_run, lifecycle_lease)
                    subprocess_env = {"GPU_WRAPPER_URL": str(transport["endpoint"])}
                except Exception as exc:
                    mark_transport_pre_submit_failure(db, pipeline_run, exc)
                    db.expire_all()
                    failed_run = db.query(type(pipeline_run)).filter(type(pipeline_run).id == pipeline_run.id).first()
                    if failed_run:
                        release_gpu_after_pipeline(db, failed_run, lifecycle_lease)
                    return {"kind": "pipeline_run", "id": str(pipeline_run.id), "status": "gpu_transport_failed_before_submit"}
            try:
                run_pipeline_subprocess(
                    pipeline_run.id,
                    bool(request.get("apply")),
                    int(request.get("limit") or len(snapshot) or 1),
                    material_ids=material_ids,
                    input_objects=input_objects,
                    reprocess_completed=reprocess_completed,
                    command_override=command_override,
                    start_message=start_message,
                    worker_id=worker_id,
                    pipeline_env_override=subprocess_env,
                )
                # ``run-staged`` may return code 0 while reporting GPU_OFFLINE
                # in its structured payload.  Preserve that payload, but
                # classify the exact no-external-identity shape for the common
                # failure stop authority rather than retaining an owned GPU.
                if lifecycle_lease is not None:
                    db.expire_all()
                    terminal_run = db.query(type(pipeline_run)).filter(type(pipeline_run).id == pipeline_run.id).first()
                    if terminal_run:
                        mark_gpu_offline_before_submit_if_applicable(db, terminal_run)
            finally:
                if lifecycle_lease is not None:
                    db.expire_all()
                    completed_run = db.query(type(pipeline_run)).filter(type(pipeline_run).id == pipeline_run.id).first()
                    if completed_run:
                        release_gpu_after_pipeline(db, completed_run, lifecycle_lease)
            return {"kind": "pipeline_run", "id": str(pipeline_run.id)}

        metadata_job = claim_next_metadata_job(db, worker_id)
        if metadata_job:
            return {"kind": "metadata_job", **execute_metadata_job(metadata_job.id, worker_id)}
        return None
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute durable Luceon parse and metadata tasks.")
    parser.add_argument("--worker-id", default=f"material-task-{socket.gethostname()}")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if not args.loop and not args.once:
        parser.error("--loop or --once is required")

    db = SessionLocal()
    try:
        recovered = recover_stale_tasks(db)
        recovered["gpu_lifecycle_leases"] = reconcile_stale_lifecycle_leases(db)
    finally:
        db.close()
    if any(recovered.values()):
        print(json.dumps({"recovered": recovered}, ensure_ascii=False), flush=True)

    retry_delay = 1.0
    while True:
        try:
            record_runtime_worker_heartbeat("material_task", args.worker_id)
            result = consume_once(args.worker_id)
            retry_delay = 1.0
            if result:
                print(json.dumps(result, ensure_ascii=False), flush=True)
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "worker_loop_error": type(exc).__name__,
                        "message": str(exc)[:1000],
                        "retry_in_seconds": retry_delay,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            if args.once:
                return 2
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 30.0)
            continue
        if args.once:
            return 0
        time.sleep(1 if not result else 0.2)


if __name__ == "__main__":
    raise SystemExit(main())
