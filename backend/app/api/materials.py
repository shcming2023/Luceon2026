import mimetypes
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.material import (
    CodexSkillJob,
    Material,
    MaterialOutput,
    MetadataJob,
    PipelineEvent,
    PipelineRun,
    PipelineRunItem,
)
from app.models.material_metadata import MaterialMetadata
from app.models.user import User
from app.services.clean_to_standard import CleanToStandardPreflightError, clean_to_standard_preflight, start_clean_to_standard_run
from app.services.codex_skill_jobs import (
    CodexSkillJobError,
    cancel_codex_skill_job,
    create_codex_skill_job,
    enqueue_legacy_refresh_codex_jobs,
    get_codex_skill_job,
    latest_codex_skill_job_for_material,
    list_codex_skill_jobs,
    retry_codex_skill_job,
)
from app.services.material_inventory import (
    INPUT_BUCKET,
    PipelinePreflightError,
    latest_timed_out_popo_batch_id,
    latest_pipeline_run,
    material_summary,
    minio_client,
    run_popo_resume_preflight,
    run_pipeline_preflight,
    run_cloud_deferred_pipeline_preflight,
    apply_pipeline_resource_gate,
    start_popo_resume_run,
    start_pipeline_run,
    sync_material_inventory,
    upload_input_pdfs,
)
from app.services.upload_policy import pdf_upload_capabilities
from app.services.upload_policy import load_pdf_upload_policy
from app.services.material_metadata import (
    MetadataExtractionError,
    extract_metadata_with_ai,
    get_or_create_metadata,
    metadata_for_materials,
    metadata_options,
    metadata_to_dict,
    upsert_manual_metadata,
)
from app.services.material_artifacts import (
    ArtifactNotFoundError,
    material_artifact_catalog,
    open_artifact_stream,
    parse_single_range,
    resolve_material_artifact,
    stream_response_body,
)
from app.services.luceon_review import ObjectRef
from app.services.material_outputs import sync_material_outputs_for_material
from app.services.material_task_queue import (
    MaterialTaskError,
    enqueue_metadata_job,
    list_metadata_jobs,
    list_pipeline_runs,
    pipeline_attempts_for_items,
    pipeline_run_detail,
    retry_metadata_job,
)
from app.services.popo_to_raw import (
    PopoToRawPreflightError,
    PopoToRawPublishError,
    latest_successful_popo_to_raw_dry_run,
    popo_to_raw_preflight,
    publish_popo_to_raw_dry_run,
    start_popo_to_raw_run,
)
from app.services.popo_to_raw_audit import audit_popo_to_raw_run
from app.services.raw_to_clean import RawToCleanPreflightError, raw_to_clean_preflight, start_raw_to_clean_run
from app.services.worker_v1_policy import v1_batch_enabled, v1_policy
from app.utils.minio_client import get_presigned_url
from app.utils.user_dep import get_asset_download_user_id, get_user_id, is_pipeline_admin, require_pipeline_admin
from app.workflow_v2.database import workflow_session_factory
from app.workflow_v2.models import WorkflowJob
from app.workflow_v2.service import list_workflow_jobs

router = APIRouter()
RUN_STAMP_PATTERN = re.compile(r"(20\d{12})")


def _run_stamp(value: str | None) -> str:
    match = RUN_STAMP_PATTERN.search(str(value or ""))
    return match.group(1) if match else ""


CODEX_JOB_STATUS_PRIORITY = {
    "running": 40,
    "queued": 35,
    "dry_run_succeeded": 30,
    "validating": 25,
    "published": 10,
}

RECENT_UPLOAD_WINDOW = timedelta(hours=24)


def _material_activity_sort_key(material: Material, codex_job: CodexSkillJob | None = None) -> tuple[int, str, int, int]:
    run_stamp = max(
        _run_stamp(material.latex_run_id),
        _run_stamp(material.standard_run_id),
        _run_stamp(material.clean_run_id),
        _run_stamp(material.raw_run_id),
        _run_stamp(material.popo_run_id),
        _run_stamp(material.mineru_run_id),
    )
    created = material.created_at.strftime("%Y%m%d%H%M%S%f") if material.created_at else ""
    codex_priority = CODEX_JOB_STATUS_PRIORITY.get(codex_job.status, 0) if codex_job else 0
    codex_created = codex_job.created_at.strftime("%Y%m%d%H%M%S%f") if codex_job and codex_job.created_at else ""
    activity_stamp = max(created, codex_created, f"{run_stamp}000000" if run_stamp else "")
    job_status = codex_job.status if codex_job else ""
    recent_upload = bool(
        material.source_type == "uploaded"
        and material.stage_status == "input"
        and material.created_at
        and material.created_at >= datetime.now(timezone.utc).replace(tzinfo=None) - RECENT_UPLOAD_WINDOW
    )
    if material.pipeline_status == "running" or job_status in {"running", "validating"}:
        activity_priority = 3
    elif recent_upload:
        activity_priority = 2
    elif job_status == "queued":
        activity_priority = 1
    else:
        activity_priority = 0
    return (activity_priority, activity_stamp, codex_priority, int(material.id or 0))


