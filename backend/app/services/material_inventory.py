import hashlib
import json
import os
import posixpath
import subprocess
import tempfile
import threading
import time
import shutil
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import fitz
from fastapi import UploadFile
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.material import Material, MaterialOutput, PipelineEvent, PipelineRun, PipelineRunItem, PipelineStageAttempt
from app.models.review_asset import ReviewAsset
from app.services.codex_elegantbook import (
    ELEGANTBOOK_BUCKET,
    list_all_codex_elegantbook_manifest_refs,
    output_from_ref,
)
from app.services.luceon_review import (
    ObjectRef,
    clean_path,
    list_input_pdf_objects,
    list_manifest_objects,
    minio_client,
    parse_json_bytes,
    read_object,
    resolve_manifest,
)
from app.services.material_outputs import sync_material_outputs_for_material
from app.services.material_task_queue import (
    MaterialTaskError,
    add_pipeline_run_items,
    mark_pipeline_items_running,
    material_snapshot,
    pipeline_idempotency_key,
    pipeline_run_items,
    project_pipeline_result,
    touch_pipeline_lease,
)
from app.services.runtime_settings import pipeline_env
from app.services.compshare_lifecycle import CompShareConfig
from app.services.upload_policy import load_pdf_upload_policy
from app.services.gpu_runtime_settings import GpuRuntimeSnapshot, load_snapshot as load_gpu_runtime_snapshot, required_gpu_disk_bytes


INPUT_BUCKET = "eduassets-input"
MINERU_BUCKET = "eduassets-mineru"
POPO_BUCKET = "eduassets-minerupopo"
LATEX_BUCKET = "eduassets-latex"
ELEGANTBOOK_OUTPUT_BUCKET = ELEGANTBOOK_BUCKET
RAW_BUCKET = "eduassets-raw"
CLEAN_BUCKET = "eduassets-clean"
STANDARD_BUCKET = "eduassets-standard"

DEFAULT_PIPELINE_SCRIPT = str(Path(__file__).resolve().parents[2] / "scripts" / "luceon_pdf_pipeline.py")
PIPELINE_SCRIPT = os.getenv("LUCEON_PIPELINE_SCRIPT", DEFAULT_PIPELINE_SCRIPT)
PIPELINE_WORKDIR = os.getenv("LUCEON_PIPELINE_WORKDIR", str(Path(PIPELINE_SCRIPT).resolve().parent))
_UPLOAD_POLICY = load_pdf_upload_policy()
LARGE_PDF_THRESHOLD_BYTES = _UPLOAD_POLICY.large_pdf_threshold_bytes
LARGE_PDF_PAGE_THRESHOLD = _UPLOAD_POLICY.large_pdf_page_threshold
LARGE_PDF_MIN_HEADROOM_BYTES = _UPLOAD_POLICY.min_gpu_headroom_bytes
LARGE_PDF_EXPANSION_FACTOR = _UPLOAD_POLICY.expansion_factor
DEFAULT_PIPELINE_WAIT_TIMEOUT_SECONDS = _UPLOAD_POLICY.default_stage_timeout_seconds
LARGE_PDF_PIPELINE_WAIT_TIMEOUT_SECONDS = _UPLOAD_POLICY.large_stage_timeout_seconds
UPLOAD_CHUNK_BYTES = _UPLOAD_POLICY.upload_chunk_bytes
MAX_UPLOAD_PDF_BYTES = _UPLOAD_POLICY.max_file_bytes
MAX_UPLOAD_PDF_PAGES = _UPLOAD_POLICY.max_file_pages


class PipelinePreflightError(RuntimeError):
    def __init__(self, preflight: dict[str, Any]):
        super().__init__("综合预检未通过")
        self.preflight = preflight


def source_hash(bucket: str, object_name: str) -> str:
    return hashlib.sha256(f"{bucket}/{object_name}".encode("utf-8")).hexdigest()[:24]


def material_id_from_sha256(sha256: str) -> str:
    return f"pdf-{sha256[:16]}" if sha256 else ""


def is_material_placeholder(value: str | None) -> bool:
    cleaned = str(value or "").strip()
    return cleaned.startswith("pdf-") and "." not in Path(cleaned).name


def display_name_from_candidates(title: str, filename: str, input_ref: ObjectRef | None = None) -> str:
    for value in (filename, title):
        cleaned = str(value or "").strip()
        if cleaned and not is_material_placeholder(cleaned):
            return cleaned
    if input_ref:
        name = Path(input_ref.object).name
        if name:
            return name
    return filename or title


def infer_ids_from_manifest_path(manifest_object: str) -> tuple[str, str]:
    parts = clean_path(manifest_object).split("/")
    if len(parts) >= 4 and parts[-1] == "manifest.json":
        return parts[-3], parts[-2]
    return "", ""


def list_two_level_manifests(bucket: str, prefix: str, limit: int | None = None) -> list[str]:
    normalized_prefix = clean_path(prefix).rstrip("/") + "/"
    rows: list[str] = []
    for material_item in minio_client.list_objects(bucket, prefix=normalized_prefix, recursive=False):
        raw_material_prefix = str(getattr(material_item, "object_name", "") or "").replace("\\", "/").lstrip("/")
        if not raw_material_prefix.endswith("/") and not getattr(material_item, "is_dir", False):
            continue
        material_prefix = clean_path(raw_material_prefix).rstrip("/") + "/"
        for run_item in minio_client.list_objects(bucket, prefix=material_prefix, recursive=False):
            raw_run_prefix = str(getattr(run_item, "object_name", "") or "").replace("\\", "/").lstrip("/")
            if raw_run_prefix.endswith("/") or getattr(run_item, "is_dir", False):
                run_prefix = clean_path(raw_run_prefix).rstrip("/") + "/"
                manifest_object = f"{run_prefix}manifest.json"
                if object_exists(bucket, manifest_object):
                    rows.append(manifest_object)
                    if limit and len(rows) >= limit:
                        return sorted(rows)
    return sorted(rows)


def object_exists(bucket: str, object_name: str) -> bool:
    try:
        minio_client.stat_object(bucket, object_name)
        return True
    except Exception:
        return False


def object_stat(bucket: str, object_name: str) -> tuple[int | None, str | None]:
    try:
        stat = minio_client.stat_object(bucket, object_name)
    except Exception:
        return None, None
    return getattr(stat, "size", None), getattr(stat, "content_type", None)


def read_json_object(bucket: str, object_name: str) -> dict[str, Any]:
    try:
        return parse_json_bytes(read_object(bucket, object_name))
    except Exception:
        return {}


def material_query(db: Session, user_id: str, material_id: str = "", input_ref: ObjectRef | None = None):
    query = db.query(Material).filter(Material.user_id == user_id)
    by_material = query.filter(Material.material_id == material_id).first() if material_id else None
    by_input = (
        query.filter(Material.input_bucket == input_ref.bucket, Material.input_object == input_ref.object).first()
        if input_ref
        else None
    )
    if by_material and by_input and by_material.id != by_input.id:
        return merge_material_rows(db, by_input, by_material)
    return by_input or by_material


def merge_material_rows(db: Session, target: Material, duplicate: Material) -> Material:
    fields = [
        "material_id",
        "source_hash",
        "title",
        "filename",
        "source_type",
        "input_sha256",
        "size_bytes",
        "content_type",
        "mineru_manifest_bucket",
        "mineru_manifest_object",
        "mineru_run_id",
        "popo_manifest_bucket",
        "popo_manifest_object",
        "popo_run_id",
        "latex_manifest_bucket",
        "latex_manifest_object",
        "latex_run_id",
        "raw_manifest_bucket",
        "raw_manifest_object",
        "raw_run_id",
        "clean_manifest_bucket",
        "clean_manifest_object",
        "clean_run_id",
        "standard_manifest_bucket",
        "standard_manifest_object",
        "standard_run_id",
        "standard_quality_score",
        "review_asset_id",
    ]
    for field in fields:
        if not getattr(target, field) and getattr(duplicate, field):
            setattr(target, field, getattr(duplicate, field))
    target.promote_stage(duplicate.stage_status or "input")
    if duplicate.last_synced_at and not target.last_synced_at:
        target.last_synced_at = duplicate.last_synced_at
    db.delete(duplicate)
    db.flush()
    return target


def upsert_material(
    db: Session,
    user_id: str,
    title: str,
    filename: str,
    material_id: str = "",
    input_ref: ObjectRef | None = None,
    source_type: str = "imported",
) -> Material:
    display_name = display_name_from_candidates(title, filename, input_ref)
    material = material_query(db, user_id, material_id=material_id, input_ref=input_ref)
    if not material:
        material = Material(
            user_id=user_id,
            title=display_name or material_id or "untitled.pdf",
            filename=display_name or material_id or "untitled.pdf",
            material_id=material_id or None,
            source_type=source_type,
            stage_status="input",
            pipeline_status="idle",
        )
        db.add(material)
    if material_id and not material.material_id:
        material.material_id = material_id
    if display_name and (not material.title or is_material_placeholder(material.title)):
        material.title = display_name
    if display_name and (not material.filename or is_material_placeholder(material.filename)):
        material.filename = display_name
    if input_ref:
        material.input_bucket = input_ref.bucket
        material.input_object = input_ref.object
        material.source_hash = source_hash(input_ref.bucket, input_ref.object)
        size_bytes, content_type = object_stat(input_ref.bucket, input_ref.object)
        material.size_bytes = material.size_bytes or size_bytes
        material.content_type = material.content_type or content_type
    material.last_synced_at = datetime.utcnow()
    return material


