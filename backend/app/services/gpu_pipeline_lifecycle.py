from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.models.material import PipelineEvent, PipelineRun, PipelineRunItem
from app.services.compshare_lifecycle import (
    CompShareConfig,
    CompShareLifecycleError,
    LifecycleLease,
    SafeStopContext,
    UCloudCompShareClient,
    ensure_running,
    ssh_readiness_probe,
    stop_when_safe,
)


def lifecycle_enabled() -> bool:
    return os.getenv("COMPSHARE_LIFECYCLE_ENABLED", "false").lower() in {"1", "true", "yes", "on"}


def _event(db: Session, run: PipelineRun, stage: str, message: str, *, level: str = "info", payload: dict | None = None) -> None:
    db.add(
        PipelineEvent(
            run_id=run.id,
            user_id=run.user_id,
            stage=stage,
            message=message,
            level=level,
            payload_json=json.dumps(payload or {}, ensure_ascii=False),
        )
    )


def _merge_summary(run: PipelineRun, patch: dict[str, Any]) -> None:
    summary = run.summary()
    summary.update(patch)
    run.summary_json = json.dumps(summary, ensure_ascii=False)


def mark_lifecycle_failure(db: Session, run: PipelineRun, exc: BaseException) -> None:
    code = exc.code if isinstance(exc, CompShareLifecycleError) else "gpu_lifecycle_failed"
    evidence = exc.evidence if isinstance(exc, CompShareLifecycleError) else {}
    run.status = "failed"
    run.current_stage = "gpu_lifecycle_failed"
    run.error_message = str(exc)
    run.finished_at = datetime.utcnow()
    run.queue_slot = None
    lifecycle = run.summary().get("gpu_lifecycle")
    lifecycle = dict(lifecycle) if isinstance(lifecycle, dict) else {}
    lifecycle.update({"status": "failed", "error_domain": code, "evidence": evidence})
    _merge_summary(run, {"gpu_lifecycle": lifecycle})
    _event(db, run, "gpu_lifecycle_failed", "GPU 生命周期或就绪门禁失败", level="error", payload={"error_domain": code, "evidence": evidence})
    items = db.query(PipelineRunItem).filter(PipelineRunItem.run_id == run.id).all()
    for item in items:
        item.status = "failed"
        item.current_stage = "gpu_lifecycle_failed"
        item.error_code = code
        item.error_message = str(exc)
        item.finished_at = datetime.utcnow()
    db.commit()


def acquire_gpu_for_pipeline(
    db: Session,
    run: PipelineRun,
    *,
    client_factory: Callable[[CompShareConfig], Any] = UCloudCompShareClient,
    readiness_probe: Callable[[CompShareConfig, str], dict[str, Any]] = ssh_readiness_probe,
) -> LifecycleLease | None:
    if not lifecycle_enabled():
        return None
    config = CompShareConfig.from_env()
    missing = config.missing_fields()
    if missing:
        raise CompShareLifecycleError("cloud_config_incomplete", f"Compshare config missing: {', '.join(missing)}")
    run.current_stage = "gpu_starting"
    _event(db, run, "gpu_starting", "正在核验并按需启动 Compshare GPU 实例", payload={"cloud": config.public_identity()})
    db.commit()
    client = client_factory(config)

    def persist_lease(current: LifecycleLease) -> None:
        _merge_summary(
            run,
            {
                "gpu_lifecycle": {
                    "status": "ready" if current.phase == "ready" else "acquiring",
                    "managed": True,
                    "lease": current.to_dict(),
                }
            },
        )
        db.commit()

    lease = ensure_running(
        client,
        config,
        lambda: readiness_probe(config, os.getenv("GPU_WRAPPER_URL", "")),
        lease_id=f"pipeline-{run.id}-{run.idempotency_key[:16] if run.idempotency_key else 'unkeyed'}",
        checkpoint=persist_lease,
    )
    run.current_stage = "gpu_ready"
    _merge_summary(run, {"gpu_lifecycle": {"status": "ready", "managed": True, "lease": lease.to_dict()}})
    _event(
        db,
        run,
        "gpu_ready",
        "Compshare 实例、SSH、GPU、磁盘与受保护服务均已就绪",
        payload={
            "prior_state": lease.prior_state,
            "started_by_pipeline": lease.started_by_pipeline,
            "lifecycle_owned": lease.lifecycle_owned,
            "lease_sha256": lease.to_dict()["lease_sha256"],
        },
    )
    db.commit()
    return lease