def _latest_codex_jobs_for_materials(db: Session, user_id: str, material_ids: list[int]) -> dict[int, CodexSkillJob]:
    if not material_ids:
        return {}
    jobs = (
        db.query(CodexSkillJob)
        .filter(CodexSkillJob.user_id == user_id, CodexSkillJob.material_pk.in_(material_ids))
        .order_by(CodexSkillJob.created_at.desc(), CodexSkillJob.id.desc())
        .all()
    )
    latest: dict[int, CodexSkillJob] = {}
    for job in jobs:
        if job.material_pk and job.material_pk not in latest:
            latest[job.material_pk] = job
    return latest


def _latest_workflow_jobs_for_materials(user_id: str, material_ids: list[int]) -> tuple[dict[int, dict], bool]:
    if not material_ids:
        return {}, True
    workflow_db = None
    try:
        workflow_db = workflow_session_factory()()
        jobs = (
            workflow_db.query(WorkflowJob)
            .filter(WorkflowJob.user_id == user_id, WorkflowJob.material_pk.in_(material_ids))
            .order_by(WorkflowJob.created_at.desc(), WorkflowJob.id.desc())
            .all()
        )
        latest: dict[int, dict] = {}
        for job in jobs:
            if job.material_pk not in latest:
                latest[job.material_pk] = job.to_dict()
        return latest, True
    except Exception:
        return {}, False
    finally:
        if workflow_db is not None:
            workflow_db.close()


def _refinement_projection(
    material: Material,
    codex_job: CodexSkillJob | None,
    workflow_job: WorkflowJob | dict | None,
    current_output: MaterialOutput | None,
    *,
    workflow_available: bool = True,
) -> dict:
    output_available = bool(current_output or (material.latex_manifest_bucket and material.latex_manifest_object))
    output = current_output.to_dict() if current_output else None
    if not workflow_available:
        task = None
        task_status = "unavailable"
        task_source = "workflow_v2_unavailable"
    elif workflow_job:
        task = workflow_job if isinstance(workflow_job, dict) else workflow_job.to_dict()
        task_status = str(task.get("status") or "idle")
        task_source = "workflow_v2"
    elif codex_job:
        task = codex_job.to_dict()
        task_status = str(codex_job.status or "idle")
        task_source = "legacy_codex"
    else:
        task = None
        task_status = "idle"
        task_source = "none"
    return {
        "refinement_output_status": "succeeded" if output_available else "idle",
        "current_refinement_output": output,
        "latest_refinement_status": task_status,
        "latest_refinement_job": task,
        "latest_refinement_source": task_source,
        # Backward-compatible aggregate: actionable task state wins; otherwise
        # preserve the fact that a durable output is already available.
        "refinement_status": task_status if task_status != "idle" else ("succeeded" if output_available else "idle"),
    }


def _refinement_status(
    material: Material,
    codex_job: CodexSkillJob | None,
    workflow_job: WorkflowJob | dict | None = None,
    current_output: MaterialOutput | None = None,
    *,
    workflow_available: bool = True,
) -> str:
    return _refinement_projection(
        material,
        codex_job,
        workflow_job,
        current_output,
        workflow_available=workflow_available,
    )["refinement_status"]


class PipelineStartRequest(BaseModel):
    apply: bool = False
    limit: int = 5
    material_id: str = ""
    input_object: str = ""
    material_ids: list[str] = Field(default_factory=list)
    input_objects: list[str] = Field(default_factory=list)
    material_pks: list[int] = Field(default_factory=list)
    reprocess_completed: bool = False


class PipelinePreflightRequest(BaseModel):
    limit: int = 5
    material_id: str = ""
    input_object: str = ""
    material_ids: list[str] = Field(default_factory=list)
    input_objects: list[str] = Field(default_factory=list)
    material_pks: list[int] = Field(default_factory=list)
    reprocess_completed: bool = False


class PopoToRawStartRequest(BaseModel):
    publish: bool = False
    force: bool = False


class PopoToRawPublishDryRunRequest(BaseModel):
    run_id: int
    force: bool = True


class RawToCleanStartRequest(BaseModel):
    publish: bool = True
    force: bool = False


class CleanToStandardStartRequest(BaseModel):
    publish: bool = True
    force: bool = False


class CodexSkillJobCreateRequest(BaseModel):
    mode: str = "new_pdf"
    requested_skill: str = "luceon-popo-to-refined-elegantbook"
    skill_version: str = "draft"
    run_reason: str = ""
    force: bool = False
    payload: dict = Field(default_factory=dict)


