from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.workflow_v3.contracts import WORKFLOW_VERSION, contracts_for_version
from app.workflow_v3.release import require_qualification_environment
from app.workflow_v3.pricing import (
    PricingError,
    aggregate_model_costs,
    validate_release_pricing,
)
from app.workflow_v3.models import (
    WorkflowV3Candidate,
    WorkflowV3Evaluation,
    WorkflowV3Event,
    WorkflowV3Execution,
    WorkflowV3Job,
    WorkflowV3ModelCall,
    WorkflowV3ProjectionOutbox,
    WorkflowV3Promotion,
    WorkflowV3ReviewResolution,
    WorkflowV3SkillRelease,
    WorkflowV3StageRun,
)


class ProjectionRetryNotFoundError(ValueError):
    pass


class ProjectionRetryConflictError(ValueError):
    pass


def _require_sha256(value: str, field_name: str) -> str:
    normalized = (value or "").lower()
    if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
        raise ValueError(f"{field_name} must be a lowercase SHA-256")
    return normalized


def register_skill_release(
    db: Session,
    *,
    release_version: str,
    manifest_sha256: str,
    package_bucket: str,
    package_object: str,
    package_sha256: str,
    workflow_version: str,
    template_sha256: str,
    runtime_identity_sha256: str,
    manifest: dict,
    registered_by: str,
    qualification: bool = False,
) -> tuple[WorkflowV3SkillRelease, bool]:
    """Register an immutable release manifest after validating its V3 contract."""
    manifest_sha256 = _require_sha256(manifest_sha256, "manifest_sha256")
    package_sha256 = _require_sha256(package_sha256, "package_sha256")
    template_sha256 = _require_sha256(template_sha256, "template_sha256")
    runtime_identity_sha256 = _require_sha256(runtime_identity_sha256, "runtime_identity_sha256")
    contracts = contracts_for_version(workflow_version)
    if not release_version or not package_bucket or not package_object or not registered_by:
        raise ValueError("release identity, package object, and registered_by are required")
    if not isinstance(manifest, dict):
        raise ValueError("release manifest must be an object")

    if manifest.get("schema_version") != "luceon.worker-v3-skill-release/v1":
        raise ValueError("release manifest schema is not executable by Worker V3")
    if manifest.get("version") != release_version:
        raise ValueError("release manifest version does not match registration")
    status = manifest.get("status")
    eligibility = manifest.get("eligibility")
    if qualification:
        require_qualification_environment()
        if status != "incomplete" or not isinstance(eligibility, dict):
            raise ValueError(
                "qualification registration requires one incomplete release"
            )
        if any(value is not False for value in eligibility.values()):
            raise ValueError(
                "qualification release cannot claim channel eligibility"
            )
    else:
        if status not in {"rc", "stable"} or not isinstance(eligibility, dict):
            raise ValueError(
                "release manifest is incomplete or has no eligibility evidence"
            )
        eligible = (
            eligibility.get("rc_eligible")
            if status == "rc"
            else eligibility.get("stable_eligible")
        )
        if eligible is not True:
            raise ValueError(
                "release manifest is not eligible for its declared channel"
            )
    template = manifest.get("template")
    if not isinstance(template, dict) or template.get("tree_sha256") != template_sha256:
        raise ValueError("release manifest template tree does not match registration")
    if runtime_identity_for_manifest(manifest) != runtime_identity_sha256:
        raise ValueError("release manifest runtime identity does not match registration")
    try:
        validate_release_pricing(manifest.get("model_policy", {}))
    except PricingError as exc:
        raise ValueError(
            f"release manifest pricing is invalid: {exc.code}: {exc}"
        ) from exc

    entrypoints = manifest.get("entrypoints")
    if not isinstance(entrypoints, dict):
        raise ValueError("release manifest entrypoints are missing")
    definitions = entrypoints.get("definitions")
    formal_ids = entrypoints.get("formal")
    if not isinstance(definitions, dict) or not isinstance(formal_ids, list):
        raise ValueError("release manifest formal entrypoints are invalid")
    expected_stage_keys = {contract.key for contract in contracts}
    formal_pairs: dict[str, dict[str, str]] = {}
    for identifier in formal_ids:
        definition = definitions.get(identifier)
        if not isinstance(identifier, str) or not isinstance(definition, dict):
            raise ValueError("release manifest formal entrypoint definition is missing")
        stage = definition.get("stage")
        execution_role = definition.get("execution_role")
        if stage not in expected_stage_keys or execution_role not in {"producer", "evaluator"}:
            raise ValueError("release manifest formal entrypoint role or stage is invalid")
        stage_roles = formal_pairs.setdefault(stage, {})
        if execution_role in stage_roles:
            raise ValueError("release manifest has duplicate formal stage roles")
        stage_roles[execution_role] = identifier
    if set(formal_pairs) != expected_stage_keys or any(
        set(roles) != {"producer", "evaluator"} for roles in formal_pairs.values()
    ):
        raise ValueError(
            "formal release entrypoints must provide one producer and one evaluator "
            "for every registered workflow stage"
        )

    existing = (
        db.query(WorkflowV3SkillRelease)
        .filter(WorkflowV3SkillRelease.manifest_sha256 == manifest_sha256)
        .first()
    )
    if existing:
        same = (
            existing.release_version == release_version
            and existing.package_bucket == package_bucket
            and existing.package_object == package_object
            and existing.package_sha256 == package_sha256
            and existing.workflow_version == workflow_version
            and existing.template_sha256 == template_sha256
            and existing.runtime_identity_sha256 == runtime_identity_sha256
            and existing.status
            == ("qualification" if qualification else "registered")
        )
        if not same:
            raise ValueError("manifest SHA is already registered with different immutable identity")
        return existing, False
    version_conflict = (
        db.query(WorkflowV3SkillRelease)
        .filter(WorkflowV3SkillRelease.release_version == release_version)
        .first()
    )
    if version_conflict:
        raise ValueError("release version is already bound to a different manifest")

    release = WorkflowV3SkillRelease(
        release_version=release_version,
        manifest_sha256=manifest_sha256,
        package_bucket=package_bucket,
        package_object=package_object,
        package_sha256=package_sha256,
        workflow_version=workflow_version,
        template_sha256=template_sha256,
        runtime_identity_sha256=runtime_identity_sha256,
        manifest_json=WorkflowV3SkillRelease.dump(manifest),
        status="qualification" if qualification else "registered",
        registered_by=registered_by,
    )
    db.add(release)
    db.flush()
    return release, True


