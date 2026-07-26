import json
import mimetypes
import os
import hashlib
import logging
import re
from collections import Counter
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import fitz
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.final_review import FinalReviewAnnotation, FinalReviewSession
from app.models.material import Material
from app.models.review_asset import ReviewAsset
from app.services import final_review as final_review_service
from app.services.codex_elegantbook import (
    output_artifact_paths,
    output_artifact_volumes,
    output_from_ref,
    select_elegantbook_output,
)
from app.services.luceon_review import (
    ObjectRef,
    clean_path,
    is_missing_object_error,
    list_input_pdf_objects,
    list_manifest_objects,
    object_exists,
    presigned,
    read_object,
    resolve_manifest,
)
from app.services.material_outputs import (
    material_output_or_404,
    output_from_material_output,
    sync_material_outputs_for_material,
)
from app.services.outline_review import build_outline_review
from app.services.popo_to_raw import latest_successful_popo_to_raw_dry_run
from app.services.review_pdf_preview import ensure_review_pdf_preview_isolated
from app.services.source_map import normalize_source_map, synthesize_page_markdown, synthesize_page_markdown_from_content_list
from app.utils.minio_client import minio_client
from app.utils.user_dep import get_asset_download_user_id, get_user_id

router = APIRouter()


class ManifestImportRequest(BaseModel):
    manifest_bucket: str = "eduassets-minerupopo"
    manifest_object: str
    title: str | None = None


class ManifestBatchImportRequest(BaseModel):
    manifest_bucket: str = "eduassets-minerupopo"
    manifest_objects: list[str]


class ManifestSyncRequest(BaseModel):
    manifest_bucket: str = "eduassets-minerupopo"
    prefix: str = "minerupopo/"
    input_bucket: str = "eduassets-input"
    input_prefix: str = ""
    include_input_pdfs: bool = True
    limit: int = 300


class ReviewMetadataRequest(BaseModel):
    review_status: str = "pending"
    review_tags: list[str] = []
    review_note: str = ""


class FinalReviewSessionRequest(BaseModel):
    asset_id: int
    reuse_open: bool = True


class FinalReviewAnnotationRequest(BaseModel):
    issue_type: str
    severity: str = "major"
    status: str = "draft"
    human_note: str = ""
    anchors: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)


class FinalReviewAnnotationPatchRequest(BaseModel):
    issue_type: str | None = None
    severity: str | None = None
    status: str | None = None
    human_note: str | None = None
    anchors: dict[str, Any] | None = None
    evidence: dict[str, Any] | None = None


class FinalReviewDecisionRequest(BaseModel):
    decision: str
    reviewer_note: str = ""


REVIEW_STATUSES = {"pending", "pass", "needs_fix", "reject"}
PDF_PAGE_CACHE_ROOT = Path(os.getenv("LUCEON_REVIEW_PDF_PAGE_CACHE", "/data/pdf-page-cache"))
REVIEW_PDF_CACHE_ROOT = Path(os.getenv("LUCEON_REVIEW_PDF_CACHE", "/data/review-pdf-cache"))
REVIEW_PDF_MIN_SOURCE_BYTES = 12 * 1024 * 1024
REVIEW_PDF_DPI = 120
REVIEW_PDF_JPEG_QUALITY = 58
REVIEW_PDF_MAX_EDGE = 2000
REVIEW_PDF_CACHE_VERSION = "review-pdf-v2-120dpi-q58-max2000"
logger = logging.getLogger(__name__)


def _ref(bucket: str | None, object_name: str | None) -> ObjectRef | None:
    if bucket and object_name:
        return ObjectRef(bucket=bucket, object=object_name)
    return None


def _asset_or_404(asset_id: int, user_id: str, db: Session) -> ReviewAsset:
    asset = db.query(ReviewAsset).filter(ReviewAsset.id == asset_id, ReviewAsset.user_id == user_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail="审查资产不存在")
    return asset


def _read_ref(ref: ObjectRef | None, missing_detail: str) -> bytes:
    if not ref:
        raise HTTPException(status_code=404, detail=missing_detail)
    try:
        return read_object(ref.bucket, ref.object)
    except Exception as exc:
        if is_missing_object_error(exc):
            raise HTTPException(status_code=404, detail=missing_detail)
        raise HTTPException(status_code=500, detail=str(exc))


def _stat_ref(ref: ObjectRef | None, missing_detail: str):
    if not ref:
        raise HTTPException(status_code=404, detail=missing_detail)
    try:
        return minio_client.stat_object(ref.bucket, ref.object)
    except Exception as exc:
        if is_missing_object_error(exc):
            raise HTTPException(status_code=404, detail=missing_detail)
        raise HTTPException(status_code=500, detail=str(exc))


def _read_ref_range(ref: ObjectRef, offset: int, length: int, missing_detail: str) -> bytes:
    try:
        response = minio_client.get_object(ref.bucket, ref.object, offset=offset, length=length)
        try:
            return response.read()
        finally:
            close = getattr(response, "close", None)
            if close:
                close()
            release_conn = getattr(response, "release_conn", None)
            if release_conn:
                release_conn()
    except Exception as exc:
        if is_missing_object_error(exc):
            raise HTTPException(status_code=404, detail=missing_detail)
        raise HTTPException(status_code=500, detail=str(exc))


def _stream_ref(ref: ObjectRef, missing_detail: str):
    try:
        response = minio_client.get_object(ref.bucket, ref.object)
    except Exception as exc:
        if is_missing_object_error(exc):
            raise HTTPException(status_code=404, detail=missing_detail)
        raise HTTPException(status_code=500, detail=str(exc))

    def iter_chunks():
        try:
            for chunk in response.stream(64 * 1024):
                if chunk:
                    yield chunk
        finally:
            close = getattr(response, "close", None)
            if close:
                close()
            release_conn = getattr(response, "release_conn", None)
            if release_conn:
                release_conn()

    return iter_chunks()


def _stream_file(path: Path):
    def iter_chunks():
        with path.open("rb") as fh:
            while True:
                chunk = fh.read(64 * 1024)
                if not chunk:
                    break
                yield chunk

    return iter_chunks()


def _skip_http_compression(media_type: str | None) -> bool:
    normalized = (media_type or "").split(";", 1)[0].lower()
    return normalized in {
        "application/pdf",
        "application/zip",
        "application/x-zip-compressed",
    } or normalized.startswith("image/")


def _parse_range_header(range_header: str | None, size: int) -> tuple[int, int] | None:
    if not range_header or not range_header.lower().startswith("bytes="):
        return None
    range_spec = range_header.split("=", 1)[1].strip()
    if "," in range_spec or "-" not in range_spec:
        return None
    start_raw, end_raw = range_spec.split("-", 1)
    try:
        if not start_raw:
            suffix = int(end_raw)
            if suffix <= 0:
                raise ValueError
            start = max(0, size - suffix)
            end = size - 1
        else:
            start = int(start_raw)
            end = int(end_raw) if end_raw else size - 1
    except ValueError:
        return None
    if start < 0 or start >= size or end < start:
        raise HTTPException(status_code=416, detail="请求范围不可用", headers={"Content-Range": f"bytes */{size}"})
    return start, min(end, size - 1)


def _http_cache_headers(ref: ObjectRef, stat: Any) -> dict[str, str]:
    raw_etag = str(getattr(stat, "etag", "") or "").strip('"')
    if not raw_etag:
        raw_etag = hashlib.sha256(
            f"{ref.bucket}|{ref.object}|{getattr(stat, 'size', 0)}|{getattr(stat, 'last_modified', '')}".encode("utf-8")
        ).hexdigest()
    headers = {
        "ETag": f'"{raw_etag}"',
        "Cache-Control": "private, max-age=3600, must-revalidate",
    }
    last_modified = getattr(stat, "last_modified", None)
    if last_modified:
        try:
            headers["Last-Modified"] = format_datetime(last_modified, usegmt=True)
        except (TypeError, ValueError):
            pass
    return headers


def _is_not_modified(request: Request, headers: dict[str, str]) -> bool:
    if_none_match = request.headers.get("if-none-match", "")
    etag = headers.get("ETag", "")
    return bool(etag and (if_none_match.strip() == "*" or etag in [part.strip() for part in if_none_match.split(",")]))


def _effective_range_header(request: Request, headers: dict[str, str]) -> str | None:
    range_header = request.headers.get("range")
    if_range = request.headers.get("if-range")
    if if_range and if_range not in {headers.get("ETag"), headers.get("Last-Modified")}:
        return None
    return range_header


def _read_file_range(path: Path, offset: int, length: int) -> bytes:
    with path.open("rb") as handle:
        handle.seek(offset)
        return handle.read(length)


def _review_pdf_cache_key(ref: ObjectRef, stat: Any) -> str:
    marker = "|".join(
        [
            REVIEW_PDF_CACHE_VERSION,
            ref.bucket,
            ref.object,
            str(getattr(stat, "etag", "") or ""),
            str(getattr(stat, "last_modified", "") or ""),
            str(getattr(stat, "size", "") or ""),
        ]
    )
    return hashlib.sha256(marker.encode("utf-8")).hexdigest()


def _source_pdf_ref_for_asset(asset: ReviewAsset) -> ObjectRef | None:
    ref = _ref(asset.source_pdf_bucket, asset.source_pdf_object)
    if asset.input_pdf_bucket and asset.input_pdf_object:
        ref = _ref(asset.input_pdf_bucket, asset.input_pdf_object)
    return ref


def _pdf_cache_key(ref: ObjectRef, stat: Any, page: int, width: int) -> str:
    marker = "|".join(
        [
            ref.bucket,
            ref.object,
            str(getattr(stat, "etag", "") or ""),
            str(getattr(stat, "last_modified", "") or ""),
            str(getattr(stat, "size", "") or ""),
            str(page),
            str(width),
        ]
    )
    return hashlib.sha256(marker.encode("utf-8")).hexdigest()


