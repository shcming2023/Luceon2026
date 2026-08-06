from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.material import (
    Material,
    MetadataJob,
    PipelineEvent,
    PipelineRun,
    PipelineRunItem,
    PipelineStageAttempt,
)
from app.models.material_metadata import MaterialMetadata
from app.services.luceon_review import read_object


PIPELINE_LEASE_SECONDS = max(120, int(os.getenv("LUCEON_PIPELINE_LEASE_SECONDS", "1800")))
METADATA_LEASE_SECONDS = max(60, int(os.getenv("LUCEON_METADATA_LEASE_SECONDS", "300")))
METADATA_PROMPT_VERSION = os.getenv("LUCEON_METADATA_PROMPT_VERSION", "material-metadata-v1")
METADATA_MODEL = os.getenv("DEEPSEEK_DEFAULT_MODEL", os.getenv("DEEPSEEK_MODEL", ""))
TERMINAL_PIPELINE_STATUSES = {"succeeded", "partial", "failed", "cancelled"}
TERMINAL_ITEM_STATUSES = {"succeeded", "failed", "cancelled"}


class MaterialTaskError(RuntimeError):
    pass


def material_snapshot(db: Session, user_id: str, material_pks: list[int]) -> list[dict[str, Any]]:
    ordered_ids: list[int] = []
    for value in material_pks:
        material_pk = int(value)
        if material_pk not in ordered_ids:
            ordered_ids.append(material_pk)
    if not ordered_ids:
        raise MaterialTaskError("至少选择一本PDF")
    if len(ordered_ids) > 5:
        raise MaterialTaskError("常规解析批次最多5本PDF")

    rows = (
        db.query(Material)
        .filter(Material.user_id == user_id, Material.id.in_(ordered_ids), Material.ignored.is_(False))
        .all()
    )
    by_id = {int(row.id): row for row in rows}
    missing = [str(value) for value in ordered_ids if value not in by_id]
    if missing:
        raise MaterialTaskError(f"材料不存在或无权访问: {', '.join(missing)}")

    snapshot: list[dict[str, Any]] = []
    identities: dict[str, int] = {}
    for material_pk in ordered_ids:
        row = by_id[material_pk]
        if not row.material_id or not row.input_bucket or not row.input_object:
            raise MaterialTaskError(f"材料 {row.filename} 缺少稳定身份或源PDF对象")
        identity = row.input_sha256 or row.material_id
        duplicate_pk = identities.get(identity)
        if duplicate_pk and duplicate_pk != material_pk:
            raise MaterialTaskError(f"批次包含重复PDF: {row.filename}")
        identities[identity] = material_pk
        snapshot.append(
            {
                "material_pk": material_pk,
                "material_id": row.material_id,
                "filename": row.filename,
                "input_bucket": row.input_bucket,
                "input_object": row.input_object,
                "input_sha256": row.input_sha256 or "",
                "source_hash": row.source_hash or "",
                "size_bytes": int(row.size_bytes or 0),
                "page_count": int(row.page_count or 0),
                "mineru_manifest": Material._ref(row.mineru_manifest_bucket, row.mineru_manifest_object),
                "popo_manifest": Material._ref(row.popo_manifest_bucket, row.popo_manifest_object),
            }
        )
    return snapshot


