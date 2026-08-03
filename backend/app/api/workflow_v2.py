from __future__ import annotations

import io
import json
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.material import Material, MaterialOutput
from app.services.luceon_review import read_object
from app.utils.user_dep import get_asset_download_user_id, get_user_id
from app.workflow_v2.contracts import stage_contracts
from app.workflow_v2.database import get_workflow_db, workflow_database_health
from app.workflow_v2.service import (
    WORKFLOW_VERSION,
    create_workflow_job,
    list_workflow_job_summaries,
    list_workflow_job_summary_page,
    list_workflow_jobs,
    workflow_job_detail,
)
from app.workflow_v2.queue import enqueue, enqueue_codex_repair, worker_health
from app.workflow_v2.runner import SUPPORTED_EXECUTORS
from app.workflow_v2.golden_set import GOLDEN_COHORT_VERSION, list_golden_set
from app.workflow_v2.models import ArtifactVersion, RepairAttempt, StageRun, WorkflowJob
from app.workflow_v2.policy import SIDECAR_ENABLED, execution_policy
from app.workflow_v2.sidecar import prepare_codex_repair_request
from app.workflow_v2.sidecar_apply import register_codex_response
from app.workflow_v2.state_machine import WorkflowTransitionError, authorize_execution, request_cancellation, restart_from_stage, retry_failed_stage


router = APIRouter(prefix="/workflow-v2")


class WorkflowJobCreateRequest(BaseModel):
    priority: int = Field(default=100, ge=0, le=1000)
    payload: dict = Field(default_factory=dict)


class WorkflowJobBatchCreateRequest(BaseModel):
    material_pks: list[int] = Field(min_length=1, max_length=100)
    priority: int = Field(default=100, ge=0, le=1000)
    payload: dict = Field(default_factory=dict)


class CodexRepairResponseRequest(BaseModel):
    repair_diff: str = Field(min_length=1, max_length=1_000_000)
    rationale: dict = Field(default_factory=dict)
    rule_suggestions: dict | list = Field(default_factory=dict)


def _output_projection(job_id: str, outputs: list[MaterialOutput] | list[dict]) -> dict:
    rows = [output if isinstance(output, dict) else output.to_dict() for output in outputs]
    job_outputs = [output for output in rows if output.get("output_run_id") == job_id]
    accepted_job_output = next(
        (output for output in job_outputs if output.get("quality_status") == "passed"),
        job_outputs[0] if job_outputs else None,
    )
    current_material_output = next(
        (output for output in rows if output.get("is_current") and output.get("quality_status") == "passed"),
        None,
    )
    return {
        "outputs": job_outputs,
        "job_output_id": accepted_job_output.get("id", "") if accepted_job_output else "",
        "current_output_id": accepted_job_output.get("id", "") if accepted_job_output else "",
        "current_material_output": current_material_output,
        "current_material_output_id": current_material_output.get("id", "") if current_material_output else "",
    }


def workflow_db_dependency():
    try:
        yield from get_workflow_db()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/health")
def workflow_health(user_id: str = Depends(get_user_id)):
    _ = user_id
    worker = worker_health()
    return {
        "workflow_version": WORKFLOW_VERSION,
        "database": workflow_database_health(),
        "v1_batch_frozen": True,
        "v1_auto_retry_frozen": True,
        "execution_enabled": worker["available"],
        "policy": execution_policy(),
        "worker": worker,
    }


@router.get("/contracts")
def workflow_contracts(user_id: str = Depends(get_user_id)):
    _ = user_id
    return {"workflow_version": WORKFLOW_VERSION, "stages": stage_contracts()}


@router.get("/golden-set")
def workflow_golden_set(user_id: str = Depends(get_user_id), workflow_db: Session = Depends(workflow_db_dependency)):
    _ = user_id
    return {"cohort_version": GOLDEN_COHORT_VERSION, "execution_authorized": False, "cases": list_golden_set(workflow_db)}