def _render_pdf_page_png(pdf_bytes: bytes, page: int, width: int) -> tuple[bytes, int]:
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"PDF 无法打开：{exc}") from exc
    try:
        page_count = doc.page_count
        if page < 1 or page > page_count:
            raise HTTPException(status_code=404, detail=f"PDF 第 {page} 页不存在，共 {page_count} 页")
        pdf_page = doc.load_page(page - 1)
        rect = pdf_page.rect
        scale = width / rect.width if rect.width else 1
        matrix = fitz.Matrix(scale, scale)
        pixmap = pdf_page.get_pixmap(matrix=matrix, alpha=False)
        return pixmap.tobytes("png"), page_count
    finally:
        doc.close()


def _source_map_for_asset(asset: ReviewAsset, db: Session | None = None) -> dict[str, Any]:
    middle_ref = _ref(asset.middle_json_bucket, asset.middle_json_object)
    if not middle_ref and db:
        material = _material_for_asset(asset, db)
        mineru_manifest_ref = _manifest_ref_from_material(material, "mineru")
        mineru_manifest = _read_json_optional(mineru_manifest_ref)
        candidates: list[ObjectRef | None] = []
        if mineru_manifest_ref and isinstance(mineru_manifest, dict):
            objects = mineru_manifest.get("objects") if isinstance(mineru_manifest.get("objects"), dict) else {}
            for key in ("middle", "middle_json", "middle_json_v2"):
                candidates.append(_ref_from_stage_manifest_value(objects.get(key), mineru_manifest_ref))
                candidates.append(_ref_from_stage_manifest_value(mineru_manifest.get(key), mineru_manifest_ref))
        if mineru_manifest_ref and asset.material_id:
            stem = Path(asset.material_id).name
            candidates.extend(
                [
                    _same_prefix_ref(mineru_manifest_ref, f"{stem}_middle.json"),
                    _same_prefix_ref(mineru_manifest_ref, "middle.json"),
                ]
            )
        middle_ref = _first_existing_ref(candidates)
    content = _read_ref(middle_ref, "middle_json 不存在")
    try:
        middle_json = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    if not isinstance(middle_json, dict):
        return {"pages": []}
    return normalize_source_map(middle_json)


def _json_ref(ref: ObjectRef | None) -> dict[str, Any] | None:
    if not ref:
        return None
    content = _read_ref(ref, "JSON 对象不存在")
    try:
        parsed = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return parsed if isinstance(parsed, dict) else None


def _json_value_ref(ref: ObjectRef | None) -> Any:
    if not ref:
        return None
    content = _read_ref(ref, "JSON 对象不存在")
    try:
        return json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=str(exc))


def _material_for_asset(asset: ReviewAsset, db: Session) -> Material | None:
    query = db.query(Material).filter(Material.user_id == asset.user_id)
    if asset.material_id:
        material = query.filter(Material.material_id == asset.material_id).order_by(Material.id.desc()).first()
        if material:
            return material
    if asset.input_pdf_bucket and asset.input_pdf_object:
        return (
            query.filter(
                Material.input_bucket == asset.input_pdf_bucket,
                Material.input_object == asset.input_pdf_object,
            )
            .order_by(Material.id.desc())
            .first()
        )
    return None


def _manifest_ref_from_material(material: Material | None, stage: str) -> ObjectRef | None:
    if not material:
        return None
    if stage == "mineru":
        return _ref(material.mineru_manifest_bucket, material.mineru_manifest_object)
    if stage in {"popo", "minerupopo"}:
        return _ref(material.popo_manifest_bucket, material.popo_manifest_object)
    if stage == "latex":
        return _ref(material.latex_manifest_bucket, material.latex_manifest_object)
    if stage == "raw":
        return _ref(material.raw_manifest_bucket, material.raw_manifest_object)
    if stage == "clean":
        return _ref(material.clean_manifest_bucket, material.clean_manifest_object)
    if stage == "standard":
        return _ref(material.standard_manifest_bucket, material.standard_manifest_object)
    return None


def _same_prefix_ref(manifest_ref: ObjectRef | None, filename: str) -> ObjectRef | None:
    if not manifest_ref:
        return None
    prefix = manifest_ref.object.rsplit("/", 1)[0] + "/" if "/" in manifest_ref.object else ""
    return ObjectRef(manifest_ref.bucket, clean_path(f"{prefix}{filename}"))


def _read_json_optional(ref: ObjectRef | None) -> dict[str, Any] | None:
    if not ref or not object_exists(ref.bucket, ref.object):
        return None
    return _json_ref(ref)


def _read_jsonl_optional(ref: ObjectRef | None, limit: int = 120) -> list[dict[str, Any]]:
    if not ref or not object_exists(ref.bucket, ref.object):
        return []
    text = read_object(ref.bucket, ref.object).decode("utf-8", errors="replace")
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
        if len(rows) >= limit:
            break
    return rows


def _ref_from_stage_manifest_value(value: Any, manifest_ref: ObjectRef) -> ObjectRef | None:
    bucket = manifest_ref.bucket
    object_name = ""
    if isinstance(value, dict):
        bucket = str(value.get("bucket") or bucket)
        object_name = clean_path(value.get("object") or value.get("key") or value.get("path"))
    elif isinstance(value, str):
        object_name = clean_path(value)
    if not object_name:
        return None
    if "/" not in object_name:
        prefix = manifest_ref.object.rsplit("/", 1)[0] + "/" if "/" in manifest_ref.object else ""
        object_name = clean_path(f"{prefix}{object_name}")
    return ObjectRef(bucket, object_name)


def _first_existing_ref(candidates: list[ObjectRef | None]) -> ObjectRef | None:
    for ref in candidates:
        if ref and object_exists(ref.bucket, ref.object):
            return ref
    return None


def _stage_markdown_ref(manifest_ref: ObjectRef | None, manifest: dict[str, Any] | None) -> ObjectRef | None:
    if not manifest_ref:
        return None
    keys = (
        "clean_md",
        "standard_md",
        "clean_markdown",
        "standard_markdown",
        "markdown",
        "markdown_file",
        "raw_markdown",
        "body_markdown",
        "out_md",
        "content_markdown",
    )
    candidates: list[ObjectRef | None] = []
    if isinstance(manifest, dict):
        objects = manifest.get("objects") if isinstance(manifest.get("objects"), dict) else {}
        for key in keys:
            candidates.append(_ref_from_stage_manifest_value(objects.get(key), manifest_ref))
            candidates.append(_ref_from_stage_manifest_value(manifest.get(key), manifest_ref))
    candidates.extend(
        [
            _same_prefix_ref(manifest_ref, "clean.md"),
            _same_prefix_ref(manifest_ref, "standard.md"),
            _same_prefix_ref(manifest_ref, "raw.md"),
            _same_prefix_ref(manifest_ref, "body.md"),
            _same_prefix_ref(manifest_ref, "full.md"),
            _same_prefix_ref(manifest_ref, "content.md"),
        ]
    )
    return _first_existing_ref(candidates)


def _stage_content_list_ref(manifest_ref: ObjectRef | None, manifest: dict[str, Any] | None) -> ObjectRef | None:
    if not manifest_ref:
        return None
    keys = ("content_list", "content_list_v2", "input_content_list", "input_content_list_v2")
    candidates: list[ObjectRef | None] = []
    if isinstance(manifest, dict):
        objects = manifest.get("objects") if isinstance(manifest.get("objects"), dict) else {}
        for key in keys:
            candidates.append(_ref_from_stage_manifest_value(objects.get(key), manifest_ref))
            candidates.append(_ref_from_stage_manifest_value(manifest.get(key), manifest_ref))
    candidates.extend(
        [
            _same_prefix_ref(manifest_ref, "content_list.json"),
            _same_prefix_ref(manifest_ref, "content_list_v2.json"),
            _same_prefix_ref(manifest_ref, "input_content_list.json"),
            _same_prefix_ref(manifest_ref, "input_content_list_v2.json"),
        ]
    )
    return _first_existing_ref(candidates)


def _read_text_optional(ref: ObjectRef | None) -> str | None:
    if not ref or not object_exists(ref.bucket, ref.object):
        return None
    return read_object(ref.bucket, ref.object).decode("utf-8", errors="replace")


def _read_jsonl_optional(ref: ObjectRef | None, limit: int = 2000) -> list[dict[str, Any]]:
    text = _read_text_optional(ref)
    if not text:
        return []
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
        if len(rows) >= limit:
            break
    return rows


def _read_local_json_optional(path: Path | None) -> dict[str, Any] | None:
    if not path or not path.exists():
        return None
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _read_local_text_optional(path: Path | None) -> str | None:
    if not path or not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _read_local_jsonl_optional(path: Path | None, limit: int = 2000) -> list[dict[str, Any]]:
    text = _read_local_text_optional(path)
    if not text:
        return []
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
        if len(rows) >= limit:
            break
    return rows


def _local_ref_dict(path: Path | None) -> dict[str, str]:
    return {"bucket": "local-dry-run" if path else "", "object": str(path or "")}


def _content_list_for_asset(asset: ReviewAsset, db: Session | None = None) -> Any:
    candidates: list[ObjectRef | None] = []
    manifest_ref = _manifest_ref_for_asset(asset)
    manifest = _manifest_for_asset(asset)
    candidates.append(_stage_content_list_ref(manifest_ref, manifest))
    if db:
        material = _material_for_asset(asset, db)
        mineru_manifest_ref = _manifest_ref_from_material(material, "mineru")
        mineru_manifest = _read_json_optional(mineru_manifest_ref)
        candidates.append(_stage_content_list_ref(mineru_manifest_ref, mineru_manifest))
    content_list_ref = _first_existing_ref(candidates)
    if not content_list_ref:
        return None
    return _json_value_ref(content_list_ref)