def pipeline_idempotency_key(user_id: str, mode: str, snapshot: list[dict[str, Any]]) -> str:
    payload = {
        "user_id": user_id,
        "mode": mode,
        "materials": [
            {
                "material_pk": row["material_pk"],
                "material_id": row["material_id"],
                "input_sha256": row["input_sha256"],
                "input_object": row["input_object"],
            }
            for row in snapshot
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def add_pipeline_run_items(db: Session, run: PipelineRun, snapshot: list[dict[str, Any]]) -> list[PipelineRunItem]:
    rows = []
    for item in snapshot:
        row = PipelineRunItem(
            run_id=run.id,
            user_id=run.user_id,
            material_pk=item["material_pk"],
            material_id=item["material_id"],
            input_bucket=item["input_bucket"],
            input_object=item["input_object"],
            input_sha256=item["input_sha256"] or None,
            filename=item["filename"],
            status="queued",
            current_stage="queued",
        )
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


def pipeline_run_items(db: Session, run_id: int, user_id: str | None = None) -> list[PipelineRunItem]:
    query = db.query(PipelineRunItem).filter(PipelineRunItem.run_id == run_id)
    if user_id is not None:
        query = query.filter(PipelineRunItem.user_id == user_id)
    return query.order_by(PipelineRunItem.id.asc()).all()


def pipeline_attempts_for_items(db: Session, items: list[PipelineRunItem]) -> dict[int, list[dict[str, Any]]]:
    item_ids = [row.id for row in items]
    if not item_ids:
        return {}
    attempts = (
        db.query(PipelineStageAttempt)
        .filter(PipelineStageAttempt.run_item_id.in_(item_ids))
        .order_by(PipelineStageAttempt.run_item_id.asc(), PipelineStageAttempt.created_at.asc(), PipelineStageAttempt.id.asc())
        .all()
    )
    grouped: dict[int, list[dict[str, Any]]] = {}
    for attempt in attempts:
        grouped.setdefault(attempt.run_item_id, []).append(attempt.to_dict())
    return grouped


def pipeline_progress_projection(run: PipelineRun, items: list[PipelineRunItem]) -> dict[str, Any]:
    """Aggregate parent progress from durable per-item stages without treating remote completion as frozen."""
    if run.status in {"succeeded", "failed", "partial", "cancelled"}:
        return {"percent": 100, "basis": "terminal_run", "item_percents": [100 for _ in items]}
    weights = {
        "queued": 0,
        "pipeline_command": 2,
        "mineru_remote_pending": 5,
        "mineru_remote_queued": 8,
        "mineru_remote_running": 20,
        "mineru_remote_succeeded": 35,
        "mineru_freezing": 40,
        "mineru_frozen": 50,
        "popo_remote_pending": 52,
        "popo_remote_queued": 55,
        "popo_remote_running": 70,
        "popo_remote_succeeded": 85,
        "popo_freezing": 90,
        "popo_frozen": 100,
    }
    item_percents = [
        weights.get(str(item.current_stage or ""), 100 if item.status in {"succeeded", "failed"} else 2)
        for item in items
    ]
    percent = round(sum(item_percents) / len(item_percents)) if item_percents else (10 if run.status == "running" else 0)
    if run.current_stage == "popo_model_loading":
        percent = max(percent, 50)
    elif run.current_stage == "popo_model_ready":
        percent = max(percent, 52)
    return {"percent": min(99, max(0, percent)), "basis": "per_item_stage_aggregate", "item_percents": item_percents}


def pipeline_run_detail(db: Session, run: PipelineRun) -> dict[str, Any]:
    items = pipeline_run_items(db, run.id, run.user_id)
    attempts = pipeline_attempts_for_items(db, items)
    material_pks = [item.material_pk for item in items]
    metadata_rows = (
        db.query(MetadataJob)
        .filter(MetadataJob.user_id == run.user_id, MetadataJob.material_pk.in_(material_pks))
        .order_by(MetadataJob.created_at.desc(), MetadataJob.id.desc())
        .all()
        if material_pks
        else []
    )
    metadata_by_material: dict[int, list[dict[str, Any]]] = {}
    for job in metadata_rows:
        metadata_by_material.setdefault(job.material_pk, []).append(job.to_dict())
    value = run.to_dict()
    progress = pipeline_progress_projection(run, items)
    value["progress_percent"] = progress["percent"]
    value["progress_basis"] = progress["basis"]
    value["items"] = [
        {
            **item.to_dict(),
            "attempts": attempts.get(item.id, []),
            "metadata_jobs": metadata_by_material.get(item.material_pk, []),
        }
        for item in items
    ]
    value["events"] = [
        row.to_dict()
        for row in (
            db.query(PipelineEvent)
            .filter(PipelineEvent.run_id == run.id, PipelineEvent.user_id == run.user_id)
            .order_by(PipelineEvent.created_at.asc(), PipelineEvent.id.asc())
            .all()
        )
    ]
    return value


def list_pipeline_runs(
    db: Session,
    user_id: str,
    *,
    page: int = 1,
    page_size: int = 20,
    status: str = "",
) -> dict[str, Any]:
    query = db.query(PipelineRun).filter(PipelineRun.user_id == user_id)
    if status:
        query = query.filter(PipelineRun.status == status)
    total = query.count()
    rows = (
        query.order_by(PipelineRun.created_at.desc(), PipelineRun.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    summaries = []
    for row in rows:
        value = row.to_dict()
        value.pop("summary", None)
        value.pop("request", None)
        summaries.append(value)
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "runs": summaries,
    }


def _claim_row(db: Session, model, row_id: int, worker_id: str, lease_seconds: int) -> bool:
    now = datetime.utcnow()
    updated = (
        db.query(model)
        .filter(model.id == row_id, model.status == "queued")
        .update(
            {
                model.status: "running",
                model.worker_id: worker_id,
                model.started_at: now,
                model.heartbeat_at: now,
                model.lease_expires_at: now + timedelta(seconds=lease_seconds),
                model.attempt_count: model.attempt_count + 1,
            },
            synchronize_session=False,
        )
    )
    db.commit()
    return bool(updated)


def recover_stale_tasks(db: Session) -> dict[str, int]:
    now = datetime.utcnow()
    pipeline_count = (
        db.query(PipelineRun)
        .filter(PipelineRun.status == "running", PipelineRun.lease_expires_at.isnot(None), PipelineRun.lease_expires_at < now)
        .update(
            {
                PipelineRun.status: "queued",
                PipelineRun.current_stage: "recovered_after_worker_loss",
                PipelineRun.worker_id: None,
                PipelineRun.lease_expires_at: None,
            },
            synchronize_session=False,
        )
    )
    metadata_count = (
        db.query(MetadataJob)
        .filter(MetadataJob.status == "running", MetadataJob.lease_expires_at.isnot(None), MetadataJob.lease_expires_at < now)
        .update(
            {
                MetadataJob.status: "queued",
                MetadataJob.worker_id: None,
                MetadataJob.lease_expires_at: None,
            },
            synchronize_session=False,
        )
    )
    db.commit()
    return {"pipeline_runs": pipeline_count, "metadata_jobs": metadata_count}


def claim_next_pipeline_run(db: Session, worker_id: str) -> PipelineRun | None:
    recover_stale_tasks(db)
    candidate = (
        db.query(PipelineRun)
        .filter(PipelineRun.status == "queued")
        .order_by(PipelineRun.created_at.asc(), PipelineRun.id.asc())
        .first()
    )
    if not candidate or not _claim_row(db, PipelineRun, candidate.id, worker_id, PIPELINE_LEASE_SECONDS):
        return None
    return db.query(PipelineRun).filter(PipelineRun.id == candidate.id).one()


def touch_pipeline_lease(db: Session, run_id: int, worker_id: str) -> bool:
    now = datetime.utcnow()
    updated = (
        db.query(PipelineRun)
        .filter(PipelineRun.id == run_id, PipelineRun.status == "running", PipelineRun.worker_id == worker_id)
        .update(
            {
                PipelineRun.heartbeat_at: now,
                PipelineRun.lease_expires_at: now + timedelta(seconds=PIPELINE_LEASE_SECONDS),
            },
            synchronize_session=False,
        )
    )
    db.commit()
    return bool(updated)


def _attempt_for_stage(db: Session, item: PipelineRunItem, stage: str) -> PipelineStageAttempt:
    latest = (
        db.query(PipelineStageAttempt)
        .filter(PipelineStageAttempt.run_item_id == item.id, PipelineStageAttempt.stage == stage)
        .order_by(PipelineStageAttempt.attempt.desc(), PipelineStageAttempt.id.desc())
        .first()
    )
    if latest and latest.status not in {"succeeded", "failed", "skipped"}:
        return latest
    attempt = PipelineStageAttempt(
        run_item_id=item.id,
        user_id=item.user_id,
        stage=stage,
        attempt=(latest.attempt + 1) if latest else 1,
        status="running",
        started_at=datetime.utcnow(),
    )
    db.add(attempt)
    db.flush()
    return attempt


def mark_pipeline_items_running(db: Session, run: PipelineRun) -> None:
    now = datetime.utcnow()
    for item in pipeline_run_items(db, run.id, run.user_id):
        if item.status in TERMINAL_ITEM_STATUSES:
            continue
        item.status = "running"
        item.current_stage = "pipeline_command"
        item.started_at = item.started_at or now
        _attempt_for_stage(db, item, "mineru")
    db.commit()


def _freeze_map(values: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(values, list):
        return {}
    return {
        str(row.get("material_id") or ""): row
        for row in values
        if isinstance(row, dict) and row.get("material_id")
    }


def _error_map(values: Any) -> dict[str, dict[str, Any]]:
    return _freeze_map(values)


def _manifest_ref(value: dict[str, Any]) -> tuple[str, str]:
    ref = value.get("manifest") if isinstance(value.get("manifest"), dict) else {}
    return str(ref.get("bucket") or ""), str(ref.get("object") or "")


def _finish_attempt(
    attempt: PipelineStageAttempt,
    *,
    status: str,
    batch_id: str = "",
    external_run_id: str = "",
    input_manifest: tuple[str, str] = ("", ""),
    output_manifest: tuple[str, str] = ("", ""),
    error: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
) -> None:
    attempt.status = status
    attempt.external_batch_id = batch_id or None
    attempt.external_run_id = external_run_id or None
    attempt.input_manifest_bucket = input_manifest[0] or None
    attempt.input_manifest_object = input_manifest[1] or None
    attempt.output_manifest_bucket = output_manifest[0] or None
    attempt.output_manifest_object = output_manifest[1] or None
    attempt.error_code = str((error or {}).get("reason") or "") or None
    error_value = (error or {}).get("error")
    attempt.error_message = (
        str(error_value.get("message") or error_value)
        if isinstance(error_value, dict)
        else str(error_value or "")
    ) or None
    attempt.evidence_json = json.dumps(evidence or {}, ensure_ascii=False)
    attempt.finished_at = datetime.utcnow()


def project_pipeline_result(db: Session, run: PipelineRun, payload: dict[str, Any]) -> str:
    mineru_freezes = _freeze_map(payload.get("mineru_freezes"))
    mineru_errors = _error_map(payload.get("mineru_errors"))
    popo = payload.get("popo") if isinstance(payload.get("popo"), dict) else {}
    popo_freezes = _freeze_map(popo.get("freezes"))
    popo_errors = _error_map(popo.get("errors"))
    mineru_batch_id = str(payload.get("mineru_batch_id") or payload.get("existing_mineru_batch_id") or "")
    popo_batch_id = str(popo.get("batch_id") or popo.get("existing_popo_batch_id") or "")
    items = pipeline_run_items(db, run.id, run.user_id)
    now = datetime.utcnow()

    for item in items:
        material = db.query(Material).filter(Material.id == item.material_pk, Material.user_id == item.user_id).first()
        mineru_freeze = mineru_freezes.get(item.material_id)
        mineru_error = mineru_errors.get(item.material_id)
        mineru_attempt = _attempt_for_stage(db, item, "mineru")
        existing_mineru = bool(material and material.mineru_manifest_object)
        if mineru_freeze or (run.mode == "resume_popo" and existing_mineru):
            mineru_ref = _manifest_ref(mineru_freeze or {})
            if not mineru_ref[1] and material:
                mineru_ref = (material.mineru_manifest_bucket or "", material.mineru_manifest_object or "")
            mineru_run_id = str((mineru_freeze or {}).get("run_id") or (material.mineru_run_id if material else "") or "")
            _finish_attempt(
                mineru_attempt,
                status="succeeded",
                batch_id=mineru_batch_id,
                external_run_id=mineru_run_id,
                output_manifest=mineru_ref,
                evidence={"reused": bool(run.mode == "resume_popo" and not mineru_freeze)},
            )
            item.mineru_run_id = mineru_run_id or None
            item.mineru_manifest_bucket, item.mineru_manifest_object = mineru_ref[0] or None, mineru_ref[1] or None
            if material and mineru_ref[1]:
                material.mineru_run_id = mineru_run_id or material.mineru_run_id
                material.mineru_manifest_bucket = mineru_ref[0]
                material.mineru_manifest_object = mineru_ref[1]
                material.promote_stage("mineru_done")
        else:
            _finish_attempt(
                mineru_attempt,
                status="failed",
                batch_id=mineru_batch_id,
                external_run_id=str((mineru_error or {}).get("run_id") or ""),
                error=mineru_error or {"reason": "mineru_not_frozen"},
                evidence=mineru_error or {},
            )

        popo_freeze = popo_freezes.get(item.material_id)
        popo_error = popo_errors.get(item.material_id)
        popo_attempt = _attempt_for_stage(db, item, "popo")
        if popo_freeze:
            popo_ref = _manifest_ref(popo_freeze)
            popo_run_id = str(popo_freeze.get("run_id") or "")
            _finish_attempt(
                popo_attempt,
                status="succeeded",
                batch_id=popo_batch_id,
                external_run_id=popo_run_id,
                input_manifest=(item.mineru_manifest_bucket or "", item.mineru_manifest_object or ""),
                output_manifest=popo_ref,
                evidence=popo_freeze,
            )
            item.status = "succeeded"
            item.current_stage = "popo_frozen"
            item.popo_run_id = popo_run_id or None
            item.popo_manifest_bucket, item.popo_manifest_object = popo_ref[0] or None, popo_ref[1] or None
            item.error_code = None
            item.error_message = None
            if material:
                material.popo_run_id = popo_run_id
                material.popo_manifest_bucket = popo_ref[0]
                material.popo_manifest_object = popo_ref[1]
                material.pipeline_status = "idle"
                material.promote_stage("popo_done")
                enqueue_metadata_job(db, item.user_id, material, automatic=True)
        else:
            reason = popo_error or mineru_error or {"reason": "popo_not_frozen"}
            _finish_attempt(
                popo_attempt,
                status="failed",
                batch_id=popo_batch_id,
                external_run_id=str((popo_error or {}).get("run_id") or ""),
                input_manifest=(item.mineru_manifest_bucket or "", item.mineru_manifest_object or ""),
                error=reason,
                evidence=reason,
            )
            item.status = "failed"
            item.current_stage = "popo_failed" if item.mineru_manifest_object else "mineru_failed"
            item.error_code = str(reason.get("reason") or item.current_stage)
            error_value = reason.get("error")
            item.error_message = (
                str(error_value.get("message") or error_value)
                if isinstance(error_value, dict)
                else str(error_value or item.error_code)
            )
            if material:
                material.pipeline_status = "failed"
        item.finished_at = now

    success = sum(1 for item in items if item.status == "succeeded")
    failed = sum(1 for item in items if item.status == "failed")
    run.total = len(items)
    run.processed = success + failed
    run.success = success
    run.failed = failed
    if items and success == len(items):
        outcome = "succeeded"
    elif success:
        outcome = "partial"
    else:
        outcome = "failed"
    run.status = outcome
    run.queue_slot = None
    run.current_stage = "finished" if outcome == "succeeded" else outcome
    run.finished_at = now
    run.worker_id = None
    run.lease_expires_at = None
    db.flush()
    return outcome


def _source_manifest(material: Material) -> tuple[str, str]:
    if material.popo_manifest_object:
        return material.popo_manifest_bucket or "eduassets-minerupopo", material.popo_manifest_object
    if material.mineru_manifest_object:
        return material.mineru_manifest_bucket or "eduassets-mineru", material.mineru_manifest_object
    return "", ""


def _manifest_sha256(bucket: str, object_name: str) -> str:
    if not bucket or not object_name:
        return ""
    try:
        return hashlib.sha256(read_object(bucket, object_name)).hexdigest()
    except Exception:
        return ""


def metadata_idempotency_key(material: Material, manifest_sha256: str, manifest_ref: tuple[str, str]) -> str:
    identity = manifest_sha256 or f"{manifest_ref[0]}/{manifest_ref[1]}"
    payload = f"{material.id}:{material.material_id or ''}:{identity}:{METADATA_MODEL}:{METADATA_PROMPT_VERSION}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def enqueue_metadata_job(
    db: Session,
    user_id: str,
    material: Material,
    *,
    force: bool = False,
    automatic: bool = False,
) -> MetadataJob | None:
    metadata = (
        db.query(MaterialMetadata)
        .filter(MaterialMetadata.user_id == user_id, MaterialMetadata.material_pk == material.id)
        .first()
    )
    if metadata and metadata.manual_override and not force:
        if automatic:
            return None
        raise MaterialTaskError("该材料元数据已被人工修改，后台任务不会覆盖")
    manifest_ref = _source_manifest(material)
    manifest_sha256 = _manifest_sha256(*manifest_ref)
    key = metadata_idempotency_key(material, manifest_sha256, manifest_ref)
    existing = (
        db.query(MetadataJob)
        .filter(MetadataJob.user_id == user_id, MetadataJob.idempotency_key == key)
        .first()
    )
    if existing:
        return existing
    job = MetadataJob(
        user_id=user_id,
        material_pk=material.id,
        material_id=material.material_id or "",
        status="queued",
        idempotency_key=key,
        source_manifest_bucket=manifest_ref[0] or None,
        source_manifest_object=manifest_ref[1] or None,
        source_manifest_sha256=manifest_sha256 or None,
        model=METADATA_MODEL or None,
        prompt_version=METADATA_PROMPT_VERSION,
        force=force,
        created_at=datetime.utcnow(),
    )
    db.add(job)
    db.flush()
    return job


def list_metadata_jobs(db: Session, user_id: str, *, material_pk: int | None = None, limit: int = 100) -> list[MetadataJob]:
    query = db.query(MetadataJob).filter(MetadataJob.user_id == user_id)
    if material_pk is not None:
        query = query.filter(MetadataJob.material_pk == material_pk)
    return query.order_by(MetadataJob.created_at.desc(), MetadataJob.id.desc()).limit(limit).all()


def retry_metadata_job(db: Session, user_id: str, material_pk: int, job_id: int) -> MetadataJob:
    job = (
        db.query(MetadataJob)
        .filter(
            MetadataJob.id == job_id,
            MetadataJob.user_id == user_id,
            MetadataJob.material_pk == material_pk,
        )
        .first()
    )
    if not job:
        raise MaterialTaskError("AI 元数据任务不存在")
    if job.status not in {"failed", "skipped_manual_override"}:
        raise MaterialTaskError("只有失败的 AI 元数据任务可以重试")
    metadata = (
        db.query(MaterialMetadata)
        .filter(MaterialMetadata.user_id == user_id, MaterialMetadata.material_pk == material_pk)
        .first()
    )
    if metadata and metadata.manual_override and not job.force:
        raise MaterialTaskError("人工元数据已经确认，后台重试不会覆盖")
    job.status = "queued"
    job.worker_id = None
    job.heartbeat_at = None
    job.lease_expires_at = None
    job.error_message = None
    job.finished_at = None
    db.flush()
    return job


def claim_next_metadata_job(db: Session, worker_id: str) -> MetadataJob | None:
    recover_stale_tasks(db)
    candidate = (
        db.query(MetadataJob)
        .filter(MetadataJob.status == "queued")
        .order_by(MetadataJob.created_at.asc(), MetadataJob.id.asc())
        .first()
    )
    if not candidate or not _claim_row(db, MetadataJob, candidate.id, worker_id, METADATA_LEASE_SECONDS):
        return None
    return db.query(MetadataJob).filter(MetadataJob.id == candidate.id).one()


def touch_metadata_lease(db: Session, job_id: int, worker_id: str) -> bool:
    now = datetime.utcnow()
    updated = (
        db.query(MetadataJob)
        .filter(MetadataJob.id == job_id, MetadataJob.status == "running", MetadataJob.worker_id == worker_id)
        .update(
            {
                MetadataJob.heartbeat_at: now,
                MetadataJob.lease_expires_at: now + timedelta(seconds=METADATA_LEASE_SECONDS),
            },
            synchronize_session=False,
        )
    )
    db.commit()
    return bool(updated)


def execute_metadata_job(job_id: int, worker_id: str) -> dict[str, Any]:
    from app.services.material_metadata import MetadataExtractionError, extract_metadata_with_ai

    db = SessionLocal()
    heartbeat_stop = threading.Event()
    heartbeat_thread = None
    try:
        job = db.query(MetadataJob).filter(MetadataJob.id == job_id).first()
        if not job or job.status != "running" or job.worker_id != worker_id:
            return {"ok": False, "reason": "metadata_job_not_claimed", "job_id": str(job_id)}

        def heartbeat_loop() -> None:
            while not heartbeat_stop.wait(10):
                heartbeat_db = SessionLocal()
                try:
                    if not touch_metadata_lease(heartbeat_db, job_id, worker_id):
                        return
                except Exception:
                    heartbeat_db.rollback()
                finally:
                    heartbeat_db.close()

        heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
        heartbeat_thread.start()
        material = db.query(Material).filter(Material.id == job.material_pk, Material.user_id == job.user_id).first()
        if not material:
            raise MaterialTaskError("材料不存在或已不属于任务用户")
        metadata = extract_metadata_with_ai(db, job.user_id, material, force=bool(job.force))
        job.status = "succeeded"
        job.result_json = json.dumps(metadata.to_dict(), ensure_ascii=False)
        job.finished_at = datetime.utcnow()
        job.worker_id = None
        job.lease_expires_at = None
        db.commit()
        return {"ok": True, "job": job.to_dict()}
    except MetadataExtractionError as exc:
        db.rollback()
        job = db.query(MetadataJob).filter(MetadataJob.id == job_id).first()
        if job:
            job.status = "skipped_manual_override" if str(exc) == "manual_override" else "failed"
            job.error_message = str(exc)
            job.finished_at = datetime.utcnow()
            job.worker_id = None
            job.lease_expires_at = None
            db.commit()
        return {"ok": False, "reason": str(exc), "job_id": str(job_id)}
    except Exception as exc:
        db.rollback()
        job = db.query(MetadataJob).filter(MetadataJob.id == job_id).first()
        if job:
            job.status = "failed"
            job.error_message = str(exc)
            job.finished_at = datetime.utcnow()
            job.worker_id = None
            job.lease_expires_at = None
            db.commit()
        return {"ok": False, "reason": str(exc), "job_id": str(job_id)}
    finally:
        heartbeat_stop.set()
        if heartbeat_thread:
            heartbeat_thread.join(timeout=2)
        db.close()
