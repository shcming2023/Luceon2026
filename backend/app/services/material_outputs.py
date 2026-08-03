from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.material import Material, MaterialOutput
from app.models.review_asset import ReviewAsset
from app.services.codex_elegantbook import ElegantBookOutput, list_elegantbook_outputs, normalize_workflow_artifact_manifest, output_priority
from app.services.luceon_review import ObjectRef, read_object


def register_elegantbook_output(
    db: Session,
    user_id: str,
    material: Material,
    output: ElegantBookOutput,
    *,
    status: str | None = None,
    quality_status: str | None = None,
) -> MaterialOutput:
    row = (
        db.query(MaterialOutput)
        .filter(
            MaterialOutput.user_id == user_id,
            MaterialOutput.manifest_bucket == output.manifest_ref.bucket,
            MaterialOutput.manifest_object == output.manifest_ref.object,
        )
        .first()
    )
    if not row:
        row = MaterialOutput(
            user_id=user_id,
            manifest_bucket=output.manifest_ref.bucket,
            manifest_object=output.manifest_ref.object,
        )
        db.add(row)
    row.material_pk = material.id
    row.material_id = output.material_id or material.material_id or ""
    review_asset = None
    if output.popo_run_id:
        review_asset = (
            db.query(ReviewAsset)
            .filter(
                ReviewAsset.user_id == user_id,
                ReviewAsset.material_id == row.material_id,
                ReviewAsset.run_id == output.popo_run_id,
            )
            .order_by(ReviewAsset.id.desc())
            .first()
        )
    row.review_asset_id = review_asset.id if review_asset else material.review_asset_id
    row.output_type = "elegantbook"
    row.origin = output.origin
    row.status = status or row.status or _default_status(output.origin)
    row.quality_status = quality_status or row.quality_status or _default_quality_status(output.origin)
    row.output_run_id = output.output_run_id
    row.popo_run_id = output.popo_run_id
    row.skill_name = _infer_skill_name(output)
    row.skill_version = str(output.manifest.get("skill_version") or output.manifest.get("version") or "")
    row.version_label = _version_label(output)
    row.metadata_json = json.dumps(_output_metadata(output), ensure_ascii=False)
    return row


def sync_material_outputs_for_material(db: Session, user_id: str, material: Material) -> list[MaterialOutput]:
    rows = [register_elegantbook_output(db, user_id, material, output) for output in list_elegantbook_outputs(material)]
    db.flush()
    _ensure_single_current(db, user_id, material, rows)
    db.flush()
    return list_material_outputs(db, user_id, material)


def list_material_outputs(db: Session, user_id: str, material: Material) -> list[MaterialOutput]:
    material_id = material.material_id or ""
    rows = (
        db.query(MaterialOutput)
        .filter(
            MaterialOutput.user_id == user_id,
            MaterialOutput.material_id == material_id,
            MaterialOutput.output_type == "elegantbook",
            MaterialOutput.manifest_object.notlike("%/work/%"),
        )
        .all()
    )
    return sorted(rows, key=_registry_sort_key, reverse=True)


def material_output_or_404(db: Session, user_id: str, output_id: int, material: Material | None = None) -> MaterialOutput | None:
    query = db.query(MaterialOutput).filter(MaterialOutput.id == output_id, MaterialOutput.user_id == user_id)
    if material and material.material_id:
        query = query.filter(MaterialOutput.material_id == material.material_id)
    return query.first()


def promote_material_output(db: Session, row: MaterialOutput, material: Material) -> MaterialOutput:
    peers = (
        db.query(MaterialOutput)
        .filter(
            MaterialOutput.user_id == row.user_id,
            MaterialOutput.material_id == row.material_id,
            MaterialOutput.output_type == row.output_type,
            MaterialOutput.id != row.id,
        )
        .all()
    )
    for peer in peers:
        peer.is_current = False
    row.status = "promoted"
    row.quality_status = "passed"
    row.is_current = True
    row.promoted_at = row.promoted_at or datetime.utcnow()
    material.latex_manifest_bucket = row.manifest_bucket
    material.latex_manifest_object = row.manifest_object
    material.latex_run_id = row.output_run_id
    material.promote_stage("latex_done")
    return row