def _image_text_signature(text: str) -> str:
    return "".join(ch.lower() for ch in text if ch.isalnum())


def _content_list_image_lookup(content_list: Any) -> dict[str, Any]:
    if isinstance(content_list, dict):
        for key in ("content_list", "items", "blocks"):
            value = content_list.get(key)
            if isinstance(value, list):
                content_list = value
                break
    if not isinstance(content_list, list):
        return {"by_index": {}, "by_text": []}
    by_index: dict[int, str] = {}
    by_text: list[tuple[str, str]] = []
    for index, item in enumerate(content_list):
        if not isinstance(item, dict):
            continue
        image_path = item.get("img_path") or item.get("image_path")
        if isinstance(image_path, str) and image_path.strip():
            clean_image_path = image_path.strip()
            by_index[index] = clean_image_path
            text = _popo_text(item.get("content") or item.get("text") or item.get("title"))
            signature = _image_text_signature(text)
            if signature:
                by_text.append((signature, clean_image_path))
    return {"by_index": by_index, "by_text": by_text}


def _manifest_ref_for_asset(asset: ReviewAsset) -> ObjectRef | None:
    if not asset.manifest_bucket or not asset.manifest_object:
        return None
    if str(asset.manifest_object).startswith("__input__/"):
        return None
    return ObjectRef(asset.manifest_bucket, asset.manifest_object)


def _manifest_for_asset(asset: ReviewAsset) -> dict[str, Any] | None:
    if asset.manifest_json:
        try:
            parsed = json.loads(asset.manifest_json)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed
    manifest_ref = _manifest_ref_for_asset(asset)
    return _read_json_optional(manifest_ref)


def _popo_raw_ref_for_asset(asset: ReviewAsset) -> ObjectRef | None:
    manifest_ref = _manifest_ref_for_asset(asset)
    manifest = _manifest_for_asset(asset)
    candidates: list[ObjectRef | None] = []
    if manifest_ref and isinstance(manifest, dict):
        objects = manifest.get("objects") if isinstance(manifest.get("objects"), dict) else {}
        for key in ("popo_raw", "inference", "label_normalization"):
            candidates.append(_ref_from_stage_manifest_value(objects.get(key), manifest_ref))
            candidates.append(_ref_from_stage_manifest_value(manifest.get(key), manifest_ref))
    if asset.popo_markdown_bucket and asset.popo_markdown_object:
        popo_text_ref = ObjectRef(asset.popo_markdown_bucket, asset.popo_markdown_object)
        candidates.extend(
            [
                _same_prefix_ref(popo_text_ref, "popo_raw.json"),
                _same_prefix_ref(popo_text_ref, "inference.json"),
                _same_prefix_ref(popo_text_ref, "label_normalization.json"),
            ]
        )
    if manifest_ref:
        candidates.extend(
            [
                _same_prefix_ref(manifest_ref, "popo_raw.json"),
                _same_prefix_ref(manifest_ref, "inference.json"),
                _same_prefix_ref(manifest_ref, "label_normalization.json"),
            ]
        )
    return _first_existing_ref(candidates)


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except ValueError:
            return None
    return None


def _popo_text(value: Any) -> str:
    if isinstance(value, str):
        text = value.replace("<|txt_split|>", " ")
        return " ".join(text.split())
    if isinstance(value, list):
        return " ".join(part for part in (_popo_text(item) for item in value) if part)
    if isinstance(value, dict):
        for key in ("content", "text", "title"):
            text = _popo_text(value.get(key))
            if text:
                return text
    return ""


def _popo_sort_key(item: dict[str, Any], index: int) -> tuple[float, float, int]:
    bbox = item.get("bbox")
    if not isinstance(bbox, list) or len(bbox) < 2:
        bbox = item.get("location", [{}])
        if isinstance(bbox, list) and bbox and isinstance(bbox[0], dict):
            bbox = bbox[0].get("bbox")
    y = float(bbox[1]) if isinstance(bbox, list) and len(bbox) > 1 and isinstance(bbox[1], int | float) else 0.0
    x = float(bbox[0]) if isinstance(bbox, list) and bbox and isinstance(bbox[0], int | float) else 0.0
    item_id = _coerce_int(item.get("id"))
    return (y, x, item_id if item_id is not None else index)


def _iter_popo_page_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict) and isinstance(value.get("pages"), dict):
        items: list[dict[str, Any]] = []
        for raw_page, page_items in value["pages"].items():
            page_number = _coerce_int(raw_page)
            if not isinstance(page_items, list) or page_number is None:
                continue
            for item in page_items:
                if isinstance(item, dict):
                    cloned = dict(item)
                    cloned.setdefault("page", page_number)
                    items.append(cloned)
        return items
    return []


def _is_popo_image_item(item: dict[str, Any]) -> bool:
    item_type = str(item.get("type") or item.get("source_label") or item.get("block_type") or "").lower()
    return "image" in item_type or "figure" in item_type


def _popo_image_path(item: dict[str, Any], text: str, image_lookup: dict[str, Any]) -> str | None:
    text_signature = _image_text_signature(text)
    if text_signature:
        best_path = None
        best_score = 0
        for signature, image_path in image_lookup.get("by_text", []):
            if not signature:
                continue
            if signature in text_signature or text_signature in signature:
                score = min(len(signature), len(text_signature))
                if score > best_score:
                    best_score = score
                    best_path = image_path
        if best_path:
            return best_path

    image_index = _coerce_int(item.get("image"))
    if image_index is not None:
        return image_lookup.get("by_index", {}).get(image_index)
    return None


def _render_popo_page_markdown(value: Any, image_lookup: dict[str, Any] | None = None) -> str:
    image_lookup = image_lookup or {}
    by_page: dict[int, list[tuple[int, dict[str, Any]]]] = {}
    for index, item in enumerate(_iter_popo_page_items(value)):
        page_number = _coerce_int(item.get("page"))
        text = _popo_text(item.get("content") or item.get("text") or item.get("title"))
        if page_number is None:
            continue
        if not text and not _is_popo_image_item(item):
            continue
        by_page.setdefault(page_number, []).append((index, item))

    sections: list[str] = []
    for page_number in sorted(by_page):
        lines = [f"# Page {page_number}"]
        items = sorted(by_page[page_number], key=lambda pair: _popo_sort_key(pair[1], pair[0]))
        for _, item in items:
            block_type = str(item.get("type") or item.get("source_label") or "block").strip() or "block"
            block_id = item.get("id") or item.get("source_id") or ""
            id_label = f" #{block_id}" if block_id else ""
            text = _popo_text(item.get("content") or item.get("text") or item.get("title"))
            image_path = _popo_image_path(item, text, image_lookup)
            if _is_popo_image_item(item) and image_path:
                alt = text[:120] if text else f"{block_type}{id_label}"
                lines.append(f"![{alt}]({image_path})")
            if text:
                lines.append(f"[{block_type}{id_label}] {text}")
            elif _is_popo_image_item(item):
                lines.append(f"[{block_type}{id_label}]")
        sections.append("\n\n".join(lines))
    return "\n\n".join(sections)


def _ref_dict(ref: ObjectRef | None) -> dict[str, str]:
    return ref.as_dict() if ref else {"bucket": "", "object": ""}


def _asset_dict_with_material(asset: ReviewAsset, db: Session) -> dict[str, Any]:
    row = asset.to_dict()
    material = _material_for_asset(asset, db)
    dry_run = latest_successful_popo_to_raw_dry_run(db, asset.user_id, material.material_id if material else "")
    row["material_stage"] = material.stage_status if material else asset.review_stage or "parse"
    row["has_raw"] = bool(material and material.raw_manifest_bucket and material.raw_manifest_object)
    row["has_clean"] = bool(material and material.clean_manifest_bucket and material.clean_manifest_object)
    row["has_standard"] = bool(material and material.standard_manifest_bucket and material.standard_manifest_object)
    row["has_latex"] = bool(material and material.latex_manifest_bucket and material.latex_manifest_object)
    row["standard_quality_score"] = material.standard_quality_score if material else None
    row["has_raw_dry_run"] = bool(dry_run)
    row["raw_dry_run_id"] = str(dry_run.id) if dry_run else ""
    row["raw_manifest"] = {
        "bucket": material.raw_manifest_bucket if material else "",
        "object": material.raw_manifest_object if material else "",
    }
    row["clean_manifest"] = {
        "bucket": material.clean_manifest_bucket if material else "",
        "object": material.clean_manifest_object if material else "",
    }
    row["standard_manifest"] = {
        "bucket": material.standard_manifest_bucket if material else "",
        "object": material.standard_manifest_object if material else "",
    }
    row["latex_manifest"] = {
        "bucket": material.latex_manifest_bucket if material else "",
        "object": material.latex_manifest_object if material else "",
    }
    return row