def link_review_asset(db: Session, material: Material) -> None:
    query = db.query(ReviewAsset).filter(ReviewAsset.user_id == material.user_id)
    review_asset = None
    if material.material_id:
        review_asset = query.filter(ReviewAsset.material_id == material.material_id).order_by(ReviewAsset.id.desc()).first()
    if not review_asset and material.input_bucket and material.input_object:
        review_asset = query.filter(
            ReviewAsset.input_pdf_bucket == material.input_bucket,
            ReviewAsset.input_pdf_object == material.input_object,
        ).order_by(ReviewAsset.id.desc()).first()
    if review_asset:
        material.review_asset_id = review_asset.id


def ensure_input_review_asset(db: Session, user_id: str, bucket: str, object_name: str) -> ReviewAsset | None:
    existing = (
        db.query(ReviewAsset)
        .filter(
            ReviewAsset.user_id == user_id,
            ReviewAsset.input_pdf_bucket == bucket,
            ReviewAsset.input_pdf_object == object_name,
        )
        .order_by(ReviewAsset.id.desc())
        .first()
    )
    if existing:
        return existing

    filename = Path(object_name).name
    asset = ReviewAsset(
        user_id=user_id,
        title=filename,
        input_filename=filename,
        review_stage="input",
        manifest_bucket=bucket,
        manifest_object=f"__input__/{object_name}",
        input_pdf_bucket=bucket,
        input_pdf_object=object_name,
        review_status="pending",
    )
    db.add(asset)
    db.flush()
    return asset


def material_has_downstream_assets(material: Material) -> bool:
    return bool(
        material.mineru_manifest_object
        or material.popo_manifest_object
        or material.latex_manifest_object
        or material.raw_manifest_object
        or material.clean_manifest_object
        or material.standard_manifest_object
    )


def assign_input_review_asset(material: Material, asset: ReviewAsset | None) -> None:
    if not asset:
        return
    if material.review_asset_id and material_has_downstream_assets(material):
        return
    material.review_asset_id = asset.id


def ensure_resolved_review_asset(db: Session, user_id: str, resolved) -> ReviewAsset:
    if resolved.input_pdf:
        input_only = (
            db.query(ReviewAsset)
            .filter(
                ReviewAsset.user_id == user_id,
                ReviewAsset.input_pdf_bucket == resolved.input_pdf.bucket,
                ReviewAsset.input_pdf_object == resolved.input_pdf.object,
                ReviewAsset.manifest_json.is_(None),
            )
            .first()
        )
        if input_only:
            db.delete(input_only)
            db.flush()

    asset = (
        db.query(ReviewAsset)
        .filter(
            ReviewAsset.user_id == user_id,
            ReviewAsset.manifest_bucket == resolved.manifest_ref.bucket,
            ReviewAsset.manifest_object == resolved.manifest_ref.object,
        )
        .first()
    )
    if not asset:
        asset = ReviewAsset(
            user_id=user_id,
            manifest_bucket=resolved.manifest_ref.bucket,
            manifest_object=resolved.manifest_ref.object,
            review_status="pending",
        )
        db.add(asset)

    fallback_title = resolved.title or resolved.input_filename or resolved.material_id or Path(resolved.manifest_ref.object).parent.name
    asset.title = asset.title or fallback_title
    asset.input_filename = resolved.input_filename or fallback_title
    asset.review_stage = resolved.review_stage
    asset.material_id = resolved.material_id
    asset.run_id = resolved.run_id
    asset.input_pdf_bucket = resolved.input_pdf.bucket if resolved.input_pdf else None
    asset.input_pdf_object = resolved.input_pdf.object if resolved.input_pdf else None
    asset.source_pdf_bucket = resolved.source_pdf.bucket if resolved.source_pdf else None
    asset.source_pdf_object = resolved.source_pdf.object if resolved.source_pdf else None
    asset.markdown_bucket = resolved.markdown.bucket if resolved.markdown else None
    asset.markdown_object = resolved.markdown.object if resolved.markdown else None
    asset.page_markdown_bucket = resolved.page_markdown.bucket if resolved.page_markdown else None
    asset.page_markdown_object = resolved.page_markdown.object if resolved.page_markdown else None
    asset.popo_markdown_bucket = resolved.popo_markdown.bucket if resolved.popo_markdown else None
    asset.popo_markdown_object = resolved.popo_markdown.object if resolved.popo_markdown else None
    asset.middle_json_bucket = resolved.middle_json.bucket if resolved.middle_json else None
    asset.middle_json_object = resolved.middle_json.object if resolved.middle_json else None
    asset.manifest_json = json.dumps(resolved.manifest, ensure_ascii=False)
    db.flush()
    return asset


def sync_input_materials(db: Session, user_id: str, limit: int | None = None) -> dict[str, int]:
    count = 0
    for object_name in list_input_pdf_objects(INPUT_BUCKET, limit=limit):
        filename = Path(object_name).name
        material = upsert_material(
            db,
            user_id,
            title=filename,
            filename=filename,
            input_ref=ObjectRef(INPUT_BUCKET, object_name),
        )
        material.promote_stage("input")
        asset = ensure_input_review_asset(db, user_id, INPUT_BUCKET, object_name)
        assign_input_review_asset(material, asset)
        link_review_asset(db, material)
        count += 1
    return {"input": count}


def sync_mineru_materials(db: Session, user_id: str, limit: int | None = None) -> dict[str, int]:
    count = 0
    for manifest_object in list_two_level_manifests(MINERU_BUCKET, "mineru/", limit=limit):
        resolved = resolve_manifest(MINERU_BUCKET, manifest_object, check_fallbacks=False)
        material_id = resolved.material_id
        run_id = resolved.run_id
        manifest = resolved.manifest
        source_pdf = manifest.get("source_pdf") if isinstance(manifest.get("source_pdf"), dict) else {}
        filename = str(source_pdf.get("filename") or material_id or Path(manifest_object).parent.name)
        input_object = clean_path(source_pdf.get("input_object"))
        input_ref = ObjectRef(str(source_pdf.get("input_bucket") or INPUT_BUCKET), input_object) if input_object else None
        material = upsert_material(db, user_id, filename, filename, material_id=material_id, input_ref=input_ref)
        asset = ensure_resolved_review_asset(db, user_id, resolved)
        material.mineru_manifest_bucket = MINERU_BUCKET
        material.mineru_manifest_object = manifest_object
        material.mineru_run_id = run_id
        material.promote_stage("mineru_done")
        material.review_asset_id = asset.id
        link_review_asset(db, material)
        count += 1
    return {"mineru": count}


def sync_popo_materials(db: Session, user_id: str, limit: int | None = None) -> dict[str, int]:
    count = 0
    for manifest_object in list_manifest_objects(POPO_BUCKET, "minerupopo/", limit=limit):
        resolved = resolve_manifest(POPO_BUCKET, manifest_object, check_fallbacks=False)
        resolved_filename = resolved.input_filename
        if not resolved_filename and resolved.input_pdf:
            resolved_filename = Path(resolved.input_pdf.object).name
        material = upsert_material(
            db,
            user_id,
            title=resolved_filename or resolved.title,
            filename=resolved_filename or resolved.title,
            material_id=resolved.material_id,
            input_ref=resolved.input_pdf,
        )
        asset = ensure_resolved_review_asset(db, user_id, resolved)
        material.popo_manifest_bucket = POPO_BUCKET
        material.popo_manifest_object = manifest_object
        material.popo_run_id = resolved.run_id
        if resolved.manifest:
            source_pdf = resolved.manifest.get("source_pdf") if isinstance(resolved.manifest.get("source_pdf"), dict) else {}
            material.input_sha256 = str(source_pdf.get("sha256") or material.input_sha256 or "")
            size = source_pdf.get("size_bytes")
            if isinstance(size, int):
                material.size_bytes = size
        material.promote_stage("popo_done")
        material.review_asset_id = asset.id
        link_review_asset(db, material)
        count += 1
    return {"popo": count}