class CodexSkillBatchRefreshRequest(BaseModel):
    limit: int = 10
    material_ids: list[str] = Field(default_factory=list)


class MaterialMetadataUpdateRequest(BaseModel):
    original_title: str = ""
    publication_date: str = ""
    publication_year: int | None = None
    edition: str = ""
    subject: str = ""
    publication_country: str = ""
    series_name: str = ""
    publisher: str = ""
    isbn: str = ""
    language: str = ""
    grade_level: str = ""
    confidence: float | None = None
    evidence: list[dict] = []


class MaterialMetadataExtractRequest(BaseModel):
    force: bool = False


def _material_or_404(material_pk: int, user_id: str, db: Session) -> Material:
    material = db.query(Material).filter(Material.id == material_pk, Material.user_id == user_id).first()
    if not material:
        raise HTTPException(status_code=404, detail="材料不存在")
    return material


def _admin_material_or_404(material_pk: int, db: Session) -> Material:
    material = db.query(Material).filter(Material.id == material_pk, Material.ignored.is_(False)).first()
    if not material:
        raise HTTPException(status_code=404, detail="材料不存在")
    return material


def _legacy_stage_gone() -> None:
    raise HTTPException(status_code=410, detail="Raw/Clean/Standard 节点已下线")


def _popo_latex_gone() -> None:
    raise HTTPException(
        status_code=410,
        detail="LuceonWeb 不再执行 Popo→LaTeX/PDF；请由 Codex 异步产出 ElegantBook 后同步消费",
    )