def runtime_identity_for_manifest(manifest: dict) -> str:
    runtime = manifest.get("runtime")
    if not isinstance(runtime, dict):
        raise ValueError("release manifest runtime is missing")
    system_tools = runtime.get("system_tools")
    identity_path = (
        system_tools.get("identity")
        if isinstance(system_tools, dict)
        else None
    )
    if isinstance(identity_path, str) and identity_path:
        files = manifest.get("files")
        if not isinstance(files, list):
            raise ValueError("release manifest files are missing")
        matches = [
            row
            for row in files
            if isinstance(row, dict) and row.get("path") == identity_path
        ]
        if len(matches) != 1:
            raise ValueError(
                "release runtime identity file is missing or duplicated"
            )
        digest = str(matches[0].get("sha256") or "")
        return _require_sha256(digest, "runtime identity file sha256")
    canonical = json.dumps(runtime, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def list_skill_releases(db: Session, *, include_retired: bool = False) -> list[dict]:
    query = db.query(WorkflowV3SkillRelease)
    if not include_retired:
        query = query.filter(WorkflowV3SkillRelease.status == "registered")
    rows = query.order_by(WorkflowV3SkillRelease.id.desc()).all()
    return [row.to_dict() for row in rows]


def create_workflow_job(
    db: Session,
    *,
    user_id: str,
    material_pk: int,
    material_id: str,
    source_popo_bucket: str,
    source_popo_object: str,
    source_popo_sha256: str,
    skill_release_version: str,
    skill_release_sha256: str,
    template_sha256: str,
    workflow_version: str = WORKFLOW_VERSION,
    payload: dict | None = None,
    priority: int = 100,
    qualification: bool = False,
) -> tuple[WorkflowV3Job, bool]:
    """Create an isolated V3 run bound to a registered immutable release."""
    contracts = contracts_for_version(workflow_version)
    source_popo_sha256 = _require_sha256(source_popo_sha256, "source_popo_sha256")
    skill_release_sha256 = _require_sha256(skill_release_sha256, "skill_release_sha256")
    template_sha256 = _require_sha256(template_sha256, "template_sha256")
    if not user_id or not material_id or not source_popo_bucket or not source_popo_object:
        raise ValueError("user, material, and frozen Popo manifest are required")

    release = (
        db.query(WorkflowV3SkillRelease)
        .filter(WorkflowV3SkillRelease.manifest_sha256 == skill_release_sha256)
        .first()
    )
    if qualification:
        require_qualification_environment()
    expected_release_status = "qualification" if qualification else "registered"
    if not release or release.status != expected_release_status:
        raise ValueError("skill release is not registered and active")
    requested_payload = payload or {}
    qualification_marker = requested_payload.get("qualification")
    if qualification and (
        not isinstance(qualification_marker, dict)
        or qualification_marker.get("enabled") is not True
        or requested_payload.get("submission_path") != "qualification_cli"
    ):
        raise ValueError("qualification job lacks its isolated CLI marker")
    if not qualification and isinstance(qualification_marker, dict):
        raise ValueError("ordinary job cannot carry a qualification marker")
    if (
        release.release_version != skill_release_version
        or release.workflow_version != workflow_version
        or release.template_sha256 != template_sha256
    ):
        raise ValueError("job release identity does not match the registered manifest")

    idempotency_key = workflow_idempotency_key(
        user_id=user_id,
        material_id=material_id,
        source_popo_bucket=source_popo_bucket,
        source_popo_object=source_popo_object,
        source_popo_sha256=source_popo_sha256,
        workflow_version=workflow_version,
        skill_release_sha256=skill_release_sha256,
        template_sha256=template_sha256,
    )
    existing = (
        db.query(WorkflowV3Job)
        .filter(WorkflowV3Job.idempotency_key == idempotency_key)
        .first()
    )
    if existing:
        existing_payload = existing.load(existing.payload_json, {})
        requested_source = requested_payload.get("source_evidence")
        existing_source = (
            existing_payload.get("source_evidence")
            if isinstance(existing_payload, dict)
            else None
        )
        requested_review = (
            requested_source.get("review_asset")
            if isinstance(requested_source, dict)
            else None
        )
        existing_review = (
            existing_source.get("review_asset")
            if isinstance(existing_source, dict)
            else None
        )
        if existing.material_pk != int(material_pk) or existing_review != requested_review:
            raise ValueError(
                "idempotent job identity conflicts with the exact frozen ReviewAsset"
            )
        return existing, False

    job = WorkflowV3Job(
        public_id=str(uuid.uuid4()),
        idempotency_key=idempotency_key,
        user_id=user_id,
        material_pk=int(material_pk),
        material_id=material_id,
        source_popo_bucket=source_popo_bucket,
        source_popo_object=source_popo_object,
        source_popo_sha256=source_popo_sha256,
        workflow_version=workflow_version,
        skill_release_id=release.id,
        skill_release_version=skill_release_version,
        skill_release_sha256=skill_release_sha256,
        template_sha256=template_sha256,
        machine_status="queued",
        spec_status="not_evaluated",
        readiness_status="not_ready",
        human_acceptance_status="pending",
        current_stage_key=contracts[0].key,
        current_generation=1,
        priority=priority,
        payload_json=WorkflowV3Job.dump(payload or {}),
    )
    db.add(job)
    db.flush()
    for index, contract in enumerate(contracts):
        db.add(
            WorkflowV3StageRun(
                workflow_job_id=job.id,
                stage_key=contract.key,
                stage_version=contract.stage_version,
                attempt=1,
                generation=1,
                machine_status="queued" if index == 0 else "pending",
                spec_status="not_evaluated",
                owner=contract.owner,
                input_kind="frozen_source" if index == 0 else "promoted_artifact",
                input_artifact_sha256=source_popo_sha256 if index == 0 else "",
            )
        )
    db.add(
        WorkflowV3Event(
            workflow_job_id=job.id,
            event_type="job_created",
            message="Worker V3 run is recorded and bound to an immutable skill release.",
            payload_json=WorkflowV3Event.dump(
                {
                    "workflow_version": workflow_version,
                    "skill_release_version": skill_release_version,
                    "skill_release_sha256": skill_release_sha256,
                    "first_stage": contracts[0].key,
                    "historical_outputs_untouched": True,
                }
            ),
        )
    )
    db.flush()
    return job, True


def workflow_idempotency_key(
    *,
    user_id: str,
    material_id: str,
    source_popo_bucket: str,
    source_popo_object: str,
    source_popo_sha256: str,
    workflow_version: str,
    skill_release_sha256: str,
    template_sha256: str,
) -> str:
    value = "\n".join(
        (
            user_id,
            material_id,
            source_popo_bucket,
            source_popo_object,
            source_popo_sha256,
            workflow_version,
            skill_release_sha256,
            template_sha256,
        )
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def workflow_job_detail(db: Session, public_id: str) -> dict | None:
    job = db.query(WorkflowV3Job).filter(WorkflowV3Job.public_id == public_id).first()
    if not job:
        return None
    stages = (
        db.query(WorkflowV3StageRun)
        .filter(WorkflowV3StageRun.workflow_job_id == job.id)
        .order_by(WorkflowV3StageRun.id.asc())
        .all()
    )
    executions = (
        db.query(WorkflowV3Execution)
        .filter(WorkflowV3Execution.workflow_job_id == job.id)
        .order_by(WorkflowV3Execution.id.asc())
        .all()
    )
    candidates = (
        db.query(WorkflowV3Candidate)
        .filter(WorkflowV3Candidate.workflow_job_id == job.id)
        .order_by(WorkflowV3Candidate.id.asc())
        .all()
    )
    evaluations = (
        db.query(WorkflowV3Evaluation)
        .filter(WorkflowV3Evaluation.workflow_job_id == job.id)
        .order_by(WorkflowV3Evaluation.id.asc())
        .all()
    )
    promotions = (
        db.query(WorkflowV3Promotion)
        .filter(WorkflowV3Promotion.workflow_job_id == job.id)
        .order_by(WorkflowV3Promotion.id.asc())
        .all()
    )
    review_resolutions = (
        db.query(WorkflowV3ReviewResolution)
        .filter(WorkflowV3ReviewResolution.workflow_job_id == job.id)
        .order_by(WorkflowV3ReviewResolution.id.asc())
        .all()
    )
    projection_outbox = (
        db.query(WorkflowV3ProjectionOutbox)
        .filter(WorkflowV3ProjectionOutbox.workflow_job_id == job.id)
        .order_by(WorkflowV3ProjectionOutbox.id.asc())
        .all()
    )
    events = (
        db.query(WorkflowV3Event)
        .filter(WorkflowV3Event.workflow_job_id == job.id)
        .order_by(WorkflowV3Event.id.asc())
        .all()
    )
    model_calls = (
        db.query(WorkflowV3ModelCall)
        .filter(WorkflowV3ModelCall.workflow_job_id == job.id)
        .order_by(WorkflowV3ModelCall.id.asc())
        .all()
    )
    stage_key_by_id = {row.id: row.stage_key for row in stages}
    model_call_rows: list[dict] = []
    for row in model_calls:
        value = row.to_dict()
        value["stage_key"] = stage_key_by_id.get(row.stage_run_id, "")
        model_call_rows.append(value)
    execution_by_stage = {
        str(row.stage_run_id): _execution_dict(row) for row in executions
    }
    candidates_by_stage: dict[str, list[dict]] = {}
    for row in candidates:
        candidates_by_stage.setdefault(str(row.stage_run_id), []).append(_candidate_dict(row))
    evaluations_by_stage: dict[str, list[dict]] = {}
    for row in evaluations:
        evaluations_by_stage.setdefault(str(row.stage_run_id), []).append(_evaluation_dict(row))
    stage_rows: list[dict] = []
    for row in stages:
        value = row.to_dict()
        stage_id = str(row.id)
        value["execution"] = execution_by_stage.get(stage_id)
        value["candidates"] = candidates_by_stage.get(stage_id, [])
        value["evaluations"] = evaluations_by_stage.get(stage_id, [])
        stage_rows.append(value)
    final_projection = next(
        (row for row in projection_outbox if row.event_kind == "final_ready"),
        None,
    )
    acceptance_projection = next(
        (
            row
            for row in projection_outbox
            if row.event_kind == "human_acceptance"
        ),
        None,
    )
    formal_output_ready = _projection_is_applied(final_projection)
    acceptance_projection_applied = _projection_is_applied(
        acceptance_projection
    )
    delivery_status = (
        "projected"
        if formal_output_ready
        else "projection_failed"
        if final_projection is not None
        and final_projection.status in {"failed", "suppressed"}
        else "projecting"
    )
    human_decision_recorded = job.human_acceptance_status in {
        "accepted",
        "rejected",
    }
    projection_errors = [
        {
            "outbox_id": str(row.id),
            "event_kind": row.event_kind,
            "status": row.status,
            "message": row.last_error,
        }
        for row in projection_outbox
        if row.last_error
    ]
    source_identity = _frozen_source_identity(job)
    return {
        **job.to_dict(),
        "filename": source_identity["filename"],
        "review_asset_id": source_identity["review_asset_id"],
        "source_pdf_sha256": source_identity["source_pdf_sha256"],
        "source_identity": source_identity,
        "final_output_id": (
            str(final_projection.projected_output_id)
            if formal_output_ready
            else ""
        ),
        "spec_ready_for_projection": job.readiness_status == "ready",
        "ready_for_user_acceptance": formal_output_ready,
        "delivery_status": delivery_status,
        "delivery_error": (
            final_projection.last_error if final_projection is not None else ""
        ),
        "projection_errors": projection_errors,
        "human_acceptance_decision_recorded": human_decision_recorded,
        "human_acceptance_effective": (
            human_decision_recorded and acceptance_projection_applied
        ),
        "human_accepted": (
            job.human_acceptance_status == "accepted"
            and acceptance_projection_applied
        ),
        "stages": stage_rows,
        "executions": [_execution_dict(row) for row in executions],
        "candidates": [_candidate_dict(row) for row in candidates],
        "evaluations": [_evaluation_dict(row) for row in evaluations],
        "promotions": [_promotion_dict(row) for row in promotions],
        "review_resolutions": [row.to_dict() for row in review_resolutions],
        "projection_outbox": [row.to_dict() for row in projection_outbox],
        "events": [row.to_dict() for row in events],
        "model_calls": model_call_rows,
        "model_costs": aggregate_model_costs(
            model_calls,
            stage_key_by_id=stage_key_by_id,
        ),
    }


def retry_projection_outbox(
    db: Session,
    *,
    public_id: str,
    outbox_id: int,
    requested_by: str,
    max_attempts: int = 5,
) -> WorkflowV3ProjectionOutbox:
    """Requeue one failed projection after an explicit administrator decision.

    API wiring must authorize a pipeline administrator before calling this
    function. It deliberately refuses active, applied, suppressed, foreign,
    and attempt-exhausted rows.
    """

    if not isinstance(requested_by, str) or not requested_by.strip():
        raise ValueError("projection retry requester identity is required")
    job = (
        db.query(WorkflowV3Job)
        .filter(WorkflowV3Job.public_id == public_id)
        .one_or_none()
    )
    if job is None:
        raise ProjectionRetryNotFoundError("Worker V3 job was not found")
    row = db.get(WorkflowV3ProjectionOutbox, int(outbox_id))
    if row is None or row.workflow_job_id != job.id:
        raise ProjectionRetryNotFoundError(
            "projection outbox does not belong to this job"
        )
    if row.status != "failed":
        raise ProjectionRetryConflictError(
            "only a failed projection can be retried"
        )
    if row.attempt_count >= max(1, int(max_attempts)):
        raise ProjectionRetryConflictError(
            "projection retry budget is exhausted"
        )
    if (
        row.applied_at is not None
        or row.applied_identity
        or row.projected_output_id is not None
    ):
        raise ProjectionRetryConflictError(
            "an applied projection cannot be retried"
        )
    previous_error = row.last_error
    row.status = "pending"
    row.lease_owner = ""
    row.lease_expires_at = None
    row.last_error = (
        f"manual retry requested by {requested_by.strip()}; "
        f"previous error: {previous_error}"
    )[-8_000:]
    row.updated_at = datetime.utcnow()
    db.flush()
    return row


def list_workflow_jobs(
    db: Session,
    *,
    user_id: str,
    material_pk: int | None = None,
    machine_status: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[dict], int]:
    query = db.query(WorkflowV3Job).filter(WorkflowV3Job.user_id == user_id)
    if material_pk is not None:
        query = query.filter(WorkflowV3Job.material_pk == material_pk)
    if machine_status is not None:
        if machine_status not in {
            "queued",
            "running",
            "needs_review",
            "failed",
            "cancelled",
            "succeeded",
        }:
            raise ValueError("unknown Worker V3 machine status")
        query = query.filter(WorkflowV3Job.machine_status == machine_status)
    total = query.count()
    jobs = (
        query.order_by(WorkflowV3Job.created_at.desc())
        .offset(max(int(offset), 0))
        .limit(min(max(limit, 1), 200))
        .all()
    )
    return [workflow_job_detail(db, row.public_id) for row in jobs], total


def _projection_is_applied(
    row: WorkflowV3ProjectionOutbox | None,
) -> bool:
    return bool(
        row
        and row.status == "applied"
        and _is_sha256(row.applied_identity)
        and row.projected_output_id
        and row.projected_manifest_bucket
        and row.projected_manifest_object
        and _is_sha256(row.projected_manifest_sha256)
    )


def _frozen_source_identity(job: WorkflowV3Job) -> dict:
    payload = job.load(job.payload_json, {})
    source = payload.get("source_evidence") if isinstance(payload, dict) else None
    source = source if isinstance(source, dict) else {}
    source_pdf = (
        source.get("source_pdf")
        if isinstance(source.get("source_pdf"), dict)
        else {}
    )
    popo = (
        source.get("popo_manifest")
        if isinstance(source.get("popo_manifest"), dict)
        else {}
    )
    review = (
        source.get("review_asset")
        if isinstance(source.get("review_asset"), dict)
        else {}
    )
    filename = str(source.get("filename") or "").strip()
    material_id = str(source.get("material_id") or "").strip()
    review_asset_id = str(review.get("id") or "").strip()
    source_pdf_sha256 = str(source_pdf.get("sha256") or "").strip()
    errors: list[str] = []
    if not filename:
        errors.append("source_evidence.filename is missing")
    if material_id != job.material_id:
        errors.append("source_evidence.material_id differs from the job")
    if (
        popo.get("bucket") != job.source_popo_bucket
        or popo.get("object") != job.source_popo_object
        or popo.get("sha256") != job.source_popo_sha256
    ):
        errors.append("source_evidence.popo_manifest differs from the job")
    if not _is_sha256(source_pdf_sha256):
        errors.append("source_evidence.source_pdf.sha256 is invalid")
    if not review_asset_id.isdigit() or int(review_asset_id) <= 0:
        errors.append("source_evidence.review_asset.id is invalid")
    if (
        review.get("bucket") != job.source_popo_bucket
        or review.get("object") != job.source_popo_object
        or review.get("sha256") != job.source_popo_sha256
    ):
        errors.append("source_evidence.review_asset differs from the frozen Popo source")
    return {
        "verified": not errors,
        "errors": errors,
        "filename": filename,
        "material_pk": str(job.material_pk),
        "material_id": material_id,
        "source_pdf": {
            "bucket": str(source_pdf.get("bucket") or ""),
            "object": str(source_pdf.get("object") or ""),
            "sha256": source_pdf_sha256,
            "size_bytes": source_pdf.get("size_bytes"),
        },
        "source_pdf_sha256": source_pdf_sha256,
        "popo_manifest": {
            "bucket": str(popo.get("bucket") or ""),
            "object": str(popo.get("object") or ""),
            "sha256": str(popo.get("sha256") or ""),
        },
        "review_asset_id": review_asset_id,
    }


def _is_sha256(value: str) -> bool:
    return len(value or "") == 64 and all(
        char in "0123456789abcdef" for char in value
    )


def _execution_dict(row: WorkflowV3Execution) -> dict:
    return {
        "id": str(row.id),
        "stage_run_id": str(row.stage_run_id),
        "producer_identity": row.producer_identity,
        "machine_status": row.machine_status,
        "skill_release_sha256": row.skill_release_sha256,
        "runtime_identity_sha256": row.runtime_identity_sha256,
        "generation": row.generation,
        "review_resolution_sha256": row.review_resolution_sha256,
        "metrics": row.load(row.metrics_json, {}),
        "error": {"code": row.error_code, "message": row.error_message},
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "heartbeat_at": row.heartbeat_at.isoformat() if row.heartbeat_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


def _candidate_dict(row: WorkflowV3Candidate) -> dict:
    return {
        "id": str(row.id),
        "stage_run_id": str(row.stage_run_id),
        "execution_id": str(row.execution_id),
        "artifact_kind": row.artifact_kind,
        "bucket": row.bucket,
        "object": row.object_name,
        "sha256": row.sha256,
        "size_bytes": row.size_bytes,
        "immutable": row.immutable,
        "generation": row.generation,
        "review_resolution_sha256": row.review_resolution_sha256,
        "status": row.status,
        "metadata": row.load(row.metadata_json, {}),
    }


def _evaluation_dict(row: WorkflowV3Evaluation) -> dict:
    return {
        "id": str(row.id),
        "stage_run_id": str(row.stage_run_id),
        "candidate_id": str(row.candidate_id),
        "evaluator_identity": row.evaluator_identity,
        "evaluator_version": row.evaluator_version,
        "policy_sha256": row.policy_sha256,
        "decision": row.decision,
        "spec_passed": row.spec_passed,
        "gate_results": row.load(row.gate_results_json, {}),
        "findings": row.load(row.findings_json, []),
        "generation": row.generation,
        "review_resolution_sha256": row.review_resolution_sha256,
    }


def _promotion_dict(row: WorkflowV3Promotion) -> dict:
    return {
        "id": str(row.id),
        "stage_run_id": str(row.stage_run_id),
        "candidate_id": str(row.candidate_id),
        "evaluation_id": str(row.evaluation_id),
        "artifact_sha256": row.artifact_sha256,
        "promoted_by": row.promoted_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