def sync_downstream_stage(
    db: Session,
    user_id: str,
    bucket: str,
    prefix: str,
    stage: str,
    limit: int | None = None,
) -> dict[str, int]:
    count = 0
    for manifest_object in list_two_level_manifests(bucket, prefix, limit=limit):
        material_id, run_id = infer_ids_from_manifest_path(manifest_object)
        manifest = read_json_object(bucket, manifest_object)
        material = material_query(db, user_id, material_id=material_id)
        if not material:
            continue
        if stage == "latex_done":
            material.latex_manifest_bucket = bucket
            material.latex_manifest_object = manifest_object
            material.latex_run_id = run_id
        elif stage == "raw_done":
            material.raw_manifest_bucket = bucket
            material.raw_manifest_object = manifest_object
            material.raw_run_id = run_id
        elif stage == "clean_done":
            material.clean_manifest_bucket = bucket
            material.clean_manifest_object = manifest_object
            material.clean_run_id = run_id
        else:
            material.standard_manifest_bucket = bucket
            material.standard_manifest_object = manifest_object
            material.standard_run_id = run_id
            material.standard_quality_score = standard_quality_score_from_manifest(manifest)
        material.promote_stage(stage)
        link_review_asset(db, material)
        count += 1
    return {stage.replace("_done", ""): count}


def sync_codex_elegantbook_outputs(db: Session, user_id: str, limit: int | None = None) -> dict[str, int]:
    count = 0
    for ref in list_all_codex_elegantbook_manifest_refs(limit=limit):
        manifest = read_json_object(ref.bucket, ref.object)
        probe = Material(user_id=user_id, title="", filename="", material_id=str(manifest.get("material_id") or ""))
        output = output_from_ref(ref, probe, manifest)
        if not output or not output.material_id:
            continue
        material = material_query(db, user_id, material_id=output.material_id)
        if not material:
            continue
        if output.popo_run_id and not material.popo_run_id:
            material.popo_run_id = output.popo_run_id
        material.latex_manifest_bucket = ref.bucket
        material.latex_manifest_object = ref.object
        material.latex_run_id = output.output_run_id
        material.promote_stage("latex_done")
        link_review_asset(db, material)
        count += 1
    return {"elegantbook": count}


def standard_quality_score_from_manifest(manifest: dict[str, Any]) -> int | None:
    score = manifest.get("quality_score")
    if isinstance(score, dict):
        score = score.get("score")
    if not isinstance(score, int):
        acceptance = manifest.get("acceptance") if isinstance(manifest.get("acceptance"), dict) else {}
        score = acceptance.get("quality_score")
        if isinstance(score, dict):
            score = score.get("score")
    if not isinstance(score, int):
        quality = manifest.get("quality") if isinstance(manifest.get("quality"), dict) else {}
        score = quality.get("score")
    if isinstance(score, int) and 0 <= score <= 100:
        return score
    return None


def sync_material_inventory(db: Session, user_id: str, limit: int | None = None) -> dict[str, Any]:
    summary: dict[str, int] = {}
    sync_steps = [
        lambda: sync_input_materials(db, user_id, limit),
        lambda: sync_mineru_materials(db, user_id, limit),
        lambda: sync_popo_materials(db, user_id, limit),
        lambda: sync_downstream_stage(db, user_id, LATEX_BUCKET, "latex/", "latex_done", limit),
        lambda: sync_codex_elegantbook_outputs(db, user_id, limit),
    ]
    for sync_step in sync_steps:
        partial = sync_step()
        summary.update(partial)
        db.flush()
    output_query = (
        db.query(Material)
        .filter(Material.user_id == user_id, Material.material_id.isnot(None))
        .order_by(Material.last_synced_at.desc(), Material.id.desc())
    )
    if limit:
        output_query = output_query.limit(limit)
    for material in output_query.all():
        sync_material_outputs_for_material(db, user_id, material)
    summary["material_outputs"] = db.query(MaterialOutput).filter(MaterialOutput.user_id == user_id).count()
    total = db.query(Material).filter(Material.user_id == user_id).count()
    stages = {
        stage: db.query(Material).filter(Material.user_id == user_id, Material.stage_status == stage).count()
        for stage in ["input", "mineru_done", "popo_done", "latex_done", "failed"]
    }
    return {"total": total, "scanned": summary, "stages": stages, "availability": material_availability(db, user_id)}


def sync_pipeline_run_inventory(db: Session, user_id: str, run_id: int) -> dict[str, int]:
    synced = 0
    for item in pipeline_run_items(db, run_id, user_id):
        material = db.query(Material).filter(Material.id == item.material_pk, Material.user_id == user_id).first()
        if not material:
            continue
        manifest_bucket = item.popo_manifest_bucket or item.mineru_manifest_bucket or ""
        manifest_object = item.popo_manifest_object or item.mineru_manifest_object or ""
        if manifest_bucket and manifest_object:
            resolved = resolve_manifest(manifest_bucket, manifest_object, check_fallbacks=False)
            asset = ensure_resolved_review_asset(db, user_id, resolved)
            material.review_asset_id = asset.id
        sync_material_outputs_for_material(db, user_id, material)
        synced += 1
    return {"materials": synced}


def _readback_pdf_identity(bucket: str, object_name: str) -> tuple[int, str]:
    response = minio_client.get_object(bucket, object_name)
    remote_digest = hashlib.sha256()
    remote_size = 0
    try:
        for chunk in response.stream(UPLOAD_CHUNK_BYTES):
            remote_digest.update(chunk)
            remote_size += len(chunk)
    finally:
        response.close()
        response.release_conn()
    return remote_size, remote_digest.hexdigest()