def _standard_navigation_for_material(material: Material | None) -> dict[str, Any]:
    standard_manifest_ref = _manifest_ref_from_material(material, "standard")
    standard_manifest = _read_json_optional(standard_manifest_ref)
    document_ref = None
    if isinstance(standard_manifest, dict):
        objects = standard_manifest.get("objects") if isinstance(standard_manifest.get("objects"), dict) else {}
        document_ref = _ref_from_stage_manifest_value(objects.get("standard_document_json"), standard_manifest_ref) if standard_manifest_ref else None
        document_ref = document_ref or (_ref_from_stage_manifest_value(objects.get("standard_document"), standard_manifest_ref) if standard_manifest_ref else None)
    document_ref = document_ref or _same_prefix_ref(standard_manifest_ref, "standard_document.json")
    document = _read_json_optional(document_ref)
    if not isinstance(document, dict):
        return {"available": False, "outline": [], "blocks": []}

    blocks = document.get("blocks") if isinstance(document.get("blocks"), list) else []
    outline_rows = []
    block_rows = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_id = str(block.get("id") or "")
        markdown = str(block.get("markdown") or "")
        heading_match = re.match(r"^(#{1,6})\s+(.+?)\s*$", markdown)
        row = {
            "block_id": block_id,
            "type": block.get("type") or "",
            "subtype": block.get("subtype") or "",
            "line_start": block.get("line_start"),
            "line_end": block.get("line_end"),
            "heading_path": block.get("heading_path") if isinstance(block.get("heading_path"), list) else [],
        }
        block_rows.append(row)
        if heading_match:
            title = heading_match.group(2).strip()
            outline_rows.append(
                {
                    **row,
                    "title": title,
                    "level": len(heading_match.group(1)),
                    "path": row["heading_path"],
                }
            )
    return {
        "available": True,
        "manifest": _ref_dict(standard_manifest_ref),
        "document": _ref_dict(document_ref),
        "outline": outline_rows,
        "blocks": block_rows,
    }


def _save_resolved_asset(resolved, user_id: str, db: Session, overwrite_title: bool = True) -> ReviewAsset:
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

    if overwrite_title or not asset.title:
        asset.title = resolved.title
    asset.input_filename = resolved.input_filename or resolved.title
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
    return asset


def _input_pdf_asset_exists(user_id: str, bucket: str, object_name: str, db: Session) -> bool:
    return (
        db.query(ReviewAsset.id)
        .filter(
            ReviewAsset.user_id == user_id,
            ReviewAsset.input_pdf_bucket == bucket,
            ReviewAsset.input_pdf_object == object_name,
        )
        .first()
        is not None
    )


def _save_input_pdf_asset(bucket: str, object_name: str, user_id: str, db: Session) -> ReviewAsset | None:
    if _input_pdf_asset_exists(user_id, bucket, object_name, db):
        return None

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
    return asset


def _build_review_report(asset: ReviewAsset, source_map: dict[str, Any] | None = None) -> str:
    source_map = source_map or {"pages": []}
    pages = source_map.get("pages") if isinstance(source_map, dict) else []
    blocks: list[dict[str, Any]] = []
    if isinstance(pages, list):
        for page in pages:
            if isinstance(page, dict) and isinstance(page.get("blocks"), list):
                blocks.extend(block for block in page["blocks"] if isinstance(block, dict))

    type_counts = Counter(str(block.get("type") or "other") for block in blocks)
    table_count = type_counts.get("table", 0)
    image_count = type_counts.get("image", 0)
    text_count = type_counts.get("text", 0) + type_counts.get("list", 0)
    title_count = type_counts.get("title", 0)
    tags = ", ".join(asset._review_tags()) or "无"
    status_names = {
        "pending": "待审查",
        "pass": "通过",
        "needs_fix": "需修正",
        "reject": "退回",
    }
    generated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    return "\n".join(
        [
            f"# {asset.title} 溯源审查报告",
            "",
            f"- 生成时间: {generated_at}",
            f"- 审查状态: {status_names.get(asset.review_status or 'pending', asset.review_status or 'pending')}",
            f"- 审查标签: {tags}",
            f"- material_id: {asset.material_id or ''}",
            f"- run_id: {asset.run_id or ''}",
            f"- manifest: {asset.manifest_bucket}/{asset.manifest_object}",
            "",
            "## 解析结构概览",
            "",
            f"- PDF 页数: {len(pages) if isinstance(pages, list) else 0}",
            f"- bbox 总数: {len(blocks)}",
            f"- 正文/列表 bbox: {text_count}",
            f"- 标题 bbox: {title_count}",
            f"- 表格 bbox: {table_count}",
            f"- 图片 bbox: {image_count}",
            "",
            "## 人工审查记录",
            "",
            asset.review_note or "暂无审查备注。",
        ]
    )


