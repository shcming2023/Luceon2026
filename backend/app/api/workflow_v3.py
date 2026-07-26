from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tarfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.material import Material, MaterialOutput
from app.models.review_asset import ReviewAsset
from app.models.user import User
from app.services.codex_elegantbook import (
    output_artifact_paths,
    output_artifact_volumes,
)
from app.services.luceon_review import clean_path, read_object
from app.services.material_outputs import output_from_material_output
from app.utils.user_dep import get_user_id, require_pipeline_admin
from app.workflow_v3.contracts import WORKFLOW_VERSION, stage_contracts
from app.workflow_v3.database import (
    get_workflow_v3_db,
    initialize_workflow_v3_database,
    workflow_v3_session_factory,
)
from app.workflow_v3.models import WorkflowV3Job, WorkflowV3SkillRelease
from app.workflow_v3.operations import operational_snapshot
from app.workflow_v3.release import ReleaseValidationError, verify_release_directory
from app.workflow_v3.service import (
    ProjectionRetryConflictError,
    ProjectionRetryNotFoundError,
    create_workflow_job,
    list_skill_releases,
    list_workflow_jobs,
    register_skill_release,
    retry_projection_outbox,
    runtime_identity_for_manifest,
    workflow_job_detail,
)
from app.workflow_v3.state_machine import (
    WorkflowV3TransitionError,
    apply_review_resolution,
    cancel_job,
    record_human_acceptance,
    retry_failed_stage,
)


router = APIRouter(prefix="/workflow-v3")

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RELEASE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,127}$")
_INPUT_BUCKET = "eduassets-input"
_MINERU_MANIFEST_SCHEMA = "luceon-gpu-wrapper-mineru-only-manifest/v1"
_POPO_MANIFEST_SCHEMA = "luceon-gpu-wrapper-popo-from-frozen-mineru-manifest/v1"
_MAX_CANDIDATE_DOWNLOAD_BUNDLE_BYTES = 256_000_000


class WorkflowV3JobSource(BaseModel):
    material_pk: int = Field(gt=0)
    popo_manifest_sha256: str = Field(min_length=64, max_length=64)


class WorkflowV3BatchCreateRequest(BaseModel):
    sources: list[WorkflowV3JobSource] = Field(min_length=1, max_length=100)
    skill_release_version: str = Field(min_length=1, max_length=64)
    skill_release_sha256: str = Field(min_length=64, max_length=64)
    priority: int = Field(default=100, ge=0, le=1000)
    payload: dict = Field(default_factory=dict)


class WorkflowV3HumanAcceptanceRequest(BaseModel):
    accepted: bool
    output_id: int = Field(gt=0)
    manifest_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    reason: str = Field(default="", max_length=4000)


class WorkflowV3CancelRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class WorkflowV3ArtifactIdentity(BaseModel):
    model_config = {"extra": "forbid"}

    bucket: str = Field(min_length=1, max_length=128)
    object: str = Field(min_length=1, max_length=1024)
    sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    size_bytes: int = Field(gt=0)


class WorkflowV3ReviewResolutionRequest(BaseModel):
    model_config = {"extra": "forbid"}

    idempotency_key: str = Field(min_length=8, max_length=128)
    resolution_manifest: WorkflowV3ArtifactIdentity


class WorkflowV3InstalledReleaseRequest(BaseModel):
    release_id: str = Field(min_length=3, max_length=128)