async def upload_input_pdfs(files: list[UploadFile], user_id: str, db: Session) -> dict[str, Any]:
    policy = load_pdf_upload_policy()
    results = []
    if len(files) > policy.max_request_files:
        raise ValueError(f"单次请求最多 {policy.max_request_files} 个 PDF")
    declared_sizes = [max(0, int(getattr(item, "size", 0) or 0)) for item in files]
    if sum(declared_sizes) > policy.max_request_bytes:
        raise ValueError(f"上传请求总字节超过配置上限 {policy.max_request_bytes}")
    Path(policy.temp_dir).mkdir(parents=True, exist_ok=True)
    required_free = policy.min_local_temp_free_bytes + max(declared_sizes or [0])
    if shutil.disk_usage(policy.temp_dir).free < required_free:
        raise ValueError("上传临时目录空间不足，multipart 已落盘但无法安全完成 PDF 冻结")
    for file in files:
        filename = Path(file.filename or "untitled.pdf").name
        if not filename.lower().endswith(".pdf"):
            results.append({"filename": filename, "status": "failed", "error_message": "仅支持 PDF"})
            continue
        temp_path = ""
        created_object_name = ""
        try:
            digest = hashlib.sha256()
            size_bytes = 0
            with tempfile.NamedTemporaryFile(
                prefix="luceon-pdf-upload-", suffix=".pdf", delete=False, dir=policy.temp_dir
            ) as staged:
                temp_path = staged.name
                first_chunk = True
                while True:
                    chunk = await file.read(UPLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    if first_chunk:
                        first_chunk = False
                        if not chunk.startswith(b"%PDF-"):
                            raise ValueError("文件内容不是 PDF")
                    size_bytes += len(chunk)
                    effective_max_bytes = min(policy.max_file_bytes, MAX_UPLOAD_PDF_BYTES)
                    if size_bytes > effective_max_bytes:
                        raise ValueError(f"PDF 超过服务器限制 {effective_max_bytes} bytes")
                    if sum(item.get("size_bytes", 0) for item in results) + size_bytes > policy.max_request_bytes:
                        raise ValueError(f"上传请求总字节超过配置上限 {policy.max_request_bytes}")
                    digest.update(chunk)
                    staged.write(chunk)
                staged.flush()
                os.fsync(staged.fileno())
            if size_bytes == 0:
                raise ValueError("PDF 文件为空")
            sha256 = digest.hexdigest()
            material_id = material_id_from_sha256(sha256)
            with fitz.open(temp_path) as document:
                page_count = int(document.page_count)
                if page_count <= 0:
                    raise ValueError("PDF 没有可读取页面")
                effective_max_pages = min(policy.max_file_pages, MAX_UPLOAD_PDF_PAGES)
                if page_count > effective_max_pages:
                    raise ValueError(f"PDF 超过服务器页数限制 {effective_max_pages}")
                if document.needs_pass:
                    raise ValueError("PDF 已加密，无法冻结处理")
            existing = (
                db.query(Material)
                .filter(
                    Material.user_id == user_id,
                    Material.ignored.is_(False),
                    (Material.input_sha256 == sha256) | (Material.material_id == material_id),
                )
                .order_by(Material.id.asc())
                .first()
            )
            if existing:
                duplicate_bucket = str(existing.input_bucket or INPUT_BUCKET)
                duplicate_object = str(existing.input_object or "")
                if not duplicate_object or not object_exists(duplicate_bucket, duplicate_object):
                    raise ValueError("重复 PDF 的既有 MinIO 冻结对象缺失")
                remote_size, remote_sha256 = _readback_pdf_identity(duplicate_bucket, duplicate_object)
                if remote_size != size_bytes or remote_sha256 != sha256:
                    raise ValueError("重复 PDF 的既有 MinIO 对象 SHA/size 漂移")
                existing.input_sha256 = existing.input_sha256 or sha256
                existing.size_bytes = existing.size_bytes or size_bytes
                existing.page_count = existing.page_count or page_count
                results.append({"filename": filename, "status": "duplicate", "size_bytes": size_bytes, "eligibility_status": "uploaded_but_gpu_resource_review" if size_bytes >= policy.large_pdf_threshold_bytes or page_count >= policy.large_pdf_page_threshold else "gpu_eligible", "material": existing.to_dict()})
                continue
            object_name = f"pdf/{material_id}/{sha256}.pdf"
            if not object_exists(INPUT_BUCKET, object_name):
                with open(temp_path, "rb") as source:
                    minio_client.put_object(
                        INPUT_BUCKET,
                        object_name,
                        source,
                        length=size_bytes,
                        content_type="application/pdf",
                        metadata={"sha256": sha256, "material-id": material_id},
                    )
                created_object_name = object_name
            remote_size, remote_sha256 = _readback_pdf_identity(INPUT_BUCKET, object_name)
            if remote_size != size_bytes or remote_sha256 != sha256:
                raise ValueError("本地 MinIO 独立回读 SHA/size 不一致")
            material = upsert_material(
                db,
                user_id,
                filename,
                filename,
                material_id=material_id,
                input_ref=ObjectRef(INPUT_BUCKET, object_name),
                source_type="uploaded",
            )
            material.input_sha256 = sha256
            material.size_bytes = size_bytes
            material.page_count = page_count
            material.content_type = "application/pdf"
            material.pipeline_status = "input_frozen"
            material.promote_stage("input")
            asset = ensure_input_review_asset(db, user_id, INPUT_BUCKET, object_name)
            assign_input_review_asset(material, asset)
            link_review_asset(db, material)
            db.flush()
            db.commit()
            results.append({"filename": filename, "status": "success", "size_bytes": size_bytes, "eligibility_status": "uploaded_but_gpu_resource_review" if size_bytes >= policy.large_pdf_threshold_bytes or page_count >= policy.large_pdf_page_threshold else "gpu_eligible", "material": material.to_dict()})
        except ValueError as exc:
            db.rollback()
            if created_object_name:
                try:
                    minio_client.remove_object(INPUT_BUCKET, created_object_name)
                except Exception:
                    pass
            results.append({"filename": filename, "status": "failed", "size_bytes": 0, "eligibility_status": "rejected_by_config", "error_message": str(exc)})
        except Exception as exc:
            db.rollback()
            if created_object_name:
                try:
                    minio_client.remove_object(INPUT_BUCKET, created_object_name)
                except Exception:
                    pass
            results.append({"filename": filename, "status": "failed", "size_bytes": 0, "eligibility_status": "rejected_by_config", "error_message": f"PDF 冻结失败: {exc}"})
        finally:
            if temp_path:
                Path(temp_path).unlink(missing_ok=True)
    return {
        "upload_policy_sha256": policy.identity_sha256(),
        "total": len(results),
        "success": sum(1 for item in results if item["status"] in {"success", "duplicate"}),
        "duplicates": sum(1 for item in results if item["status"] == "duplicate"),
        "failed": sum(1 for item in results if item["status"] == "failed"),
        "files": results,
    }


def latest_pipeline_run(db: Session, user_id: str) -> PipelineRun | None:
    return (
        db.query(PipelineRun)
        .filter(PipelineRun.user_id == user_id)
        .order_by(PipelineRun.created_at.desc(), PipelineRun.id.desc())
        .first()
    )


def create_pipeline_event(db: Session, run: PipelineRun, message: str, stage: str = "", level: str = "info", payload: dict | None = None) -> None:
    db.add(
        PipelineEvent(
            run_id=run.id,
            user_id=run.user_id,
            level=level,
            stage=stage,
            message=message,
            payload_json=json.dumps(payload or {}, ensure_ascii=False),
        )
    )


def pipeline_limit(limit: int) -> int:
    return max(1, min(int(limit or 5), load_pdf_upload_policy().max_gpu_batch_files))


def pipeline_wait_timeout_seconds(snapshot: list[dict[str, Any]]) -> int:
    policy = load_pdf_upload_policy()
    is_large = any(
        int(row.get("size_bytes") or 0) >= policy.large_pdf_threshold_bytes
        or int(row.get("page_count") or 0) >= policy.large_pdf_page_threshold
        for row in snapshot
    )
    return policy.large_stage_timeout_seconds if is_large else policy.default_stage_timeout_seconds


def _pipeline_target_values(single: str, multiple: list[str] | tuple[str, ...] | None) -> list[str]:
    values: list[str] = []
    for raw in [single, *(multiple or [])]:
        value = str(raw or "").strip()
        if value and value not in values:
            values.append(value)
    return values


def pipeline_target_args(
    material_id: str = "",
    input_object: str = "",
    *,
    material_ids: list[str] | tuple[str, ...] | None = None,
    input_objects: list[str] | tuple[str, ...] | None = None,
) -> list[str]:
    args: list[str] = []
    for value in _pipeline_target_values(material_id, material_ids):
        args.extend(["--material-id", value])
    for value in _pipeline_target_values(input_object, input_objects):
        args.extend(["--input-object", value])
    return args


def pipeline_command(
    apply: bool,
    limit: int,
    material_id: str = "",
    input_object: str = "",
    *,
    material_ids: list[str] | tuple[str, ...] | None = None,
    input_objects: list[str] | tuple[str, ...] | None = None,
    reprocess_completed: bool = False,
    timeout_seconds: int = DEFAULT_PIPELINE_WAIT_TIMEOUT_SECONDS,
) -> list[str]:
    target_args = pipeline_target_args(
        material_id,
        input_object,
        material_ids=material_ids,
        input_objects=input_objects,
    )
    if apply:
        command = ["python3", PIPELINE_SCRIPT, "run-staged", "--limit", str(pipeline_limit(limit))]
        if target_args:
            command.extend(["--skip-sha", "--input-status-only"])
        if reprocess_completed:
            command.append("--reprocess-completed")
        command.extend(target_args)
        command.extend(["--timeout-seconds", str(max(1, int(timeout_seconds)))])
        command.extend(["--apply", "--wait"])
    else:
        command = ["python3", PIPELINE_SCRIPT, "plan-next", "--limit", str(pipeline_limit(limit))]
        if target_args:
            command.extend(["--skip-sha", "--input-status-only"])
        command.extend(target_args)
    return command


def popo_resume_command(
    existing_mineru_batch_id: str,
    material_id: str,
    input_object: str,
    *,
    apply: bool,
    existing_popo_batch_id: str = "",
    timeout_seconds: int = DEFAULT_PIPELINE_WAIT_TIMEOUT_SECONDS,
) -> list[str]:
    command = ["python3", PIPELINE_SCRIPT, "run-staged", "--limit", "1", "--skip-sha", "--input-status-only"]
    command.extend(pipeline_target_args(material_id, input_object))
    if existing_popo_batch_id:
        command.extend(["--existing-popo-batch-id", existing_popo_batch_id])
    else:
        command.extend(["--existing-mineru-batch-id", existing_mineru_batch_id, "--reuse-frozen-mineru"])
    if apply:
        command.extend(["--timeout-seconds", str(max(1, int(timeout_seconds)))])
        command.extend(["--apply", "--wait"])
    return command


def latest_timed_out_popo_batch_id(db: Session, user_id: str, material: Material) -> str:
    attempt = (
        db.query(PipelineStageAttempt)
        .join(PipelineRunItem, PipelineRunItem.id == PipelineStageAttempt.run_item_id)
        .filter(
            PipelineRunItem.user_id == user_id,
            PipelineRunItem.material_pk == material.id,
            PipelineStageAttempt.stage == "popo",
            PipelineStageAttempt.status == "failed",
            PipelineStageAttempt.error_code == "popo_wait_timeout",
            PipelineStageAttempt.external_batch_id.isnot(None),
        )
        .order_by(PipelineStageAttempt.finished_at.desc(), PipelineStageAttempt.id.desc())
        .first()
    )
    return str(attempt.external_batch_id or "") if attempt else ""


def frozen_mineru_resume_context(material: Material) -> dict[str, str]:
    if material.popo_manifest_object:
        raise ValueError("Popo 已冻结，无需恢复")
    manifest_object = str(material.mineru_manifest_object or "")
    manifest_bucket = str(material.mineru_manifest_bucket or MINERU_BUCKET)
    if not manifest_object and material.material_id:
        prefix = f"mineru/{material.material_id}/"
        candidates = [
            str(getattr(item, "object_name", "") or "")
            for item in minio_client.list_objects(MINERU_BUCKET, prefix=prefix, recursive=True)
            if str(getattr(item, "object_name", "") or "").endswith("/manifest.json")
        ]
        manifest_object = max(candidates, default="")
        manifest_bucket = MINERU_BUCKET
    if not manifest_object:
        raise ValueError("缺少正式 MinerU manifest")
    manifest = read_json_object(manifest_bucket, manifest_object)
    source = manifest.get("source_pdf") if isinstance(manifest.get("source_pdf"), dict) else {}
    expected = {
        "status": "mineru_done_frozen",
        "material_id": str(material.material_id or ""),
        "input_object": str(material.input_object or ""),
    }
    actual = {
        "status": str(manifest.get("status") or ""),
        "material_id": str(manifest.get("material_id") or ""),
        "input_object": str(source.get("input_object") or ""),
    }
    mismatches = [key for key, value in expected.items() if not value or value != actual[key]]
    batch_id = str(manifest.get("batch_id") or "")
    run_id = str(manifest.get("run_id") or "")
    marker_object = f"_status/{expected['material_id']}/{run_id}.mineru_done_frozen.json"
    marker = read_json_object(INPUT_BUCKET, marker_object) if run_id else {}
    if mismatches or not batch_id or not run_id or str(marker.get("status") or "") != "mineru_done_frozen":
        raise ValueError("MinerU 冻结身份、批次或状态标记不完整")
    return {
        "material_id": expected["material_id"],
        "input_object": expected["input_object"],
        "mineru_batch_id": batch_id,
        "mineru_run_id": run_id,
        "mineru_manifest_bucket": manifest_bucket,
        "mineru_manifest_object": manifest_object,
        "mineru_marker_object": marker_object,
    }


def run_popo_resume_preflight(material: Material, *, existing_popo_batch_id: str = "") -> dict[str, Any]:
    try:
        context = frozen_mineru_resume_context(material)
    except ValueError as exc:
        return {
            "ready": False,
            "status": "FROZEN_MINERU_INVALID",
            "checked_at": datetime.utcnow().isoformat(),
            "selected_count": 0,
            "active_marker_count": 0,
            "gpu_ok": False,
            "staged_api_ok": False,
            "plan_status": "FROZEN_MINERU_INVALID",
            "reason": str(exc),
        }
    command = popo_resume_command(
        context["mineru_batch_id"],
        context["material_id"],
        context["input_object"],
        apply=False,
        existing_popo_batch_id=existing_popo_batch_id,
    )
    completed = subprocess.run(
        command,
        cwd=PIPELINE_WORKDIR,
        env=pipeline_env(),
        text=True,
        capture_output=True,
        timeout=180,
    )
    payload = parse_pipeline_json(completed.stdout)
    ready = completed.returncode == 0 and payload.get("status") == "DRY_RUN" and int(payload.get("selected_count") or 0) == 1
    payload.update(
        {
            "ready": ready,
            "checked_at": datetime.utcnow().isoformat(),
            "returncode": completed.returncode,
            "command_text": " ".join(command),
            "plan_status": str(payload.get("status") or "ERROR"),
            "active_marker_count": 0,
            "resume_context": context,
            "existing_popo_batch_id": existing_popo_batch_id,
        }
    )
    health = payload.get("health") if isinstance(payload.get("health"), dict) else {}
    staged = payload.get("staged_api_probe") if isinstance(payload.get("staged_api_probe"), dict) else {}
    payload["gpu_ok"] = bool(health.get("ok"))
    payload["staged_api_ok"] = bool(staged.get("available"))
    if completed.returncode != 0:
        payload["stdout_tail"] = (completed.stdout or "")[-4000:]
        payload["stderr_tail"] = (completed.stderr or "")[-4000:]
    return payload


def pipeline_preflight_command(
    limit: int,
    material_id: str = "",
    input_object: str = "",
    *,
    material_ids: list[str] | tuple[str, ...] | None = None,
    input_objects: list[str] | tuple[str, ...] | None = None,
    reprocess_completed: bool = False,
) -> list[str]:
    command = ["python3", PIPELINE_SCRIPT, "preflight", "--limit", str(pipeline_limit(limit))]
    target_args = pipeline_target_args(
        material_id,
        input_object,
        material_ids=material_ids,
        input_objects=input_objects,
    )
    if target_args:
        command.extend(["--skip-sha", "--input-status-only"])
    if reprocess_completed:
        command.append("--reprocess-completed")
    command.extend(target_args)
    return command


def parse_pipeline_json(stdout: str) -> dict[str, Any]:
    try:
        parsed = json.loads(stdout or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _list_count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def pipeline_result_counts(payload: dict[str, Any], fallback_total: int = 0) -> dict[str, int]:
    total = int(payload.get("selected_count") or fallback_total or 0)
    status = str(payload.get("status") or "").upper()
    popo = payload.get("popo") if isinstance(payload.get("popo"), dict) else {}
    has_popo_counts = "freezes" in popo or "errors" in popo

    popo_success = _list_count(popo.get("freezes"))
    popo_failed = _list_count(popo.get("errors"))
    mineru_success = _list_count(payload.get("mineru_freezes"))
    mineru_failed = _list_count(payload.get("mineru_errors"))

    if has_popo_counts:
        success = popo_success
        failed = popo_failed
    else:
        success = mineru_success
        failed = mineru_failed

    if status == "DONE" and total and success == 0 and failed == 0:
        success = total
    elif status == "PARTIAL" and total and success + failed < total:
        failed = total - success

    processed = success + failed
    if total:
        processed = min(total, processed)
    return {"total": total, "processed": processed, "success": success, "failed": failed}


def pipeline_run_outcome(payload: dict[str, Any], returncode: int, apply: bool) -> str:
    status = str(payload.get("status") or "").upper()
    if status == "PARTIAL":
        return "partial"
    if returncode != 0:
        return "failed"
    if apply and status != "DONE":
        return "failed"
    return "succeeded"


def run_pipeline_preflight(
    limit: int = 5,
    material_id: str = "",
    input_object: str = "",
    *,
    material_ids: list[str] | tuple[str, ...] | None = None,
    input_objects: list[str] | tuple[str, ...] | None = None,
    reprocess_completed: bool = False,
) -> dict[str, Any]:
    command = pipeline_preflight_command(
        limit,
        material_id=material_id,
        input_object=input_object,
        material_ids=material_ids,
        input_objects=input_objects,
        reprocess_completed=reprocess_completed,
    )
    completed = subprocess.run(
        command,
        cwd=PIPELINE_WORKDIR,
        env=pipeline_env(),
        text=True,
        capture_output=True,
        timeout=180,
    )
    payload = parse_pipeline_json(completed.stdout)
    payload.setdefault("ready", False)
    payload.setdefault("status", "ERROR" if completed.returncode else "UNKNOWN")
    payload.setdefault("checked_at", datetime.utcnow().isoformat())
    payload["returncode"] = completed.returncode
    payload["command_text"] = " ".join(command)
    if completed.returncode != 0:
        payload["ready"] = False
        payload["stdout_tail"] = (completed.stdout or "")[-4000:]
        payload["stderr_tail"] = (completed.stderr or "")[-4000:]
    return payload


def run_cloud_deferred_pipeline_preflight(
    snapshot: list[dict[str, Any]],
    *,
    reprocess_completed: bool = False,
    gpu_settings: GpuRuntimeSnapshot | None = None,
) -> dict[str, Any]:
    """Plan exact inputs without assuming a stopped GPU wrapper is online.

    Cloud lifecycle mutation remains worker-only. This check proves the local
    frozen denominator and lifecycle configuration; SSH/wrapper/GPU/disk are
    deliberately reported as deferred and are revalidated after Running.
    """
    if gpu_settings is None:
        return {"ready": False, "status": "GPU_SETTINGS_MISSING", "selected_count": len(snapshot)}
    missing = [key for key in ("region", "zone", "project_id", "uhost_id", "ssh_host") if not str(getattr(gpu_settings, key, "")).strip()]
    if gpu_settings.credential_status != "present":
        missing.append("credentials")
    if missing:
        return {
            "ready": False,
            "status": "CLOUD_CONFIG_INCOMPLETE",
            "checked_at": datetime.utcnow().isoformat(),
            "selected_count": len(snapshot),
            "cloud": {"configured": False, "missing_fields": missing, "uhost_id": gpu_settings.uhost_id, "region": gpu_settings.region, "zone": gpu_settings.zone},
        }
    if not snapshot or any(not row.get("input_sha256") or not row.get("input_object") for row in snapshot):
        return {
            "ready": False,
            "status": "INPUT_SNAPSHOT_INCOMPLETE",
            "checked_at": datetime.utcnow().isoformat(),
            "selected_count": len(snapshot),
            "cloud": {"configured": True, "uhost_id": gpu_settings.uhost_id, "region": gpu_settings.region, "zone": gpu_settings.zone},
        }
    identities = [str(row["input_sha256"]) for row in snapshot]
    if len(identities) != len(set(identities)):
        return {
            "ready": False,
            "status": "DUPLICATE_INPUT_IDENTITY",
            "checked_at": datetime.utcnow().isoformat(),
            "selected_count": len(snapshot),
            "cloud": {"configured": True, "uhost_id": gpu_settings.uhost_id, "region": gpu_settings.region, "zone": gpu_settings.zone},
        }
    return {
        "ready": True,
        "status": "CLOUD_LIFECYCLE_DEFERRED",
        "checked_at": datetime.utcnow().isoformat(),
        "selected_count": len(snapshot),
        "gpu_ok": False,
        "staged_api_ok": False,
        "readiness_deferred_until_running": True,
        "cloud": {"configured": True, "describe_first": True, "uhost_id": gpu_settings.uhost_id, "region": gpu_settings.region, "zone": gpu_settings.zone, "settings_sha256": gpu_settings.settings_sha256},
        "snapshot_sha256": hashlib.sha256(
            json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "reprocess_completed": bool(reprocess_completed),
    }


def apply_pipeline_resource_gate(
    payload: dict[str, Any],
    snapshot: list[dict[str, Any]] | None = None,
    gpu_settings: GpuRuntimeSnapshot | None = None,
) -> dict[str, Any]:
    selected = payload.get("selected") if isinstance(payload.get("selected"), list) else []
    rows = list(snapshot or selected)
    total_input_bytes = sum(
        max(0, int(row.get("size_bytes") or row.get("source_pdf_size_bytes") or 0))
        for row in rows
        if isinstance(row, dict)
    )
    max_page_count = max(
        [max(0, int(row.get("page_count") or 0)) for row in rows if isinstance(row, dict)] or [0]
    )
    policy = load_pdf_upload_policy()
    large_pdf_count = sum(
        1
        for row in rows
        if isinstance(row, dict)
        and (
            int(row.get("size_bytes") or row.get("source_pdf_size_bytes") or 0) >= policy.large_pdf_threshold_bytes
            or int(row.get("page_count") or 0) >= policy.large_pdf_page_threshold
        )
    )
    gpu_settings = gpu_settings
    configured_minimum = gpu_settings.min_free_disk_bytes if gpu_settings else policy.min_gpu_headroom_bytes
    configured_expansion = gpu_settings.expansion_factor if gpu_settings else policy.expansion_factor
    configured_reserve = gpu_settings.disk_reserve_bytes if gpu_settings else 0
    gate: dict[str, Any] = {
        "applies": bool(rows),
        "ok": True,
        "status": "not_applicable" if not rows else "ready",
        "large_pdf_count": large_pdf_count,
        "selected_input_bytes": total_input_bytes,
        "max_page_count": max_page_count,
        "large_pdf_threshold_bytes": policy.large_pdf_threshold_bytes,
        "large_pdf_page_threshold": policy.large_pdf_page_threshold,
    }
    if len(rows) > policy.max_gpu_batch_files or total_input_bytes > policy.max_gpu_batch_input_bytes:
        gate.update(
            {
                "ok": False,
                "status": "rejected_by_config",
                "reason": "解析批次超过配置的文件数或总字节上限",
                "max_gpu_batch_files": policy.max_gpu_batch_files,
                "max_gpu_batch_input_bytes": policy.max_gpu_batch_input_bytes,
            }
        )
        payload["resource_gate"] = gate
        return payload
    if not rows:
        payload["resource_gate"] = gate
        return payload

    # Resource measurement cannot replace an earlier readiness failure. In
    # particular, manual lifecycle mode must preserve the actionable
    # GPU_OFFLINE result instead of rewriting it as a zero-byte disk error.
    if payload.get("ready") is False:
        gate.update(
            {
                "applies": False,
                "ok": False,
                "status": "not_evaluated_upstream_blocked",
                "reason": f"upstream preflight blocked: {payload.get('status') or 'unknown'}",
            }
        )
        payload["resource_gate"] = gate
        return payload

    # A stopped cloud instance has no observable wrapper quota or remote disk
    # until the worker has completed Describe/Start, SSH and service readiness.
    # Preserve the denominator and explicitly defer this measurement.
    if payload.get("status") == "CLOUD_LIFECYCLE_DEFERRED":
        gate.update(
            {
                "ok": True,
                "status": "deferred_until_gpu_ready",
                "required_headroom_bytes": max(configured_minimum, int(total_input_bytes * configured_expansion + configured_reserve)),
                "configured_minimum_bytes": configured_minimum,
                "configured_reserve_bytes": configured_reserve,
                "expansion_factor": configured_expansion,
                "reason": "远端磁盘与 Wrapper 配额将在实例 Running 后由 Worker 重新核验",
            }
        )
        payload["resource_gate"] = gate
        return payload

    wrapper_health = payload.get("health") if isinstance(payload.get("health"), dict) else {}
    wrapper_health = wrapper_health.get("health") if isinstance(wrapper_health.get("health"), dict) else {}
    artifact_limit = int(wrapper_health.get("artifact_limit_bytes") or 0)
    artifact_used = int(wrapper_health.get("artifact_used_bytes") or 0)
    quota_available = max(0, artifact_limit - artifact_used) if artifact_limit > 0 else 0
    disk = wrapper_health.get("disk") if isinstance(wrapper_health.get("disk"), dict) else {}
    disk_available = int(
        wrapper_health.get("disk_available_bytes")
        or wrapper_health.get("disk_free_bytes")
        or disk.get("available_bytes")
        or disk.get("free_bytes")
        or 0
    )
    historical_factors = []
    for value in [
        wrapper_health.get("historical_artifact_expansion_factor"),
        *(row.get("historical_artifact_expansion_factor") for row in rows if isinstance(row, dict)),
    ]:
        try:
            factor = float(value)
        except (TypeError, ValueError):
            continue
        if factor > 0:
            historical_factors.append(factor)
    expansion_factor = max([float(configured_expansion), *historical_factors])
    required = max(configured_minimum, int(total_input_bytes * expansion_factor + configured_reserve))
    available_candidates = [value for value in (quota_available, disk_available) if value > 0]
    available = min(available_candidates) if len(available_candidates) == 2 else 0
    gate.update(
        {
            "artifact_limit_bytes": artifact_limit,
            "artifact_used_bytes": artifact_used,
            "artifact_quota_available_bytes": quota_available,
            "disk_available_bytes": disk_available,
            "available_headroom_bytes": available,
            "required_headroom_bytes": required,
            "expansion_factor": expansion_factor,
            "configured_minimum_bytes": configured_minimum,
            "configured_reserve_bytes": configured_reserve,
        }
    )
    if artifact_limit <= 0:
        gate.update({"ok": False, "status": "headroom_unknown", "reason": "GPU Wrapper 未报告临时产物配额"})
    elif disk_available <= 0:
        gate.update({"ok": False, "status": "disk_headroom_unknown", "reason": "GPU Wrapper 未报告真实磁盘余量"})
    elif available < required:
        gate.update({"ok": False, "status": "insufficient_headroom", "reason": "GPU 临时产物余量不足"})
    if not gate["ok"]:
        payload["ready"] = False
        payload["status"] = "GPU_RESOURCE_HEADROOM_BLOCKED"
    payload["resource_gate"] = gate
    return payload


def start_pipeline_run(
    db: Session,
    user_id: str,
    apply: bool = False,
    limit: int = 5,
    material_id: str = "",
    input_object: str = "",
    material_ids: list[str] | tuple[str, ...] | None = None,
    input_objects: list[str] | tuple[str, ...] | None = None,
    material_pks: list[int] | tuple[int, ...] | None = None,
    reprocess_completed: bool = False,
) -> PipelineRun:
    resolved_pks = [int(value) for value in material_pks or []]
    if not resolved_pks:
        requested_material_ids = _pipeline_target_values(material_id, material_ids)
        requested_input_objects = _pipeline_target_values(input_object, input_objects)
        target_query = db.query(Material).filter(Material.user_id == user_id, Material.ignored.is_(False))
        if requested_material_ids or requested_input_objects:
            filters = []
            if requested_material_ids:
                filters.append(Material.material_id.in_(requested_material_ids))
            if requested_input_objects:
                filters.append(Material.input_object.in_(requested_input_objects))
            target_query = target_query.filter(or_(*filters))
            resolved_pks = [int(row.id) for row in target_query.order_by(Material.id.asc()).all()]
    if apply and not resolved_pks:
        raise MaterialTaskError("正式解析必须提交明确的材料快照")
    snapshot = material_snapshot(db, user_id, resolved_pks) if resolved_pks else []
    if apply and snapshot and not reprocess_completed:
        completed = [
            str(row.get("material_id") or row.get("filename") or row.get("material_pk"))
            for row in snapshot
            if str((row.get("popo_manifest") or {}).get("bucket") or "")
            and str((row.get("popo_manifest") or {}).get("object") or "")
        ]
        if completed:
            raise MaterialTaskError(
                "材料已存在冻结 Popo 结果，普通重试不会重复提交 GPU；如需新版本请使用管理员 reprocess："
                + ", ".join(completed)
            )
    if snapshot:
        material_ids = [str(row["material_id"]) for row in snapshot]
        input_objects = [str(row["input_object"]) for row in snapshot]
        material_id = ""
        input_object = ""
        limit = len(snapshot)
    mode = "reprocess" if apply and reprocess_completed else ("apply" if apply else "dry_run")
    idempotency_key = pipeline_idempotency_key(user_id, mode, snapshot) if snapshot else ""
    active = (
        db.query(PipelineRun)
        .filter(PipelineRun.status.in_(["queued", "running"]))
        .order_by(PipelineRun.created_at.desc())
        .first()
    )
    if active:
        if active.user_id == user_id and idempotency_key and active.idempotency_key == idempotency_key:
            return active
        raise MaterialTaskError("已有解析任务占用串行GPU队列")
    gpu_settings = load_gpu_runtime_snapshot(db)
    cloud_lifecycle_enabled = gpu_settings.effective_automatic
    preflight = None
    if apply:
        preflight = (
            run_cloud_deferred_pipeline_preflight(snapshot, reprocess_completed=reprocess_completed, gpu_settings=gpu_settings)
            if cloud_lifecycle_enabled
            else run_pipeline_preflight(
                limit,
                material_id=material_id,
                input_object=input_object,
                material_ids=material_ids,
                input_objects=input_objects,
                reprocess_completed=reprocess_completed,
            )
        )
        preflight = apply_pipeline_resource_gate(preflight, snapshot, gpu_settings)
    if apply and not bool(preflight and preflight.get("ready")):
        raise PipelinePreflightError(preflight or {})
    run = PipelineRun(
        user_id=user_id,
        status="queued",
        mode=mode,
        idempotency_key=idempotency_key or None,
        queue_slot="gpu",
        command=" ".join(
            pipeline_command(
                apply=apply,
                limit=limit,
                material_id=material_id,
                input_object=input_object,
                material_ids=material_ids,
                input_objects=input_objects,
                reprocess_completed=reprocess_completed,
                timeout_seconds=pipeline_wait_timeout_seconds(snapshot),
            )
        ),
        current_stage="queued",
        total=len(snapshot) or (int(preflight.get("selected_count") or 0) if preflight else 0),
        request_json=json.dumps(
            {
                "apply": apply,
                "limit": limit,
                "snapshot": snapshot,
                "material_ids": list(material_ids or []),
                "input_objects": list(input_objects or []),
                "reprocess_completed": reprocess_completed,
                "gpu_runtime_snapshot": gpu_settings.public_dict(),
            },
            ensure_ascii=False,
        ),
        summary_json=json.dumps(
            {
                "preflight": preflight,
                "material_id": material_id,
                "input_object": input_object,
                "material_ids": _pipeline_target_values(material_id, material_ids),
                "input_objects": _pipeline_target_values(input_object, input_objects),
                "reprocess_completed": reprocess_completed,
            },
            ensure_ascii=False,
        )
        if preflight or material_id or input_object or material_ids or input_objects
        else None,
        created_at=datetime.utcnow(),
    )
    db.add(run)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        active = (
            db.query(PipelineRun)
            .filter(PipelineRun.status.in_(["queued", "running"]))
            .order_by(PipelineRun.created_at.desc(), PipelineRun.id.desc())
            .first()
        )
        if active and active.user_id == user_id and active.idempotency_key == idempotency_key:
            return active
        raise MaterialTaskError("已有解析任务占用串行GPU队列") from exc
    if snapshot:
        add_pipeline_run_items(db, run, snapshot)
        db.query(Material).filter(Material.id.in_([row["material_pk"] for row in snapshot])).update(
            {Material.pipeline_status: "queued"}, synchronize_session=False
        )
    create_pipeline_event(
        db,
        run,
        "已创建解析任务，等待后台执行" if apply else "已创建解析预检任务，默认不提交 GPU",
        stage="queued",
        payload={"preflight": preflight} if preflight else None,
    )
    db.commit()
    return run


def start_popo_resume_run(
    db: Session,
    user_id: str,
    material: Material,
    *,
    requested_by_user_id: str = "",
) -> PipelineRun:
    active = (
        db.query(PipelineRun)
        .filter(PipelineRun.status.in_(["queued", "running"]))
        .order_by(PipelineRun.created_at.desc())
        .first()
    )
    if active:
        raise MaterialTaskError("已有解析任务占用串行GPU队列")
    existing_popo_batch_id = latest_timed_out_popo_batch_id(db, user_id, material)
    preflight = run_popo_resume_preflight(material, existing_popo_batch_id=existing_popo_batch_id)
    if not preflight.get("ready"):
        raise PipelinePreflightError(preflight)
    context = preflight["resume_context"]
    material.mineru_run_id = context["mineru_run_id"]
    material.mineru_manifest_bucket = context["mineru_manifest_bucket"]
    material.mineru_manifest_object = context["mineru_manifest_object"]
    material.promote_stage("mineru_done")
    command = popo_resume_command(
        context["mineru_batch_id"],
        context["material_id"],
        context["input_object"],
        apply=True,
        existing_popo_batch_id=existing_popo_batch_id,
        timeout_seconds=pipeline_wait_timeout_seconds(material_snapshot(db, user_id, [material.id])),
    )
    resume_idempotency_key = hashlib.sha256(
        f"{user_id}:resume_popo:{material.id}:{context['mineru_manifest_object']}".encode("utf-8")
    ).hexdigest()
    run = PipelineRun(
        user_id=user_id,
        status="queued",
        mode="resume_popo",
        idempotency_key=resume_idempotency_key,
        queue_slot="gpu",
        command=" ".join(command),
        request_json=json.dumps(
            {
                "apply": True,
                "limit": 1,
                "snapshot": material_snapshot(db, user_id, [material.id]),
                "resume_context": context,
                "existing_popo_batch_id": existing_popo_batch_id,
            },
            ensure_ascii=False,
        ),
        current_stage="queued",
        total=1,
        summary_json=json.dumps(
            {"preflight": preflight, "requested_by_user_id": requested_by_user_id, **context},
            ensure_ascii=False,
        ),
        created_at=datetime.utcnow(),
    )
    db.add(run)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        active = (
            db.query(PipelineRun)
            .filter(PipelineRun.status.in_(["queued", "running"]))
            .order_by(PipelineRun.created_at.desc(), PipelineRun.id.desc())
            .first()
        )
        if active and active.user_id == user_id and active.idempotency_key == resume_idempotency_key:
            return active
        raise MaterialTaskError("已有解析任务占用串行GPU队列") from exc
    add_pipeline_run_items(db, run, material_snapshot(db, user_id, [material.id]))
    material.pipeline_status = "queued"
    create_pipeline_event(
        db,
        run,
        "已创建 Popo 恢复任务，将复用正式冻结的 MinerU 产物",
        stage="queued",
        payload={"preflight": preflight, "requested_by_user_id": requested_by_user_id},
    )
    db.commit()
    return run


def _project_live_pipeline_progress(db: Session, run: PipelineRun, progress: dict[str, Any]) -> None:
    stage = str(progress.get("stage") or "")
    phase = str(progress.get("phase") or "")
    status = str(progress.get("status") or "")
    material_id = str(progress.get("material_id") or "")
    ordinal = int(progress.get("ordinal") or 0)
    total = int(progress.get("total") or run.total or 0)
    run_id = str(progress.get("run_id") or "")
    batch_id = str(progress.get("batch_id") or "")
    if phase == "model_switch":
        run.current_stage = "popo_model_loading" if status == "loading" else "popo_model_ready"
        message = "正在切换并加载 Popo 模型" if status == "loading" else "Popo 模型已就绪"
        create_pipeline_event(db, run, message, stage=run.current_stage, payload=progress)
        return
    if stage not in {"mineru", "popo"} or not material_id:
        return
    item = (
        db.query(PipelineRunItem)
        .filter(PipelineRunItem.run_id == run.id, PipelineRunItem.material_id == material_id)
        .first()
    )
    if not item:
        return
    attempt = (
        db.query(PipelineStageAttempt)
        .filter(PipelineStageAttempt.run_item_id == item.id, PipelineStageAttempt.stage == stage)
        .order_by(PipelineStageAttempt.attempt.desc(), PipelineStageAttempt.id.desc())
        .first()
    )
    now = datetime.utcnow()
    if attempt:
        attempt.status = "running"
        attempt.started_at = attempt.started_at or now
        attempt.external_batch_id = batch_id or attempt.external_batch_id
        attempt.external_run_id = run_id or attempt.external_run_id
        evidence = attempt.evidence() if hasattr(attempt, "evidence") else {}
        evidence.setdefault("timeline", []).append(progress)
        attempt.evidence_json = json.dumps(evidence, ensure_ascii=False)
    if phase == "remote":
        item.current_stage = f"{stage}_remote_{status or 'pending'}"
        message = f"第 {ordinal}/{total} 本 {stage.upper()} 远端状态：{status or 'pending'}"
    elif phase == "freeze":
        item.current_stage = f"{stage}_{'frozen' if status == 'frozen' else 'freezing'}"
        message = (
            f"第 {ordinal}/{total} 本 {stage.upper()} 已冻结到本地 MinIO"
            if status == "frozen"
            else f"第 {ordinal}/{total} 本正在冻结 {stage.upper()} 产物"
        )
        if status == "frozen":
            manifest = progress.get("manifest") if isinstance(progress.get("manifest"), dict) else {}
            if stage == "mineru":
                item.mineru_run_id = run_id or item.mineru_run_id
                item.mineru_manifest_bucket = str(manifest.get("bucket") or "") or item.mineru_manifest_bucket
                item.mineru_manifest_object = str(manifest.get("object") or "") or item.mineru_manifest_object
            else:
                item.popo_run_id = run_id or item.popo_run_id
                item.popo_manifest_bucket = str(manifest.get("bucket") or "") or item.popo_manifest_bucket
                item.popo_manifest_object = str(manifest.get("object") or "") or item.popo_manifest_object
    else:
        return
    run.current_stage = item.current_stage
    run.processed = sum(
        row.current_stage == "popo_frozen"
        for row in pipeline_run_items(db, run.id, run.user_id)
    )
    run.success = run.processed
    create_pipeline_event(db, run, message, stage=item.current_stage, payload=progress)


def run_pipeline_subprocess(
    run_id: int,
    apply: bool,
    limit: int,
    material_id: str = "",
    input_object: str = "",
    material_ids: list[str] | tuple[str, ...] | None = None,
    input_objects: list[str] | tuple[str, ...] | None = None,
    reprocess_completed: bool = False,
    command_override: list[str] | None = None,
    start_message: str = "开始执行现有 Luceon first-stage 调度脚本",
    worker_id: str = "",
    pipeline_env_override: dict[str, str] | None = None,
) -> None:
    db = SessionLocal()
    heartbeat_stop = threading.Event()
    heartbeat_thread = None
    try:
        run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
        if not run:
            return
        if worker_id and (run.status != "running" or run.worker_id != worker_id):
            raise RuntimeError("解析任务未被当前worker可靠领取")
        run.status = "running"
        run.started_at = run.started_at or datetime.utcnow()
        run.current_stage = "pipeline_command"
        mark_pipeline_items_running(db, run)
        create_pipeline_event(db, run, start_message, stage="pipeline_command")
        db.commit()

        if worker_id:
            def heartbeat_loop() -> None:
                while not heartbeat_stop.wait(10):
                    heartbeat_db = SessionLocal()
                    try:
                        if not touch_pipeline_lease(heartbeat_db, run_id, worker_id):
                            return
                    except Exception:
                        heartbeat_db.rollback()
                    finally:
                        heartbeat_db.close()

            heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
            heartbeat_thread.start()

        command = command_override or pipeline_command(
            apply=apply,
            limit=limit,
            material_id=material_id,
            input_object=input_object,
            material_ids=material_ids,
            input_objects=input_objects,
            reprocess_completed=reprocess_completed,
        )
        subprocess_env = pipeline_env()
        if pipeline_env_override:
            # The lifecycle owner supplies only non-secret, lease-bound
            # transport values (currently the loopback wrapper URL).  This
            # deliberately overrides a possibly transient direct endpoint.
            subprocess_env.update({str(key): str(value) for key, value in pipeline_env_override.items()})
        process = subprocess.Popen(
            command,
            cwd=PIPELINE_WORKDIR,
            env=subprocess_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
        )
        output_lines: list[str] = []
        result_lines: list[str] = []
        assert process.stdout is not None
        for line in process.stdout:
            output_lines.append(line)
            if line.startswith("__LUCEON_PROGRESS__"):
                try:
                    progress = json.loads(line.removeprefix("__LUCEON_PROGRESS__"))
                    _project_live_pipeline_progress(db, run, progress)
                    db.commit()
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
                continue
            result_lines.append(line)
        returncode = process.wait()
        stdout = "".join(result_lines)
        output = "".join(output_lines)[-8000:]
        error = ""
        payload = parse_pipeline_json(stdout)
        counts = pipeline_result_counts(payload, fallback_total=run.total)
        run.total = counts["total"]
        run.processed = counts["processed"]
        run.success = counts["success"]
        run.failed = counts["failed"]
        run.finished_at = datetime.utcnow()
        outcome = pipeline_run_outcome(payload, returncode, apply)
        # GPU lifecycle ownership/readiness is recorded before the subprocess
        # starts.  Preserve that immutable context when adding the terminal
        # pipeline result instead of replacing it.
        summary = {**run.summary(), "returncode": returncode, "result": payload}
        if returncode != 0 or error:
            summary.update({"stdout_tail": output, "stderr_tail": error})
        run.summary_json = json.dumps(summary, ensure_ascii=False)
        if pipeline_run_items(db, run.id, run.user_id):
            outcome = project_pipeline_result(db, run, payload)
            counts = {
                "total": run.total,
                "processed": run.processed,
                "success": run.success,
                "failed": run.failed,
            }
        else:
            run.status = outcome
            run.queue_slot = None
        if outcome == "succeeded":
            run.status = "succeeded"
            run.current_stage = "finished"
            create_pipeline_event(
                db,
                run,
                "解析任务执行完成",
                stage="finished",
                payload={"returncode": returncode, "pipeline_status": payload.get("status")},
            )
        elif outcome == "partial":
            run.current_stage = "partial"
            run.error_message = f"批量解析部分完成：成功 {run.success}，失败 {run.failed}"
            create_pipeline_event(
                db,
                run,
                "批量解析部分完成；成功样本已独立冻结，失败样本保留错误证据",
                stage="partial",
                level="warning",
                payload={"returncode": returncode, "pipeline_status": payload.get("status"), **counts},
            )
        else:
            run.current_stage = "failed"
            run.error_message = error or output or f"pipeline exited with {returncode} ({payload.get('status') or 'unknown'})"
            create_pipeline_event(
                db,
                run,
                "解析任务执行失败",
                stage="failed",
                level="error",
                payload={"returncode": returncode, "pipeline_status": payload.get("status")},
            )

        # Persist the per-book frozen result before the potentially long full
        # inventory scan. A scan failure must never roll back a reliable GPU
        # terminal state or hold its lease heartbeat inside one SQLite write
        # transaction.
        db.commit()
        heartbeat_stop.set()
        if heartbeat_thread:
            heartbeat_thread.join(timeout=2)
            heartbeat_thread = None

        sync_exc = None
        for sync_attempt in range(1, 4):
            sync_db = SessionLocal()
            try:
                sync_pipeline_run_inventory(sync_db, run.user_id, run.id)
                sync_db.commit()
                sync_exc = None
                break
            except Exception as exc:
                sync_db.rollback()
                sync_exc = exc
                if sync_attempt < 3:
                    time.sleep(sync_attempt)
            finally:
                sync_db.close()
        if sync_exc is not None:
            run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
            create_pipeline_event(
                db,
                run,
                "终态任务的已冻结产物同步未完成",
                stage="inventory_sync",
                level="warning",
                payload={"error": str(sync_exc), "attempts": 3},
            )
            db.commit()
    except Exception as exc:
        run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
        if run:
            run.status = "failed"
            run.queue_slot = None
            run.current_stage = "failed"
            run.finished_at = datetime.utcnow()
            run.error_message = str(exc)
            run.worker_id = None
            run.lease_expires_at = None
            for item in pipeline_run_items(db, run.id, run.user_id):
                if item.status not in {"succeeded", "failed", "cancelled"}:
                    item.status = "failed"
                    item.current_stage = "worker_failed"
                    item.error_code = "pipeline_worker_exception"
                    item.error_message = str(exc)
                    item.finished_at = datetime.utcnow()
            create_pipeline_event(db, run, "解析任务异常退出", stage="failed", level="error", payload={"error": str(exc)})
            db.commit()
    finally:
        heartbeat_stop.set()
        if heartbeat_thread:
            heartbeat_thread.join(timeout=2)
        db.close()


def material_summary(db: Session, user_id: str) -> dict[str, Any]:
    visible = (Material.user_id == user_id, Material.ignored.is_(False))
    total = db.query(Material).filter(*visible).count()
    stages = {
        stage: db.query(Material).filter(*visible, Material.stage_status == stage).count()
        for stage in ["input", "mineru_done", "popo_done", "latex_done", "failed"]
    }
    latest = latest_pipeline_run(db, user_id)
    return {
        "total": total,
        "stages": stages,
        "availability": material_availability(db, user_id),
        "latest_run": latest.to_dict() if latest else None,
    }


def material_availability(db: Session, user_id: str) -> dict[str, int]:
    rows = db.query(Material).filter(Material.user_id == user_id, Material.ignored.is_(False)).all()
    return {
        "input": sum(1 for row in rows if row.input_object),
        "mineru_done": sum(1 for row in rows if row.mineru_manifest_object or row.popo_manifest_object),
        "popo_done": sum(1 for row in rows if row.popo_manifest_object),
        "latex_done": sum(1 for row in rows if row.latex_manifest_object),
    }