def _fetch_json(url: str, *, bearer_key: str = "") -> tuple[int, Any]:
    headers = {"Accept": "application/json"}
    if bearer_key:
        headers["Authorization"] = f"Bearer {bearer_key}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read()
            status = int(getattr(response, "status", 200) or 200)
    except urllib.error.HTTPError as exc:
        return int(exc.code), None
    return status, json.loads(raw.decode("utf-8"))


def _required_nonnegative_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool):
        raise ValueError(key)
    number = int(value)
    if number < 0:
        raise ValueError(key)
    return number


def _inventory_rows(payload: Any) -> list[dict[str, Any]] | None:
    if not isinstance(payload, dict):
        return None
    for key in ("jobs", "batches", "items", "data"):
        rows = payload.get(key)
        if isinstance(rows, list):
            if not all(isinstance(row, dict) for row in rows):
                raise ValueError("inventory_row_schema_invalid")
            return rows
    return None


def _inventory_row_id(row: dict[str, Any]) -> str:
    for key in ("id", "job_id", "batch_id", "run_id"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    raise ValueError("inventory_row_id_missing")


def _complete_protected_inventory(
    wrapper_url: str,
    path: str,
    *,
    bearer_key: str,
    page_size: int = 100,
) -> dict[str, Any]:
    terminal = {"succeeded", "failed", "cancelled", "done", "completed", "terminal"}
    seen_ids: set[str] = set()
    seen_cursors: set[str] = set()
    active = 0
    pages = 0
    expected_total: int | None = None
    cursor = ""
    while True:
        query = {"limit": page_size}
        if cursor:
            query["cursor"] = cursor
        url = wrapper_url.rstrip("/") + path + "?" + urllib.parse.urlencode(query)
        status, payload = _fetch_json(url, bearer_key=bearer_key)
        if status != 200:
            raise ValueError(f"inventory_http_{status}")
        if not isinstance(payload, dict):
            raise ValueError("inventory_payload_invalid")
        rows = _inventory_rows(payload)
        if rows is None:
            raise ValueError("inventory_rows_missing")
        total_value = payload.get("total")
        if total_value is None:
            total_value = payload.get("total_count")
        if total_value is None:
            raise ValueError("inventory_total_missing")
        total = _required_nonnegative_int({"total": total_value}, "total")
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            raise ValueError("inventory_total_drift")
        pages += 1
        for row in rows:
            row_id = _inventory_row_id(row)
            if row_id in seen_ids:
                raise ValueError("inventory_duplicate_id")
            seen_ids.add(row_id)
            if str(row.get("status") or "").lower() not in terminal:
                active += 1
        next_cursor = str(payload.get("next_cursor") or "").strip()
        has_more = payload.get("has_more")
        if has_more is not None and not isinstance(has_more, bool):
            raise ValueError("inventory_has_more_invalid")
        if len(seen_ids) > total:
            raise ValueError("inventory_rows_exceed_total")
        more_required = len(seen_ids) < total
        if more_required:
            if not next_cursor or has_more is False:
                raise ValueError("inventory_pagination_incomplete")
            if next_cursor in seen_cursors:
                raise ValueError("inventory_duplicate_cursor")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
            continue
        if next_cursor or has_more is True:
            raise ValueError("inventory_eof_inconsistent")
        return {"total": total, "unique_ids": len(seen_ids), "active": active, "pages": pages, "eof_verified": True}


def remote_activity_snapshot(wrapper_url: str, *, bearer_key: str = "") -> dict[str, Any]:
    if not wrapper_url:
        return {"verified": False, "idle_verified": False, "active_jobs": None, "reason": "wrapper_url_missing"}
    try:
        status, payload = _fetch_json(wrapper_url.rstrip("/") + "/api/v1/health")
        if status != 200 or not isinstance(payload, dict):
            raise ValueError("health_payload_invalid")
        wrapper_counts = {
            key: _required_nonnegative_int(payload, key)
            for key in ("queued_jobs", "queued_batches", "queued_mineru_batches", "queued_popo_batches")
        }
        mineru_raw = payload.get("mineru_health")
        if isinstance(mineru_raw, str):
            mineru = json.loads(mineru_raw)
        else:
            mineru = mineru_raw
        if not isinstance(mineru, dict):
            raise ValueError("mineru_health_invalid")
        mineru_counts = {
            key: _required_nonnegative_int(mineru, key)
            for key in ("queued_tasks", "processing_tasks")
        }
    except Exception as exc:
        return {
            "verified": False,
            "idle_verified": False,
            "active_jobs": None,
            "reason": type(exc).__name__,
        }

    health_active = sum(wrapper_counts.values()) + sum(mineru_counts.values())
    if not bearer_key:
        return {
            "verified": False,
            "idle_verified": False,
            "all_required_denominators_verified": False,
            "active_jobs": None,
            "reason": "protected_inventory_bearer_missing",
            "wrapper_counts": wrapper_counts,
            "mineru_counts": mineru_counts,
            "protected_inventory": {"status": "not_requested", "active": None},
        }
    try:
        inventory_results = {
            name: _complete_protected_inventory(wrapper_url, path, bearer_key=bearer_key)
            for name, path in (
                ("jobs", "/api/v1/jobs"),
                ("mineru_batches", "/api/v1/mineru/batches"),
                ("popo_batches", "/api/v1/popo/batches"),
            )
        }
    except Exception as exc:
        return {
            "verified": False,
            "idle_verified": False,
            "all_required_denominators_verified": False,
            "active_jobs": None,
            "reason": f"protected_inventory_{exc}",
            "wrapper_counts": wrapper_counts,
            "mineru_counts": mineru_counts,
            "protected_inventory": {"status": "unverified", "active": None},
        }
    protected_active = sum(row["active"] for row in inventory_results.values())
    active_count = max(health_active, protected_active)
    inventories = {
        "status": "verified",
        "active": protected_active,
        "all_required_denominators_verified": True,
        "inventories": inventory_results,
    }
    return {
        "verified": True,
        "idle_verified": active_count == 0,
        "all_required_denominators_verified": True,
        "active_jobs": active_count,
        "active_total": active_count,
        "health_active_total": health_active,
        "protected_active_total": protected_active,
        "wrapper_counts": wrapper_counts,
        "mineru_counts": mineru_counts,
        "protected_inventory": inventories,
    }


def wrapper_active_jobs(wrapper_url: str) -> dict[str, Any]:
    """Backward-compatible name returning the v2 remote activity snapshot."""

    return remote_activity_snapshot(wrapper_url, bearer_key=os.getenv("GPU_WRAPPER_API_KEY", ""))


def _all_results_frozen(db: Session, run: PipelineRun) -> bool:
    items = db.query(PipelineRunItem).filter(PipelineRunItem.run_id == run.id).all()
    return bool(items) and all(
        item.status == "succeeded"
        and bool(item.mineru_manifest_bucket and item.mineru_manifest_object)
        and bool(item.popo_manifest_bucket and item.popo_manifest_object)
        for item in items
    )


def _safe_stop_context(
    *,
    queue_empty: bool,
    remote: dict[str, Any],
    all_results_frozen_local: bool,
    grace_elapsed: bool,
) -> SafeStopContext:
    authority_verified = bool(
        remote.get("verified")
        and remote.get("all_required_denominators_verified")
        and remote.get("idle_verified")
        and remote.get("active_total") == 0
    )
    return SafeStopContext(
        queue_empty=queue_empty,
        remote_active_jobs=0 if authority_verified else 1,
        remote_idle_verified=authority_verified,
        all_results_frozen_local=all_results_frozen_local,
        grace_elapsed=grace_elapsed,
    )


def release_gpu_after_pipeline(
    db: Session,
    run: PipelineRun,
    lease: LifecycleLease | None,
    *,
    client_factory: Callable[[CompShareConfig], Any] = UCloudCompShareClient,
    remote_jobs_probe: Callable[[str], dict[str, Any]] = wrapper_active_jobs,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if lease is None:
        return {"stopped": False, "status": "unmanaged"}
    config = CompShareConfig.from_env()
    auto_stop = os.getenv("COMPSHARE_AUTO_STOP", "true").lower() in {"1", "true", "yes", "on"}
    if not auto_stop:
        result = {"stopped": False, "status": "retained_running", "blockers": ["auto_stop_disabled"]}
    else:
        active_other = (
            db.query(PipelineRun)
            .filter(PipelineRun.id != run.id, PipelineRun.status.in_(["queued", "running"]))
            .count()
        )
        remote = remote_jobs_probe(os.getenv("GPU_WRAPPER_URL", ""))
        grace_seconds = max(0, int(os.getenv("COMPSHARE_STOP_GRACE_SECONDS", "60") or "60"))
        if grace_seconds:
            sleep(grace_seconds)
        context = _safe_stop_context(
            queue_empty=active_other == 0,
            remote=remote,
            all_results_frozen_local=_all_results_frozen(db, run),
            grace_elapsed=True,
        )

        def persist_stop(current: LifecycleLease) -> None:
            _merge_summary(run, {"gpu_lifecycle": {"status": "stopping", "managed": True, "lease": current.to_dict()}})
            db.commit()

        result = stop_when_safe(client_factory(config), config, lease, context, checkpoint=persist_stop)
        result["remote_probe"] = remote
    lifecycle_status = "stopped" if result.get("stopped") else "retained_running"
    _merge_summary(
        run,
        {
            "gpu_lifecycle": {
                "status": lifecycle_status,
                "managed": True,
                "lease": result.get("lease") or lease.to_dict(),
            },
            "gpu_shutdown": result,
        },
    )
    stage = "gpu_stopped" if result.get("stopped") else "gpu_retained"
    _event(
        db,
        run,
        stage,
        "Compshare 实例已由云控制面确认停止" if result.get("stopped") else "Compshare 实例保持运行并已记录原因",
        level="info" if result.get("stopped") else "warning",
        payload={"status": result.get("status"), "blockers": result.get("blockers", [])},
    )
    db.commit()
    return result


def reconcile_stale_lifecycle_leases(
    db: Session,
    *,
    client_factory: Callable[[CompShareConfig], Any] = UCloudCompShareClient,
    remote_jobs_probe: Callable[[str], dict[str, Any]] = wrapper_active_jobs,
    limit: int = 20,
) -> dict[str, int]:
    """Recover only leases whose accepted Start proves pipeline ownership."""

    result = {"examined": 0, "stopped": 0, "retained": 0, "invalid": 0}
    if not lifecycle_enabled():
        return result
    config = CompShareConfig.from_env()
    if config.missing_fields():
        return result
    rows = db.query(PipelineRun).order_by(PipelineRun.id.desc()).limit(max(1, limit)).all()
    for run in rows:
        lifecycle = run.summary().get("gpu_lifecycle")
        if not isinstance(lifecycle, dict) or not isinstance(lifecycle.get("lease"), dict):
            continue
        try:
            lease = LifecycleLease.from_dict(lifecycle["lease"])
        except CompShareLifecycleError:
            result["invalid"] += 1
            continue
        if lease.current_state == "Stopped" or not lease.lifecycle_owned or not lease.started_by_pipeline:
            continue
        result["examined"] += 1
        active_other = (
            db.query(PipelineRun)
            .filter(PipelineRun.id != run.id, PipelineRun.status.in_(["queued", "running"]))
            .count()
        )
        remote = remote_jobs_probe(os.getenv("GPU_WRAPPER_URL", ""))
        items = db.query(PipelineRunItem).filter(PipelineRunItem.run_id == run.id).all()
        failed_before_submit = bool(items) and all(
            item.current_stage == "gpu_lifecycle_failed"
            and not item.mineru_manifest_object
            and not item.popo_manifest_object
            for item in items
        )
        local_safe = _all_results_frozen(db, run) or failed_before_submit
        try:
            acquired = datetime.fromisoformat(lease.acquired_at.replace("Z", "+00:00"))
            if acquired.tzinfo is None:
                acquired = acquired.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            result["invalid"] += 1
            _event(
                db,
                run,
                "gpu_retained",
                "生命周期租约时间无效；实例保持运行并等待人工核验",
                level="warning",
                payload={"status": "retained_running", "blockers": ["lease_timestamp_invalid"]},
            )
            db.commit()
            continue
        grace = max(0, int(os.getenv("COMPSHARE_STOP_GRACE_SECONDS", "60") or "60"))
        grace_elapsed = (datetime.now(timezone.utc) - acquired.astimezone(timezone.utc)).total_seconds() >= grace
        context = _safe_stop_context(
            queue_empty=active_other == 0,
            remote=remote,
            all_results_frozen_local=local_safe,
            grace_elapsed=grace_elapsed,
        )

        def persist(current: LifecycleLease) -> None:
            lifecycle["lease"] = current.to_dict()
            lifecycle["status"] = "reconciling"
            _merge_summary(run, {"gpu_lifecycle": lifecycle})
            db.commit()

        stop_result = stop_when_safe(client_factory(config), config, lease, context, checkpoint=persist)
        lifecycle["lease"] = stop_result.get("lease") or lease.to_dict()
        lifecycle["status"] = "stopped" if stop_result.get("stopped") else "retained_running"
        _merge_summary(
            run,
            {
                "gpu_lifecycle": lifecycle,
                "gpu_shutdown": {**stop_result, "remote_probe": remote, "reconciled": True},
            },
        )
        _event(
            db,
            run,
            "gpu_stopped" if stop_result.get("stopped") else "gpu_retained",
            "过期生命周期租约已安全停止" if stop_result.get("stopped") else "过期生命周期租约保留运行并记录阻断",
            level="info" if stop_result.get("stopped") else "warning",
            payload={"status": stop_result.get("status"), "blockers": stop_result.get("blockers", [])},
        )
        db.commit()
        result["stopped" if stop_result.get("stopped") else "retained"] += 1
    return result