@router.post("/review/assets/from_manifest")
def import_review_asset(
    payload: ManifestImportRequest,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    try:
        resolved = resolve_manifest(
            payload.manifest_bucket,
            payload.manifest_object,
            title=payload.title or "",
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    asset = _save_resolved_asset(resolved, user_id, db)

    db.commit()
    db.refresh(asset)
    return asset.to_dict()


@router.post("/review/assets/batch_from_manifest")
def import_review_assets_batch(
    payload: ManifestBatchImportRequest,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    seen: set[str] = set()
    results = []
    for raw_object in payload.manifest_objects:
        object_name = raw_object.strip()
        if not object_name or object_name in seen:
            continue
        seen.add(object_name)
        try:
            resolved = resolve_manifest(payload.manifest_bucket, object_name, check_fallbacks=False)
            asset = _save_resolved_asset(resolved, user_id, db, overwrite_title=False)
            db.flush()
            results.append({"manifest_object": object_name, "status": "success", "asset": asset.to_dict()})
        except Exception as exc:
            results.append({"manifest_object": object_name, "status": "failed", "message": str(exc)})

    db.commit()
    return {
        "total": len(results),
        "success": sum(1 for item in results if item["status"] == "success"),
        "failed": sum(1 for item in results if item["status"] == "failed"),
        "results": results,
    }


@router.post("/review/assets/sync_minio")
def sync_review_assets_from_minio(
    payload: ManifestSyncRequest,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    limit = max(1, payload.limit)
    try:
        manifest_objects = list_manifest_objects(payload.manifest_bucket, payload.prefix, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    results = []
    manifest_success = 0
    manifest_failed = 0
    for object_name in manifest_objects:
        try:
            resolved = resolve_manifest(payload.manifest_bucket, object_name)
            asset = _save_resolved_asset(resolved, user_id, db, overwrite_title=False)
            db.flush()
            results.append({"manifest_object": object_name, "status": "success", "asset": asset.to_dict()})
            manifest_success += 1
        except Exception as exc:
            results.append({"manifest_object": object_name, "status": "failed", "message": str(exc)})
            manifest_failed += 1

    input_objects: list[str] = []
    input_added = 0
    input_existing = 0
    if payload.include_input_pdfs:
        try:
            input_objects = list_input_pdf_objects(payload.input_bucket, payload.input_prefix, limit=limit)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        for object_name in input_objects:
            asset = _save_input_pdf_asset(payload.input_bucket, object_name, user_id, db)
            if asset:
                db.flush()
                input_added += 1
                results.append({"input_object": object_name, "status": "input_added", "asset": asset.to_dict()})
            else:
                input_existing += 1

    db.commit()
    return {
        "scanned": len(manifest_objects) + len(input_objects),
        "success": manifest_success + input_added + input_existing,
        "failed": manifest_failed,
        "manifest_scanned": len(manifest_objects),
        "manifest_success": manifest_success,
        "manifest_failed": manifest_failed,
        "input_scanned": len(input_objects),
        "input_added": input_added,
        "input_existing": input_existing,
        "results": results,
    }


@router.get("/review/assets")
def list_review_assets(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str = Query("", description="按标题、material_id 或 manifest 搜索"),
    view: str = Query("", description="审查视角：page、outline、standard 或 compare"),
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    query = db.query(ReviewAsset).filter(ReviewAsset.user_id == user_id)
    if search:
        like = f"%{search}%"
        query = query.filter(
            (ReviewAsset.title.like(like))
            | (ReviewAsset.input_filename.like(like))
            | (ReviewAsset.material_id.like(like))
            | (ReviewAsset.manifest_object.like(like))
        )
    if view == "page":
        query = query.filter(
            ReviewAsset.manifest_json.isnot(None),
            ReviewAsset.manifest_json != "",
            ~ReviewAsset.review_stage.in_(["raw", "clean"]),
        )
        total = query.count()
        assets = query.order_by(ReviewAsset.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "files": [_asset_dict_with_material(asset, db) for asset in assets],
        }
    if view in {"outline", "standard", "compare"}:
        rows = [_asset_dict_with_material(asset, db) for asset in query.order_by(ReviewAsset.created_at.desc()).all()]
        if view == "outline":
            rows = [row for row in rows if row.get("has_raw") or row.get("has_clean") or row.get("has_raw_dry_run")]
        elif view == "standard":
            rows = [row for row in rows if row.get("has_standard")]
        else:
            rows = [row for row in rows if row.get("has_latex")]
        total = len(rows)
        start = (page - 1) * page_size
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "files": rows[start : start + page_size],
        }
    total = query.count()
    assets = query.order_by(ReviewAsset.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "files": [_asset_dict_with_material(asset, db) for asset in assets],
    }


def _final_review_archive_or_error(session: FinalReviewSession, db: Session) -> dict[str, Any]:
    try:
        return final_review_service.export_session_artifacts(session, db)
    except Exception as exc:
        return {"error": str(exc)}


def _final_review_asset_material(session: FinalReviewSession, user_id: str, db: Session) -> tuple[ReviewAsset, Material]:
    asset = _asset_or_404(session.review_asset_id, user_id, db)
    material = final_review_service.require_standard_material(asset, db)
    return asset, material


def _validate_annotation_values(issue_type: str, severity: str, status: str) -> None:
    if issue_type not in final_review_service.ISSUE_TYPES:
        raise HTTPException(status_code=400, detail="不支持的终审问题类型")
    if severity not in final_review_service.SEVERITIES:
        raise HTTPException(status_code=400, detail="不支持的严重程度")
    if status not in final_review_service.ANNOTATION_STATUSES:
        raise HTTPException(status_code=400, detail="不支持的批注状态")


@router.get("/review/final/assets")
def list_final_review_assets(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str = Query("", description="按标题、material_id 或 manifest 搜索"),
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    query = db.query(ReviewAsset).filter(ReviewAsset.user_id == user_id)
    if search:
        like = f"%{search}%"
        query = query.filter(
            (ReviewAsset.title.like(like))
            | (ReviewAsset.input_filename.like(like))
            | (ReviewAsset.material_id.like(like))
            | (ReviewAsset.manifest_object.like(like))
        )
    rows = [_asset_dict_with_material(asset, db) for asset in query.order_by(ReviewAsset.created_at.desc()).all()]
    rows = [row for row in rows if row.get("has_standard")]
    total = len(rows)
    start = (page - 1) * page_size
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "files": rows[start : start + page_size],
        "issue_types": sorted(final_review_service.ISSUE_TYPES),
        "severities": sorted(final_review_service.SEVERITIES),
        "statuses": sorted(final_review_service.ANNOTATION_STATUSES),
    }


@router.post("/review/final/sessions")
def create_final_review_session(
    payload: FinalReviewSessionRequest,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    asset = _asset_or_404(payload.asset_id, user_id, db)
    material = final_review_service.require_standard_material(asset, db)
    if payload.reuse_open:
        existing = (
            db.query(FinalReviewSession)
            .filter(
                FinalReviewSession.user_id == user_id,
                FinalReviewSession.review_asset_id == asset.id,
                FinalReviewSession.standard_run_id == (material.standard_run_id or ""),
                FinalReviewSession.status == "open",
            )
            .order_by(FinalReviewSession.id.desc())
            .first()
        )
        if existing:
            return final_review_service.session_to_dict(existing, db)

    session = FinalReviewSession(
        user_id=user_id,
        review_asset_id=asset.id,
        material_id=material.material_id or asset.material_id or "",
        standard_run_id=material.standard_run_id or "",
        status="open",
        summary_json=final_review_service.json_dumps(
            {
                "title": asset.title,
                "filename": asset.display_filename(),
                "standard_quality_score": material.standard_quality_score,
                "created_from": "standard",
            }
        ),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return final_review_service.session_to_dict(session, db)


@router.get("/review/final/sessions/{session_id}")
def get_final_review_session(
    session_id: int,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    session = final_review_service.session_or_404(session_id, user_id, db)
    return final_review_service.session_to_dict(session, db)


@router.post("/review/final/sessions/{session_id}/annotations")
def create_final_review_annotation(
    session_id: int,
    payload: FinalReviewAnnotationRequest,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    session = final_review_service.session_or_404(session_id, user_id, db)
    _final_review_asset_material(session, user_id, db)
    status = payload.status if payload.status in {"draft", "submitted"} else payload.status
    _validate_annotation_values(payload.issue_type, payload.severity, status)
    if status not in {"draft", "submitted"}:
        raise HTTPException(status_code=400, detail="新建批注只能保存为草稿或提交")
    annotation = FinalReviewAnnotation(
        session_id=session.id,
        user_id=user_id,
        issue_type=payload.issue_type,
        severity=payload.severity,
        status=status,
        human_note=payload.human_note.strip(),
        anchors_json=final_review_service.json_dumps(payload.anchors),
        evidence_json=final_review_service.json_dumps(payload.evidence),
    )
    db.add(annotation)
    db.commit()
    db.refresh(annotation)
    return annotation.to_dict()


@router.patch("/review/final/annotations/{annotation_id}")
def patch_final_review_annotation(
    annotation_id: int,
    payload: FinalReviewAnnotationPatchRequest,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    annotation = final_review_service.annotation_or_404(annotation_id, user_id, db)
    session = final_review_service.session_or_404(annotation.session_id, user_id, db)
    _final_review_asset_material(session, user_id, db)
    if annotation.status in {"project_accepted", "project_rejected", "resolved"}:
        raise HTTPException(status_code=400, detail="该批注已进入项目结论，不能继续编辑")
    issue_type = payload.issue_type if payload.issue_type is not None else annotation.issue_type
    severity = payload.severity if payload.severity is not None else annotation.severity
    status = payload.status if payload.status is not None else annotation.status
    _validate_annotation_values(issue_type, severity, status)
    annotation.issue_type = issue_type
    annotation.severity = severity
    annotation.status = status
    if payload.human_note is not None:
        annotation.human_note = payload.human_note.strip()
    if payload.anchors is not None:
        annotation.anchors_json = final_review_service.json_dumps(payload.anchors)
    if payload.evidence is not None:
        annotation.evidence_json = final_review_service.json_dumps(payload.evidence)
    db.commit()
    db.refresh(annotation)
    return annotation.to_dict()


@router.delete("/review/final/annotations/{annotation_id}")
def delete_final_review_annotation(
    annotation_id: int,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    annotation = final_review_service.annotation_or_404(annotation_id, user_id, db)
    if annotation.status != "draft":
        raise HTTPException(status_code=400, detail="只有草稿批注可以删除")
    db.delete(annotation)
    db.commit()
    return {"status": "deleted", "annotation_id": str(annotation_id)}


@router.post("/review/final/sessions/{session_id}/submit")
def submit_final_review_session(
    session_id: int,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    session = final_review_service.session_or_404(session_id, user_id, db)
    _final_review_asset_material(session, user_id, db)
    annotations = final_review_service.annotations_for_session(session.id, db)
    for annotation in annotations:
        if annotation.status == "draft":
            annotation.status = "submitted"
    session.status = "submitted"
    session.submitted_at = datetime.utcnow()
    db.commit()
    db.refresh(session)
    return {
        "session": final_review_service.session_to_dict(session, db),
        "archive": _final_review_archive_or_error(session, db),
    }


@router.post("/review/final/annotations/{annotation_id}/verify")
def verify_final_review_annotation(
    annotation_id: int,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    annotation = final_review_service.annotation_or_404(annotation_id, user_id, db)
    session = final_review_service.session_or_404(annotation.session_id, user_id, db)
    asset, material = _final_review_asset_material(session, user_id, db)
    verification = final_review_service.verify_annotation(annotation, asset, material, db)
    db.commit()
    db.refresh(verification)
    db.refresh(annotation)
    return {
        "annotation": annotation.to_dict(),
        "verification": verification.to_dict(),
        "archive": _final_review_archive_or_error(session, db),
    }


@router.post("/review/final/sessions/{session_id}/verify")
def verify_final_review_session(
    session_id: int,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    session = final_review_service.session_or_404(session_id, user_id, db)
    asset, material = _final_review_asset_material(session, user_id, db)
    annotations = [row for row in final_review_service.annotations_for_session(session.id, db) if row.status != "draft"]
    verifications = [final_review_service.verify_annotation(annotation, asset, material, db) for annotation in annotations]
    db.commit()
    for verification in verifications:
        db.refresh(verification)
    db.refresh(session)
    return {
        "session": final_review_service.session_to_dict(session, db),
        "verified_count": len(verifications),
        "archive": _final_review_archive_or_error(session, db),
    }


@router.patch("/review/final/annotations/{annotation_id}/decision")
def decide_final_review_annotation(
    annotation_id: int,
    payload: FinalReviewDecisionRequest,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    annotation = final_review_service.annotation_or_404(annotation_id, user_id, db)
    session = final_review_service.session_or_404(annotation.session_id, user_id, db)
    _final_review_asset_material(session, user_id, db)
    decision = final_review_service.add_decision(annotation, payload.decision, payload.reviewer_note, user_id, db)
    db.commit()
    db.refresh(annotation)
    db.refresh(decision)
    return {
        "annotation": annotation.to_dict(),
        "decision": decision.to_dict(),
        "archive": _final_review_archive_or_error(session, db),
    }


@router.get("/review/final/sessions/{session_id}/export")
def export_final_review_session(
    session_id: int,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    session = final_review_service.session_or_404(session_id, user_id, db)
    _final_review_asset_material(session, user_id, db)
    archive = final_review_service.export_session_artifacts(session, db)
    return {
        "session": final_review_service.session_to_dict(session, db),
        "archive": archive,
    }


@router.patch("/review/assets/{asset_id}/metadata")
def update_review_metadata(
    asset_id: int,
    payload: ReviewMetadataRequest,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    asset = _asset_or_404(asset_id, user_id, db)
    if payload.review_status not in REVIEW_STATUSES:
        raise HTTPException(status_code=400, detail="不支持的审查状态")
    tags = [tag.strip() for tag in payload.review_tags if tag.strip()]
    asset.review_status = payload.review_status
    asset.review_tags = json.dumps(tags[:20], ensure_ascii=False)
    asset.review_note = payload.review_note.strip()
    db.commit()
    db.refresh(asset)
    return asset.to_dict()


@router.get("/review/assets/{asset_id}")
def review_asset_detail(
    asset_id: int,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    return _asset_dict_with_material(_asset_or_404(asset_id, user_id, db), db)


@router.get("/review/assets/{asset_id}/latex_compare")
def review_asset_latex_compare(
    asset_id: int,
    output_id: int | None = Query(None, description="material_outputs.id；为空时使用当前默认输出"),
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    asset = _asset_or_404(asset_id, user_id, db)
    material = _material_for_asset(asset, db)
    if not material:
        raise HTTPException(status_code=404, detail="材料不存在")
    registry_rows = sync_material_outputs_for_material(db, user_id, material)
    db.commit()
    selected_registry_row = None
    output = None
    if output_id:
        selected_registry_row = material_output_or_404(db, user_id, output_id, material)
        if not selected_registry_row:
            raise HTTPException(status_code=404, detail="指定 ElegantBook 输出不存在")
        output = output_from_material_output(selected_registry_row, material)
        if not output:
            raise HTTPException(status_code=404, detail="指定 ElegantBook 输出 manifest 不存在")
    else:
        current_rows = [row for row in registry_rows if row.is_current]
        if current_rows:
            selected_registry_row = current_rows[0]
            output = output_from_material_output(selected_registry_row, material)
        if not output:
            output = select_elegantbook_output(material)
    fallback_manifest_ref = _manifest_ref_from_material(material, "latex")
    fallback_manifest = _read_json_optional(fallback_manifest_ref)
    if not output and fallback_manifest_ref and isinstance(fallback_manifest, dict):
        output = output_from_ref(fallback_manifest_ref, material, fallback_manifest)
    if not output:
        raise HTTPException(status_code=404, detail="ElegantBook/LaTeX 产物不存在")
    manifest_ref = output.manifest_ref
    manifest = output.manifest
    paths = output_artifact_paths(output)
    volume_paths = output_artifact_volumes(output)
    compiled_pdf = paths["compiled_pdf"]
    package_zip = paths["package_zip"]
    compile_report = paths["compile_report"]
    if volume_paths:
        compiled_pdf = volume_paths[0]["compiled_pdf"]
        package_zip = volume_paths[0]["package_zip"]
        compile_report = volume_paths[0]["compile_report"] or compile_report
    final_review_report = paths["final_review_report"]
    final_review_report_json = paths["final_review_report_json"]
    render_review = paths["render_review"]
    render_review_json = paths["render_review_json"]
    run_state = paths["run_state"]
    compile_report_ref = _same_prefix_ref(manifest_ref, compile_report)
    artifact_stage = "elegantbook"
    output_query = f"&output_id={selected_registry_row.id}" if selected_registry_row and selected_registry_row.id else ""

    def artifact_url(path: str) -> str:
        return f"/api/review/assets/{asset_id}/artifact?stage={artifact_stage}&path={quote(path)}{output_query}"

    volume_rows = [
        {
            **row,
            "latex_pdf_url": artifact_url(row["compiled_pdf"]),
            "download_urls": {
                "compiled_pdf": artifact_url(row["compiled_pdf"]),
                "package_zip": artifact_url(row["package_zip"]),
                "compile_report": (
                    artifact_url(row["compile_report"])
                    if row["compile_report"]
                    else ""
                ),
            },
        }
        for row in volume_paths
    ]
    return {
        "asset_id": str(asset.id),
        "material_pk": str(material.id),
        "material_id": material.material_id if material else asset.material_id or "",
        "stage": artifact_stage,
        "output_id": str(selected_registry_row.id) if selected_registry_row and selected_registry_row.id else "",
        "output_origin": output.origin,
        "output_run_id": output.output_run_id,
        "available_outputs": [row.to_dict() for row in registry_rows] or ([output.to_dict()] if output else []),
        "manifest": _ref_dict(manifest_ref),
        "manifest_json": manifest,
        "compile_report": _read_json_optional(compile_report_ref) or {},
        "source_pdf_url": f"/api/review/assets/{asset_id}/review_pdf",
        "source_pdf_original_url": f"/api/review/assets/{asset_id}/content",
        "latex_pdf_url": artifact_url(compiled_pdf),
        "volumes": volume_rows,
        "download_urls": {
            "compiled_pdf": artifact_url(compiled_pdf),
            "package_zip": artifact_url(package_zip),
            "compile_report": artifact_url(compile_report),
            "latex_polish_report": artifact_url(paths["latex_polish_report"]),
            "latex_polish_report_json": artifact_url(paths["latex_polish_report_json"]),
            "final_review_report": artifact_url(final_review_report),
            "final_review_report_json": artifact_url(final_review_report_json),
            "render_review": artifact_url(render_review),
            "render_review_json": artifact_url(render_review_json),
            "run_state": artifact_url(run_state),
        },
    }


def _manual_latex_workspace_gone() -> None:
    raise HTTPException(
        status_code=410,
        detail="LuceonWeb 不再提供内置 LaTeX 工作区或 Overleaf 接入；请在 PDF 比对页下载 ElegantBook ZIP 后外部编辑",
    )


@router.get("/review/public/overleaf/{asset_id}/package.zip")
def download_public_overleaf_package(asset_id: int, token: str = Query("")):
    _ = (asset_id, token)
    _manual_latex_workspace_gone()


@router.get("/review/assets/{asset_id}/overleaf")
@router.post("/review/assets/{asset_id}/overleaf/start")
@router.put("/review/assets/{asset_id}/overleaf/project")
@router.get("/review/assets/{asset_id}/overleaf/package.zip")
@router.post("/review/assets/{asset_id}/overleaf/revisions/import")
def review_asset_overleaf_workflow_gone(asset_id: int):
    _ = asset_id
    _manual_latex_workspace_gone()


@router.get("/review/assets/{asset_id}/overleaf/revisions/{revision_id}/artifact")
def review_asset_overleaf_revision_artifact_gone(asset_id: int, revision_id: str, kind: str = Query("compiled_pdf")):
    _ = (asset_id, revision_id, kind)
    _manual_latex_workspace_gone()


@router.get("/review/assets/{asset_id}/latex_workspace")
@router.get("/review/assets/{asset_id}/latex_workspace/file")
@router.put("/review/assets/{asset_id}/latex_workspace/file")
@router.post("/review/assets/{asset_id}/latex_workspace/compile")
@router.get("/review/assets/{asset_id}/latex_workspace/artifact")
def review_asset_latex_workspace_gone(asset_id: int):
    _ = asset_id
    _manual_latex_workspace_gone()


@router.post("/review/assets/{asset_id}/report")
def generate_review_report(
    asset_id: int,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    asset = _asset_or_404(asset_id, user_id, db)
    try:
        source_map = _source_map_for_asset(asset, db)
    except HTTPException:
        source_map = {"pages": []}
    asset.report_text = _build_review_report(asset, source_map)
    asset.report_generated_at = datetime.utcnow()
    db.commit()
    db.refresh(asset)
    return {"report": asset.report_text, "asset": asset.to_dict()}


@router.get("/review/assets/{asset_id}/report")
def download_review_report(
    asset_id: int,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    asset = _asset_or_404(asset_id, user_id, db)
    report = asset.report_text or _build_review_report(asset)
    filename = quote(f"{asset.title}-review-report.md")
    return PlainTextResponse(
        report,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@router.get("/review/assets/{asset_id}/content")
def review_asset_content(
    request: Request,
    asset_id: int,
    user_id: str = Depends(get_asset_download_user_id),
    db: Session = Depends(get_db),
):
    asset = _asset_or_404(asset_id, user_id, db)
    ref = _source_pdf_ref_for_asset(asset)
    if not ref:
        raise HTTPException(status_code=404, detail="源 PDF 不存在")
    stat = _stat_ref(ref, "源 PDF 不存在")
    size = int(getattr(stat, "size", 0) or 0)
    media_type = getattr(stat, "content_type", None) or mimetypes.guess_type(asset.title)[0] or "application/pdf"
    encoded_filename = quote(asset.to_dict()["filename"])
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": f"inline; filename*=UTF-8''{encoded_filename}",
        "Content-Encoding": "identity",
        **_http_cache_headers(ref, stat),
    }
    if _is_not_modified(request, headers):
        return Response(status_code=304, headers=headers)
    range_value = _parse_range_header(_effective_range_header(request, headers), size)
    if range_value:
        start, end = range_value
        content = _read_ref_range(ref, start, end - start + 1, "源 PDF 不存在")
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        headers["Content-Length"] = str(len(content))
        return Response(
            content,
            status_code=206,
            media_type=media_type,
            headers=headers,
        )

    headers["Content-Length"] = str(size)
    return StreamingResponse(_stream_ref(ref, "源 PDF 不存在"), media_type=media_type, headers=headers)


@router.get("/review/assets/{asset_id}/review_pdf")
def review_asset_review_pdf(
    request: Request,
    asset_id: int,
    user_id: str = Depends(get_asset_download_user_id),
    db: Session = Depends(get_db),
):
    asset = _asset_or_404(asset_id, user_id, db)
    ref = _source_pdf_ref_for_asset(asset)
    if not ref:
        raise HTTPException(status_code=404, detail="源 PDF 不存在")
    source_stat = _stat_ref(ref, "源 PDF 不存在")
    source_size = int(getattr(source_stat, "size", 0) or 0)
    if source_size < REVIEW_PDF_MIN_SOURCE_BYTES:
        response = review_asset_content(request, asset_id, user_id, db)
        response.headers["X-Review-PDF"] = "original-small"
        return response

    cache_key = _review_pdf_cache_key(ref, source_stat)
    cache_path = REVIEW_PDF_CACHE_ROOT / f"{cache_key}.pdf"
    skip_path = cache_path.with_suffix(".skip")
    try:
        source_pdf = b"" if cache_path.exists() or skip_path.exists() else _read_ref(ref, "源 PDF 不存在")
        preview = ensure_review_pdf_preview_isolated(
            source_pdf,
            cache_path,
            source_size=source_size,
            dpi=REVIEW_PDF_DPI,
            jpeg_quality=REVIEW_PDF_JPEG_QUALITY,
            max_edge=REVIEW_PDF_MAX_EDGE,
        )
    except Exception as exc:
        logger.warning("review PDF preview failed for %s/%s: %s", ref.bucket, ref.object, exc)
        preview = None

    if not preview:
        response = review_asset_content(request, asset_id, user_id, db)
        response.headers["X-Review-PDF"] = "original-fallback"
        return response

    size = preview.size
    modified_at = datetime.fromtimestamp(preview.path.stat().st_mtime, tz=timezone.utc)
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(Path(asset.to_dict()['filename']).stem + '-review.pdf')}",
        "Content-Encoding": "identity",
        "Cache-Control": "private, max-age=3600, must-revalidate",
        "ETag": f'"{cache_key}"',
        "Last-Modified": format_datetime(modified_at, usegmt=True),
        "X-Review-PDF": "compressed",
        "X-Review-PDF-Linearized": "1" if preview.linearized else "0",
        "X-Review-PDF-Pages": str(preview.page_count),
        "X-Review-PDF-Original-Length": str(source_size),
    }
    if _is_not_modified(request, headers):
        return Response(status_code=304, headers=headers)
    range_value = _parse_range_header(_effective_range_header(request, headers), size)
    if range_value:
        start, end = range_value
        content = _read_file_range(preview.path, start, end - start + 1)
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        headers["Content-Length"] = str(len(content))
        return Response(content, status_code=206, media_type="application/pdf", headers=headers)

    headers["Content-Length"] = str(size)
    return StreamingResponse(_stream_file(preview.path), media_type="application/pdf", headers=headers)


@router.get("/review/assets/{asset_id}/page_image")
def review_asset_page_image(
    asset_id: int,
    page: int = Query(1, ge=1, description="PDF 页码，从 1 开始"),
    width: int = Query(1200, ge=480, le=1800, description="渲染图片宽度"),
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    asset = _asset_or_404(asset_id, user_id, db)
    ref = _source_pdf_ref_for_asset(asset)
    if not ref:
        raise HTTPException(status_code=404, detail="源 PDF 不存在")
    stat = _stat_ref(ref, "源 PDF 不存在")
    cache_key = _pdf_cache_key(ref, stat, page, width)
    cache_path = PDF_PAGE_CACHE_ROOT / f"{cache_key}.png"
    headers = {
        "Cache-Control": "private, max-age=86400",
        "X-PDF-Page": str(page),
        "X-PDF-Image-Width": str(width),
    }
    if cache_path.exists():
        return Response(cache_path.read_bytes(), media_type="image/png", headers={**headers, "X-PDF-Page-Cache": "hit"})

    pdf_bytes = _read_ref(ref, "源 PDF 不存在")
    png_bytes, page_count = _render_pdf_page_png(pdf_bytes, page, width)
    PDF_PAGE_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_suffix(".tmp")
    tmp_path.write_bytes(png_bytes)
    tmp_path.replace(cache_path)
    return Response(
        png_bytes,
        media_type="image/png",
        headers={**headers, "X-PDF-Page-Cache": "miss", "X-PDF-Page-Count": str(page_count)},
    )


@router.get("/review/assets/{asset_id}/download_url")
def review_asset_download_url(
    asset_id: int,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    asset = _asset_or_404(asset_id, user_id, db)
    ref = _source_pdf_ref_for_asset(asset)
    if not ref:
        raise HTTPException(status_code=404, detail="源 PDF 不存在")
    return {"url": presigned(ref)}


def _artifact_base_refs(asset: ReviewAsset, stage: str, db: Session, user_id: str, output_id: int | None = None) -> list[ObjectRef]:
    normalized_stage = stage.lower().strip()
    refs: list[ObjectRef | None] = []
    if normalized_stage == "mineru":
        refs.extend(
            [
                _ref(asset.markdown_bucket, asset.markdown_object),
                _ref(asset.page_markdown_bucket, asset.page_markdown_object),
                _ref(asset.middle_json_bucket, asset.middle_json_object),
            ]
        )
        material = _material_for_asset(asset, db)
        refs.append(_manifest_ref_from_material(material, "mineru"))
    elif normalized_stage in {"popo", "minerupopo"}:
        refs.extend(
            [
                _ref(asset.popo_markdown_bucket, asset.popo_markdown_object),
                _manifest_ref_for_asset(asset),
            ]
        )
    elif normalized_stage in {"raw", "clean", "standard", "latex"}:
        material = _material_for_asset(asset, db)
        manifest_ref = _manifest_ref_from_material(material, normalized_stage)
        manifest = _read_json_optional(manifest_ref)
        refs.extend(
            [
                _stage_markdown_ref(manifest_ref, manifest),
                manifest_ref,
            ]
        )
    elif normalized_stage == "elegantbook":
        material = _material_for_asset(asset, db)
        output = None
        if material and output_id:
            row = material_output_or_404(db, user_id, output_id, material)
            if not row:
                raise HTTPException(status_code=404, detail="指定 ElegantBook 输出不存在")
            output = output_from_material_output(row, material)
        if not output:
            output = select_elegantbook_output(material) if material else None
        if output:
            refs.append(output.manifest_ref)
        else:
            refs.append(_manifest_ref_from_material(material, "latex"))
    else:
        raise HTTPException(status_code=400, detail="不支持的资源阶段")
    return [ref for ref in refs if ref]


def _rewrite_standard_html_artifact(content: bytes, asset_id: int, stage: str) -> bytes:
    if stage.lower().strip() != "standard":
        return content
    try:
        html_text = content.decode("utf-8")
    except UnicodeDecodeError:
        return content

    def replace_attr(match: re.Match[str]) -> str:
        attr = match.group(1)
        quote_char = match.group(2)
        raw_path = match.group(3).strip()
        if not raw_path or raw_path.startswith(("#", "/", "http://", "https://", "data:", "blob:", "mailto:")):
            return match.group(0)
        proxied = f"/api/review/assets/{asset_id}/artifact?stage=standard&path={quote(clean_path(raw_path))}"
        return f"{attr}={quote_char}{proxied}{quote_char}"

    rewritten = re.sub(r'\b(src|href)=(["\'])(?!#|/|https?://|data:|blob:|mailto:)([^"\']+)\2', replace_attr, html_text)
    return rewritten.encode("utf-8")


@router.get("/review/assets/{asset_id}/artifact")
def review_asset_artifact(
    request: Request,
    asset_id: int,
    stage: str = Query("mineru", description="资源阶段：mineru、popo、elegantbook、latex、raw、clean 或 standard"),
    path: str = Query(..., description="相对资源路径，如 images/a.jpg"),
    output_id: int | None = Query(None, description="material_outputs.id；仅 elegantbook 阶段使用"),
    user_id: str = Depends(get_asset_download_user_id),
    db: Session = Depends(get_db),
):
    asset = _asset_or_404(asset_id, user_id, db)
    relative_path = path.replace("\\", "/").strip()
    if not relative_path or relative_path.startswith("/") or "://" in relative_path:
        raise HTTPException(status_code=400, detail="资源路径必须是相对路径")
    if any(part == ".." for part in relative_path.split("/")):
        raise HTTPException(status_code=400, detail="资源路径不允许跳出阶段目录")
    relative_path = clean_path(relative_path)

    if stage.lower().strip() == "raw":
        material = _material_for_asset(asset, db)
        dry_run = latest_successful_popo_to_raw_dry_run(db, user_id, material.material_id if material else "")
        dry_summary = dry_run.summary() if dry_run else {}
        dry_body_final = Path(str(dry_summary.get("body_final") or "")) if dry_summary else None
        if dry_body_final and dry_body_final.exists():
            candidate = (dry_body_final / relative_path).resolve()
            try:
                candidate.relative_to(dry_body_final.resolve())
            except ValueError:
                raise HTTPException(status_code=400, detail="资源路径不允许跳出 Raw 预演目录")
            if candidate.exists() and candidate.is_file():
                media_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
                return StreamingResponse(iter([candidate.read_bytes()]), media_type=media_type)

    for base_ref in _artifact_base_refs(asset, stage, db, user_id, output_id):
        prefix = base_ref.object.rsplit("/", 1)[0] + "/" if "/" in base_ref.object else ""
        object_name = clean_path(f"{prefix}{relative_path}")
        if object_exists(base_ref.bucket, object_name):
            ref = ObjectRef(base_ref.bucket, object_name)
            media_type = mimetypes.guess_type(object_name)[0] or "application/octet-stream"
            stat = _stat_ref(ref, "资源文件不存在")
            size = int(getattr(stat, "size", 0) or 0)
            headers = {
                "Accept-Ranges": "bytes",
                "Content-Disposition": f"inline; filename*=UTF-8''{quote(Path(object_name).name)}",
                **_http_cache_headers(ref, stat),
            }
            if _skip_http_compression(media_type):
                headers["Content-Encoding"] = "identity"
            if _is_not_modified(request, headers):
                return Response(status_code=304, headers=headers)
            range_value = _parse_range_header(_effective_range_header(request, headers), size)
            if range_value:
                start, end = range_value
                content = _read_ref_range(ref, start, end - start + 1, "资源文件不存在")
                headers["Content-Range"] = f"bytes {start}-{end}/{size}"
                headers["Content-Length"] = str(len(content))
                return Response(content, status_code=206, media_type=media_type, headers=headers)

            headers["Content-Length"] = str(size)
            if media_type == "text/html":
                content = read_object(ref.bucket, ref.object)
                content = _rewrite_standard_html_artifact(content, asset_id, stage)
                headers["Content-Length"] = str(len(content))
                return StreamingResponse(iter([content]), media_type=media_type, headers=headers)
            return StreamingResponse(_stream_ref(ref, "资源文件不存在"), media_type=media_type, headers=headers)
    raise HTTPException(status_code=404, detail="资源文件不存在")


@router.get("/review/assets/{asset_id}/parsed_content")
def review_asset_parsed_content(
    asset_id: int,
    variant: str = Query("markdown_page", description="markdown、markdown_page、popo_page 或 popo"),
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    asset = _asset_or_404(asset_id, user_id, db)
    popo_ref = _ref(asset.popo_markdown_bucket, asset.popo_markdown_object)
    if variant == "markdown_page":
        page_ref = _ref(asset.page_markdown_bucket, asset.page_markdown_object)
        if page_ref:
            return _read_ref(page_ref, "按页 Markdown 不存在").decode("utf-8", errors="replace")
        content_list = _content_list_for_asset(asset, db)
        content = synthesize_page_markdown_from_content_list(content_list)
        if content:
            return content
        try:
            return synthesize_page_markdown(_source_map_for_asset(asset, db))
        except HTTPException as exc:
            if exc.status_code != 404 or not popo_ref:
                raise
            return _read_ref(popo_ref, "Popo Markdown 不存在").decode("utf-8", errors="replace")
    if variant == "markdown":
        markdown_ref = _ref(asset.markdown_bucket, asset.markdown_object)
        if not markdown_ref:
            material = _material_for_asset(asset, db)
            mineru_manifest_ref = _manifest_ref_from_material(material, "mineru")
            mineru_manifest = _read_json_optional(mineru_manifest_ref)
            markdown_ref = _stage_markdown_ref(mineru_manifest_ref, mineru_manifest)
        return _read_ref(markdown_ref, "MinerU Markdown 不存在").decode("utf-8", errors="replace")
    if variant == "popo_page":
        popo_raw_ref = _popo_raw_ref_for_asset(asset)
        popo_raw = _json_value_ref(popo_raw_ref)
        image_lookup = _content_list_image_lookup(_content_list_for_asset(asset, db))
        content = _render_popo_page_markdown(popo_raw, image_lookup=image_lookup)
        if not content:
            raise HTTPException(status_code=404, detail="Popo 按页结果不可用")
        return content
    if variant == "popo":
        return _read_ref(popo_ref, "Popo Markdown 不存在").decode("utf-8", errors="replace")
    raise HTTPException(status_code=400, detail="不支持的 Markdown 变体")


@router.get("/review/assets/{asset_id}/source_map")
def review_asset_source_map(
    asset_id: int,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    asset = _asset_or_404(asset_id, user_id, db)
    try:
        return _source_map_for_asset(asset, db)
    except HTTPException as exc:
        if exc.status_code == 404:
            return {"pages": []}
        raise


@router.get("/review/assets/{asset_id}/outline_review")
def review_asset_outline_review(
    asset_id: int,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    asset = _asset_or_404(asset_id, user_id, db)
    try:
        source_map = _source_map_for_asset(asset, db)
    except HTTPException:
        source_map = {"pages": []}

    document_tree = None
    if asset.manifest_json:
        try:
            manifest = json.loads(asset.manifest_json)
        except json.JSONDecodeError:
            manifest = {}
        objects = manifest.get("objects") if isinstance(manifest, dict) and isinstance(manifest.get("objects"), dict) else {}
        document_tree_ref = None
        value = objects.get("document_tree") if isinstance(objects, dict) else None
        if isinstance(value, dict):
            document_tree_ref = _ref(str(value.get("bucket") or ""), str(value.get("object") or ""))
        if document_tree_ref:
            try:
                document_tree = _json_ref(document_tree_ref)
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise

    material = _material_for_asset(asset, db)
    raw_manifest_ref = _manifest_ref_from_material(material, "raw")
    clean_manifest_ref = _manifest_ref_from_material(material, "clean")
    clean_manifest = _read_json_optional(clean_manifest_ref)
    clean_markdown_ref = _stage_markdown_ref(clean_manifest_ref, clean_manifest)
    dry_run = latest_successful_popo_to_raw_dry_run(db, user_id, material.material_id if material else "")
    dry_summary = dry_run.summary() if dry_run else {}
    dry_body_final = Path(str(dry_summary.get("body_final") or "")) if dry_summary else None
    use_dry_run = bool(dry_body_final and dry_body_final.exists())

    if use_dry_run:
        raw_manifest = _read_local_json_optional(dry_body_final / "manifest.json")
        raw_markdown_ref = None
        raw_markdown_text = _read_local_text_optional(dry_body_final / "clean.md")
        popo_outline = _read_local_json_optional(dry_body_final / "popo_outline.json")
        outline_candidates_ref = None
        outline_candidates = _read_local_jsonl_optional(dry_body_final / "outline_candidates.jsonl")
        outline_candidates_summary = _read_local_json_optional(dry_body_final / "outline_candidates_summary.json")
        outline_decision = _read_local_json_optional(dry_body_final / "outline_decision.json")
        visual_decisions = _read_local_json_optional(dry_body_final / "visual_decisions.json")
        chunk_boundary_report = _read_local_json_optional(dry_body_final / "chunk_boundary_report.json")
        outline_apply_report = _read_local_json_optional(dry_body_final / "outline_apply_report.json")
        image_closure_report = _read_local_json_optional(dry_body_final / "image_closure_report.json")
        raw_stage_refs = {
            "raw_manifest": _local_ref_dict(dry_body_final / "manifest.json"),
            "raw_markdown": _local_ref_dict(dry_body_final / "clean.md"),
            "popo_outline": _local_ref_dict(dry_body_final / "popo_outline.json"),
            "outline_candidates": _local_ref_dict(dry_body_final / "outline_candidates.jsonl"),
            "outline_candidates_summary": _local_ref_dict(dry_body_final / "outline_candidates_summary.json"),
            "outline_decision": _local_ref_dict(dry_body_final / "outline_decision.json"),
            "visual_decisions": _local_ref_dict(dry_body_final / "visual_decisions.json"),
            "chunk_boundary_report": _local_ref_dict(dry_body_final / "chunk_boundary_report.json"),
            "raw_block_assignments": _local_ref_dict(dry_body_final / "raw_block_assignments.jsonl"),
            "unassigned_blocks": _local_ref_dict(dry_body_final / "unassigned_blocks.jsonl"),
            "outline_apply_report": _local_ref_dict(dry_body_final / "outline_apply_report.json"),
            "image_closure_report": _local_ref_dict(dry_body_final / "image_closure_report.json"),
        }
    else:
        raw_manifest = _read_json_optional(raw_manifest_ref)
        raw_markdown_ref = _stage_markdown_ref(raw_manifest_ref, raw_manifest)
        raw_markdown_text = _read_text_optional(raw_markdown_ref)
        popo_outline = _read_json_optional(_same_prefix_ref(raw_manifest_ref, "popo_outline.json"))
        outline_candidates_ref = _same_prefix_ref(raw_manifest_ref, "outline_candidates.jsonl")
        outline_candidates = _read_jsonl_optional(outline_candidates_ref)
        outline_candidates_summary = _read_json_optional(_same_prefix_ref(raw_manifest_ref, "outline_candidates_summary.json"))
        outline_decision = _read_json_optional(_same_prefix_ref(raw_manifest_ref, "outline_decision.json"))
        visual_decisions = _read_json_optional(_same_prefix_ref(raw_manifest_ref, "visual_decisions.json"))
        chunk_boundary_report = _read_json_optional(_same_prefix_ref(raw_manifest_ref, "chunk_boundary_report.json"))
        outline_apply_report = _read_json_optional(_same_prefix_ref(raw_manifest_ref, "outline_apply_report.json"))
        image_closure_report = _read_json_optional(_same_prefix_ref(raw_manifest_ref, "image_closure_report.json"))
        raw_stage_refs = {
            "raw_manifest": _ref_dict(raw_manifest_ref),
            "raw_markdown": _ref_dict(raw_markdown_ref),
            "popo_outline": _ref_dict(_same_prefix_ref(raw_manifest_ref, "popo_outline.json")),
            "outline_candidates": _ref_dict(outline_candidates_ref),
            "outline_candidates_summary": _ref_dict(_same_prefix_ref(raw_manifest_ref, "outline_candidates_summary.json")),
            "outline_decision": _ref_dict(_same_prefix_ref(raw_manifest_ref, "outline_decision.json")),
            "visual_decisions": _ref_dict(_same_prefix_ref(raw_manifest_ref, "visual_decisions.json")),
            "chunk_boundary_report": _ref_dict(_same_prefix_ref(raw_manifest_ref, "chunk_boundary_report.json")),
            "raw_block_assignments": _ref_dict(_same_prefix_ref(raw_manifest_ref, "raw_block_assignments.jsonl")),
            "unassigned_blocks": _ref_dict(_same_prefix_ref(raw_manifest_ref, "unassigned_blocks.jsonl")),
            "outline_apply_report": _ref_dict(_same_prefix_ref(raw_manifest_ref, "outline_apply_report.json")),
            "image_closure_report": _ref_dict(_same_prefix_ref(raw_manifest_ref, "image_closure_report.json")),
        }

    result = build_outline_review(
        source_map,
        document_tree,
        raw_manifest=raw_manifest,
        raw_markdown=raw_markdown_text,
        clean_manifest=clean_manifest,
        clean_markdown=_read_text_optional(clean_markdown_ref),
        popo_outline=popo_outline,
        outline_candidates=outline_candidates,
        outline_candidates_summary=outline_candidates_summary,
        outline_decision=outline_decision,
        visual_decisions=visual_decisions,
        chunk_boundary_report=chunk_boundary_report,
        outline_apply_report=outline_apply_report,
        image_closure_report=image_closure_report,
        stage_refs={
            **raw_stage_refs,
            "clean_manifest": _ref_dict(clean_manifest_ref),
            "clean_markdown": _ref_dict(clean_markdown_ref),
        },
        material={
            "id": str(material.id) if material else "",
            "material_id": material.material_id or "" if material else "",
            "stage_status": material.stage_status or "" if material else "",
            "title": material.title or "" if material else "",
            "filename": material.filename or "" if material else "",
            "raw_review_source": "dry_run" if use_dry_run else "published_raw",
            "raw_dry_run_id": str(dry_run.id) if dry_run else "",
        },
    )
    result["standard_navigation"] = _standard_navigation_for_material(material)
    return result


@router.get("/review/assets/{asset_id}/popo/status")
def review_asset_popo_status(
    asset_id: int,
    user_id: str = Depends(get_user_id),
    db: Session = Depends(get_db),
):
    asset = _asset_or_404(asset_id, user_id, db)
    if asset.popo_markdown_bucket and asset.popo_markdown_object:
        return {"status": "success", "message": ""}
    return {"status": "not_available", "message": "Popo Markdown 不存在"}