@router.post("/materials/{material_pk}/jobs")
def create_job(
    material_pk: int,
    payload: WorkflowJobCreateRequest,
    user_id: str = Depends(get_user_id),
    material_db: Session = Depends(get_db),
    workflow_db: Session = Depends(workflow_db_dependency),
):
    material = (
        material_db.query(Material)
        .filter(Material.id == material_pk, Material.user_id == user_id)
        .first()
    )
    if not material:
        raise HTTPException(status_code=404, detail="材料不存在")
    try:
        job, created = create_workflow_job(
            workflow_db,
            user_id=user_id,
            material=material,
            payload=payload.payload,
            priority=payload.priority,
        )
        workflow_db.commit()
    except ValueError as exc:
        workflow_db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    detail = workflow_job_detail(workflow_db, job.public_id)
    return {"created": created, "job": detail}


@router.post("/jobs/batch")
def create_jobs_batch(
    payload: WorkflowJobBatchCreateRequest,
    user_id: str = Depends(get_user_id),
    material_db: Session = Depends(get_db),
    workflow_db: Session = Depends(workflow_db_dependency),
):
    ordered_ids = list(dict.fromkeys(int(value) for value in payload.material_pks))
    materials = (
        material_db.query(Material)
        .filter(Material.user_id == user_id, Material.id.in_(ordered_ids), Material.ignored.is_(False))
        .all()
    )
    by_id = {int(row.id): row for row in materials}
    results = []
    for material_pk in ordered_ids:
        material = by_id.get(material_pk)
        if not material:
            results.append({"material_pk": str(material_pk), "status": "failed", "error": "材料不存在或无权访问"})
            continue
        try:
            job, created = create_workflow_job(
                workflow_db,
                user_id=user_id,
                material=material,
                payload={**payload.payload, "source": payload.payload.get("source") or "refinement_tasks_batch"},
                priority=payload.priority,
            )
            workflow_db.commit()
            results.append(
                {
                    "material_pk": str(material_pk),
                    "material_id": material.material_id or "",
                    "status": "created" if created else "existing",
                    "job": workflow_job_detail(workflow_db, job.public_id),
                }
            )
        except ValueError as exc:
            workflow_db.rollback()
            results.append(
                {
                    "material_pk": str(material_pk),
                    "material_id": material.material_id or "",
                    "status": "failed",
                    "error": str(exc),
                }
            )
    return {
        "total": len(results),
        "created": sum(1 for row in results if row["status"] == "created"),
        "existing": sum(1 for row in results if row["status"] == "existing"),
        "failed": sum(1 for row in results if row["status"] == "failed"),
        "results": results,
    }