def output_from_material_output(row: MaterialOutput, material: Material) -> ElegantBookOutput | None:
    manifest = _read_manifest(row.manifest_bucket, row.manifest_object)
    if not isinstance(manifest, dict):
        return None
    manifest = normalize_workflow_artifact_manifest(manifest)
    return ElegantBookOutput(
        manifest_ref=ObjectRef(row.manifest_bucket, row.manifest_object),
        manifest=manifest,
        material_id=row.material_id or material.material_id or "",
        popo_run_id=row.popo_run_id or material.popo_run_id or "",
        output_run_id=row.output_run_id or row.popo_run_id or "",
        origin=row.origin,
        created_at=str(manifest.get("created_at") or row.created_at or ""),
    )


def _read_manifest(bucket: str, object_name: str) -> dict[str, Any] | None:
    try:
        value = json.loads(read_object(bucket, object_name).decode("utf-8"))
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _ensure_single_current(db: Session, user_id: str, material: Material, _rows: list[MaterialOutput]) -> None:
    registry_rows = (
        db.query(MaterialOutput)
        .filter(
            MaterialOutput.user_id == user_id,
            MaterialOutput.material_id == (material.material_id or ""),
            MaterialOutput.output_type == "elegantbook",
            MaterialOutput.manifest_object.notlike("%/work/%"),
        )
        .all()
    )
    if not registry_rows:
        return
    eligible_rows = [
        row
        for row in registry_rows
        if row.quality_status == "passed" and row.status in {"promoted", "published"}
    ]
    if not eligible_rows:
        for row in registry_rows:
            row.is_current = False
        if any(
            row.manifest_bucket == material.latex_manifest_bucket
            and row.manifest_object == material.latex_manifest_object
            for row in registry_rows
        ):
            material.latex_manifest_bucket = None
            material.latex_manifest_object = None
            material.latex_run_id = None
            if material.popo_manifest_bucket and material.popo_manifest_object:
                material.stage_status = "popo_done"
        return
    current_rows = [row for row in eligible_rows if row.is_current]
    if current_rows:
        winner = sorted(current_rows, key=_registry_sort_key, reverse=True)[0]
    else:
        winner = sorted(eligible_rows, key=_registry_sort_key, reverse=True)[0]
        winner.is_current = True
        if winner.status == "candidate":
            winner.status = "promoted"
        winner.promoted_at = winner.promoted_at or datetime.utcnow()
    for row in (
        row for row in registry_rows if row.id != winner.id
    ):
        row.is_current = False
    material.latex_manifest_bucket = winner.manifest_bucket
    material.latex_manifest_object = winner.manifest_object
    material.latex_run_id = winner.output_run_id
    material.promote_stage("latex_done")


def _registry_sort_key(row: MaterialOutput) -> tuple[int, int, str, str]:
    origin_rank = {"worker_v3": 50, "worker_v2": 40, "codex_refined": 30, "codex_skill": 25, "codex_elegantbook": 20, "legacy_selfloop": 10}.get(row.origin, 0)
    status_rank = {"promoted": 30, "published": 20, "candidate": 10}.get(row.status, 0)
    created = row.created_at.isoformat() if row.created_at else ""
    return (status_rank, origin_rank, created, row.manifest_object)


def _default_status(origin: str) -> str:
    return "promoted" if origin in {"legacy_selfloop", "codex_refined"} else "candidate"


def _default_quality_status(origin: str) -> str:
    return "passed" if origin in {"legacy_selfloop", "codex_refined"} else "unchecked"


def _infer_skill_name(output: ElegantBookOutput) -> str:
    if output.origin == "worker_v3":
        return "luceon-popo-to-refined-elegantbook"
    if output.origin == "worker_v2":
        return "worker-v2-core-production"
    if output.origin == "legacy_selfloop":
        return "legacy-selfloop"
    text = json.dumps(output.manifest, ensure_ascii=False).lower()
    if "refine-elegantbook-latex" in text:
        return "refine-elegantbook-latex"
    return str(output.manifest.get("skill_name") or "luceon-popo-to-refined-elegantbook")


def _version_label(output: ElegantBookOutput) -> str:
    if output.origin == "worker_v3":
        return f"Worker V3 · {output.output_run_id}"
    if output.origin == "worker_v2":
        return f"Worker V2.3 · {output.output_run_id}"
    if output.origin == "legacy_selfloop":
        return "legacy baseline"
    return output.output_run_id or output.created_at or "codex output"


def _output_metadata(output: ElegantBookOutput) -> dict[str, Any]:
    return {
        "created_at": output.created_at,
        "priority": list(output_priority(output)),
        "manifest_schema": output.manifest.get("schema"),
    }
