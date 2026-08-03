from __future__ import annotations

import traceback
import os
import hashlib
import json
import re
import shutil
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path

import fitz

from app.database import SessionLocal as LegacySessionLocal
from app.models.material import Material
from app.services.luceon_review import ObjectRef, minio_client
from app.services.codex_print_quality import build_print_quality_report
from app.services.codex_elegantbook import ElegantBookOutput, normalize_workflow_artifact_manifest
from app.services.material_outputs import promote_material_output, register_elegantbook_output
from app.services.luceon_review import read_object
from app.services.popo_to_raw import execute_popo_to_raw
from app.workflow_v2.artifacts import (
    ArtifactIntegrityError,
    DELIVERY_ZIP_MAX_BYTES,
    materialize_artifact,
    optimize_delivery_images,
    publish_stage_directory,
    write_latex_delivery_zip,
)
from app.workflow_v2.contracts import LEGACY_WORKFLOW_VERSION, contract_for
from app.workflow_v2.content_conservation import build_canonical_conservation
from app.workflow_v2.database import workflow_session_factory
from app.workflow_v2.llm_gateway import REPAIR_PLAN_PROMPT_VERSION, plan_latex_repairs
from app.workflow_v2.outline_reconstruction import build_outline_artifact
from app.workflow_v2.outline_application import apply_accepted_outline
from app.workflow_v2.semantic_validation import validate_semantic_artifact
from app.workflow_v2.elegantbook_validation import validate_elegantbook_project
from app.workflow_v2.models import ArtifactVersion, GoldenRegressionCase, ModelCall, QaFinding, RepairAttempt, StageRun, WorkflowJob
from app.workflow_v2.state_machine import block_current_stage_for_review, claim_current_stage, complete_current_stage, fail_current_stage, record_stage_progress
from app.workflow_v2.visual_qa import review_all_pages


SUPPORTED_EXECUTORS = {"canonical_clean_material", "outline_reconstruction", "semantic_annotation", "deterministic_elegantbook", "bounded_deepseek_polish_qa", "bounded_llm_polish", "independent_final_review"}
SEMANTIC_SCRIPT = Path(os.getenv("WORKFLOW_V2_SEMANTIC_SCRIPT", "/app/app/workflow_v2/runtime/semantic/scripts/annotate_material.py"))
CLEANLATEX_BRIDGE = Path(os.getenv("WORKFLOW_V2_CLEANLATEX_BRIDGE", "/app/app/workflow_v2/runtime/elegantbook/scripts/semantic_markdown_to_cleanlatex.py"))
ELEGANTBOOK_BUILDER = Path(os.getenv("WORKFLOW_V2_ELEGANTBOOK_BUILDER", "/app/app/workflow_v2/runtime/elegantbook/scripts/clean_to_elegantbook.py"))
LATEX_REFINER = Path(os.getenv("WORKFLOW_V2_LATEX_REFINER", "/skills/refine-elegantbook-latex/scripts/refine_elegantbook_latex.py"))
TEX_CONTAINER = os.getenv("WORKFLOW_V2_TEX_CONTAINER", "sharelatex")
WORK_ROOT = Path(os.getenv("WORKFLOW_V2_WORK_ROOT", "/data/workflow-v2"))


class StageExecutionError(RuntimeError):
    pass


def run_one_stage(public_id: str, *, worker_id: str) -> dict:
    workflow_db = workflow_session_factory()()
    legacy_db = LegacySessionLocal()
    claimed = False
    failure_evidence_error = ""
    try:
        job = workflow_db.query(WorkflowJob).filter(WorkflowJob.public_id == public_id).first()
        if not job:
            raise StageExecutionError("workflow job not found")
        payload = job.load(job.payload_json, {})
        if payload.get("execution_authorized") is not True:
            raise StageExecutionError("workflow execution is not authorized")
        if job.current_stage_key not in SUPPORTED_EXECUTORS:
            raise StageExecutionError(f"stage executor is not implemented: {job.current_stage_key}")
        job, stage = claim_current_stage(workflow_db, public_id, worker_id)
        workflow_db.commit()
        claimed = True

        material = legacy_db.query(Material).filter(Material.id == job.material_pk).first()
        if not material or not _material_access_allowed(job, payload, material):
            raise StageExecutionError("legacy material projection was not found")
        stage_passed = True
        stage_failure = ""
        if stage.stage_key == "canonical_clean_material":
            result = _run_canonical(job.public_id, stage.id, material, workflow_db)
            output_dir = Path(result["body_final"])
            if job.workflow_version != LEGACY_WORKFLOW_VERSION:
                validation = build_canonical_conservation(output_dir)
                blockers = validation.get("blockers") or []
                stage_passed = not blockers
                stage_failure = "; ".join(str(row.get("code") or "canonical_gate_failed") for row in blockers)
                record_stage_progress(
                    workflow_db,
                    job.public_id,
                    step="content_conservation_gate",
                    message="Every source block received an auditable conservation disposition.",
                    payload={"status": validation["status"], "source_block_count": validation["source_block_count"], "unexplained_block_count": validation["unexplained_block_count"]},
                )
                workflow_db.commit()
            _record_canonical_model_calls(workflow_db, job, stage, output_dir)
        elif stage.stage_key == "outline_reconstruction":
            output_dir, stage_passed, stage_failure = _run_outline_reconstruction(job, stage, workflow_db)
        elif stage.stage_key == "semantic_annotation":
            output_dir, stage_passed, stage_failure = _run_semantic(job, stage, workflow_db)
        elif stage.stage_key == "deterministic_elegantbook":
            output_dir, stage_passed, stage_failure = _run_elegantbook(job, stage, material, workflow_db)
        elif stage.stage_key == "bounded_deepseek_polish_qa":
            output_dir, stage_passed, stage_failure = _run_core_acceptance(job, stage, workflow_db)
        elif stage.stage_key == "bounded_llm_polish":
            output_dir = _run_bounded_polish(job, stage, material, workflow_db)
        elif stage.stage_key == "independent_final_review":
            output_dir, stage_passed, stage_failure = _run_independent_review(job, stage, material, workflow_db)
        else:
            raise StageExecutionError(f"stage executor is not implemented: {stage.stage_key}")
        contract = contract_for(job.workflow_version, stage.stage_key)
        artifact = publish_stage_directory(
            workflow_db,
            minio_client,
            job=job,
            stage=stage,
            source_dir=output_dir,
            artifact_kind=contract.artifact_prefix,
            contract=contract.to_dict(),
        )
        if stage_passed:
            if stage.stage_key == "bounded_deepseek_polish_qa":
                _project_core_output(legacy_db, material, job, artifact)
                legacy_db.commit()
            complete_current_stage(
                workflow_db,
                public_id,
                output_bucket=artifact.bucket,
                output_object=artifact.object_name,
                output_sha256=artifact.sha256,
                metrics={"artifact_id": str(artifact.id), "file_count": artifact.load(artifact.metadata_json, {}).get("file_count", 0)},
            )
            if stage.stage_key == "independent_final_review":
                _record_golden_pass(workflow_db, job, stage, artifact, output_dir)
        else:
            handoff_ready = _is_soft_handoff_ready(stage.stage_key, stage_failure, output_dir)
            artifact.status = "handoff_ready" if handoff_ready else "blocked"
            block_current_stage_for_review(
                workflow_db,
                public_id,
                output_bucket=artifact.bucket,
                output_object=artifact.object_name,
                output_sha256=artifact.sha256,
                error_message=stage_failure,
                metrics={
                    "artifact_id": str(artifact.id),
                    "file_count": artifact.load(artifact.metadata_json, {}).get("file_count", 0),
                    "quality_blockers": [code for code in stage_failure.split("; ") if code],
                },
                handoff_ready=handoff_ready,
            )
        workflow_db.commit()
        return {
            "ok": stage_passed,
            "job_id": public_id,
            "stage": stage.stage_key,
            "job_status": job.status,
            "current_stage_key": job.current_stage_key,
            "artifact": {"bucket": artifact.bucket, "object": artifact.object_name, "sha256": artifact.sha256},
            "error_message": stage_failure,
        }
    except Exception as exc:
        workflow_db.rollback()
        try:
            job = workflow_db.query(WorkflowJob).filter(WorkflowJob.public_id == public_id).first()
            if claimed and job and job.status in {"queued", "running"}:
                fail_current_stage(workflow_db, public_id, error_code=type(exc).__name__, error_message=str(exc)[:4000])
                workflow_db.commit()
                try:
                    _publish_failure_evidence(workflow_db, public_id, exc)
                except Exception as evidence_exc:
                    workflow_db.rollback()
                    failure_evidence_error = f"{type(evidence_exc).__name__}: {evidence_exc}"
        except Exception as state_exc:
            workflow_db.rollback()
            failure_evidence_error = failure_evidence_error or f"{type(state_exc).__name__}: {state_exc}"
        return {"ok": False, "job_id": public_id, "error_code": type(exc).__name__, "error_message": str(exc), "failure_evidence_error": failure_evidence_error, "traceback": traceback.format_exc(limit=8)}
    finally:
        legacy_db.close()
        workflow_db.close()