@router.get("/jobs/{public_id}")
def get_job(
    public_id: str,
    user_id: str = Depends(get_user_id),
    material_db: Session = Depends(get_db),
    workflow_db: Session = Depends(workflow_db_dependency),
):
    detail = workflow_job_detail(workflow_db, public_id)
    if not detail or detail["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="Worker V2.3 任务不存在")
    material = (
        material_db.query(Material)
        .filter(Material.user_id == user_id, Material.id == int(detail["material_pk"]))
        .first()
    )
    outputs = (
        material_db.query(MaterialOutput)
        .filter(MaterialOutput.user_id == user_id, MaterialOutput.material_pk == int(detail["material_pk"]))
        .order_by(MaterialOutput.created_at.desc(), MaterialOutput.id.desc())
        .all()
    )
    return {
        **detail,
        "filename": material.filename if material else "",
        "review_asset_id": str(material.review_asset_id or "") if material else "",
        **_output_projection(public_id, outputs),
    }


def _review_candidate_artifact(workflow_db: Session, job: WorkflowJob) -> ArtifactVersion:
    artifact = (
        workflow_db.query(ArtifactVersion)
        .filter(
            ArtifactVersion.workflow_job_id == job.id,
            ArtifactVersion.artifact_kind == "elegantbook-candidate",
        )
        .order_by(ArtifactVersion.id.desc())
        .first()
    )
    if not artifact:
        raise HTTPException(status_code=404, detail="没有可审阅的排版候选件")
    return artifact


def _candidate_file(artifact: ArtifactVersion, path: str) -> bytes:
    manifest = json.loads(read_object(artifact.bucket, artifact.object_name))
    inventory = {str(row.get("path") or "") for row in manifest.get("files") or []}
    if path not in inventory:
        raise HTTPException(status_code=404, detail=f"候选件缺少 {path}")
    prefix = artifact.object_name.rsplit("/manifest.json", 1)[0]
    return read_object(artifact.bucket, f"{prefix}/files/{path}")


def _current_review_report(workflow_db: Session, job: WorkflowJob) -> dict:
    artifact = (
        workflow_db.query(ArtifactVersion)
        .join(StageRun, ArtifactVersion.stage_run_id == StageRun.id)
        .filter(
            ArtifactVersion.workflow_job_id == job.id,
            StageRun.stage_key == job.current_stage_key,
            StageRun.status.in_(("needs_review", "blocked", "handoff_ready")),
        )
        .order_by(ArtifactVersion.id.desc())
        .first()
    )
    if not artifact:
        return {}
    try:
        return json.loads(_candidate_file(artifact, "core-acceptance.json"))
    except HTTPException as exc:
        if exc.status_code == 404:
            return {}
        raise


def _merge_review_blockers(*reports: dict) -> list[dict]:
    blockers: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for report in reports:
        for blocker in report.get("blockers") or []:
            key = (str(blocker.get("code") or ""), str(blocker.get("detail") or ""))
            if key in seen:
                continue
            seen.add(key)
            blockers.append(blocker)
    return blockers


def _review_restart_stage(current_stage_key: str, blockers: list[dict]) -> str:
    if current_stage_key == "bounded_deepseek_polish_qa" and any(
        blocker.get("code") == "latex_project_structure_invalid"
        or "LaTeX delivery is missing required paths:" in str(blocker.get("detail") or "")
        for blocker in blockers
    ):
        return "deterministic_elegantbook"
    return current_stage_key


@router.get("/jobs/{public_id}/review-candidate")
def get_review_candidate(
    public_id: str,
    user_id: str = Depends(get_user_id),
    workflow_db: Session = Depends(workflow_db_dependency),
):
    job = workflow_db.query(WorkflowJob).filter(
        WorkflowJob.public_id == public_id,
        WorkflowJob.user_id == user_id,
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Worker V2.3 任务不存在")
    artifact = _review_candidate_artifact(workflow_db, job)
    try:
        quality = json.loads(_candidate_file(artifact, "quality-blockers.json"))
        validation = json.loads(_candidate_file(artifact, "elegantbook-validation.json"))
        compile_report = json.loads(_candidate_file(artifact, "compile-report.json"))
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail=f"候选件证据无法读取：{exc}") from exc
    current_report = _current_review_report(workflow_db, job)
    blockers = _merge_review_blockers(current_report, quality, validation)
    return {
        "artifact": {
            "id": str(artifact.id),
            "sha256": artifact.sha256,
            "status": artifact.status,
            "immutable": artifact.immutable,
            "created_at": artifact.created_at.isoformat() if artifact.created_at else None,
        },
        "status": current_report.get("status") or quality.get("status") or validation.get("status"),
        "blockers": blockers,
        "restart_stage_key": _review_restart_stage(job.current_stage_key, blockers),
        "diagnostics": (validation.get("evidence") or {}).get("latex_diagnostics") or compile_report.get("diagnostics") or {},
        "files": {
            "pdf": f"/api/workflow-v2/jobs/{public_id}/review-candidate/pdf",
            "latex_zip": f"/api/workflow-v2/jobs/{public_id}/review-candidate/latex",
            "validation": f"/api/workflow-v2/jobs/{public_id}/review-candidate/validation",
            "compile_report": f"/api/workflow-v2/jobs/{public_id}/review-candidate/compile",
        },
    }


@router.get("/jobs/{public_id}/review-candidate/{kind}")
def download_review_candidate_file(
    public_id: str,
    kind: str,
    user_id: str = Depends(get_asset_download_user_id),
    workflow_db: Session = Depends(workflow_db_dependency),
):
    job = workflow_db.query(WorkflowJob).filter(
        WorkflowJob.public_id == public_id,
        WorkflowJob.user_id == user_id,
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Worker V2.3 任务不存在")
    files = {
        "pdf": ("main.pdf", "application/pdf", "worker-v2.3-candidate.pdf", "inline"),
        "latex": ("latex-project.zip", "application/zip", "latex-project-needs-review.zip", "attachment"),
        "validation": ("elegantbook-validation.json", "application/json", "elegantbook-validation.json", "attachment"),
        "compile": ("compile-report.json", "application/json", "compile-report.json", "attachment"),
    }
    if kind not in files:
        raise HTTPException(status_code=404, detail="未知的候选件文件类型")
    artifact = _review_candidate_artifact(workflow_db, job)
    path, media_type, filename, disposition = files[kind]
    content = _candidate_file(artifact, path)
    return StreamingResponse(
        io.BytesIO(content),
        media_type=media_type,
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
    )


@router.post("/jobs/{public_id}/handoff")
def handoff_review_candidate(
    public_id: str,
    user_id: str = Depends(get_user_id),
    workflow_db: Session = Depends(workflow_db_dependency),
):
    job = workflow_db.query(WorkflowJob).filter(
        WorkflowJob.public_id == public_id,
        WorkflowJob.user_id == user_id,
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Worker V2.3 任务不存在")
    if job.status not in {"needs_review", "handoff_ready"}:
        raise HTTPException(status_code=409, detail="只有已生成完整交接产物的任务可以转人工处理")
    artifact = _review_candidate_artifact(workflow_db, job)
    repair = (
        workflow_db.query(RepairAttempt)
        .filter(
            RepairAttempt.workflow_job_id == job.id,
            RepairAttempt.repair_kind == "manual_handoff",
            RepairAttempt.patch_artifact_id == artifact.id,
            RepairAttempt.status == "running",
        )
        .first()
    )
    if not repair:
        repair = RepairAttempt(
            workflow_job_id=job.id,
            repair_kind="manual_handoff",
            status="running",
            allowed_scope_json=RepairAttempt.dump({"files": ["main.tex", "images/"]}),
            invariants_json=RepairAttempt.dump({
                "locked_template": True,
                "no_custom_commands_or_environments": True,
                "latex_zip_root_entries": ["images/", "figure/", "main.tex", "elegantbook.cls"],
                "latex_zip_max_bytes": 50_000_000,
            }),
            patch_artifact_id=artifact.id,
            result_json=RepairAttempt.dump({"candidate_sha256": artifact.sha256}),
            error_message="",
        )
        workflow_db.add(repair)
        workflow_db.flush()
    workflow_db.commit()
    return {"handoff": repair.to_dict(), "job": workflow_job_detail(workflow_db, public_id)}


@router.post("/jobs/{public_id}/revalidate")
def revalidate_review_candidate(
    public_id: str,
    user_id: str = Depends(get_user_id),
    workflow_db: Session = Depends(workflow_db_dependency),
):
    job = workflow_db.query(WorkflowJob).filter(
        WorkflowJob.public_id == public_id,
        WorkflowJob.user_id == user_id,
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Worker V2.3 任务不存在")
    if job.status not in {"needs_review", "handoff_ready"}:
        raise HTTPException(status_code=409, detail="只有待处理候选件可以重新验证")
    handoffs = workflow_db.query(RepairAttempt).filter(
        RepairAttempt.workflow_job_id == job.id,
        RepairAttempt.repair_kind == "manual_handoff",
        RepairAttempt.status == "running",
    ).all()
    if not handoffs:
        raise HTTPException(status_code=409, detail="请先登记转人工处理，再重新验证候选件")
    try:
        current_report = _current_review_report(workflow_db, job)
        restart_stage_key = _review_restart_stage(
            job.current_stage_key,
            _merge_review_blockers(current_report),
        )
        for handoff in handoffs:
            handoff.status = "succeeded"
            handoff.finished_at = datetime.utcnow()
        _, stage = restart_from_stage(workflow_db, public_id, restart_stage_key)
        workflow_db.commit()
        message_id = enqueue(public_id)
    except WorkflowTransitionError as exc:
        workflow_db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=f"重新验证已记录，但 Redis 入队失败：{exc}") from exc
    return {"queued": True, "message_id": message_id, "attempt": stage.attempt, "job": workflow_job_detail(workflow_db, public_id)}


@router.get("/jobs")
def get_jobs(
    material_pk: int | None = None,
    limit: int = 50,
    user_id: str = Depends(get_user_id),
    workflow_db: Session = Depends(workflow_db_dependency),
):
    return {"jobs": list_workflow_jobs(workflow_db, user_id=user_id, material_pk=material_pk, limit=limit)}


@router.get("/job-summaries")
def get_job_summaries(
    limit: int | None = None,
    page: int = 1,
    page_size: int = 20,
    status: str = "",
    user_id: str = Depends(get_user_id),
    material_db: Session = Depends(get_db),
    workflow_db: Session = Depends(workflow_db_dependency),
):
    def enrich(payload: dict) -> dict:
        jobs = payload.get("jobs") or []
        material_pks = [int(job["material_pk"]) for job in jobs if str(job.get("material_pk") or "").isdigit()]
        materials = (
            material_db.query(Material)
            .filter(Material.user_id == user_id, Material.id.in_(material_pks))
            .all()
            if material_pks
            else []
        )
        by_id = {str(row.id): row for row in materials}
        output_rows = (
            material_db.query(MaterialOutput)
            .filter(MaterialOutput.user_id == user_id, MaterialOutput.material_pk.in_(material_pks))
            .order_by(MaterialOutput.created_at.desc(), MaterialOutput.id.desc())
            .all()
            if material_pks
            else []
        )
        outputs_by_material: dict[str, list[MaterialOutput]] = {}
        for output in output_rows:
            outputs_by_material.setdefault(str(output.material_pk), []).append(output)
        payload["jobs"] = [
            {
                **job,
                "filename": by_id[job["material_pk"]].filename if job.get("material_pk") in by_id else "",
                "review_asset_id": str(by_id[job["material_pk"]].review_asset_id or "") if job.get("material_pk") in by_id else "",
                **_output_projection(job["id"], outputs_by_material.get(job.get("material_pk") or "", [])),
            }
            for job in jobs
        ]
        return payload

    if limit is not None:
        jobs = list_workflow_job_summaries(workflow_db, user_id=user_id, limit=limit)
        return enrich({
            "jobs": jobs,
            "status_counts": {
                "passed": sum(job.get("business_status") == "passed" for job in jobs),
                "blocked": sum(job.get("business_status") == "blocked" for job in jobs),
                "running": sum(job.get("business_status") == "running" for job in jobs),
            },
        })
    return enrich(
        list_workflow_job_summary_page(
            workflow_db,
            user_id=user_id,
            page=max(page, 1),
            page_size=min(max(page_size, 1), 100),
            status=status,
        )
    )


@router.post("/jobs/{public_id}/cancel")
def cancel_job(
    public_id: str,
    user_id: str = Depends(get_user_id),
    workflow_db: Session = Depends(workflow_db_dependency),
):
    detail = workflow_job_detail(workflow_db, public_id)
    if not detail or detail["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="Worker V2.3 任务不存在")
    request_cancellation(workflow_db, public_id)
    workflow_db.commit()
    return workflow_job_detail(workflow_db, public_id)


@router.post("/jobs/{public_id}/retry")
def retry_job(
    public_id: str,
    user_id: str = Depends(get_user_id),
    workflow_db: Session = Depends(workflow_db_dependency),
):
    detail = workflow_job_detail(workflow_db, public_id)
    if not detail or detail["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="Worker V2.3 任务不存在")
    try:
        retry_failed_stage(workflow_db, public_id)
        workflow_db.commit()
        message_id = enqueue(public_id)
    except WorkflowTransitionError as exc:
        workflow_db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=f"重试已记录，但 Redis 入队失败：{exc}") from exc
    return {"queued": True, "message_id": message_id, "job": workflow_job_detail(workflow_db, public_id)}


@router.post("/jobs/{public_id}/run")
def run_job(
    public_id: str,
    user_id: str = Depends(get_user_id),
    workflow_db: Session = Depends(workflow_db_dependency),
):
    detail = workflow_job_detail(workflow_db, public_id)
    if not detail or detail["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="Worker V2.3 任务不存在")
    if detail["current_stage_key"] not in SUPPORTED_EXECUTORS:
        raise HTTPException(status_code=409, detail="当前阶段执行器尚未内化完成")
    try:
        authorize_execution(workflow_db, public_id, requested_by=user_id)
        workflow_db.commit()
        message_id = enqueue(public_id)
    except WorkflowTransitionError as exc:
        workflow_db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=f"任务已记录，但 Redis 入队失败：{exc}") from exc
    return {"queued": True, "message_id": message_id, "job": workflow_job_detail(workflow_db, public_id)}


@router.post("/jobs/{public_id}/restart/{stage_key}")
def restart_job_from_stage(
    public_id: str,
    stage_key: str,
    user_id: str = Depends(get_user_id),
    workflow_db: Session = Depends(workflow_db_dependency),
):
    detail = workflow_job_detail(workflow_db, public_id)
    if not detail or detail["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="Worker V2.3 任务不存在")
    try:
        _, stage = restart_from_stage(workflow_db, public_id, stage_key)
        workflow_db.commit()
        message_id = enqueue(public_id)
    except WorkflowTransitionError as exc:
        workflow_db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=f"重建分支已记录，但 Redis 入队失败：{exc}") from exc
    return {"queued": True, "message_id": message_id, "attempt": stage.attempt, "job": workflow_job_detail(workflow_db, public_id)}


@router.get("/jobs/{public_id}/repairs")
def get_job_repairs(
    public_id: str,
    user_id: str = Depends(get_user_id),
    workflow_db: Session = Depends(workflow_db_dependency),
):
    job = workflow_db.query(WorkflowJob).filter(WorkflowJob.public_id == public_id, WorkflowJob.user_id == user_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Worker V2.3 任务不存在")
    repairs = (
        workflow_db.query(RepairAttempt)
        .filter(RepairAttempt.workflow_job_id == job.id)
        .order_by(RepairAttempt.id.desc())
        .all()
    )
    return {"repairs": [repair.to_dict() for repair in repairs]}


@router.post("/jobs/{public_id}/repairs/codex")
def create_codex_repair(
    public_id: str,
    user_id: str = Depends(get_user_id),
    workflow_db: Session = Depends(workflow_db_dependency),
):
    if not SIDECAR_ENABLED:
        raise HTTPException(status_code=409, detail="Codex Sidecar 已暂停；当前仅收敛 Worker V2.3 核心生产门禁")
    job = workflow_db.query(WorkflowJob).filter(WorkflowJob.public_id == public_id, WorkflowJob.user_id == user_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Worker V2.3 任务不存在")
    try:
        repair, artifact = prepare_codex_repair_request(workflow_db, public_id)
        workflow_db.commit()
    except ValueError as exc:
        workflow_db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "repair": repair.to_dict(),
        "request_artifact": {"id": str(artifact.id), "sha256": artifact.sha256, "bucket": artifact.bucket, "object": artifact.object_name},
    }


@router.post("/jobs/{public_id}/repairs/{repair_id}/apply", status_code=202)
def apply_codex_repair_response(
    public_id: str,
    repair_id: int,
    payload: CodexRepairResponseRequest,
    user_id: str = Depends(get_user_id),
    workflow_db: Session = Depends(workflow_db_dependency),
):
    if not SIDECAR_ENABLED:
        raise HTTPException(status_code=409, detail="Codex Sidecar 已暂停；历史修复记录仍保留为只读审计证据")
    job = workflow_db.query(WorkflowJob).filter(
        WorkflowJob.public_id == public_id,
        WorkflowJob.user_id == user_id,
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Worker V2.3 任务不存在")
    repair = workflow_db.query(RepairAttempt).filter(
        RepairAttempt.id == repair_id,
        RepairAttempt.workflow_job_id == job.id,
        RepairAttempt.repair_kind == "codex_sidecar_patch",
    ).first()
    if not repair:
        raise HTTPException(status_code=404, detail="Codex 定向修复请求不存在")
    if repair.status != "queued":
        raise HTTPException(status_code=409, detail="Codex 定向修复请求不是待提交状态")
    response_dir = Path("/data/workflow-v2-sidecar-responses") / public_id / f"repair-{repair_id}"
    if response_dir.exists():
        shutil.rmtree(response_dir)
    response_dir.mkdir(parents=True)
    (response_dir / "repair.diff").write_text(payload.repair_diff, encoding="utf-8")
    (response_dir / "rationale.json").write_text(
        json.dumps(payload.rationale, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (response_dir / "rule-suggestions.json").write_text(
        json.dumps(payload.rule_suggestions, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    try:
        registration = register_codex_response(workflow_db, repair_id=repair_id, response_dir=response_dir)
        workflow_db.commit()
    except ValueError as exc:
        workflow_db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        workflow_db.rollback()
        raise HTTPException(status_code=422, detail=f"Codex patch 响应验证或冻结失败：{exc}") from exc
    try:
        message_id = enqueue_codex_repair(public_id, repair_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=f"Codex 响应已冻结，但 Redis 入队失败：{exc}") from exc
    return {
        "accepted": True,
        "message_id": message_id,
        "registration": registration,
        "job": workflow_job_detail(workflow_db, public_id),
    }