@router.get("/materials")
def list_materials(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str = Query("", description="按文件名、material_id、MinIO 对象或教材元数据搜索"),
    stage: str = Query("", description="按阶段筛选"),
    metadata_status: str = Query("", description="按元数据状态筛选"),
    subject: str = Query("", description="按学科筛选"),
    country: str = Query("", description="按出版国家筛选"),
    series: str = Query("", description="按系列名筛选"),
    year_from: int | None = Query(None, ge=0, le=2200),
    year_to: int | None = Query(None, ge=0, le=2200),
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    metadata_join = and_(MaterialMetadata.user_id == user_id, MaterialMetadata.material_pk == Material.id)
    query = db.query(Material).outerjoin(MaterialMetadata, metadata_join).filter(Material.user_id == user_id, Material.ignored.is_(False))
    if search:
        like = f"%{search}%"
        query = query.filter(
            or_(
                Material.title.like(like),
                Material.filename.like(like),
                Material.material_id.like(like),
                Material.input_object.like(like),
                MaterialMetadata.original_title.like(like),
                MaterialMetadata.series_name.like(like),
                MaterialMetadata.publisher.like(like),
                MaterialMetadata.isbn.like(like),
                MaterialMetadata.subject.like(like),
                MaterialMetadata.edition.like(like),
            )
        )
    if metadata_status == "missing":
        query = query.filter(or_(MaterialMetadata.id.is_(None), MaterialMetadata.status == "missing"))
    elif metadata_status:
        query = query.filter(MaterialMetadata.status == metadata_status)
    if subject:
        query = query.filter(MaterialMetadata.subject == subject)
    if country:
        query = query.filter(MaterialMetadata.publication_country == country)
    if series:
        query = query.filter(MaterialMetadata.series_name.like(f"%{series}%"))
    if year_from is not None:
        query = query.filter(MaterialMetadata.publication_year >= year_from)
    if year_to is not None:
        query = query.filter(MaterialMetadata.publication_year <= year_to)
    if stage == "pdf":
        query = query.filter(Material.input_object.isnot(None))
    elif stage == "mineru":
        query = query.filter(or_(Material.mineru_manifest_object.isnot(None), Material.popo_manifest_object.isnot(None)))
    elif stage == "popo":
        query = query.filter(Material.popo_manifest_object.isnot(None))
    elif stage == "latex":
        query = query.filter(Material.latex_manifest_object.isnot(None))
    elif stage:
        query = query.filter(Material.stage_status == stage)

    rows_all = query.all()
    material_ids = [int(row.id) for row in rows_all if row.id]
    latest_codex_jobs = _latest_codex_jobs_for_materials(db, user_id, material_ids)
    latest_workflow_jobs, workflow_available = _latest_workflow_jobs_for_materials(user_id, material_ids)
    total = len(rows_all)
    rows_all.sort(key=lambda row: _material_activity_sort_key(row, latest_codex_jobs.get(int(row.id or 0))), reverse=True)
    rows = rows_all[(page - 1) * page_size : page * page_size]
    metadata_map = metadata_for_materials(db, user_id, [row.id for row in rows])
    current_outputs: dict[int, MaterialOutput] = {}
    for output in (
        db.query(MaterialOutput)
        .filter(
            MaterialOutput.user_id == user_id,
            MaterialOutput.material_pk.in_([row.id for row in rows]),
            MaterialOutput.is_current.is_(True),
            MaterialOutput.quality_status == "passed",
        )
        .order_by(MaterialOutput.created_at.desc(), MaterialOutput.id.desc())
        .all()
    ):
        if output.material_pk:
            current_outputs.setdefault(int(output.material_pk), output)
    material_rows = []
    for row in rows:
        data = row.to_dict()
        data["book_metadata"] = metadata_to_dict(metadata_map.get(row.id))
        dry_run = latest_successful_popo_to_raw_dry_run(db, user_id, row.material_id or "")
        data["raw_dry_run_available"] = bool(dry_run)
        data["raw_dry_run_id"] = str(dry_run.id) if dry_run else ""
        latest_codex_job = latest_codex_jobs.get(int(row.id or 0)) or latest_codex_skill_job_for_material(db, user_id, row)
        data["codex_job"] = latest_codex_job.to_dict() if latest_codex_job else None
        data.update(
            _refinement_projection(
                row,
                latest_codex_job,
                latest_workflow_jobs.get(int(row.id or 0)),
                current_outputs.get(int(row.id or 0)),
                workflow_available=workflow_available,
            )
        )
        material_rows.append(data)
    return {"total": total, "page": page, "page_size": page_size, "materials": material_rows}


@router.get("/materials/metadata/options")
def get_material_metadata_options(
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    return metadata_options(db, user_id)


@router.get("/materials/{material_pk}/metadata")
def get_material_metadata(
    material_pk: int,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    material = _material_or_404(material_pk, user_id, db)
    metadata = get_or_create_metadata(db, user_id, material)
    db.commit()
    db.refresh(metadata)
    return metadata.to_dict()


@router.patch("/materials/{material_pk}/metadata")
def update_material_metadata(
    material_pk: int,
    payload: MaterialMetadataUpdateRequest,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    material = _material_or_404(material_pk, user_id, db)
    metadata = upsert_manual_metadata(db, user_id, material, payload.model_dump())
    db.commit()
    db.refresh(metadata)
    return metadata.to_dict()


@router.post("/materials/{material_pk}/metadata/extract")
def extract_material_metadata(
    material_pk: int,
    payload: MaterialMetadataExtractRequest,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    material = _material_or_404(material_pk, user_id, db)
    try:
        metadata = extract_metadata_with_ai(db, user_id, material, force=payload.force)
        db.commit()
        db.refresh(metadata)
        return metadata.to_dict()
    except MetadataExtractionError as exc:
        db.rollback()
        metadata = get_or_create_metadata(db, user_id, material)
        metadata.status = "failed"
        metadata.extraction_error = str(exc)
        db.commit()
        if str(exc) == "manual_override":
            raise HTTPException(status_code=409, detail="该材料元数据已被人工修改；如需覆盖，请勾选强制重新提取")
        raise HTTPException(status_code=409, detail=f"元数据提取失败：{exc}")


@router.post("/materials/{material_pk}/metadata/jobs", status_code=202)
def create_material_metadata_job(
    material_pk: int,
    payload: MaterialMetadataExtractRequest,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    material = _material_or_404(material_pk, user_id, db)
    try:
        job = enqueue_metadata_job(db, user_id, material, force=payload.force)
        if not job:
            raise MaterialTaskError("人工元数据已经确认，无需后台提取")
        db.commit()
        db.refresh(job)
        return job.to_dict()
    except MaterialTaskError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/materials/{material_pk}/metadata/jobs")
def get_material_metadata_jobs(
    material_pk: int,
    limit: int = Query(50, ge=1, le=200),
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    _material_or_404(material_pk, user_id, db)
    return {"jobs": [job.to_dict() for job in list_metadata_jobs(db, user_id, material_pk=material_pk, limit=limit)]}


@router.post("/materials/{material_pk}/metadata/jobs/{job_id}/retry", status_code=202)
def retry_material_metadata_job(
    material_pk: int,
    job_id: int,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    _material_or_404(material_pk, user_id, db)
    try:
        job = retry_metadata_job(db, user_id, material_pk, job_id)
        db.commit()
        db.refresh(job)
        return job.to_dict()
    except MaterialTaskError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/materials/{material_pk}/lineage")
def get_material_lineage(
    material_pk: int,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    material = _material_or_404(material_pk, user_id, db)
    items = (
        db.query(PipelineRunItem)
        .filter(PipelineRunItem.user_id == user_id, PipelineRunItem.material_pk == material.id)
        .order_by(PipelineRunItem.created_at.desc(), PipelineRunItem.id.desc())
        .limit(100)
        .all()
    )
    attempts = pipeline_attempts_for_items(db, items)
    pipeline_items = []
    for item in items:
        run = db.query(PipelineRun).filter(PipelineRun.id == item.run_id, PipelineRun.user_id == user_id).first()
        pipeline_items.append(
            {
                **item.to_dict(),
                "run": run.to_dict() if run else None,
                "attempts": attempts.get(item.id, []),
            }
        )

    metadata_jobs = (
        db.query(MetadataJob)
        .filter(MetadataJob.user_id == user_id, MetadataJob.material_pk == material.id)
        .order_by(MetadataJob.created_at.desc(), MetadataJob.id.desc())
        .limit(100)
        .all()
    )
    outputs = (
        db.query(MaterialOutput)
        .filter(MaterialOutput.user_id == user_id, MaterialOutput.material_pk == material.id)
        .order_by(MaterialOutput.created_at.desc(), MaterialOutput.id.desc())
        .limit(100)
        .all()
    )

    workflow_status = {"available": True, "error": ""}
    workflow_jobs: list[dict] = []
    try:
        workflow_db = workflow_session_factory()()
        try:
            workflow_jobs = list_workflow_jobs(workflow_db, user_id=user_id, material_pk=material.id, limit=100)
        finally:
            workflow_db.close()
    except Exception as exc:
        workflow_status = {"available": False, "error": str(exc)}

    return {
        "material": material.to_dict(),
        "pipeline_items": pipeline_items,
        "metadata_jobs": [job.to_dict() for job in metadata_jobs],
        "workflow_jobs": workflow_jobs,
        "workflow_status": workflow_status,
        "outputs": [output.to_dict() for output in outputs],
        "review": {
            "asset_id": str(material.review_asset_id) if material.review_asset_id else "",
            "available": bool(material.review_asset_id),
        },
    }


@router.get("/materials/summary")
def get_material_summary(
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    return material_summary(db, user_id)


@router.post("/materials/sync")
def sync_materials(
    limit: int | None = Query(None, ge=1, le=1000),
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    try:
        summary = sync_material_inventory(db, user_id, limit=limit)
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"同步 MinIO 资产失败: {exc}")
    return summary


async def bounded_material_upload_files(request: Request):
    policy = load_pdf_upload_policy()
    try:
        form = await request.form(
            max_files=policy.max_request_files,
            max_fields=policy.max_request_fields,
            max_part_size=policy.max_file_bytes,
        )
    except StarletteHTTPException as exc:
        detail = str(exc.detail)
        if "Too many files" in detail or "Too many fields" in detail or detail.startswith("upload_envelope_exceeded:"):
            status_code = 413
        elif detail == "upload_client_disconnected":
            status_code = 400
        else:
            status_code = exc.status_code
        raise HTTPException(status_code=status_code, detail=detail) from exc
    try:
        files = [value for key, value in form.multi_items() if key == "files" and isinstance(value, StarletteUploadFile)]
        if not files:
            raise HTTPException(status_code=422, detail="至少提交一个 PDF 文件")
        yield files
    finally:
        await form.close()


UPLOAD_OPENAPI = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["files"],
                    "properties": {"files": {"type": "array", "items": {"type": "string", "format": "binary"}}},
                }
            }
        },
    }
}


@router.post(
    "/materials/upload",
    openapi_extra=UPLOAD_OPENAPI,
    responses={
        413: {"description": "Configured multipart file/count/aggregate envelope exceeded"},
        507: {"description": "Temporary upload storage preflight failed before body consumption"},
    },
)
async def upload_materials(
    files: list[UploadFile] = Depends(bounded_material_upload_files),
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    try:
        return await upload_input_pdfs(files, user_id, db)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=413, detail=str(exc))
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"上传 PDF 失败: {exc}")


@router.get("/materials/upload/capabilities")
def upload_capabilities(user_id: str = Depends(get_user_id)):
    del user_id
    return pdf_upload_capabilities()


@router.get("/materials/pipeline/status")
def pipeline_status(
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    run = latest_pipeline_run(db, user_id)
    events = []
    if run:
        events = (
            db.query(PipelineEvent)
            .filter(PipelineEvent.run_id == run.id, PipelineEvent.user_id == user_id)
            .order_by(PipelineEvent.created_at.desc(), PipelineEvent.id.desc())
            .limit(20)
            .all()
        )
    return {
        "run": pipeline_run_detail(db, run) if run else None,
        "events": [event.to_dict() for event in events],
        "audit": audit_popo_to_raw_run(db, run) if run and run.mode == "popo2raw" else None,
    }


@router.get("/materials/pipeline/runs")
def get_pipeline_runs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str = Query(""),
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    return list_pipeline_runs(db, user_id, page=page, page_size=page_size, status=status)


@router.get("/materials/pipeline/runs/{run_id}")
def get_pipeline_run(
    run_id: int,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    run = db.query(PipelineRun).filter(PipelineRun.id == run_id, PipelineRun.user_id == user_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="解析任务不存在")
    return pipeline_run_detail(db, run)


@router.get("/materials/pipeline/runs/{run_id}/artifact")
def pipeline_run_artifact(
    run_id: int,
    path: str = Query(..., min_length=1),
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    run = db.query(PipelineRun).filter(PipelineRun.id == run_id, PipelineRun.user_id == user_id).first()
    if not run or run.mode != "popo2raw":
        raise HTTPException(status_code=404, detail="任务产物不存在")

    summary = run.summary()
    body_final = Path(str(summary.get("body_final") or "")).resolve()
    if not body_final.exists() or not body_final.is_dir():
        raise HTTPException(status_code=404, detail="dry-run 产物目录不存在")

    requested = (body_final / path).resolve()
    if body_final != requested and body_final not in requested.parents:
        raise HTTPException(status_code=400, detail="非法产物路径")
    if not requested.exists() or not requested.is_file():
        raise HTTPException(status_code=404, detail="产物文件不存在")

    media_type = mimetypes.guess_type(requested.name)[0] or "application/octet-stream"

    def iter_file():
        with requested.open("rb") as stream:
            while True:
                chunk = stream.read(64 * 1024)
                if not chunk:
                    break
                yield chunk

    return StreamingResponse(
        iter_file(),
        media_type=media_type,
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(requested.name)}"},
    )


@router.post("/materials/{material_pk}/codex-jobs")
def create_material_codex_job(
    material_pk: int,
    payload: CodexSkillJobCreateRequest,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    material = _material_or_404(material_pk, user_id, db)
    try:
        job_payload = dict(payload.payload or {})
        if payload.run_reason:
            job_payload["run_reason"] = payload.run_reason
        job = create_codex_skill_job(
            db,
            user_id,
            material,
            mode=payload.mode,
            requested_skill=payload.requested_skill,
            skill_version=payload.skill_version,
            force=payload.force,
            payload=job_payload,
        )
        db.commit()
        db.refresh(job)
    except CodexSkillJobError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"创建 Codex 精修任务失败: {exc}")
    return job.to_dict()


@router.get("/materials/{material_pk}/codex-jobs")
def list_material_codex_jobs(
    material_pk: int,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    material = _material_or_404(material_pk, user_id, db)
    return {"jobs": [job.to_dict() for job in list_codex_skill_jobs(db, user_id, material)]}


@router.post("/materials/codex-jobs/batch-refresh-legacy")
def create_legacy_refresh_codex_jobs(
    payload: CodexSkillBatchRefreshRequest,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    if not v1_batch_enabled():
        raise HTTPException(
            status_code=409,
            detail="Worker V1 批量刷新已冻结；黄金 10 组通过前仅保留历史审计。",
        )
    try:
        summary = enqueue_legacy_refresh_codex_jobs(
            db,
            user_id,
            limit=payload.limit,
            material_ids=payload.material_ids,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"创建旧产物刷新队列失败: {exc}")
    return summary


@router.get("/materials/codex-jobs/v1-policy")
def get_worker_v1_policy(user_id: str = Depends(get_user_id)):
    _ = user_id
    return v1_policy()


@router.get("/materials/codex-jobs/{job_id}")
def get_material_codex_job(
    job_id: int,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    job = get_codex_skill_job(db, user_id, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Codex 精修任务不存在")
    return job.to_dict()


@router.post("/materials/codex-jobs/{job_id}/cancel")
def cancel_material_codex_job(
    job_id: int,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    job = get_codex_skill_job(db, user_id, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Codex 精修任务不存在")
    try:
        cancel_codex_skill_job(job)
        db.commit()
        db.refresh(job)
    except CodexSkillJobError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    return job.to_dict()


@router.post("/materials/codex-jobs/{job_id}/retry")
def retry_material_codex_job(
    job_id: int,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    job = get_codex_skill_job(db, user_id, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Codex 精修任务不存在")
    try:
        retry_codex_skill_job(job)
        db.commit()
        db.refresh(job)
    except CodexSkillJobError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    return job.to_dict()


@router.post("/materials/pipeline/preflight")
def pipeline_preflight(
    payload: PipelinePreflightRequest,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    try:
        if payload.reprocess_completed:
            user = db.query(User).filter(User.id == int(user_id)).first()
            if not user or not is_pipeline_admin(user):
                raise HTTPException(status_code=403, detail="仅管线管理员可创建已完成资产的新解析版本")
        if payload.material_pks:
            from app.services.material_task_queue import material_snapshot

            snapshot = material_snapshot(db, user_id, payload.material_pks)
            payload.material_ids = [str(row["material_id"]) for row in snapshot]
            payload.input_objects = [str(row["input_object"]) for row in snapshot]
            payload.material_id = ""
            payload.input_object = ""
            payload.limit = len(snapshot)
        else:
            snapshot = []
        result = (
            run_cloud_deferred_pipeline_preflight(snapshot, reprocess_completed=payload.reprocess_completed)
            if os.getenv("COMPSHARE_LIFECYCLE_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
            else run_pipeline_preflight(
                payload.limit,
                material_id=payload.material_id,
                input_object=payload.input_object,
                material_ids=payload.material_ids,
                input_objects=payload.input_objects,
                reprocess_completed=payload.reprocess_completed,
            )
        )
        result = apply_pipeline_resource_gate(result, snapshot)
        result["snapshot"] = snapshot
        return result
    except HTTPException:
        raise
    except MaterialTaskError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"解析预检失败: {exc}")


@router.post("/materials/pipeline/start")
def start_pipeline(
    payload: PipelineStartRequest,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    try:
        if payload.apply:
            user = db.query(User).filter(User.id == int(user_id)).first()
            if not user or not is_pipeline_admin(user):
                raise HTTPException(status_code=403, detail="仅管线管理员可启动或唤醒 GPU 解析任务")
        if payload.reprocess_completed:
            user = db.query(User).filter(User.id == int(user_id)).first()
            if not user or not is_pipeline_admin(user):
                raise HTTPException(status_code=403, detail="仅管线管理员可创建已完成资产的新解析版本")
        run = start_pipeline_run(
            db,
            user_id,
            apply=payload.apply,
            limit=payload.limit,
            material_id=payload.material_id,
            input_object=payload.input_object,
            material_ids=payload.material_ids,
            input_objects=payload.input_objects,
            material_pks=payload.material_pks,
            reprocess_completed=payload.reprocess_completed,
        )
    except HTTPException:
        raise
    except PipelinePreflightError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail={"message": "综合预检未通过", "preflight": exc.preflight})
    except MaterialTaskError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"启动解析任务失败: {exc}")
    return pipeline_run_detail(db, run)


@router.post("/materials/{material_pk}/pipeline/resume-popo/preflight")
def preflight_resume_popo(
    material_pk: int,
    admin_user: User = Depends(require_pipeline_admin),
    db: Session = Depends(get_db),
):
    material = _admin_material_or_404(material_pk, db)
    try:
        existing_popo_batch_id = latest_timed_out_popo_batch_id(db, str(material.user_id), material)
        return run_popo_resume_preflight(material, existing_popo_batch_id=existing_popo_batch_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Popo 恢复预检失败: {exc}")


@router.post("/materials/{material_pk}/pipeline/resume-popo/start")
def start_resume_popo(
    material_pk: int,
    admin_user: User = Depends(require_pipeline_admin),
    db: Session = Depends(get_db),
):
    material = _admin_material_or_404(material_pk, db)
    try:
        run = start_popo_resume_run(db, str(material.user_id), material, requested_by_user_id=str(admin_user.id))
    except PipelinePreflightError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail={"message": "Popo 恢复预检未通过", "preflight": exc.preflight})
    except MaterialTaskError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"启动 Popo 恢复失败: {exc}")
    return pipeline_run_detail(db, run)


@router.post("/materials/{material_pk}/popo2latex/preflight")
def preflight_popo_to_latex(
    material_pk: int,
    force: bool = Query(False),
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    _ = (material_pk, force, user_id, db)
    _popo_latex_gone()


@router.post("/materials/{material_pk}/popo2latex/start")
def start_popo_to_latex(
    material_pk: int,
    payload: dict | None = None,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    _ = (material_pk, payload, user_id, db)
    _popo_latex_gone()


@router.post("/materials/{material_pk}/popo2raw/preflight")
def preflight_popo_to_raw(
    material_pk: int,
    force: bool = Query(False),
    publish: bool = Query(False),
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    _ = (material_pk, force, publish, user_id, db)
    _legacy_stage_gone()


@router.post("/materials/{material_pk}/popo2raw/start")
def start_popo_to_raw(
    material_pk: int,
    payload: PopoToRawStartRequest,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    _ = (material_pk, payload, user_id, db)
    _legacy_stage_gone()


@router.post("/materials/{material_pk}/popo2raw/publish_dry_run")
def publish_popo_to_raw_dry_run_endpoint(
    material_pk: int,
    payload: PopoToRawPublishDryRunRequest,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    _ = (material_pk, payload, user_id, db)
    _legacy_stage_gone()


@router.post("/materials/{material_pk}/raw2clean/preflight")
def preflight_raw_to_clean(
    material_pk: int,
    force: bool = Query(False),
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    _ = (material_pk, force, user_id, db)
    _legacy_stage_gone()


@router.post("/materials/{material_pk}/raw2clean/start")
def start_raw_to_clean(
    material_pk: int,
    payload: RawToCleanStartRequest,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    _ = (material_pk, payload, user_id, db)
    _legacy_stage_gone()


@router.post("/materials/{material_pk}/clean2standard/preflight")
def preflight_clean_to_standard(
    material_pk: int,
    force: bool = Query(False),
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    _ = (material_pk, force, user_id, db)
    _legacy_stage_gone()


@router.post("/materials/{material_pk}/clean2standard/start")
def start_clean_to_standard(
    material_pk: int,
    payload: CleanToStandardStartRequest,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    _ = (material_pk, payload, user_id, db)
    _legacy_stage_gone()


@router.get("/materials/{material_pk}")
def material_detail(
    material_pk: int,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    material = _material_or_404(material_pk, user_id, db)
    data = material.to_dict()
    latest_codex_job = latest_codex_skill_job_for_material(db, user_id, material)
    data["codex_job"] = latest_codex_job.to_dict() if latest_codex_job else None
    return data


@router.get("/materials/{material_pk}/download_url")
def material_download_url(
    material_pk: int,
    user_id: str = Depends(get_asset_download_user_id),
    db: Session = Depends(get_db),
):
    material = _material_or_404(material_pk, user_id, db)
    if not material.input_object:
        raise HTTPException(status_code=404, detail="源 PDF 不存在")
    return {"url": get_presigned_url(material.input_bucket or INPUT_BUCKET, material.input_object)}


@router.get("/materials/{material_pk}/content")
def material_content(
    request: Request,
    material_pk: int,
    user_id: str = Depends(get_asset_download_user_id),
    db: Session = Depends(get_db),
):
    return material_artifact_download(request, material_pk, "source", user_id, db)


@router.get("/materials/{material_pk}/artifacts")
def material_artifacts(
    material_pk: int,
    user_id: str = Depends(get_asset_download_user_id),
    db: Session = Depends(get_db),
):
    material = _material_or_404(material_pk, user_id, db)
    return material_artifact_catalog(db, user_id, material)


@router.get("/materials/{material_pk}/artifacts/{artifact_id}/download")
def material_artifact_download(
    request: Request,
    material_pk: int,
    artifact_id: str,
    user_id: str = Depends(get_asset_download_user_id),
    db: Session = Depends(get_db),
):
    material = _material_or_404(material_pk, user_id, db)
    try:
        artifact = resolve_material_artifact(db, user_id, material, artifact_id)
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    ref_value = artifact.get("_ref") if isinstance(artifact.get("_ref"), dict) else {}
    ref = ObjectRef(str(ref_value.get("bucket") or ""), str(ref_value.get("object") or ""))
    size = int(artifact.get("size_bytes") or 0)
    if not ref.bucket or not ref.object or size <= 0:
        raise HTTPException(status_code=404, detail="数字资产对象不存在")
    try:
        byte_range = parse_single_range(request.headers.get("range"), size)
    except (ValueError, TypeError):
        raise HTTPException(status_code=416, detail="Range 无效", headers={"Content-Range": f"bytes */{size}"})
    offset = byte_range[0] if byte_range else 0
    length = byte_range[1] - byte_range[0] + 1 if byte_range else size
    try:
        response = open_artifact_stream(ref, offset=offset, length=length)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"数字资产读取失败: {exc}")
    encoded_filename = quote(str(artifact.get("filename") or "artifact.bin"))
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
    }
    if artifact.get("etag"):
        headers["ETag"] = f'"{artifact["etag"]}"'
    if artifact.get("sha256"):
        headers["X-Content-SHA256"] = str(artifact["sha256"])
    status_code = 200
    if byte_range:
        status_code = 206
        headers["Content-Range"] = f"bytes {byte_range[0]}-{byte_range[1]}/{size}"
    return StreamingResponse(
        stream_response_body(response),
        status_code=status_code,
        media_type=str(artifact.get("content_type") or "application/octet-stream"),
        headers=headers,
    )


@router.get("/materials/{material_pk}/popo_latex_export")
def material_popo_latex_export(
    material_pk: int,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    _ = (material_pk, user_id, db)
    _popo_latex_gone()


@router.get("/materials/{material_pk}/latex_export")
def material_latex_export(
    material_pk: int,
    stage: str = Query("popo", description="已下线；请在 PDF 比对页下载 Codex ElegantBook ZIP"),
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    _ = (material_pk, stage, user_id, db)
    _popo_latex_gone()


@router.get("/materials/{material_pk}/review_target")
def material_review_target(
    material_pk: int,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    material = _material_or_404(material_pk, user_id, db)
    if not material.review_asset_id:
        raise HTTPException(status_code=404, detail="审查资产尚未同步")
    outputs = sync_material_outputs_for_material(db, user_id, material)
    db.commit()
    selected = outputs[0] if outputs else None
    return {
        "review_asset_id": str(material.review_asset_id),
        "output_id": str(selected.id) if selected and selected.id else "",
    }