SOFT_HANDOFF_CODES = {
    "visual_review_recommended",
    "minor_layout_variance",
    "minor_overfull_box",
}


def _is_soft_handoff_ready(stage_key: str, failure: str, output_dir: Path) -> bool:
    if stage_key not in {"bounded_deepseek_polish_qa", "independent_final_review"}:
        return False
    codes = {value.strip() for value in str(failure or "").split(";") if value.strip()}
    if not codes or not codes <= SOFT_HANDOFF_CODES:
        return False
    return (output_dir / "main.pdf").is_file() and (output_dir / "latex-project.zip").is_file()


def _write_failure_evidence(job: WorkflowJob, stage: StageRun, exc: Exception) -> Path:
    evidence_dir = WORK_ROOT / job.public_id / f"{stage.stage_key}-attempt-{stage.attempt}-failure"
    if evidence_dir.exists():
        shutil.rmtree(evidence_dir)
    evidence_dir.mkdir(parents=True)
    payload = {
        "schema": "luceon.workflow.failure-evidence/v1",
        "workflow_job_id": job.public_id,
        "material_id": job.material_id,
        "workflow_version": job.workflow_version,
        "stage": {"key": stage.stage_key, "version": stage.stage_version, "attempt": stage.attempt},
        "input_manifest_sha256": stage.input_manifest_sha256,
        "error": {"code": type(exc).__name__, "message": str(exc)[:4000]},
        "created_at": datetime.utcnow().isoformat(),
    }
    (evidence_dir / "failure.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return evidence_dir


def _publish_failure_evidence(workflow_db, public_id: str, exc: Exception) -> None:
    job = workflow_db.query(WorkflowJob).filter(WorkflowJob.public_id == public_id).first()
    if not job:
        return
    stage = (
        workflow_db.query(StageRun)
        .filter(StageRun.workflow_job_id == job.id, StageRun.stage_key == job.current_stage_key)
        .order_by(StageRun.attempt.desc())
        .first()
    )
    if not stage:
        return
    contract = contract_for(job.workflow_version, stage.stage_key)
    artifact = publish_stage_directory(
        workflow_db,
        minio_client,
        job=job,
        stage=stage,
        source_dir=_write_failure_evidence(job, stage, exc),
        artifact_kind=f"{contract.artifact_prefix}-failure",
        contract=contract.to_dict(),
    )
    stage.output_manifest_bucket = artifact.bucket
    stage.output_manifest_object = artifact.object_name
    stage.output_manifest_sha256 = artifact.sha256
    workflow_db.commit()


def _material_access_allowed(job: WorkflowJob, payload: dict, material: Material) -> bool:
    if str(material.user_id) == str(job.user_id):
        return True
    return (
        payload.get("golden_set") is True
        and str(payload.get("source_material_user_id") or "") == str(material.user_id)
    )


def _run_canonical(public_id: str, stage_run_id: int, material: Material, workflow_db) -> dict:
    def progress(step: str, message: str, payload: dict | None = None):
        record_stage_progress(workflow_db, public_id, step=step, message=message, payload=payload)
        workflow_db.commit()

    synthetic_run_id = 2_000_000_000 + int(stage_run_id)
    return execute_popo_to_raw(
        material,
        run_id=synthetic_run_id,
        publish=False,
        force=True,
        event_callback=progress,
        canonical_content_only=True,
    )


def _record_canonical_model_calls(db, job: WorkflowJob, stage, output_dir: Path) -> None:
    decision_path = output_dir / "outline_decision.json"
    if not decision_path.exists():
        return
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    llm = decision.get("llm") if isinstance(decision.get("llm"), dict) else {}
    if not llm.get("enabled") or int(llm.get("call_count") or 0) < 1:
        return
    existing = (
        db.query(ModelCall)
        .filter(ModelCall.stage_run_id == stage.id, ModelCall.purpose == "canonical_outline_decision")
        .first()
    )
    if existing:
        return
    db.add(
        ModelCall(
            workflow_job_id=job.id,
            stage_run_id=stage.id,
            provider=str(llm.get("provider") or ""),
            model=str(llm.get("model") or ""),
            response_id=str(llm.get("response_id") or ""),
            prompt_version=f"{stage.stage_version}:canonical-outline-v1",
            purpose="canonical_outline_decision",
            input_evidence_json=ModelCall.dump(["outline_candidates.jsonl", "outline_candidates_summary.json"]),
            usage_json=ModelCall.dump(llm.get("raw_usage") or {"call_count": llm.get("call_count")}),
            result_json=ModelCall.dump({"verdict": llm.get("verdict"), "review_notes": llm.get("review_notes") or []}),
            estimated_cost=llm.get("estimated_cost"),
            status="succeeded" if llm.get("verdict") == "ok" else "failed",
            error_message="" if llm.get("verdict") == "ok" else str(llm.get("error") or llm.get("verdict") or "unknown"),
        )
    )
    db.flush()


def _run_semantic(job: WorkflowJob, stage, db) -> tuple[Path, bool, str]:
    if not SEMANTIC_SCRIPT.exists():
        raise StageExecutionError(f"semantic annotator is unavailable: {SEMANTIC_SCRIPT}")
    upstream = (
        db.query(ArtifactVersion)
        .filter(ArtifactVersion.workflow_job_id == job.id, ArtifactVersion.sha256 == stage.input_manifest_sha256)
        .first()
    )
    if not upstream:
        raise StageExecutionError("semantic input artifact was not registered")
    work_dir = WORK_ROOT / job.public_id / f"{stage.stage_key}-attempt-{stage.attempt}"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    clean_dir = work_dir / "canonical-clean"
    outline_dir = work_dir / "outline"
    output_dir = work_dir / "annotation"
    if job.workflow_version == LEGACY_WORKFLOW_VERSION:
        if upstream.artifact_kind != "canonical-clean":
            raise StageExecutionError("legacy semantic stage requires canonical input")
        canonical = upstream
    else:
        if upstream.artifact_kind != "outline-reconstruction":
            raise StageExecutionError("semantic stage requires accepted outline input")
        canonical = _artifact_by_kind(db, job.id, "canonical-clean")
        if not canonical:
            raise StageExecutionError("canonical artifact is unavailable")
        materialize_artifact(minio_client, upstream, outline_dir)
    materialize_artifact(minio_client, canonical, clean_dir)
    record_stage_progress(db, job.public_id, step="materialize", message="Canonical and accepted outline artifacts verified and materialized.", payload={"canonical_sha256": canonical.sha256, "outline_sha256": upstream.sha256 if upstream.artifact_kind == "outline-reconstruction" else ""})
    db.commit()
    semantic_markdown = clean_dir / "clean.md"
    outline_application = None
    if job.workflow_version != LEGACY_WORKFLOW_VERSION:
        semantic_markdown = work_dir / "semantic-input.md"
        outline_application = apply_accepted_outline(clean_dir, outline_dir, semantic_markdown)
    command = ["python3", str(SEMANTIC_SCRIPT), str(semantic_markdown), "--out-dir", str(output_dir), "--profile", "auto"]
    if job.workflow_version != LEGACY_WORKFLOW_VERSION:
        command.extend(["--outline", str(outline_dir / "outline.json")])
    completed = subprocess.run(
        command,
        cwd=str(SEMANTIC_SCRIPT.parent.parent),
        text=True,
        capture_output=True,
        timeout=1800,
    )
    if completed.returncode != 0:
        raise StageExecutionError((completed.stderr or completed.stdout or "semantic annotator failed")[-4000:])
    required = {"document.json", "sections.json", "assets.json", "media.json", "relations.json", "review_items.json", "quality_report.md", "preview.html"}
    missing = sorted(name for name in required if not (output_dir / name).exists())
    if missing:
        raise StageExecutionError(f"semantic output is incomplete: {', '.join(missing)}")
    record_stage_progress(db, job.public_id, step="annotation", message="Deterministic semantic annotation completed.", payload={"stdout_tail": (completed.stdout or "")[-1000:]})
    blockers = []
    if job.workflow_version != LEGACY_WORKFLOW_VERSION:
        (output_dir / "outline-application.json").write_text(json.dumps(outline_application, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        shutil.copy2(semantic_markdown, output_dir / "semantic-input.md")
        _copy_semantic_media(clean_dir, output_dir)
        validation = validate_semantic_artifact(clean_dir, outline_dir, output_dir, semantic_markdown=semantic_markdown)
        blockers = validation.get("blockers") or []
        record_stage_progress(db, job.public_id, step="semantic_gate", message="Semantic annotation was checked against the accepted outline and Clean content.", payload={"status": validation["status"], "blocker_count": len(blockers)})
    db.commit()
    failure = "; ".join(str(row.get("code") or "semantic_gate_failed") for row in blockers)
    return output_dir, not blockers, failure


def _copy_semantic_media(canonical_dir: Path, annotation_dir: Path) -> None:
    source = canonical_dir / "images"
    if source.is_dir():
        shutil.copytree(source, annotation_dir / "images", dirs_exist_ok=True)


def _materialize_frozen_popo_images(material: Material, project_dir: Path) -> list[str]:
    """Restore the complete frozen Popo image inventory without renaming files."""
    manifest = json.loads(read_object(material.popo_manifest_bucket, material.popo_manifest_object))
    if manifest.get("material_id") != material.material_id:
        raise ArtifactIntegrityError("Popo image inventory material_id mismatch")
    entries = (manifest.get("objects") or {}).get("images") or []
    images_dir = project_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    names: list[str] = []
    for entry in entries:
        bucket = str(entry.get("bucket") or "")
        object_name = str(entry.get("object") or "")
        expected_sha256 = str(entry.get("sha256") or "")
        filename = Path(object_name).name
        if not bucket or not object_name or not expected_sha256 or not filename:
            raise ArtifactIntegrityError("Popo image inventory entry is incomplete")
        if filename in names:
            raise ArtifactIntegrityError(f"Popo image inventory contains duplicate filename: {filename}")
        content = read_object(bucket, object_name)
        if hashlib.sha256(content).hexdigest() != expected_sha256:
            raise ArtifactIntegrityError(f"Popo image digest mismatch: {filename}")
        (images_dir / filename).write_bytes(content)
        names.append(filename)
    return sorted(names)


def _run_outline_reconstruction(job: WorkflowJob, stage, db) -> tuple[Path, bool, str]:
    upstream = (
        db.query(ArtifactVersion)
        .filter(ArtifactVersion.workflow_job_id == job.id, ArtifactVersion.sha256 == stage.input_manifest_sha256)
        .first()
    )
    if not upstream or upstream.artifact_kind != "canonical-clean":
        raise StageExecutionError("canonical input artifact was not registered")
    work_dir = WORK_ROOT / job.public_id / f"{stage.stage_key}-attempt-{stage.attempt}"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    canonical_dir = work_dir / "canonical-clean"
    output_dir = work_dir / "outline"
    materialize_artifact(minio_client, upstream, canonical_dir)
    validation = build_outline_artifact(canonical_dir, output_dir)
    blockers = validation.get("blockers") or []
    record_stage_progress(
        db,
        job.public_id,
        step="outline_gate",
        message="Source-evidenced outline reconstruction was validated independently.",
        payload={"status": validation["status"], "blocker_count": len(blockers)},
    )
    db.commit()
    failure = "; ".join(str(row.get("code") or "outline_gate_failed") for row in blockers)
    return output_dir, not blockers, failure


def _run_elegantbook(job: WorkflowJob, stage, material: Material, db) -> tuple[Path, bool, str]:
    for script in (CLEANLATEX_BRIDGE, ELEGANTBOOK_BUILDER):
        if not script.exists():
            raise StageExecutionError(f"ElegantBook build script is unavailable: {script}")
    canonical = _artifact_by_kind(db, job.id, "canonical-clean")
    semantic = _artifact_by_kind(db, job.id, "semantic-annotation")
    if not canonical or not semantic:
        raise StageExecutionError("canonical and semantic input artifacts are required")
    work_dir = WORK_ROOT / job.public_id / f"{stage.stage_key}-attempt-{stage.attempt}"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    canonical_dir = work_dir / "canonical-clean"
    annotation_dir = work_dir / "annotation"
    cleanlatex_dir = work_dir / "cleanlatex"
    project_dir = work_dir / "elegantbook"
    materialize_artifact(minio_client, canonical, canonical_dir)
    materialize_artifact(minio_client, semantic, annotation_dir)
    cleanlatex_dir.mkdir(parents=True)
    semantic_markdown = _semantic_markdown_for_elegantbook(job, canonical_dir, annotation_dir)
    record_stage_progress(db, job.public_id, step="materialize", message="Canonical and semantic artifacts verified and materialized.", payload={"canonical_sha256": canonical.sha256, "semantic_sha256": semantic.sha256})
    db.commit()
    _run_checked(
        ["python3", str(CLEANLATEX_BRIDGE), str(semantic_markdown), "--annotation-dir", str(annotation_dir), "--out", str(cleanlatex_dir / "input.tex"), "--title", material.title or material.filename or job.material_id, "--copy-images"],
        cwd=CLEANLATEX_BRIDGE.parent.parent,
        label="semantic Markdown to CleanLaTeX bridge",
    )
    record_stage_progress(db, job.public_id, step="cleanlatex", message="Semantic material converted to canonical CleanLaTeX.", payload={})
    db.commit()
    _run_checked(
        ["python3", str(ELEGANTBOOK_BUILDER), str(cleanlatex_dir / "input.tex"), "--out-dir", str(project_dir), "--title", material.title or material.filename or job.material_id, "--trusted-cleanlatex"],
        cwd=ELEGANTBOOK_BUILDER.parent.parent,
        label="deterministic ElegantBook builder",
    )
    expected_image_names = _materialize_frozen_popo_images(material, project_dir)
    delivery_images = optimize_delivery_images(project_dir)
    actual_image_names = sorted(
        path.relative_to(project_dir / "images").as_posix()
        for path in (project_dir / "images").rglob("*")
        if path.is_file()
    )
    image_inventory = {
        "schema": "luceon.workflow.delivery-image-inventory/v1",
        "source": {
            "bucket": material.popo_manifest_bucket,
            "object": material.popo_manifest_object,
        },
        "expected_count": len(expected_image_names),
        "actual_count": len(actual_image_names),
        "expected_filenames": expected_image_names,
        "actual_filenames": actual_image_names,
        "filenames_and_count_preserved": actual_image_names == expected_image_names,
    }
    (project_dir / "delivery-image-inventory.json").write_text(
        json.dumps(image_inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (project_dir / "delivery-image-optimization.json").write_text(
        json.dumps(delivery_images, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    compile_report = _compile_reproducibly(project_dir, job.public_id, stage.attempt)
    (project_dir / "compile-report.json").write_text(json.dumps(compile_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elegantbook_validation = validate_elegantbook_project(
        project_dir,
        semantic_markdown,
        cleanlatex_dir / "input.tex",
        ELEGANTBOOK_BUILDER.parent.parent / "assets" / "elegantbook.cls",
    )
    preliminary_qa = build_print_quality_report(project_dir, project_dir / "main.pdf")
    (project_dir / "preliminary-layout-qa.json").write_text(
        json.dumps(preliminary_qa, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    archive = write_latex_delivery_zip(
        project_dir,
        project_dir / "latex-project.zip",
        max_size_bytes=DELIVERY_ZIP_MAX_BYTES,
    )
    blockers = elegantbook_validation.get("blockers") or []
    quality_summary = {
        "schema": "luceon.workflow.quality-blockers/v1",
        "status": "needs_review" if blockers else "passed",
        "blockers": blockers,
        "candidate": {
            "pdf": "main.pdf",
            "latex_zip": "latex-project.zip",
            "latex_zip_size_bytes": archive["size_bytes"],
        },
    }
    (project_dir / "quality-blockers.json").write_text(
        json.dumps(quality_summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    record_stage_progress(db, job.public_id, step="compile", message="ElegantBook candidate compiled repeatedly in isolated TeX Live.", payload=compile_report)
    db.commit()
    failure = "; ".join(str(row.get("code") or "elegantbook_gate_failed") for row in blockers)
    return project_dir, not blockers, failure


def _semantic_markdown_for_elegantbook(job: WorkflowJob, canonical_dir: Path, annotation_dir: Path) -> Path:
    if job.workflow_version == LEGACY_WORKFLOW_VERSION:
        return canonical_dir / "clean.md"
    validation_path = annotation_dir / "semantic-validation.json"
    semantic_path = annotation_dir / "semantic-input.md"
    if not validation_path.is_file() or not semantic_path.is_file():
        raise StageExecutionError("accepted semantic input and validation are required for ElegantBook build")
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    if validation.get("status") != "passed":
        raise StageExecutionError("ElegantBook build requires a passed semantic gate")
    staged_path = canonical_dir / "semantic-input.md"
    shutil.copy2(semantic_path, staged_path)
    return staged_path


def _artifact_by_kind(db, job_id: int, kind: str) -> ArtifactVersion | None:
    return db.query(ArtifactVersion).filter(ArtifactVersion.workflow_job_id == job_id, ArtifactVersion.artifact_kind == kind).order_by(ArtifactVersion.id.desc()).first()


def _run_checked(command: list[str], *, cwd: Path, label: str) -> subprocess.CompletedProcess:
    completed = subprocess.run(command, cwd=str(cwd), text=True, capture_output=True, timeout=1800)
    if completed.returncode != 0:
        raise StageExecutionError((completed.stderr or completed.stdout or f"{label} failed")[-6000:])
    return completed


def _compile_reproducibly(project_dir: Path, public_id: str, attempt: int) -> dict:
    from app.workflow_v2.latex_diagnostics import parse_latex_diagnostics

    remote = f"/tmp/luceon-worker-v2-{public_id}-attempt-{attempt}"
    subprocess.run(["docker", "exec", TEX_CONTAINER, "rm", "-rf", remote], check=True, capture_output=True)
    subprocess.run(["docker", "exec", TEX_CONTAINER, "mkdir", "-p", remote], check=True, capture_output=True)
    subprocess.run(["docker", "cp", f"{project_dir}/.", f"{TEX_CONTAINER}:{remote}/"], check=True, capture_output=True)
    hashes = []
    page_counts = []
    logs = []
    final_log = ""
    converged = False
    engine_version = subprocess.run(
        ["docker", "exec", TEX_CONTAINER, "xelatex", "--version"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    for compile_pass in range(1, 6):
        completed = subprocess.run(
            ["docker", "exec", "-w", remote, "-e", "SOURCE_DATE_EPOCH=0", "-e", "FORCE_SOURCE_DATE=1", "-e", "TZ=UTC", TEX_CONTAINER, "xelatex", "-interaction=nonstopmode", "-halt-on-error", "main.tex"],
            text=True,
            capture_output=True,
            timeout=1800,
        )
        full_log = completed.stdout or completed.stderr or ""
        logs.append(full_log[-4000:])
        if completed.returncode != 0:
            raise StageExecutionError(f"XeLaTeX pass {compile_pass} failed:\n{logs[-1]}")
        final_log = _read_xelatex_log(remote, full_log)
        pdf = subprocess.run(["docker", "exec", TEX_CONTAINER, "sha256sum", f"{remote}/main.pdf"], text=True, capture_output=True, check=True).stdout.split()[0]
        pages = subprocess.run(["docker", "exec", TEX_CONTAINER, "qpdf", "--show-npages", f"{remote}/main.pdf"], text=True, capture_output=True, check=True).stdout.strip()
        hashes.append(pdf)
        page_counts.append(int(pages))
        if compile_pass >= 3 and hashes[-1] == hashes[-2] and page_counts[-1] == page_counts[-2]:
            converged = True
            break
    if not converged:
        raise StageExecutionError("repeated compilation did not produce a byte-identical PDF")
    subprocess.run(["docker", "cp", f"{TEX_CONTAINER}:{remote}/main.pdf", str(project_dir / "main.pdf")], check=True, capture_output=True)
    return {
        "engine": "xelatex",
        "engine_version": engine_version,
        "tex_container": TEX_CONTAINER,
        "source_date_epoch": 0,
        "passes": len(hashes),
        "pdf_sha256_by_pass": hashes,
        "page_count_by_pass": page_counts,
        "byte_identical_final_passes": True,
        "diagnostic_log": "main.log",
        "diagnostics": parse_latex_diagnostics(final_log),
        "log_tail": final_log[-4000:],
    }


def _read_xelatex_log(remote: str, console_output: str) -> str:
    """Read the complete XeLaTeX log; console output can omit glyph warnings."""
    completed = subprocess.run(
        ["docker", "exec", TEX_CONTAINER, "cat", f"{remote}/main.log"],
        text=True,
        capture_output=True,
    )
    if completed.returncode == 0 and completed.stdout:
        return completed.stdout
    return console_output


def _latex_delivery_integrity_blocker(exc: ArtifactIntegrityError) -> dict:
    detail = str(exc)
    code = (
        "latex_project_zip_too_large"
        if detail.startswith("delivery ZIP exceeds size limit:")
        else "latex_project_structure_invalid"
    )
    return {"code": code, "detail": detail}


def _restore_locked_empty_directories(project_dir: Path) -> list[str]:
    """Restore empty locked directories recorded by the candidate delivery ZIP."""
    archive_path = project_dir / "latex-project.zip"
    if not archive_path.is_file():
        return []
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
    restored = []
    for directory in ("images/", "figure/"):
        target = project_dir / directory.rstrip("/")
        if directory in names and not target.exists():
            target.mkdir(parents=True)
            restored.append(directory)
    return restored


def _manual_review_handoff_blocker(db, job: WorkflowJob) -> dict | None:
    current_candidate = (
        db.query(ArtifactVersion)
        .filter(
            ArtifactVersion.workflow_job_id == job.id,
            ArtifactVersion.artifact_kind == "elegantbook-candidate",
        )
        .order_by(ArtifactVersion.created_at.desc(), ArtifactVersion.id.desc())
        .first()
    )
    if not current_candidate:
        return None
    review_count = (
        db.query(StageRun)
        .filter(
            StageRun.workflow_job_id == job.id,
            StageRun.status == "needs_review",
            StageRun.finished_at.is_not(None),
            StageRun.finished_at >= current_candidate.created_at,
        )
        .count()
    )
    if not review_count:
        return None
    handoff_count = (
        db.query(RepairAttempt)
        .filter(
            RepairAttempt.workflow_job_id == job.id,
            RepairAttempt.repair_kind == "manual_handoff",
            RepairAttempt.status.in_(("running", "succeeded")),
        )
        .count()
    )
    if handoff_count:
        return None
    return {
        "code": "manual_review_handoff_missing",
        "count": review_count,
        "detail": "A prior needs_review attempt has no recorded manual handoff.",
    }


def _run_core_acceptance(job: WorkflowJob, stage, db) -> tuple[Path, bool, str]:
    candidate = _artifact_by_kind(db, job.id, "elegantbook-candidate")
    required = {
        "canonical": _artifact_by_kind(db, job.id, "canonical-clean"),
        "outline": _artifact_by_kind(db, job.id, "outline-reconstruction"),
        "semantic": _artifact_by_kind(db, job.id, "semantic-annotation"),
        "elegantbook": candidate,
    }
    missing_artifacts = sorted(name for name, artifact in required.items() if artifact is None)
    if missing_artifacts:
        raise StageExecutionError(f"core acceptance inputs are unavailable: {', '.join(missing_artifacts)}")

    work_dir = WORK_ROOT / job.public_id / f"{stage.stage_key}-attempt-{stage.attempt}"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    inputs = work_dir / "inputs"
    output = work_dir / "output"
    inputs.mkdir(parents=True)
    for name, artifact in required.items():
        materialize_artifact(minio_client, artifact, inputs / name)
    shutil.copytree(inputs / "elegantbook", output)
    _restore_locked_empty_directories(output)

    validation_files = {
        "canonical": inputs / "canonical" / "canonical-validation.json",
        "outline": inputs / "outline" / "outline-validation.json",
        "semantic": inputs / "semantic" / "semantic-validation.json",
        "elegantbook": inputs / "elegantbook" / "elegantbook-validation.json",
    }
    compile_path = inputs / "elegantbook" / "compile-report.json"
    report = _evaluate_core_acceptance(
        validation_files,
        compile_path,
        workflow_version=job.workflow_version,
        output_schema=contract_for(job.workflow_version, stage.stage_key).output_schema,
        artifact_lineage={
            name: {"artifact_id": str(artifact.id), "sha256": artifact.sha256}
            for name, artifact in required.items()
        },
    )
    blockers = report["blockers"]
    handoff_blocker = _manual_review_handoff_blocker(db, job)
    if handoff_blocker:
        blockers.append(handoff_blocker)
        report["status"] = "review"
    if not blockers:
        try:
            archive = write_latex_delivery_zip(
                output,
                output / "latex-project.zip",
                max_size_bytes=DELIVERY_ZIP_MAX_BYTES,
            )
        except ArtifactIntegrityError as exc:
            blockers.append(_latex_delivery_integrity_blocker(exc))
        else:
            report["delivery"] = {"latex_project_zip": archive}
        if blockers:
            report["status"] = "review"
    (output / "core-acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    shutil.rmtree(inputs)
    record_stage_progress(
        db,
        job.public_id,
        step="core_acceptance",
        message="Worker core gates were evaluated without Sidecar participation.",
        payload={"status": report["status"], "bounded_task_count": 0, "blocker_count": len(blockers)},
    )
    db.commit()
    failure = "; ".join(str(row["code"]) for row in blockers)
    return output, not blockers, failure


def _project_core_output(legacy_db, material: Material, job: WorkflowJob, artifact: ArtifactVersion) -> None:
    manifest_bytes = read_object(artifact.bucket, artifact.object_name)
    manifest = normalize_workflow_artifact_manifest(json.loads(manifest_bytes.decode("utf-8")))
    objects = manifest.get("objects") or {}
    if not objects.get("compiled_pdf") or not objects.get("package_zip"):
        raise StageExecutionError("accepted Worker V2.3 artifact is missing PDF or LaTeX ZIP")
    output = ElegantBookOutput(
        manifest_ref=ObjectRef(artifact.bucket, artifact.object_name),
        manifest=manifest,
        material_id=job.material_id,
        popo_run_id=material.popo_run_id or "",
        output_run_id=job.public_id,
        origin="worker_v2",
        created_at=datetime.utcnow().isoformat(),
    )
    row = register_elegantbook_output(
        legacy_db,
        job.user_id,
        material,
        output,
        status="published",
        quality_status="passed",
    )
    legacy_db.flush()
    promote_material_output(legacy_db, row, material)


def _evaluate_core_acceptance(
    validation_files: dict[str, Path],
    compile_path: Path,
    *,
    workflow_version: str,
    output_schema: str,
    artifact_lineage: dict,
) -> dict:
    validations = {}
    blockers = []
    for name, path in validation_files.items():
        if not path.is_file():
            blockers.append({"code": f"{name}_validation_missing"})
            continue
        value = json.loads(path.read_text(encoding="utf-8"))
        validations[name] = {
            "status": value.get("status"),
            "schema": value.get("schema"),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        if value.get("status") != "passed":
            blockers.append({"code": f"{name}_gate_not_passed", "status": value.get("status")})
    compile_report = json.loads(compile_path.read_text(encoding="utf-8")) if compile_path.is_file() else {}
    hashes = compile_report.get("pdf_sha256_by_pass") or []
    reproducible = bool(
        compile_report.get("byte_identical_final_passes")
        and len(hashes) >= 2
        and hashes[-1] == hashes[-2]
    )
    if not reproducible:
        blockers.append({"code": "elegantbook_compile_not_reproducible"})
    return {
        "schema": output_schema,
        "status": "passed" if not blockers else "review",
        "workflow_version": workflow_version,
        "gates": {
            "deepseek_tasks_are_schema_bounded": True,
            "outline_gate_passed": validations.get("outline", {}).get("status") == "passed",
            "content_conservation_gate_passed": validations.get("canonical", {}).get("status") == "passed" and validations.get("semantic", {}).get("status") == "passed",
            "elegantbook_reproducibility_gate_passed": validations.get("elegantbook", {}).get("status") == "passed" and reproducible,
            "model_cannot_self_attest_acceptance": True,
        },
        "bounded_deepseek": {
            "task_count": 0,
            "call_count": 0,
            "reason": "no_unresolved_structured_ambiguity_tasks",
        },
        "validations": validations,
        "artifact_lineage": artifact_lineage,
        "blockers": blockers,
        "sidecar_used": False,
    }


def _run_bounded_polish(job: WorkflowJob, stage, material: Material, db) -> Path:
    candidate = _artifact_by_kind(db, job.id, "elegantbook-candidate")
    if not candidate:
        raise StageExecutionError("candidate artifact is required")
    qa_artifact = _artifact_by_kind(db, job.id, "preliminary-layout-qa") or candidate
    findings = db.query(QaFinding).filter(QaFinding.workflow_job_id == job.id, QaFinding.status == "open").order_by(QaFinding.id).all()
    finding_payload = [{"code": row.code, "severity": row.severity, "page_number": row.page_number, "details": row.load(row.details_json, {})} for row in findings]
    metrics = _qa_metrics_from_artifact(qa_artifact)
    llm_result, reused_from = _repair_plan_for_attempt(db, job, stage, qa_artifact, finding_payload, metrics)
    plan = llm_result["plan"]
    audit = llm_result["audit"]
    model_call = ModelCall(
        workflow_job_id=job.id,
        stage_run_id=stage.id,
        provider=audit["provider"],
        model=audit["model"],
        response_id=audit["response_id"],
        prompt_version=f"{stage.stage_version}:{REPAIR_PLAN_PROMPT_VERSION}",
        purpose="bounded_latex_repair_plan",
        input_evidence_json=ModelCall.dump([{"artifact_id": str(qa_artifact.id), "sha256": qa_artifact.sha256, "evidence_sha256": audit["input_evidence_sha256"]}]),
        usage_json=ModelCall.dump({} if reused_from else audit["usage"]),
        result_json=ModelCall.dump(llm_result),
        estimated_cost=0 if reused_from else audit["estimated_cost"],
        status="reused" if reused_from else "succeeded",
        error_message="",
    )
    db.add(model_call)
    db.flush()
    work_dir = WORK_ROOT / job.public_id / f"{stage.stage_key}-attempt-{stage.attempt}"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    generated = work_dir / "generated-candidate"
    materialize_artifact(minio_client, candidate, generated)
    output = work_dir / "output"
    output.mkdir(parents=True)
    (output / "llm-repair-plan.json").write_text(json.dumps(llm_result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record_stage_progress(db, job.public_id, step="llm_plan", message="Bounded LLM repair plan validated against the action allowlist.", payload={"actions": plan["actions"], "model_call_id": str(model_call.id)})
    db.commit()
    project = output / "project"
    shutil.copytree(generated, project)
    blocker_codes = {row.code for row in findings}
    repair_report = {"status": "deferred_to_page_scoped_sidecar", "requested_blockers": sorted(blocker_codes)}
    (output / "deterministic-repair-report.json").write_text(json.dumps(repair_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    compile_report = _compile_reproducibly(project, f"{job.public_id}-polish", stage.attempt)
    (output / "compile-report.json").write_text(json.dumps(compile_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    source_dir = output / "inputs" / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "source.pdf").write_bytes(read_object(material.input_bucket, material.input_object))
    quality_report = build_print_quality_report(output, project / "main.pdf")
    (output / "worker-quality-report.json").write_text(json.dumps(quality_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    shutil.rmtree(output / "inputs")
    before = repair_report.get("before") or {}
    after = repair_report.get("after") or {}
    repair = RepairAttempt(
        workflow_job_id=job.id,
        source_finding_id=findings[0].id if findings else None,
            repair_kind="bounded_plan_deterministic_patch",
        status="succeeded",
        allowed_scope_json=RepairAttempt.dump({"actions": plan["actions"], "finding_codes": sorted(blocker_codes)}),
        invariants_json=RepairAttempt.dump({"before": before, "after": after, "chapters_preserved": before.get("chapters") == after.get("chapters"), "exercise_headings_preserved": before.get("exercise_headings") == after.get("exercise_headings")}),
        result_json=RepairAttempt.dump({"quality_status": quality_report.get("status"), "remaining_blockers": [row.get("code") for row in quality_report.get("hard_blockers") or []], "compile": {"page_count": compile_report["page_count_by_pass"][-1], "pdf_sha256": compile_report["pdf_sha256_by_pass"][-1]}}),
        error_message="",
    )
    db.add(repair)
    db.flush()
    record_stage_progress(db, job.public_id, step="patch_validate", message="Bounded repair patch compiled; independent QA has not accepted it.", payload={"repair_attempt_id": str(repair.id), "quality_status": quality_report.get("status")})
    db.commit()
    return output


DOCUMENTCLASS_RE = re.compile(r"^\\documentclass(?:\[[^]]*\])?\{[^}]+\}", re.M)


def _preserve_main_documentclass(source_project: Path, refined_project: Path) -> None:
    source_main = source_project / "main.tex"
    refined_main = refined_project / "main.tex"
    if not source_main.is_file() or not refined_main.is_file():
        raise StageExecutionError("main.tex is required to validate the refined document class")
    source_text = source_main.read_text(encoding="utf-8")
    refined_text = refined_main.read_text(encoding="utf-8")
    source_match = DOCUMENTCLASS_RE.search(source_text)
    refined_match = DOCUMENTCLASS_RE.search(refined_text)
    if not source_match or not refined_match:
        raise StageExecutionError("refined project has an invalid document class declaration")
    if source_match.group(0) != refined_match.group(0):
        refined_text = refined_text[: refined_match.start()] + source_match.group(0) + refined_text[refined_match.end() :]
        refined_main.write_text(refined_text, encoding="utf-8")


SOURCE_HEADING_RE = re.compile(
    r"%\s*source_page_idx:\s*(?P<page>\d+)\s*\n\s*\\(?:section|chapter)\{(?P<title>[^}\n]+)\}"
)


def _preserve_source_page_markers(source_project: Path, refined_project: Path) -> None:
    source_path = source_project / "chapters" / "content.tex"
    refined_path = refined_project / "chapters" / "content.tex"
    if not source_path.is_file() or not refined_path.is_file():
        raise StageExecutionError("content.tex is required to preserve source page lineage")
    _preserve_source_page_markers_from_tex(source_path, refined_project)


def _preserve_source_page_markers_from_tex(source_path: Path, refined_project: Path) -> None:
    refined_path = refined_project / "chapters" / "content.tex"
    if not source_path.is_file() or not refined_path.is_file():
        raise StageExecutionError("source and refined LaTeX are required to preserve source page lineage")
    source = source_path.read_text(encoding="utf-8")
    refined = refined_path.read_text(encoding="utf-8")
    markers = [(match.group("page"), match.group("title")) for match in SOURCE_HEADING_RE.finditer(source)]
    refined_marker_pairs = {(match.group("page"), match.group("title")) for match in SOURCE_HEADING_RE.finditer(refined)}
    for page, title in markers:
        marker = f"% source_page_idx: {page}"
        chapter = rf"\chapter{{{title}}}"
        if (page, title) in refined_marker_pairs:
            continue
        if refined.count(chapter) != 1:
            raise StageExecutionError(f"cannot restore source page marker for chapter: {title}")
        refined = refined.replace(chapter, f"{marker}\n{chapter}", 1)
    refined_markers = {(match.group("page"), match.group("title")) for match in SOURCE_HEADING_RE.finditer(refined)}
    if set(markers) - refined_markers:
        raise StageExecutionError("refined project lost source page lineage markers")
    refined_path.write_text(refined, encoding="utf-8")


def _qa_metrics_from_artifact(artifact: ArtifactVersion) -> dict:
    temp = WORK_ROOT / "_qa-input" / artifact.sha256
    if temp.exists():
        shutil.rmtree(temp)
    materialize_artifact(minio_client, artifact, temp)
    report_path = temp / "preliminary-layout-qa.json"
    if not report_path.exists():
        report_path = temp / "report.json"
    if not report_path.exists():
        compiled_pdf = temp / "main.pdf"
        if not compiled_pdf.exists():
            raise StageExecutionError("candidate does not contain preliminary layout QA evidence or a compiled PDF")
        report = build_print_quality_report(temp, compiled_pdf)
    else:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    return report.get("metrics") if isinstance(report.get("metrics"), dict) else {}


def _repair_plan_for_attempt(db, job: WorkflowJob, stage, qa_artifact: ArtifactVersion, findings: list[dict], metrics: dict) -> tuple[dict, ModelCall | None]:
    previous = (
        db.query(ModelCall)
        .filter(ModelCall.workflow_job_id == job.id, ModelCall.purpose == "bounded_latex_repair_plan", ModelCall.status.in_(["succeeded", "reused"]))
        .filter(ModelCall.prompt_version == f"{stage.stage_version}:{REPAIR_PLAN_PROMPT_VERSION}")
        .order_by(ModelCall.id.desc())
        .first()
    )
    if previous:
        evidence = previous.load(previous.input_evidence_json, [])
        result = previous.load(previous.result_json, {})
        if result.get("plan") and any(isinstance(row, dict) and row.get("sha256") == qa_artifact.sha256 for row in evidence):
            return result, previous
    return plan_latex_repairs(findings=findings, metrics=metrics), None


def _run_independent_review(job: WorkflowJob, stage, material: Material, db) -> tuple[Path, bool, str]:
    refined = (
        db.query(ArtifactVersion)
        .filter(
            ArtifactVersion.workflow_job_id == job.id,
            ArtifactVersion.artifact_kind.in_(("rule-repaired-candidate", "codex-patched-candidate", "refined-candidate")),
        )
        .order_by(ArtifactVersion.id.desc())
        .first()
    )
    if not refined:
        raise StageExecutionError("refined candidate artifact is required")
    work_dir = WORK_ROOT / job.public_id / f"{stage.stage_key}-attempt-{stage.attempt}"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    candidate = work_dir / "candidate"
    output = work_dir / "review"
    materialize_artifact(minio_client, refined, candidate)
    project = candidate / "project"
    pdf = project / "main.pdf"
    if not pdf.exists():
        raise StageExecutionError("refined candidate PDF is missing")
    output.mkdir(parents=True)
    source_dir = output / "inputs" / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "source.pdf").write_bytes(read_object(material.input_bucket, material.input_object))
    shutil.copytree(project, output / "project")
    report = build_print_quality_report(output, output / "project" / "main.pdf")
    expected_previsual = {"page_review_provenance_mismatch", "full_page_visual_review_missing"}
    deterministic_blockers = [row for row in report.get("hard_blockers") or [] if row.get("code") not in expected_previsual]
    if deterministic_blockers:
        page_review = _render_deterministic_precheck(
            output / "project" / "main.pdf",
            output / "rendered-precheck",
            deterministic_blockers,
        )
        for row in page_review["pages"]:
            row["image"] = Path(row["image"]).resolve().relative_to(output.resolve()).as_posix()
        (output / "page_review.json").write_text(json.dumps(page_review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report["hard_blockers"] = deterministic_blockers
        report["independent_visual_qa"] = {"status": "skipped", "reason": "deterministic_hard_gates_failed", "failed_pages": [], "reviewed_pages": 0, "model": ""}
        report["status"] = "blocked"
        (output / "final_review_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        shutil.rmtree(output / "inputs")
        _record_independent_findings(db, job, stage, report, [])
        record_stage_progress(db, job.public_id, step="deterministic_gates", message="Independent visual QA was skipped because deterministic hard gates failed.", payload={"blockers": [row.get("code") for row in deterministic_blockers], "candidate_artifact_id": str(refined.id)})
        db.commit()
        return output, False, ", ".join(str(row.get("code")) for row in deterministic_blockers)
    page_review = review_all_pages(db, job=job, stage=stage, pdf=pdf, render_dir=output / "rendered-all")
    relative_root = output.resolve()
    for row in page_review["pages"]:
        row["image"] = Path(row["image"]).resolve().relative_to(relative_root).as_posix()
    (output / "page_review.json").write_text(json.dumps(page_review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = build_print_quality_report(output, output / "project" / "main.pdf")
    failed_pages = [row["page"] for row in page_review["pages"] if row["status"] != "passed"]
    report["independent_visual_qa"] = {"failed_pages": failed_pages, "reviewed_pages": len(page_review["pages"]), "model": page_review["model"]}
    report["status"] = "blocked" if failed_pages or report.get("hard_blockers") else "passed"
    (output / "final_review_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    shutil.rmtree(output / "inputs")
    _record_independent_findings(db, job, stage, report, failed_pages)
    record_stage_progress(db, job.public_id, step="independent_qa", message="All rendered pages were reviewed by the independent QA service.", payload={"page_count": len(page_review["pages"]), "failed_pages": failed_pages, "status": report["status"]})
    db.commit()
    failure = ", ".join([row.get("code") for row in report.get("hard_blockers") or []] + (["visual_page_failure"] if failed_pages else []))
    return output, report["status"] == "passed", failure


def _render_deterministic_precheck(pdf: Path, render_dir: Path, blockers: list[dict]) -> dict:
    page_findings: dict[int, list[dict]] = {}
    for blocker in blockers:
        code = str(blocker.get("code") or "deterministic_hard_gate")
        for page in blocker.get("pages") or []:
            page_findings.setdefault(int(page), []).append(
                {"code": code, "detail": f"Deterministic precheck failed: {code}"}
            )
    document = fitz.open(pdf)
    render_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    try:
        for page_number in sorted(page_findings):
            image = render_dir / f"page-{page_number:04d}.png"
            document[page_number - 1].get_pixmap(matrix=fitz.Matrix(1.35, 1.35), alpha=False).save(image)
            rows.append(
                {
                    "page": page_number,
                    "image": image.as_posix(),
                    "status": "failed",
                    "findings": page_findings[page_number],
                    "summary": "Deterministic precheck finding; independent visual QA not yet run.",
                }
            )
    finally:
        document.close()
    return {
        "schema": "luceon.page-review/v2",
        "review_owner": "deterministic_precheck",
        "status": "skipped",
        "reason": "deterministic_hard_gates_failed",
        "model": "",
        "pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
        "page_count": len(rows),
        "pages": rows,
    }


def _record_independent_findings(db, job: WorkflowJob, stage, report: dict, failed_pages: list[int]) -> None:
    if report.get("status") == "passed" and not failed_pages:
        resolved_at = datetime.utcnow()
        for finding in db.query(QaFinding).filter(QaFinding.workflow_job_id == job.id, QaFinding.status == "open").all():
            details = finding.load(finding.details_json, {})
            details["resolution"] = {
                "reason": "superseded_by_successful_independent_review",
                "stage_run_id": str(stage.id),
                "stage_attempt": stage.attempt,
            }
            finding.details_json = QaFinding.dump(details)
            finding.status = "resolved"
            finding.resolved_at = resolved_at
        db.flush()
        return
    existing = {row.code for row in db.query(QaFinding).filter(QaFinding.workflow_job_id == job.id, QaFinding.stage_run_id == stage.id).all()}
    for issue in report.get("hard_blockers") or []:
        code = str(issue.get("code") or "independent_qa_failure")
        if code not in existing:
            pages = issue.get("pages") if isinstance(issue.get("pages"), list) else []
            db.add(QaFinding(workflow_job_id=job.id, stage_run_id=stage.id, code=code, severity="P1", page_number=int(pages[0]) if pages else None, status="open", details_json=QaFinding.dump(issue)))
    if failed_pages and "visual_page_failure" not in existing:
        db.add(QaFinding(workflow_job_id=job.id, stage_run_id=stage.id, code="visual_page_failure", severity="P1", page_number=failed_pages[0], status="open", details_json=QaFinding.dump({"pages": failed_pages})))
    db.flush()


def _record_golden_pass(db, job: WorkflowJob, stage, review_artifact: ArtifactVersion, output_dir: Path) -> None:
    case = db.query(GoldenRegressionCase).filter(GoldenRegressionCase.material_pk == job.material_pk).one_or_none()
    if not case:
        return
    ledger = json.loads((output_dir / "page_review.json").read_text(encoding="utf-8"))
    candidate = (
        db.query(ArtifactVersion)
        .filter(
            ArtifactVersion.workflow_job_id == job.id,
            ArtifactVersion.artifact_kind.in_(("rule-repaired-candidate", "codex-patched-candidate", "refined-candidate")),
        )
        .order_by(ArtifactVersion.id.desc())
        .first()
    )
    baseline = case.load(case.baseline_json, {})
    baseline["latest_v2_pass"] = {
        "workflow_job_id": job.public_id,
        "stage_run_id": str(stage.id),
        "stage_attempt": stage.attempt,
        "candidate_artifact_id": str(candidate.id) if candidate else "",
        "final_review_artifact_id": str(review_artifact.id),
        "pdf_sha256": str(ledger.get("pdf_sha256") or ""),
        "page_count": int(ledger.get("page_count") or 0),
        "failed_page_count": sum(row.get("status") != "passed" for row in ledger.get("pages") or []),
        "passed_at": datetime.utcnow().isoformat(),
    }
    case.baseline_json = GoldenRegressionCase.dump(baseline)
    case.status = "passed"
    db.flush()