@dataclass(frozen=True)
class FrozenPopoSnapshot:
    bucket: str
    object_name: str
    sha256: str
    material_id: str
    run_id: str
    marker_bucket: str
    marker_object: str
    marker_sha256: str
    source_pdf_bucket: str
    source_pdf_object: str
    source_pdf_sha256: str
    source_pdf_size_bytes: int
    mineru_run_id: str
    mineru_manifest_bucket: str
    mineru_manifest_object: str
    mineru_manifest_sha256: str
    mineru_manifest_size_bytes: int
    mineru_marker_bucket: str
    mineru_marker_object: str
    mineru_marker_sha256: str
    mineru_marker_size_bytes: int
    mineru_archive_bucket: str
    mineru_archive_object: str
    mineru_archive_sha256: str
    mineru_archive_size_bytes: int
    popo_manifest_size_bytes: int
    popo_marker_size_bytes: int
    popo_archive_bucket: str
    popo_archive_object: str
    popo_archive_sha256: str
    popo_archive_size_bytes: int

    def evidence(self) -> dict:
        artifacts = [
            _artifact_evidence(
                "source_pdf",
                "source-pdf",
                self.source_pdf_bucket,
                self.source_pdf_object,
                self.source_pdf_sha256,
                self.source_pdf_size_bytes,
            ),
            _artifact_evidence(
                "mineru_manifest",
                "mineru-manifest",
                self.mineru_manifest_bucket,
                self.mineru_manifest_object,
                self.mineru_manifest_sha256,
                self.mineru_manifest_size_bytes,
            ),
            _artifact_evidence(
                "mineru_frozen_marker",
                "frozen-marker",
                self.mineru_marker_bucket,
                self.mineru_marker_object,
                self.mineru_marker_sha256,
                self.mineru_marker_size_bytes,
            ),
            _artifact_evidence(
                "mineru_archive",
                "mineru-archive",
                self.mineru_archive_bucket,
                self.mineru_archive_object,
                self.mineru_archive_sha256,
                self.mineru_archive_size_bytes,
            ),
            _artifact_evidence(
                "frozen_source",
                "popo-manifest",
                self.bucket,
                self.object_name,
                self.sha256,
                self.popo_manifest_size_bytes,
            ),
            _artifact_evidence(
                "popo_frozen_marker",
                "frozen-marker",
                self.marker_bucket,
                self.marker_object,
                self.marker_sha256,
                self.popo_marker_size_bytes,
            ),
            _artifact_evidence(
                "popo_archive",
                "popo-archive",
                self.popo_archive_bucket,
                self.popo_archive_object,
                self.popo_archive_sha256,
                self.popo_archive_size_bytes,
            ),
        ]
        evidence = {
            "popo_manifest": {
                "bucket": self.bucket,
                "object": self.object_name,
                "sha256": self.sha256,
                "size_bytes": self.popo_manifest_size_bytes,
            },
            "popo_frozen_marker": {
                "bucket": self.marker_bucket,
                "object": self.marker_object,
                "sha256": self.marker_sha256,
                "size_bytes": self.popo_marker_size_bytes,
            },
            "material_id": self.material_id,
            "run_id": self.run_id,
            "stage_run_ids": {
                "mineru": self.mineru_run_id,
                "popo": self.run_id,
            },
            "source_pdf": {
                "bucket": self.source_pdf_bucket,
                "object": self.source_pdf_object,
                "sha256": self.source_pdf_sha256,
                "size_bytes": self.source_pdf_size_bytes,
            },
            "mineru_manifest": {
                "bucket": self.mineru_manifest_bucket,
                "object": self.mineru_manifest_object,
                "sha256": self.mineru_manifest_sha256,
                "size_bytes": self.mineru_manifest_size_bytes,
            },
            "mineru_frozen_marker": {
                "bucket": self.mineru_marker_bucket,
                "object": self.mineru_marker_object,
                "sha256": self.mineru_marker_sha256,
                "size_bytes": self.mineru_marker_size_bytes,
            },
            "artifacts": artifacts,
            "verified_at": datetime.utcnow().isoformat() + "Z",
        }
        evidence["input_set_sha256"] = _sha256_bytes(
            json.dumps(
                artifacts,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        return evidence


class FrozenPopoValidationError(ValueError):
    pass


def workflow_v3_feature_enabled() -> bool:
    return os.getenv("WORKFLOW_V3_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def require_workflow_v3_enabled() -> None:
    if not workflow_v3_feature_enabled():
        raise HTTPException(status_code=503, detail="Worker V3 尚未启用")


def workflow_v3_db_dependency():
    status = initialize_workflow_v3_database()
    if not status.get("ready"):
        raise HTTPException(
            status_code=503,
            detail=f"Worker V3 数据库未就绪：{status.get('detail') or 'schema unavailable'}",
        )
    try:
        yield from get_workflow_v3_db()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _normalized_sha256(value: str, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise FrozenPopoValidationError(f"{field} 不是有效的 SHA-256")
    return normalized


def _json_object(payload: bytes, label: str) -> dict:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FrozenPopoValidationError(f"{label} 不是有效的 JSON") from exc
    if not isinstance(value, dict):
        raise FrozenPopoValidationError(f"{label} 必须是 JSON 对象")
    return value


def _read_bound_object(identity: dict, label: str) -> bytes:
    bucket = str(identity.get("bucket") or "").strip()
    object_name = clean_path(identity.get("object") or "")
    sha256 = _normalized_sha256(
        str(identity.get("sha256") or ""),
        f"{label}.sha256",
    )
    size_bytes = _positive_size(identity.get("size_bytes"), label)
    if not bucket or not object_name:
        raise FrozenPopoValidationError(f"{label} 没有完整对象身份")
    try:
        payload = read_object(bucket, object_name)
    except Exception as exc:
        raise FrozenPopoValidationError(f"{label} 无法读取：{exc}") from exc
    if len(payload) != size_bytes:
        raise FrozenPopoValidationError(f"{label} size_bytes 已漂移")
    if _sha256_bytes(payload) != sha256:
        raise FrozenPopoValidationError(f"{label} SHA-256 已漂移")
    return payload


def _manifest_run_id(manifest: dict, object_name: str) -> str:
    explicit = str(manifest.get("run_id") or "").strip()
    parts = clean_path(object_name).split("/")
    inferred = parts[-2] if len(parts) >= 4 and parts[-1] == "manifest.json" else ""
    return explicit or inferred


def _artifact_evidence(
    role: str,
    kind: str,
    bucket: str,
    object_name: str,
    sha256: str,
    size_bytes: int,
) -> dict:
    return {
        "role": role,
        "kind": kind,
        "bucket": bucket,
        "object": object_name,
        "sha256": sha256,
        "size_bytes": size_bytes,
        "read_only": True,
    }


def _positive_size(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise FrozenPopoValidationError(f"{label} 缺少有效的 size_bytes")
    return value


def _manifest_object(
    manifest: dict,
    *,
    key: str,
    label: str,
) -> tuple[str, str, str, int]:
    objects = manifest.get("objects")
    raw = objects.get(key) if isinstance(objects, dict) else None
    if not isinstance(raw, dict):
        raise FrozenPopoValidationError(f"{label} 没有完整对象身份")
    bucket = str(raw.get("bucket") or "").strip()
    object_name = clean_path(raw.get("object") or "")
    sha256 = _normalized_sha256(str(raw.get("sha256") or ""), f"{label}.sha256")
    size_bytes = _positive_size(raw.get("size_bytes"), label)
    if not bucket or not object_name:
        raise FrozenPopoValidationError(f"{label} 没有完整对象身份")
    return bucket, object_name, sha256, size_bytes


def _verify_frozen_marker(
    *,
    material_id: str,
    run_id: str,
    status: str,
    manifest_bucket: str,
    manifest_object: str,
    manifest_sha256: str,
    source_pdf_sha256: str,
    source_pdf_size_bytes: int,
) -> tuple[str, str, str, int, dict]:
    marker_bucket = _INPUT_BUCKET
    marker_object = f"_status/{material_id}/{run_id}.{status}.json"
    try:
        marker_bytes = read_object(marker_bucket, marker_object)
    except Exception as exc:
        raise FrozenPopoValidationError(f"没有可验证的 {status} 标记") from exc
    marker = _json_object(marker_bytes, f"{status} 标记")
    if marker.get("schema") != "luceon-input-status-marker/v1":
        raise FrozenPopoValidationError(f"{status} 标记 schema 不受支持")
    if str(marker.get("status") or "") != status:
        raise FrozenPopoValidationError(f"{status} 标记状态不一致")
    if str(marker.get("material_id") or "") != material_id:
        raise FrozenPopoValidationError(f"{status} 标记的 material_id 与材料不一致")
    if str(marker.get("run_id") or "") != run_id:
        raise FrozenPopoValidationError(f"{status} 标记的 run_id 与 manifest 不一致")
    marker_manifest = marker.get("manifest")
    if not isinstance(marker_manifest, dict):
        raise FrozenPopoValidationError(f"{status} 标记没有 manifest 身份")
    marker_manifest_sha = _normalized_sha256(
        str(marker_manifest.get("sha256") or ""),
        f"{status}.manifest.sha256",
    )
    if (
        str(marker_manifest.get("bucket") or "") != manifest_bucket
        or clean_path(marker_manifest.get("object") or "") != manifest_object
        or marker_manifest_sha != manifest_sha256
    ):
        raise FrozenPopoValidationError(f"{status} 标记没有指向当前 manifest 字节")
    if _positive_size(marker_manifest.get("size_bytes"), f"{status}.manifest") <= 0:
        raise FrozenPopoValidationError(f"{status} 标记缺少 manifest 大小")
    marker_source_sha = _normalized_sha256(
        str(marker.get("source_pdf_sha256") or ""),
        f"{status}.source_pdf_sha256",
    )
    if marker_source_sha != source_pdf_sha256:
        raise FrozenPopoValidationError(f"{status} 标记的源 PDF SHA 与材料不一致")
    if _positive_size(marker.get("source_pdf_size_bytes"), f"{status}.source_pdf") != source_pdf_size_bytes:
        raise FrozenPopoValidationError(f"{status} 标记的源 PDF 大小与材料不一致")
    return (
        marker_bucket,
        marker_object,
        _sha256_bytes(marker_bytes),
        len(marker_bytes),
        marker,
    )


def verify_frozen_popo_manifest(
    material: Material,
    *,
    expected_sha256: str,
) -> FrozenPopoSnapshot:
    """Bind a job to one complete, immutable PDF → MinerU → Popo input set."""

    expected = _normalized_sha256(expected_sha256, "popo_manifest_sha256")
    material_id = str(material.material_id or "").strip()
    popo_bucket = str(material.popo_manifest_bucket or "").strip()
    popo_object = clean_path(material.popo_manifest_object or "")
    mineru_bucket = str(material.mineru_manifest_bucket or "").strip()
    mineru_object = clean_path(material.mineru_manifest_object or "")
    if not material_id or not popo_bucket or not popo_object:
        raise FrozenPopoValidationError("材料没有完整的 Popo manifest 身份")
    if not mineru_bucket or not mineru_object:
        raise FrozenPopoValidationError("材料没有完整的 MinerU manifest 身份")

    try:
        popo_manifest_bytes = read_object(popo_bucket, popo_object)
    except Exception as exc:
        raise FrozenPopoValidationError("无法读取 Popo manifest") from exc
    popo_manifest_sha = _sha256_bytes(popo_manifest_bytes)
    if popo_manifest_sha != expected:
        raise FrozenPopoValidationError(
            f"Popo manifest 已漂移：期望 {expected}，实际 {popo_manifest_sha}"
        )
    popo_manifest = _json_object(popo_manifest_bytes, "Popo manifest")
    if popo_manifest.get("schema") != _POPO_MANIFEST_SCHEMA:
        raise FrozenPopoValidationError("Popo manifest schema 不受 Worker V3 支持")
    if str(popo_manifest.get("material_id") or "").strip() != material_id:
        raise FrozenPopoValidationError("Popo manifest 的 material_id 与材料不一致")

    popo_run_id = _manifest_run_id(popo_manifest, popo_object)
    persisted_popo_run_id = str(material.popo_run_id or "").strip()
    if not popo_run_id:
        raise FrozenPopoValidationError("Popo manifest 缺少 run_id")
    if persisted_popo_run_id and persisted_popo_run_id != popo_run_id:
        raise FrozenPopoValidationError("Popo manifest 的 run_id 与材料不一致")

    popo_source = (
        popo_manifest.get("source_pdf")
        if isinstance(popo_manifest.get("source_pdf"), dict)
        else {}
    )
    persisted_source_sha = _normalized_sha256(
        str(material.input_sha256 or material.source_hash or ""),
        "material.source_pdf_sha256",
    )
    source_pdf_bucket = str(material.input_bucket or _INPUT_BUCKET).strip()
    source_pdf_object = clean_path(material.input_object or "")
    if not source_pdf_bucket or not source_pdf_object:
        raise FrozenPopoValidationError("材料没有完整的源 PDF 对象身份")
    source_pdf_size = _positive_size(material.size_bytes, "material.source_pdf")
    if (
        _normalized_sha256(
            str(popo_source.get("sha256") or ""),
            "popo.source_pdf.sha256",
        )
        != persisted_source_sha
        or str(popo_source.get("input_bucket") or "") != source_pdf_bucket
        or clean_path(popo_source.get("input_object") or "") != source_pdf_object
        or _positive_size(popo_source.get("size_bytes"), "popo.source_pdf")
        != source_pdf_size
    ):
        raise FrozenPopoValidationError("Popo manifest 的源 PDF 身份与材料不一致")

    upstream = (
        popo_manifest.get("upstream_mineru")
        if isinstance(popo_manifest.get("upstream_mineru"), dict)
        else {}
    )
    upstream_manifest = (
        upstream.get("manifest")
        if isinstance(upstream.get("manifest"), dict)
        else {}
    )
    mineru_run_id = str(upstream.get("run_id") or "").strip()
    persisted_mineru_run_id = str(material.mineru_run_id or "").strip()
    if (
        str(upstream_manifest.get("bucket") or "") != mineru_bucket
        or clean_path(upstream_manifest.get("object") or "") != mineru_object
        or not mineru_run_id
        or (persisted_mineru_run_id and persisted_mineru_run_id != mineru_run_id)
    ):
        raise FrozenPopoValidationError("Popo manifest 的上游 MinerU 身份与材料不一致")

    try:
        mineru_manifest_bytes = read_object(mineru_bucket, mineru_object)
    except Exception as exc:
        raise FrozenPopoValidationError("无法读取 MinerU manifest") from exc
    mineru_manifest_sha = _sha256_bytes(mineru_manifest_bytes)
    mineru_manifest = _json_object(mineru_manifest_bytes, "MinerU manifest")
    if (
        mineru_manifest.get("schema") != _MINERU_MANIFEST_SCHEMA
        or str(mineru_manifest.get("status") or "") != "mineru_done_frozen"
        or str(mineru_manifest.get("material_id") or "") != material_id
        or _manifest_run_id(mineru_manifest, mineru_object) != mineru_run_id
    ):
        raise FrozenPopoValidationError("MinerU manifest 的冻结身份或 lineage 不一致")
    mineru_source = (
        mineru_manifest.get("source_pdf")
        if isinstance(mineru_manifest.get("source_pdf"), dict)
        else {}
    )
    if (
        _normalized_sha256(
            str(mineru_source.get("sha256") or ""),
            "mineru.source_pdf.sha256",
        )
        != persisted_source_sha
        or str(mineru_source.get("input_bucket") or "") != source_pdf_bucket
        or clean_path(mineru_source.get("input_object") or "") != source_pdf_object
        or _positive_size(mineru_source.get("size_bytes"), "mineru.source_pdf")
        != source_pdf_size
    ):
        raise FrozenPopoValidationError("MinerU manifest 的源 PDF 身份与材料不一致")

    (
        mineru_archive_bucket,
        mineru_archive_object,
        mineru_archive_sha,
        mineru_archive_size,
    ) = _manifest_object(
        mineru_manifest,
        key="archive",
        label="MinerU archive",
    )
    (
        popo_archive_bucket,
        popo_archive_object,
        popo_archive_sha,
        popo_archive_size,
    ) = _manifest_object(
        popo_manifest,
        key="archive",
        label="Popo archive",
    )

    (
        mineru_marker_bucket,
        mineru_marker_object,
        mineru_marker_sha,
        mineru_marker_size,
        _mineru_marker,
    ) = _verify_frozen_marker(
        material_id=material_id,
        run_id=mineru_run_id,
        status="mineru_done_frozen",
        manifest_bucket=mineru_bucket,
        manifest_object=mineru_object,
        manifest_sha256=mineru_manifest_sha,
        source_pdf_sha256=persisted_source_sha,
        source_pdf_size_bytes=source_pdf_size,
    )
    (
        popo_marker_bucket,
        popo_marker_object,
        popo_marker_sha,
        popo_marker_size,
        popo_marker,
    ) = _verify_frozen_marker(
        material_id=material_id,
        run_id=popo_run_id,
        status="popo_done_frozen",
        manifest_bucket=popo_bucket,
        manifest_object=popo_object,
        manifest_sha256=popo_manifest_sha,
        source_pdf_sha256=persisted_source_sha,
        source_pdf_size_bytes=source_pdf_size,
    )
    marker_upstream = (
        popo_marker.get("mineru_manifest")
        if isinstance(popo_marker.get("mineru_manifest"), dict)
        else {}
    )
    if (
        str(marker_upstream.get("bucket") or "") != mineru_bucket
        or clean_path(marker_upstream.get("object") or "") != mineru_object
        or str(popo_marker.get("upstream_mineru_run_id") or "") != mineru_run_id
    ):
        raise FrozenPopoValidationError("Popo 冻结标记的上游 MinerU lineage 不完整")

    return FrozenPopoSnapshot(
        bucket=popo_bucket,
        object_name=popo_object,
        sha256=popo_manifest_sha,
        material_id=material_id,
        run_id=popo_run_id,
        marker_bucket=popo_marker_bucket,
        marker_object=popo_marker_object,
        marker_sha256=popo_marker_sha,
        source_pdf_bucket=source_pdf_bucket,
        source_pdf_object=source_pdf_object,
        source_pdf_sha256=persisted_source_sha,
        source_pdf_size_bytes=source_pdf_size,
        mineru_run_id=mineru_run_id,
        mineru_manifest_bucket=mineru_bucket,
        mineru_manifest_object=mineru_object,
        mineru_manifest_sha256=mineru_manifest_sha,
        mineru_manifest_size_bytes=len(mineru_manifest_bytes),
        mineru_marker_bucket=mineru_marker_bucket,
        mineru_marker_object=mineru_marker_object,
        mineru_marker_sha256=mineru_marker_sha,
        mineru_marker_size_bytes=mineru_marker_size,
        mineru_archive_bucket=mineru_archive_bucket,
        mineru_archive_object=mineru_archive_object,
        mineru_archive_sha256=mineru_archive_sha,
        mineru_archive_size_bytes=mineru_archive_size,
        popo_manifest_size_bytes=len(popo_manifest_bytes),
        popo_marker_size_bytes=popo_marker_size,
        popo_archive_bucket=popo_archive_bucket,
        popo_archive_object=popo_archive_object,
        popo_archive_sha256=popo_archive_sha,
        popo_archive_size_bytes=popo_archive_size,
    )


def frozen_review_asset_evidence(
    db: Session,
    material: Material,
    snapshot: FrozenPopoSnapshot,
) -> dict[str, Any]:
    review_asset_id = material.review_asset_id
    if not isinstance(review_asset_id, int) or review_asset_id <= 0:
        raise FrozenPopoValidationError("材料没有冻结的 exact Popo ReviewAsset")
    review = (
        db.query(ReviewAsset)
        .filter(
            ReviewAsset.id == review_asset_id,
            ReviewAsset.user_id == material.user_id,
            ReviewAsset.material_id == snapshot.material_id,
            ReviewAsset.run_id == snapshot.run_id,
            ReviewAsset.manifest_bucket == snapshot.bucket,
            ReviewAsset.manifest_object == snapshot.object_name,
        )
        .one_or_none()
    )
    if review is None:
        raise FrozenPopoValidationError(
            "材料的 ReviewAsset 与冻结 Popo manifest 身份不一致"
        )
    return {
        "id": str(review.id),
        "bucket": review.manifest_bucket,
        "object": review.manifest_object,
        "sha256": snapshot.sha256,
    }


def _owned_job(workflow_db: Session, public_id: str, user_id: str) -> dict:
    detail = workflow_job_detail(workflow_db, public_id)
    if not detail or str(detail.get("user_id") or "") != user_id:
        raise HTTPException(status_code=404, detail="Worker V3 任务不存在")
    return detail


def _delivery_artifact_url(
    *,
    review_asset_id: str,
    output_id: str,
    path: str,
) -> str:
    return (
        f"/api/review/assets/{review_asset_id}/artifact"
        f"?stage=elegantbook&path={quote(path, safe='')}&output_id={output_id}"
    )


def _exact_projected_output(
    detail: dict,
    material_db: Session,
) -> tuple[Material, MaterialOutput] | None:
    output_id = str(detail.get("final_output_id") or "")
    source_identity = detail.get("source_identity")
    review_asset_id = (
        str(source_identity.get("review_asset_id") or "")
        if isinstance(source_identity, dict)
        else ""
    )
    final_projection = next(
        (
            row
            for row in detail.get("projection_outbox", [])
            if row.get("event_kind") == "final_ready"
            and row.get("status") == "applied"
            and str(row.get("projected_output_id") or "") == output_id
        ),
        None,
    )
    if (
        not output_id.isdigit()
        or not review_asset_id.isdigit()
        or not isinstance(final_projection, dict)
    ):
        return None
    manifest = final_projection.get("projected_manifest")
    if not isinstance(manifest, dict):
        return None
    material = (
        material_db.query(Material)
        .filter(
            Material.id == int(detail["material_pk"]),
            Material.user_id == detail["user_id"],
            Material.material_id == detail["material_id"],
        )
        .one_or_none()
    )
    output = (
        material_db.query(MaterialOutput)
        .filter(
            MaterialOutput.id == int(output_id),
            MaterialOutput.user_id == detail["user_id"],
            MaterialOutput.material_pk == int(detail["material_pk"]),
            MaterialOutput.material_id == detail["material_id"],
            MaterialOutput.review_asset_id == int(review_asset_id),
            MaterialOutput.origin == "worker_v3",
            MaterialOutput.manifest_bucket == manifest.get("bucket"),
            MaterialOutput.manifest_object == manifest.get("object"),
        )
        .one_or_none()
    )
    if material is None or output is None:
        return None
    if (
        output.metadata_dict().get("manifest_sha256")
        != manifest.get("sha256")
    ):
        return None
    return material, output


def _formal_delivery_assets(
    detail: dict,
    material_db: Session,
    *,
    include_files: bool,
) -> dict:
    exact = _exact_projected_output(detail, material_db)
    if exact is None:
        return {
            "available": False,
            "output_id": str(detail.get("final_output_id") or ""),
            "registry_status": "",
            "formalized": False,
            "volumes": [],
        }
    material, row = exact
    output_id = str(row.id)
    review_asset_id = str(row.review_asset_id)
    result = {
        "available": True,
        "output_id": output_id,
        "registry_status": row.status,
        "quality_status": row.quality_status,
        "formalized": row.status in {"promoted", "published"},
        "manifest": {
            "bucket": row.manifest_bucket,
            "object": row.manifest_object,
            "sha256": row.metadata_dict().get("manifest_sha256", ""),
        },
        "volumes": [],
    }
    if not include_files:
        return result
    output = output_from_material_output(row, material)
    if output is None:
        return {**result, "available": False, "error": "正式输出 manifest 不可读"}
    volume_paths = output_artifact_volumes(output)
    if not volume_paths:
        paths = output_artifact_paths(output)
        volume_paths = [
            {
                "volume_id": "volume-1",
                "label": "第 1 卷",
                "compiled_pdf": paths.get("compiled_pdf", ""),
                "package_zip": paths.get("package_zip", ""),
            }
        ]
    result["volumes"] = [
        {
            "volume_id": str(volume.get("volume_id") or f"volume-{index}"),
            "label": str(volume.get("label") or f"第 {index} 卷"),
            "pdf_url": _delivery_artifact_url(
                review_asset_id=review_asset_id,
                output_id=output_id,
                path=str(volume.get("compiled_pdf") or ""),
            ),
            "zip_url": _delivery_artifact_url(
                review_asset_id=review_asset_id,
                output_id=output_id,
                path=str(volume.get("package_zip") or ""),
            ),
        }
        for index, volume in enumerate(volume_paths, start=1)
        if volume.get("compiled_pdf") and volume.get("package_zip")
    ]
    return result


def _candidate_bundle(detail: dict) -> tuple[bytes, dict, dict[str, dict]]:
    stages = [
        row
        for row in detail.get("stages", [])
        if row.get("stage_key") == "ready_for_user_acceptance"
        and row.get("machine_status") == "succeeded"
        and row.get("spec_status") == "passed"
    ]
    stages.sort(
        key=lambda row: (
            int(row.get("generation") or 0),
            int(row.get("attempt") or 0),
            int(row.get("id") or 0),
        ),
        reverse=True,
    )
    stage = stages[0] if stages else None
    promoted_id = (
        str((stage.get("promotion") or {}).get("candidate_id") or "")
        if isinstance(stage, dict)
        else ""
    )
    candidates = stage.get("candidates", []) if isinstance(stage, dict) else []
    candidate = next(
        (
            row
            for row in candidates
            if str(row.get("id") or "") == promoted_id
            and row.get("status") == "promoted"
            and row.get("artifact_kind")
            == "worker-v3-ready-for-user-acceptance-candidate"
        ),
        None,
    )
    if not isinstance(candidate, dict):
        raise ValueError("最终晋级候选不存在")
    payload = read_object(
        str(candidate.get("bucket") or ""),
        clean_path(str(candidate.get("object") or "")),
    )
    if len(payload) > _MAX_CANDIDATE_DOWNLOAD_BUNDLE_BYTES:
        raise ValueError("最终候选包超过 UI 下载读取上限")
    if len(payload) != int(candidate.get("size_bytes") or -1):
        raise ValueError("最终候选包大小与冻结证据不一致")
    if _sha256_bytes(payload) != candidate.get("sha256"):
        raise ValueError("最终候选包 SHA 与冻结证据不一致")
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            content = _candidate_json_member(
                archive,
                "candidate-content-manifest.json",
            )
            recompile = _candidate_json_member(
                archive,
                "manifests/delivery_recompile.json",
            )
    except (tarfile.TarError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError("最终候选包交付证据不可读") from exc
    if (
        content.get("job_id") != detail.get("id")
        or content.get("stage_key") != "ready_for_user_acceptance"
        or content.get("artifact_kind")
        != "worker-v3-ready-for-user-acceptance-candidate"
    ):
        raise ValueError("最终候选包身份与任务不一致")
    inventory = {
        str(row.get("path") or ""): row
        for row in content.get("files", [])
        if isinstance(row, dict) and row.get("path")
    }
    return payload, recompile, inventory


def _candidate_json_member(archive: tarfile.TarFile, name: str) -> dict:
    member = _unique_candidate_member(archive, name)
    if not member.isfile() or member.size > 8_000_000:
        raise ValueError(f"候选证据 {name} 不是可读 JSON 文件")
    extracted = archive.extractfile(member)
    if extracted is None:
        raise ValueError(f"候选证据 {name} 不可读")
    value = json.loads(extracted.read())
    if not isinstance(value, dict):
        raise ValueError(f"候选证据 {name} 必须是对象")
    return value


def _unique_candidate_member(
    archive: tarfile.TarFile,
    name: str,
) -> tarfile.TarInfo:
    matches = [member for member in archive.getmembers() if member.name == name]
    if len(matches) != 1:
        raise ValueError(f"候选包文件 {name} 必须且只能出现一次")
    return matches[0]


def _candidate_delivery_rows(detail: dict) -> list[dict]:
    _payload, recompile, inventory = _candidate_bundle(detail)
    volumes = recompile.get("volumes")
    if not isinstance(volumes, list):
        raise ValueError("最终候选包没有分卷复编证据")
    rows: list[dict] = []
    for index, volume in enumerate(volumes, start=1):
        if not isinstance(volume, dict):
            raise ValueError("最终候选包分卷证据无效")
        volume_id = str(volume.get("volume_id") or "")
        if not volume_id:
            raise ValueError("最终候选包分卷 ID 缺失")
        bindings = {
            "zip": volume.get("delivery_zip"),
            "pdf": volume.get("reviewed_pdf"),
        }
        for kind, binding in bindings.items():
            _validated_candidate_binding(binding, inventory)
        base = f"/api/workflow-v3/jobs/{detail['id']}/candidate-delivery"
        rows.append(
            {
                "volume_id": volume_id,
                "label": f"第 {index} 卷",
                "zip_url": f"{base}?kind=zip&volume_id={quote(volume_id, safe='')}",
                "pdf_url": f"{base}?kind=pdf&volume_id={quote(volume_id, safe='')}",
            }
        )
    return rows


def _validated_candidate_binding(
    binding: Any,
    inventory: dict[str, dict],
) -> tuple[str, str, int]:
    if not isinstance(binding, dict):
        raise ValueError("最终候选包文件绑定缺失")
    path = clean_path(str(binding.get("path") or ""))
    sha256 = str(binding.get("sha256") or "")
    size_bytes = int(binding.get("size_bytes") or 0)
    inventory_row = inventory.get(path)
    if (
        not path
        or not _SHA256_RE.fullmatch(sha256)
        or size_bytes <= 0
        or not isinstance(inventory_row, dict)
        or inventory_row.get("sha256") != sha256
        or int(inventory_row.get("size_bytes") or 0) != size_bytes
    ):
        raise ValueError("最终候选包文件绑定与内容清单不一致")
    return path, sha256, size_bytes


def _enrich_job_for_ui(
    detail: dict,
    material_db: Session,
    *,
    include_downloads: bool,
) -> dict:
    identity = detail.get("source_identity")
    identity_verified = (
        isinstance(identity, dict) and identity.get("verified") is True
    )
    projected = _formal_delivery_assets(
        detail,
        material_db,
        include_files=include_downloads,
    )
    output_id = str(projected.get("output_id") or "")
    review_asset_id = (
        str(identity.get("review_asset_id") or "")
        if isinstance(identity, dict)
        else ""
    )
    exact_review_entry = (
        identity_verified
        and projected.get("available") is True
        and output_id.isdigit()
        and review_asset_id.isdigit()
    )
    candidate = {"available": False, "volumes": []}
    if include_downloads:
        try:
            candidate_rows = _candidate_delivery_rows(detail)
            candidate = {"available": bool(candidate_rows), "volumes": candidate_rows}
        except (OSError, ValueError):
            candidate = {"available": False, "volumes": []}
    formal = {
        **projected,
        "volumes": (
            projected.get("volumes", [])
            if projected.get("formalized") is True
            else []
        ),
    }
    projected_candidate = {
        **projected,
        "volumes": (
            projected.get("volumes", [])
            if projected.get("available") is True
            and projected.get("formalized") is not True
            else []
        ),
    }
    return {
        **detail,
        "review_entry": {
            "available": exact_review_entry,
            "review_asset_id": review_asset_id if exact_review_entry else "",
            "final_output_id": output_id if exact_review_entry else "",
            "compare_url": (
                f"/review/compare?asset_id={review_asset_id}&output_id={output_id}"
                if exact_review_entry
                else ""
            ),
            "compare_api_url": (
                f"/api/review/assets/{review_asset_id}/latex_compare"
                f"?output_id={output_id}"
                if exact_review_entry
                else ""
            ),
        },
        "delivery_assets": {
            "candidate": candidate,
            "projected_candidate": projected_candidate,
            "formal": formal,
        },
    }


def _active_release(
    workflow_db: Session,
    *,
    release_version: str,
    manifest_sha256: str,
) -> WorkflowV3SkillRelease:
    digest = str(manifest_sha256 or "").strip().lower()
    release = (
        workflow_db.query(WorkflowV3SkillRelease)
        .filter(
            WorkflowV3SkillRelease.release_version == release_version,
            WorkflowV3SkillRelease.manifest_sha256 == digest,
            WorkflowV3SkillRelease.status == "registered",
        )
        .first()
    )
    if not release:
        raise HTTPException(status_code=404, detail="指定的 Worker V3 技能发行版不存在或已停用")
    return release


@router.get("/health")
def workflow_v3_health(user_id: str = Depends(get_user_id)):
    _ = user_id
    enabled = workflow_v3_feature_enabled()
    database = initialize_workflow_v3_database()
    control_plane_ready = enabled and bool(database.get("ready"))
    operations = None
    if control_plane_ready:
        workflow_db = workflow_v3_session_factory()()
        try:
            operations = operational_snapshot(workflow_db)
        finally:
            workflow_db.close()
    execution_enabled = bool(
        control_plane_ready
        and operations
        and operations.get("execution_enabled")
    )
    detail = str(database.get("detail") or "")
    if not enabled:
        detail = "Worker V3 feature flag is disabled"
    elif not control_plane_ready:
        detail = f"Worker V3 control plane is not ready: {detail}"
    elif execution_enabled:
        detail = "Worker V3 control plane and execution plane are ready"
    else:
        blockers = ", ".join(operations.get("blockers") or [])
        detail = f"Worker V3 control plane is ready; execution is blocked: {blockers}"
    return {
        "workflow_version": WORKFLOW_VERSION,
        "configured": bool(os.getenv("WORKFLOW_V3_DATABASE_URL", "").strip()),
        "enabled": enabled,
        "ready": control_plane_ready,
        "detail": detail,
        "execution_enabled": execution_enabled,
        "database": database,
        "operations": operations,
        "schema_auto_create": False,
        "producer_evaluator_promotion_separated": True,
    }


@router.get("/contracts")
def workflow_v3_contracts(user_id: str = Depends(get_user_id)):
    _ = user_id
    return {
        "workflow_version": WORKFLOW_VERSION,
        "enabled": workflow_v3_feature_enabled(),
        "stages": stage_contracts(),
        "status_domains": {
            "machine_status": [
                "queued",
                "running",
                "needs_review",
                "failed",
                "cancelled",
                "succeeded",
            ],
            "spec_status": [
                "not_evaluated",
                "in_progress",
                "needs_review",
                "failed",
                "passed",
            ],
            "readiness_status": ["not_ready", "ready"],
            "human_acceptance_status": ["pending", "accepted", "rejected"],
        },
    }


@router.get("/releases")
def releases(
    include_retired: bool = False,
    _enabled: None = Depends(require_workflow_v3_enabled),
    user_id: str = Depends(get_user_id),
    workflow_db: Session = Depends(workflow_v3_db_dependency),
):
    _ = user_id
    return {"items": list_skill_releases(workflow_db, include_retired=include_retired)}


@router.get("/sources/eligible")
def eligible_sources(
    search: str = Query(default="", max_length=200),
    limit: int = Query(default=100, ge=1, le=200),
    _enabled: None = Depends(require_workflow_v3_enabled),
    user_id: str = Depends(get_user_id),
    material_db: Session = Depends(get_db),
):
    query = material_db.query(Material).filter(
        Material.user_id == user_id,
        Material.ignored.is_(False),
        Material.popo_manifest_bucket.isnot(None),
        Material.popo_manifest_object.isnot(None),
    )
    normalized_search = search.strip()
    if normalized_search:
        like = f"%{normalized_search}%"
        query = query.filter(
            (Material.filename.ilike(like))
            | (Material.material_id.ilike(like))
            | (Material.title.ilike(like))
        )
    rows = query.order_by(Material.updated_at.desc(), Material.id.desc()).limit(limit).all()
    result: list[dict] = []
    for material in rows:
        try:
            object_name = clean_path(material.popo_manifest_object or "")
            manifest_bytes = read_object(str(material.popo_manifest_bucket or ""), object_name)
            snapshot = verify_frozen_popo_manifest(
                material,
                expected_sha256=_sha256_bytes(manifest_bytes),
            )
            result.append(
                {
                    "material_pk": str(material.id),
                    "material_id": snapshot.material_id,
                    "filename": material.filename,
                    "size_bytes": int(material.size_bytes or 0),
                    "page_count": int(material.page_count or 0),
                    "mineru_run_id": snapshot.mineru_run_id,
                    "mineru_manifest_sha256": snapshot.mineru_manifest_sha256,
                    "mineru_frozen_marker_sha256": snapshot.mineru_marker_sha256,
                    "popo_run_id": snapshot.run_id,
                    "popo_manifest_sha256": snapshot.sha256,
                    "popo_frozen_marker_sha256": snapshot.marker_sha256,
                    "source_pdf_sha256": snapshot.source_pdf_sha256,
                    "input_set_sha256": snapshot.evidence()["input_set_sha256"],
                    "eligible": True,
                    "error": "",
                }
            )
        except FrozenPopoValidationError as exc:
            result.append(
                {
                    "material_pk": str(material.id),
                    "material_id": material.material_id or "",
                    "filename": material.filename,
                    "size_bytes": int(material.size_bytes or 0),
                    "page_count": int(material.page_count or 0),
                    "mineru_run_id": material.mineru_run_id or "",
                    "mineru_manifest_sha256": "",
                    "mineru_frozen_marker_sha256": "",
                    "popo_run_id": material.popo_run_id or "",
                    "popo_manifest_sha256": "",
                    "popo_frozen_marker_sha256": "",
                    "source_pdf_sha256": str(material.input_sha256 or material.source_hash or ""),
                    "input_set_sha256": "",
                    "eligible": False,
                    "error": str(exc),
                }
            )
    return {"items": result}


@router.post("/admin/releases/register-installed")
def register_installed_release(
    payload: WorkflowV3InstalledReleaseRequest,
    _enabled: None = Depends(require_workflow_v3_enabled),
    admin: User = Depends(require_pipeline_admin),
    workflow_db: Session = Depends(workflow_v3_db_dependency),
):
    """Register only a server-installed, fully reverified release.

    The request names a release ID. Manifest and package identities come from
    operator-controlled files, never from request JSON.
    """

    release_id = payload.release_id.strip()
    if not _RELEASE_ID_RE.fullmatch(release_id):
        raise HTTPException(status_code=422, detail="release_id 格式无效")
    root_value = os.getenv("WORKFLOW_V3_RELEASE_ROOT", "").strip()
    registry_value = os.getenv("WORKFLOW_V3_RELEASE_REGISTRY_FILE", "").strip()
    if not root_value or not registry_value:
        raise HTTPException(status_code=503, detail="Worker V3 发行版注册目录尚未配置")
    root = Path(root_value).expanduser().resolve()
    installed = (root / release_id).resolve()
    if installed.parent != root:
        raise HTTPException(status_code=422, detail="release_id 不能越出发行版目录")
    try:
        registry = json.loads(Path(registry_value).read_text(encoding="utf-8"))
        records = registry.get("releases") if isinstance(registry, dict) else None
        record = records.get(release_id) if isinstance(records, dict) else None
        if not isinstance(record, dict):
            raise ValueError("release is absent from the server registry")
        package = record.get("package") if isinstance(record.get("package"), dict) else {}
        package_bucket = str(package.get("bucket") or "")
        package_object = clean_path(package.get("object") or "")
        package_sha256 = str(package.get("sha256") or "").lower()
        if (
            not package_bucket
            or not package_object
            or not _SHA256_RE.fullmatch(package_sha256)
        ):
            raise ValueError("registry package identity is incomplete")
        verified = verify_release_directory(installed)
        manifest = verified.manifest
        if manifest.get("release_id") != release_id:
            raise ValueError("installed release ID does not match the registry key")
        manifest_sha256 = _sha256_bytes(
            (installed / "release-manifest.json").read_bytes()
        )
        release, created = register_skill_release(
            workflow_db,
            release_version=str(manifest.get("version") or ""),
            manifest_sha256=manifest_sha256,
            package_bucket=package_bucket,
            package_object=package_object,
            package_sha256=package_sha256,
            workflow_version=WORKFLOW_VERSION,
            template_sha256=str((manifest.get("template") or {}).get("tree_sha256") or ""),
            runtime_identity_sha256=runtime_identity_for_manifest(manifest),
            manifest=manifest,
            registered_by=str(admin.email or admin.id),
        )
        workflow_db.commit()
    except (OSError, json.JSONDecodeError, ValueError, ReleaseValidationError) as exc:
        workflow_db.rollback()
        raise HTTPException(status_code=409, detail=f"发行版验证或注册失败：{exc}") from exc
    return {"created": created, "release": release.to_dict()}


@router.post("/jobs/batch")
def create_jobs_batch(
    payload: WorkflowV3BatchCreateRequest,
    _enabled: None = Depends(require_workflow_v3_enabled),
    user_id: str = Depends(get_user_id),
    material_db: Session = Depends(get_db),
    workflow_db: Session = Depends(workflow_v3_db_dependency),
):
    source_pks = [row.material_pk for row in payload.sources]
    if len(set(source_pks)) != len(source_pks):
        raise HTTPException(status_code=422, detail="同一材料不能在一个批次中重复提交")
    release = _active_release(
        workflow_db,
        release_version=payload.skill_release_version,
        manifest_sha256=payload.skill_release_sha256,
    )
    materials = (
        material_db.query(Material)
        .filter(
            Material.user_id == user_id,
            Material.id.in_(source_pks),
            Material.ignored.is_(False),
        )
        .all()
    )
    by_id = {int(row.id): row for row in materials}
    results: list[dict] = []
    for source in payload.sources:
        material = by_id.get(source.material_pk)
        if not material:
            results.append(
                {
                    "material_pk": str(source.material_pk),
                    "status": "failed",
                    "error_code": "material_not_found",
                    "error": "材料不存在或无权访问",
                }
            )
            continue
        try:
            snapshot = verify_frozen_popo_manifest(
                material,
                expected_sha256=source.popo_manifest_sha256,
            )
            source_evidence = snapshot.evidence()
            source_evidence["filename"] = material.filename
            source_evidence["material_pk"] = str(material.id)
            source_evidence["review_asset"] = frozen_review_asset_evidence(
                material_db,
                material,
                snapshot,
            )
            job, created = create_workflow_job(
                workflow_db,
                user_id=user_id,
                material_pk=int(material.id),
                material_id=snapshot.material_id,
                source_popo_bucket=snapshot.bucket,
                source_popo_object=snapshot.object_name,
                source_popo_sha256=snapshot.sha256,
                skill_release_version=release.release_version,
                skill_release_sha256=release.manifest_sha256,
                template_sha256=release.template_sha256,
                workflow_version=release.workflow_version,
                payload={
                    **payload.payload,
                    "source_evidence": source_evidence,
                    "submission_path": "public_ui",
                },
                priority=payload.priority,
            )
            workflow_db.commit()
            results.append(
                {
                    "material_pk": str(material.id),
                    "material_id": snapshot.material_id,
                    "status": "created" if created else "existing",
                    "job": workflow_job_detail(workflow_db, job.public_id),
                }
            )
        except (FrozenPopoValidationError, ValueError) as exc:
            workflow_db.rollback()
            results.append(
                {
                    "material_pk": str(material.id),
                    "material_id": material.material_id or "",
                    "status": "failed",
                    "error_code": "popo_source_not_frozen_or_drifted",
                    "error": str(exc),
                }
            )
    return {
        "total": len(results),
        "created": sum(row["status"] == "created" for row in results),
        "existing": sum(row["status"] == "existing" for row in results),
        "failed": sum(row["status"] == "failed" for row in results),
        "results": results,
    }


@router.get("/jobs")
def jobs(
    material_pk: int | None = Query(default=None, gt=0),
    machine_status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    _enabled: None = Depends(require_workflow_v3_enabled),
    user_id: str = Depends(get_user_id),
    material_db: Session = Depends(get_db),
    workflow_db: Session = Depends(workflow_v3_db_dependency),
):
    try:
        items, total = list_workflow_jobs(
            workflow_db,
            user_id=user_id,
            material_pk=material_pk,
            machine_status=machine_status,
            offset=(page - 1) * page_size,
            limit=page_size,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "items": [
            _enrich_job_for_ui(
                item,
                material_db,
                include_downloads=False,
            )
            for item in items
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/jobs/{public_id}")
def job_detail(
    public_id: str,
    _enabled: None = Depends(require_workflow_v3_enabled),
    user_id: str = Depends(get_user_id),
    material_db: Session = Depends(get_db),
    workflow_db: Session = Depends(workflow_v3_db_dependency),
):
    detail = _owned_job(workflow_db, public_id, user_id)
    return _enrich_job_for_ui(
        detail,
        material_db,
        include_downloads=True,
    )


@router.get("/jobs/{public_id}/candidate-delivery")
def candidate_delivery(
    public_id: str,
    kind: str = Query(pattern=r"^(zip|pdf)$"),
    volume_id: str = Query(min_length=1, max_length=128),
    _enabled: None = Depends(require_workflow_v3_enabled),
    user_id: str = Depends(get_user_id),
    workflow_db: Session = Depends(workflow_v3_db_dependency),
):
    detail = _owned_job(workflow_db, public_id, user_id)
    try:
        payload, recompile, inventory = _candidate_bundle(detail)
        volume = next(
            (
                row
                for row in recompile.get("volumes", [])
                if isinstance(row, dict)
                and str(row.get("volume_id") or "") == volume_id
            ),
            None,
        )
        if volume is None:
            raise ValueError("指定候选分卷不存在")
        binding = (
            volume.get("delivery_zip")
            if kind == "zip"
            else volume.get("reviewed_pdf")
        )
        path, expected_sha256, expected_size = _validated_candidate_binding(
            binding,
            inventory,
        )
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            member = _unique_candidate_member(archive, path)
            if not member.isfile() or member.size != expected_size:
                raise ValueError("候选下载文件与冻结大小不一致")
            extracted = archive.extractfile(member)
            if extracted is None:
                raise ValueError("候选下载文件不可读")
            body = extracted.read()
        if (
            len(body) != expected_size
            or _sha256_bytes(body) != expected_sha256
        ):
            raise ValueError("候选下载文件与冻结 SHA 不一致")
    except (OSError, tarfile.TarError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    source_name = str(detail.get("filename") or detail.get("material_id") or "worker-v3")
    stem = Path(source_name).stem or "worker-v3"
    suffix = "zip" if kind == "zip" else "pdf"
    filename = quote(f"{stem}-{volume_id}-candidate.{suffix}")
    return Response(
        content=body,
        media_type="application/zip" if kind == "zip" else "application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
            "X-Content-SHA256": expected_sha256,
        },
    )


@router.post(
    "/admin/jobs/{public_id}/projection-outbox/{outbox_id}/retry"
)
def retry_projection(
    public_id: str,
    outbox_id: int,
    _enabled: None = Depends(require_workflow_v3_enabled),
    admin: User = Depends(require_pipeline_admin),
    workflow_db: Session = Depends(workflow_v3_db_dependency),
):
    requested_by = str(admin.email or admin.id)
    try:
        outbox = retry_projection_outbox(
            workflow_db,
            public_id=public_id,
            outbox_id=outbox_id,
            requested_by=requested_by,
        )
        workflow_db.commit()
    except ProjectionRetryNotFoundError as exc:
        workflow_db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProjectionRetryConflictError as exc:
        workflow_db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "outbox": outbox.to_dict(),
        "job": workflow_job_detail(workflow_db, public_id),
    }


@router.post("/jobs/{public_id}/retry")
def retry_job(
    public_id: str,
    _enabled: None = Depends(require_workflow_v3_enabled),
    user_id: str = Depends(get_user_id),
    material_db: Session = Depends(get_db),
    workflow_db: Session = Depends(workflow_v3_db_dependency),
):
    detail = _owned_job(workflow_db, public_id, user_id)
    material = (
        material_db.query(Material)
        .filter(Material.id == int(detail["material_pk"]), Material.user_id == user_id)
        .first()
    )
    if not material:
        raise HTTPException(status_code=409, detail="任务对应的材料已不可用")
    try:
        verify_frozen_popo_manifest(
            material,
            expected_sha256=str(detail["source_popo_manifest"]["sha256"]),
        )
        job, stage = retry_failed_stage(workflow_db, public_id)
        workflow_db.commit()
    except (FrozenPopoValidationError, WorkflowV3TransitionError) as exc:
        workflow_db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "job": workflow_job_detail(workflow_db, job.public_id),
        "retried_stage": stage.to_dict(),
    }


@router.post("/jobs/{public_id}/review-resolution")
def resolve_review(
    public_id: str,
    payload: WorkflowV3ReviewResolutionRequest,
    _enabled: None = Depends(require_workflow_v3_enabled),
    admin: User = Depends(require_pipeline_admin),
    material_db: Session = Depends(get_db),
    workflow_db: Session = Depends(workflow_v3_db_dependency),
):
    job = (
        workflow_db.query(WorkflowV3Job)
        .filter(WorkflowV3Job.public_id == public_id)
        .one_or_none()
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Worker V3 任务不存在")
    material = (
        material_db.query(Material)
        .filter(
            Material.id == job.material_pk,
            Material.user_id == job.user_id,
        )
        .one_or_none()
    )
    if material is None:
        raise HTTPException(status_code=409, detail="任务对应的材料已不可用")
    identity = payload.resolution_manifest.model_dump()
    try:
        verify_frozen_popo_manifest(
            material,
            expected_sha256=job.source_popo_sha256,
        )
        manifest_bytes = _read_bound_object(
            identity,
            "review resolution manifest",
        )
        manifest = _json_object(
            manifest_bytes,
            "review resolution manifest",
        )
        job, resolution, recovery_stage, candidate = apply_review_resolution(
            workflow_db,
            public_id,
            idempotency_key=payload.idempotency_key,
            authorized_by=str(admin.email or admin.id),
            manifest_bucket=identity["bucket"],
            manifest_object=clean_path(identity["object"]),
            manifest_sha256=identity["sha256"],
            manifest_size_bytes=identity["size_bytes"],
            manifest=manifest,
        )
        workflow_db.commit()
    except (
        FrozenPopoValidationError,
        WorkflowV3TransitionError,
    ) as exc:
        workflow_db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "job": workflow_job_detail(workflow_db, job.public_id),
        "review_resolution": resolution.to_dict(),
        "recovery_stage": recovery_stage.to_dict(),
        "candidate": (
            {"id": str(candidate.id), "sha256": candidate.sha256}
            if candidate is not None
            else None
        ),
    }


@router.post("/jobs/{public_id}/cancel")
def cancel_workflow_job(
    public_id: str,
    payload: WorkflowV3CancelRequest,
    _enabled: None = Depends(require_workflow_v3_enabled),
    user_id: str = Depends(get_user_id),
    workflow_db: Session = Depends(workflow_v3_db_dependency),
):
    _owned_job(workflow_db, public_id, user_id)
    try:
        job, stage, execution = cancel_job(
            workflow_db,
            public_id,
            cancelled_by=user_id,
            reason=payload.reason.strip(),
        )
        workflow_db.commit()
    except WorkflowV3TransitionError as exc:
        workflow_db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "job": workflow_job_detail(workflow_db, job.public_id),
        "cancelled_stage": stage.to_dict() if stage else None,
        "cancelled_execution": execution.to_dict() if execution else None,
    }


@router.post("/jobs/{public_id}/human-acceptance")
def human_acceptance(
    public_id: str,
    payload: WorkflowV3HumanAcceptanceRequest,
    _enabled: None = Depends(require_workflow_v3_enabled),
    user_id: str = Depends(get_user_id),
    workflow_db: Session = Depends(workflow_v3_db_dependency),
):
    _owned_job(workflow_db, public_id, user_id)
    try:
        job = record_human_acceptance(
            workflow_db,
            public_id,
            accepted=payload.accepted,
            decided_by=user_id,
            output_id=payload.output_id,
            manifest_sha256=payload.manifest_sha256,
            reason=payload.reason,
        )
        workflow_db.commit()
    except WorkflowV3TransitionError as exc:
        workflow_db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"job": workflow_job_detail(workflow_db, job.public_id)}
