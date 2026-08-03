from __future__ import annotations

import hashlib
import json
import tarfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import fitz
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.base import Base
from app.models.material import Material, MaterialOutput
from app.models.review_asset import ReviewAsset
from app.workflow_v3.contracts import WORKFLOW_VERSION, contracts_for_version
from app.workflow_v3.executor import (
    ArtifactIntegrityError,
    ArtifactRef,
    DirectoryArtifactStore,
)
from app.workflow_v3.models import (
    WorkflowV3Base,
    WorkflowV3Candidate,
    WorkflowV3Evaluation,
    WorkflowV3Execution,
    WorkflowV3Job,
    WorkflowV3ProjectionOutbox,
    WorkflowV3Promotion,
    WorkflowV3SkillRelease,
    WorkflowV3StageRun,
)
from app.workflow_v3.projection import (
    WorkflowV3ProjectionProcessor,
    claim_projection_outbox,
)
from app.workflow_v3.service import (
    retry_projection_outbox,
    workflow_job_detail,
)
from app.workflow_v3.stage_evaluators import (
    PDF_RASTER_PROFILE,
    _pdf_page_raster_sha256,
)
from app.workflow_v3.state_machine import (
    WorkflowV3TransitionError,
    _enqueue_final_projection,
    record_human_acceptance,
)


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_file(path: Path) -> str:
    return _sha_bytes(path.read_bytes())


def _write(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)


def _write_pdf(
    path: Path,
    value: str,
    *,
    metadata: dict[str, str] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with fitz.open() as document:
        page = document.new_page(width=612, height=792)
        page.insert_text((72, 72), value)
        if metadata:
            document.set_metadata(metadata)
        document.save(path)


def _json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )


def _binding(root: Path, path: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha_file(path),
        "size_bytes": path.stat().st_size,
    }


def _candidate_sha(stage_key: str) -> str:
    return _sha_bytes(f"candidate:{stage_key}".encode())


def _build_final_bundle(
    tmp_path: Path,
    *,
    job_id: str,
    stage11_sha256: str,
    release_sha256: str,
    prior_candidate_shas: list[str],
    volume_count: int,
    corrupt_page_binding: bool = False,
    reverse_recompile_order: bool = False,
    misbind_compiled_pdf: bool = False,
) -> Path:
    root = tmp_path / "final-bundle"
    source_pdf = root / "lineage/source.pdf"
    _write_pdf(source_pdf, "source")
    delivery_rows = []
    audit_rows = []
    review_rows = []
    recompile_rows = []
    for index in range(1, volume_count + 1):
        volume_id = f"volume-{index}"
        delivery_zip = root / f"spec05/delivery/{volume_id}.zip"
        reviewed_pdf = root / f"spec05/delivery/{volume_id}.pdf"
        audit_report = root / f"audit/{volume_id}/latex_polish_report.json"
        raster = root / f"review/pages/{volume_id}-page-0001.png"
        recompiled_pdf = root / f"recompile/{volume_id}/main.pdf"
        compile_log = root / f"recompile/{volume_id}/main.log"
        _write(delivery_zip, f"zip:{volume_id}".encode())
        _write_pdf(reviewed_pdf, f"reviewed:{volume_id}")
        _json(
            audit_report,
            {
                "mode": "audit",
                "changes": [],
                "replacement_zip": None,
            },
        )
        _write(raster, f"png:{volume_id}:page-1".encode())
        _write_pdf(
            recompiled_pdf,
            f"reviewed:{volume_id}",
            metadata={"author": "independent-stage-11"},
        )
        _write(compile_log, f"compiled {volume_id}\n".encode())
        delivery_rows.append(
            {
                "volume_id": volume_id,
                "delivery_zip": _binding(root / "spec05", delivery_zip),
            }
        )
        audit_rows.append(
            {
                "volume_id": volume_id,
                "delivery_zip": _binding(root, delivery_zip),
                "audit_report": _binding(root, audit_report),
                "kernel_returncode": 0,
            }
        )
        image_binding = _binding(root, raster)
        if corrupt_page_binding:
            image_binding["sha256"] = "0" * 64
        review_rows.append(
            {
                "volume_id": volume_id,
                "candidate_pdf": _binding(root, reviewed_pdf),
                "candidate_pdf_sha256": _sha_file(reviewed_pdf),
                "page_count": 1,
                "pages": [
                    {
                        "page": 1,
                        "image": image_binding,
                        "image_sha256": image_binding["sha256"],
                        "status": "reviewed_passed",
                        "source_evidence": [{"source_page": 1}],
                        "findings": [],
                    }
                ],
            }
        )
        compiled_binding = _binding(
            root,
            reviewed_pdf if misbind_compiled_pdf else recompiled_pdf,
        )
        recompile_rows.append(
            {
                "volume_id": volume_id,
                "delivery_zip": _binding(root, delivery_zip),
                "delivery_zip_sha256": _sha_file(delivery_zip),
                "reviewed_pdf": _binding(root, reviewed_pdf),
                "reviewed_pdf_sha256": _sha_file(reviewed_pdf),
                "compiled_pdf": compiled_binding,
                "compiled_pdf_sha256": compiled_binding["sha256"],
                "compiled_page_count": 1,
                "raster_profile": dict(PDF_RASTER_PROFILE),
                "reviewed_page_raster_sha256":
                    _pdf_page_raster_sha256(reviewed_pdf),
                "compiled_page_raster_sha256":
                    _pdf_page_raster_sha256(recompiled_pdf),
                "visual_equivalent": True,
                "compile_log": _binding(root, compile_log),
                "compile_log_sha256": _sha_file(compile_log),
                "xelatex_version": "XeTeX test",
                "latexmk_version": "latexmk test",
            }
        )
    if reverse_recompile_order:
        recompile_rows.reverse()
    _json(
        root / "spec05/manifests/delivery_set_manifest.json",
        {
            "schema_version": "spec05-delivery-set-manifest/1.2",
            "spec_status": "passed",
            "volume_count": volume_count,
            "volumes": delivery_rows,
        },
    )
    _json(
        root / "manifests/readonly_latex_audit.json",
        {
            "schema_version": "luceon.worker-v3-readonly-latex-audit/v1",
            "input_bytes_unchanged": True,
            "replacement_product_created": False,
            "volumes": audit_rows,
        },
    )
    page_review = {
        "schema_version": "luceon.worker-v3-full-page-review-evidence/v1",
        "review_scope": "all_pages_source_fidelity",
        "source_pdf": _binding(root, source_pdf),
        "source_pdf_sha256": _sha_file(source_pdf),
        "source_page_count": 1,
        "blocking_findings": 0,
        "human_accepted": False,
        "volumes": review_rows,
    }
    _json(root / "reports/page_review.json", page_review)
    _json(
        root / "manifests/full_page_review.json",
        {
            "schema_version": "luceon.worker-v3-full-page-review/v1",
            "page_review": _binding(root, root / "reports/page_review.json"),
            "source_pdf_sha256": _sha_file(source_pdf),
            "volume_count": volume_count,
            "blocking_findings": 0,
        },
    )
    _json(
        root / "manifests/delivery_recompile.json",
        {
            "schema_version": "luceon.worker-v3-delivery-recompile/v1",
            "compiler": "latexmk-xelatex",
            "target_environment_sha256": "6" * 64,
            "volumes": recompile_rows,
        },
    )
    prior_keys = [
        contract.key for contract in contracts_for_version(WORKFLOW_VERSION)[:-1]
    ]
    chain = {
        "schema_version": "luceon.worker-v3-promotion-chain/v1",
        "promotions": [
            {
                "stage_key": key,
                "evaluation_decision": "passed",
                "promotion_status": "promoted",
                "artifact_sha256": prior_candidate_shas[index],
            }
            for index, key in enumerate(prior_keys)
        ],
    }
    _json(root / "lineage/promotion_chain.json", chain)
    _json(
        root / "lineage/lineage_attestation.json",
        {
            "schema_version": "luceon.worker-v3-page-db-minio-lineage/v1",
            "consistent": True,
            "open_blockers": [],
        },
    )
    _json(
        root / "manifests/ready_for_user_acceptance.json",
        {
            "schema_version": "luceon.worker-v3-ready-for-user-acceptance/v1",
            "machine_status": "succeeded",
            "spec_status": "passed",
            "readiness": "ready_for_user_acceptance",
            "promotion_chain": _binding(
                root,
                root / "lineage/promotion_chain.json",
            ),
            "promotion_chain_sha256": _sha_file(
                root / "lineage/promotion_chain.json"
            ),
            "lineage_attestation": _binding(
                root,
                root / "lineage/lineage_attestation.json",
            ),
            "lineage_consistent": True,
            "open_blockers": [],
            "human_accepted": False,
            "user_acceptance_record": None,
        },
    )
    inventory = []
    for path in sorted(value for value in root.rglob("*") if value.is_file()):
        inventory.append(
            {
                "path": path.relative_to(root).as_posix(),
                "role": "artifact",
                "sha256": _sha_file(path),
                "size_bytes": path.stat().st_size,
            }
        )
    _json(
        root / "candidate-content-manifest.json",
        {
            "schema_version": "luceon.worker-v3-candidate-bundle/v1",
            "job_id": job_id,
            "stage_key": "ready_for_user_acceptance",
            "stage_version": contracts_for_version(WORKFLOW_VERSION)[-1].stage_version,
            "attempt": 1,
            "artifact_kind": "worker-v3-ready-for-user-acceptance-candidate",
            "input_sha256": stage11_sha256,
            "predecessor_promotion_sha256": "7" * 64,
            "release_manifest_sha256": release_sha256,
            "files": inventory,
        },
    )
    archive = tmp_path / "ready-for-user-acceptance.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        for path in sorted(value for value in root.rglob("*") if value.is_file()):
            handle.add(
                path,
                arcname=path.relative_to(root).as_posix(),
                recursive=False,
            )
    return archive


class RecordingFormalStore:
    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}
        self.calls: list[tuple[str, str]] = []
        self.new_writes = 0

    def put_formal(
        self,
        source: Path,
        *,
        bucket: str,
        object_name: str,
        expected_sha256: str,
        content_type: str = "application/octet-stream",
    ) -> ArtifactRef:
        del content_type
        payload = source.read_bytes()
        if _sha_bytes(payload) != expected_sha256:
            raise ArtifactIntegrityError("formal source hash mismatch")
        key = (bucket, object_name)
        self.calls.append(key)
        existing = self.objects.get(key)
        if existing is None:
            self.objects[key] = payload
            self.new_writes += 1
        elif existing != payload:
            raise ArtifactIntegrityError("immutable formal object drifted")
        return ArtifactRef(
            bucket=bucket,
            object_name=object_name,
            sha256=expected_sha256,
            size_bytes=len(payload),
        )


@dataclass
class ProjectionContext:
    workflow_factory: Any
    material_factory: Any
    candidate_store: DirectoryArtifactStore
    formal_store: RecordingFormalStore
    work_root: Path
    final_outbox_id: int
    job_id: str
    material_pk: int
    old_output_id: int

    def processor(
        self,
        phase_hook=None,
        *,
        worker_id: str = "projector-test",
        formal_prefix: str = "elegantbook",
    ):
        return WorkflowV3ProjectionProcessor(
            workflow_session_factory=self.workflow_factory,
            material_session_factory=self.material_factory,
            candidate_store=self.candidate_store,
            formal_store=self.formal_store,
            work_root=self.work_root,
            worker_id=worker_id,
            formal_prefix=formal_prefix,
            phase_hook=phase_hook,
        )


def test_projection_claim_skips_release_bound_to_another_runtime(
    tmp_path: Path,
):
    context = _context(tmp_path)
    db = context.workflow_factory()
    try:
        assert (
            claim_projection_outbox(
                db,
                worker_id="projector-wrong-runtime",
                runtime_identity_sha256="9" * 64,
            )
            is None
        )
        claim = claim_projection_outbox(
            db,
            worker_id="projector-matching-runtime",
            runtime_identity_sha256="8" * 64,
        )
        assert claim is not None
    finally:
        db.rollback()
        db.close()


def _context(
    tmp_path: Path,
    *,
    volume_count: int = 1,
    shadow: bool = False,
    corrupt_page_binding: bool = False,
    reverse_recompile_order: bool = False,
    misbind_compiled_pdf: bool = False,
) -> ProjectionContext:
    workflow_engine = create_engine(
        f"sqlite:///{tmp_path / 'workflow-v3.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    material_engine = create_engine(
        f"sqlite:///{tmp_path / 'material.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    WorkflowV3Base.metadata.create_all(workflow_engine)
    Base.metadata.create_all(material_engine)
    workflow_factory = sessionmaker(
        bind=workflow_engine,
        autoflush=False,
        expire_on_commit=False,
    )
    material_factory = sessionmaker(
        bind=material_engine,
        autoflush=False,
        expire_on_commit=False,
    )
    user_id = "user-test"
    material_id = "pdf-projection-test"
    popo_run_id = "popo-run-test"
    job_id = "00000000-0000-4000-8000-000000000123"
    release_sha256 = "1" * 64
    template_sha256 = "2" * 64
    source_popo_sha256 = "3" * 64
    input_set_sha256 = "4" * 64
    contracts = contracts_for_version(WORKFLOW_VERSION)
    prior_candidate_shas = [_candidate_sha(row.key) for row in contracts[:-1]]
    archive = _build_final_bundle(
        tmp_path,
        job_id=job_id,
        stage11_sha256=prior_candidate_shas[-1],
        release_sha256=release_sha256,
        prior_candidate_shas=prior_candidate_shas,
        volume_count=volume_count,
        corrupt_page_binding=corrupt_page_binding,
        reverse_recompile_order=reverse_recompile_order,
        misbind_compiled_pdf=misbind_compiled_pdf,
    )
    candidate_store = DirectoryArtifactStore(tmp_path / "candidate-store")
    final_object = f"{job_id}/ready-for-user-acceptance/artifact"
    final_ref = candidate_store.seed(
        archive,
        bucket="worker-v3-candidates",
        object_name=final_object,
    )

    with material_factory() as db:
        material = Material(
            user_id=user_id,
            material_id=material_id,
            source_hash="5" * 64,
            title="Projection fixture",
            filename="projection-fixture.pdf",
            source_type="uploaded",
            input_bucket="eduassets-input",
            input_object=f"{material_id}/source.pdf",
            input_sha256="5" * 64,
            stage_status="popo_done",
            pipeline_status="succeeded",
            popo_manifest_bucket="eduassets-minerupopo",
            popo_manifest_object=f"minerupopo/{material_id}/{popo_run_id}/manifest.json",
            popo_run_id=popo_run_id,
        )
        db.add(material)
        db.flush()
        review = ReviewAsset(
            user_id=user_id,
            title="Projection fixture",
            input_filename="projection-fixture.pdf",
            review_stage="popo",
            material_id=material_id,
            run_id=popo_run_id,
            manifest_bucket="eduassets-minerupopo",
            manifest_object=f"minerupopo/{material_id}/{popo_run_id}/manifest.json",
            manifest_json="{}",
            review_status="completed",
        )
        db.add(review)
        db.flush()
        material.review_asset_id = review.id
        old = MaterialOutput(
            user_id=user_id,
            material_pk=material.id,
            material_id=material_id,
            review_asset_id=review.id,
            output_type="elegantbook",
            origin="worker_v2",
            status="promoted",
            quality_status="passed",
            is_current=True,
            manifest_bucket="eduassets-elegantbook",
            manifest_object=f"worker-v2/{material_id}/old/manifest.json",
            output_run_id="worker-v2-old",
            popo_run_id=popo_run_id,
        )
        db.add(old)
        db.flush()
        material.latex_manifest_bucket = old.manifest_bucket
        material.latex_manifest_object = old.manifest_object
        material.latex_run_id = old.output_run_id
        material_pk = material.id
        old_output_id = old.id
        db.commit()

    with workflow_factory() as db:
        release = WorkflowV3SkillRelease(
            release_version="skill-release-test",
            manifest_sha256=release_sha256,
            package_bucket="worker-v3-releases",
            package_object="release/package.tar.gz",
            package_sha256="9" * 64,
            workflow_version=WORKFLOW_VERSION,
            template_sha256=template_sha256,
            runtime_identity_sha256="8" * 64,
            manifest_json="{}",
            status="registered",
            registered_by="test",
        )
        db.add(release)
        db.flush()
        job = WorkflowV3Job(
            public_id=job_id,
            idempotency_key="a" * 64,
            user_id=user_id,
            material_pk=material_pk,
            material_id=material_id,
            source_popo_bucket="eduassets-minerupopo",
            source_popo_object=f"minerupopo/{material_id}/{popo_run_id}/manifest.json",
            source_popo_sha256=source_popo_sha256,
            workflow_version=WORKFLOW_VERSION,
            skill_release_id=release.id,
            skill_release_version=release.release_version,
            skill_release_sha256=release_sha256,
            template_sha256=template_sha256,
            machine_status="succeeded",
            spec_status="passed",
            readiness_status="ready",
            human_acceptance_status="pending",
            current_stage_key=contracts[-1].key,
            payload_json=WorkflowV3Job.dump(
                {
                    "shadow": shadow,
                    "source_evidence": {
                        "run_id": popo_run_id,
                        "input_set_sha256": input_set_sha256,
                        "popo_manifest": {
                            "bucket": "eduassets-minerupopo",
                            "object": (
                                f"minerupopo/{material_id}/{popo_run_id}/manifest.json"
                            ),
                            "sha256": source_popo_sha256,
                        },
                        "review_asset": {
                            "id": str(review.id),
                            "bucket": "eduassets-minerupopo",
                            "object": (
                                f"minerupopo/{material_id}/{popo_run_id}/manifest.json"
                            ),
                            "sha256": source_popo_sha256,
                        },
                    },
                }
            ),
            finished_at=datetime.utcnow(),
        )
        db.add(job)
        db.flush()
        previous_promotion_id = None
        previous_sha = source_popo_sha256
        final_stage = None
        final_candidate = None
        final_promotion = None
        for index, contract in enumerate(contracts):
            candidate_sha = (
                final_ref.sha256
                if index == len(contracts) - 1
                else prior_candidate_shas[index]
            )
            stage = WorkflowV3StageRun(
                workflow_job_id=job.id,
                stage_key=contract.key,
                stage_version=contract.stage_version,
                attempt=1,
                machine_status="succeeded",
                spec_status="passed",
                owner=contract.owner,
                input_kind=(
                    "frozen_source" if index == 0 else "promoted_artifact"
                ),
                input_promotion_id=previous_promotion_id,
                input_artifact_sha256=previous_sha,
                error_code="",
                error_message="",
                started_at=datetime.utcnow(),
                finished_at=datetime.utcnow(),
            )
            db.add(stage)
            db.flush()
            execution = WorkflowV3Execution(
                workflow_job_id=job.id,
                stage_run_id=stage.id,
                producer_identity="producer-test",
                idempotency_key=f"execution-{index}",
                machine_status="succeeded",
                skill_release_sha256=release_sha256,
                runtime_identity_sha256=release.runtime_identity_sha256,
                metrics_json="{}",
                finished_at=datetime.utcnow(),
            )
            db.add(execution)
            db.flush()
            object_name = (
                final_ref.object_name
                if index == len(contracts) - 1
                else f"{job_id}/{contract.key}/{candidate_sha}/artifact"
            )
            candidate = WorkflowV3Candidate(
                workflow_job_id=job.id,
                stage_run_id=stage.id,
                execution_id=execution.id,
                idempotency_key=f"candidate-{index}",
                artifact_kind=(
                    "worker-v3-ready-for-user-acceptance-candidate"
                    if index == len(contracts) - 1
                    else f"worker-v3-{contract.key}-candidate"
                ),
                bucket=final_ref.bucket,
                object_name=object_name,
                object_identity_hash=_sha_bytes(
                    f"{final_ref.bucket}\n{object_name}".encode()
                ),
                sha256=candidate_sha,
                size_bytes=(
                    final_ref.size_bytes if index == len(contracts) - 1 else 1
                ),
                immutable=True,
                status="promoted",
                metadata_json="{}",
            )
            db.add(candidate)
            db.flush()
            evaluation = WorkflowV3Evaluation(
                workflow_job_id=job.id,
                stage_run_id=stage.id,
                candidate_id=candidate.id,
                idempotency_key=f"evaluation-{index}",
                evaluator_identity="evaluator-test",
                evaluator_version="test-v1",
                policy_sha256="b" * 64,
                decision="passed",
                spec_passed=True,
                gate_results_json=WorkflowV3Evaluation.dump(
                    {gate: True for gate in contract.acceptance_gates}
                ),
                findings_json=WorkflowV3Evaluation.dump([]),
            )
            db.add(evaluation)
            db.flush()
            promotion = WorkflowV3Promotion(
                workflow_job_id=job.id,
                stage_run_id=stage.id,
                candidate_id=candidate.id,
                evaluation_id=evaluation.id,
                idempotency_key=f"promotion-{index}",
                artifact_sha256=candidate_sha,
                promoted_by="promoter-test",
            )
            db.add(promotion)
            db.flush()
            stage.promoted_candidate_id = candidate.id
            stage.promotion_id = promotion.id
            stage.promoted_artifact_sha256 = candidate_sha
            previous_promotion_id = promotion.id
            previous_sha = candidate_sha
            final_stage = stage
            final_candidate = candidate
            final_promotion = promotion
        outbox = _enqueue_final_projection(
            db,
            job=job,
            stage=final_stage,
            candidate=final_candidate,
            promotion=final_promotion,
        )
        db.commit()
        final_outbox_id = outbox.id
    return ProjectionContext(
        workflow_factory=workflow_factory,
        material_factory=material_factory,
        candidate_store=candidate_store,
        formal_store=RecordingFormalStore(),
        work_root=tmp_path / "projection-work",
        final_outbox_id=final_outbox_id,
        job_id=job_id,
        material_pk=material_pk,
        old_output_id=old_output_id,
    )


def _expire_lease(
    context: ProjectionContext,
    outbox_id: int | None = None,
) -> None:
    with context.workflow_factory() as db:
        row = db.get(
            WorkflowV3ProjectionOutbox,
            outbox_id or context.final_outbox_id,
        )
        row.lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
        db.commit()


def _formal_manifest(context: ProjectionContext) -> tuple[str, dict[str, Any]]:
    suffix = (
        f"elegantbook/pdf-projection-test/popo-run-test/"
        f"{context.job_id}/manifest.json"
    )
    payload = context.formal_store.objects[
        ("eduassets-elegantbook", suffix)
    ]
    return suffix, json.loads(payload)


def test_final_ready_projects_exact_noncurrent_candidate_and_allowlisted_manifest(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    result = context.processor().process_one()
    assert result["status"] == "applied"
    manifest_object, manifest = _formal_manifest(context)
    assert context.formal_store.calls[-1] == (
        "eduassets-elegantbook",
        manifest_object,
    )
    assert manifest["schema"] == "luceon.workflow.artifact-manifest/v1"
    assert manifest["origin"] == "worker_v3"
    assert manifest["volume_count"] == 1
    assert manifest["objects"]["compiled_pdf"] == "files/main.pdf"
    assert manifest["objects"]["package_zip"] == "files/latex-project.zip"
    assert manifest["volumes"][0]["objects"]["compiled_pdf"] == "files/main.pdf"
    page_review = json.loads(
        context.formal_store.objects[
            (
                "eduassets-elegantbook",
                manifest_object.removesuffix("manifest.json")
                + "files/page-review.json",
            )
        ]
    )
    assert (
        manifest["volumes"][0]["artifacts"]["compiled_pdf"]["sha256"]
        == page_review["volumes"][0]["candidate_pdf_sha256"]
    )
    published = {row["path"] for row in manifest["files"]}
    assert published == {
        "files/latex-project.zip",
        "files/main.pdf",
        "files/main.log",
        "files/latex-polish-report.json",
        "files/compile-report.json",
        "files/core-acceptance.json",
        "files/run-state.json",
        "files/delivery-set.json",
        "files/page-review.json",
        "files/readiness.json",
    }
    with context.material_factory() as db:
        rows = (
            db.query(MaterialOutput)
            .filter(MaterialOutput.material_id == "pdf-projection-test")
            .order_by(MaterialOutput.id)
            .all()
        )
        assert len(rows) == 2
        projected = rows[-1]
        assert projected.origin == "worker_v3"
        assert projected.status == "candidate"
        assert projected.quality_status == "ready_for_user_acceptance"
        assert projected.is_current is False
        review = (
            db.query(ReviewAsset)
            .filter(
                ReviewAsset.user_id == "user-test",
                ReviewAsset.material_id == "pdf-projection-test",
                ReviewAsset.run_id == "popo-run-test",
            )
            .one()
        )
        assert projected.review_asset_id == review.id
        assert rows[0].id == context.old_output_id and rows[0].is_current is True
    with context.workflow_factory() as db:
        outbox = db.get(WorkflowV3ProjectionOutbox, context.final_outbox_id)
        assert outbox.status == "applied"
        assert outbox.projected_output_id == projected.id
        assert outbox.projected_manifest_object == manifest_object
        assert outbox.projected_manifest_sha256 == _sha_bytes(
            context.formal_store.objects[
                ("eduassets-elegantbook", manifest_object)
            ]
        )


def test_delivery_status_tracks_outbox_without_mutating_stage12(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    with context.workflow_factory() as db:
        before = workflow_job_detail(db, context.job_id)
        assert before["machine_status"] == "succeeded"
        assert before["spec_status"] == "passed"
        assert before["delivery_status"] == "projecting"
        assert before["ready_for_user_acceptance"] is False

    assert context.processor().process_one()["status"] == "applied"
    with context.workflow_factory() as db:
        after = workflow_job_detail(db, context.job_id)
        assert after["machine_status"] == "succeeded"
        assert after["spec_status"] == "passed"
        assert after["delivery_status"] == "projected"
        assert after["ready_for_user_acceptance"] is True
        assert after["human_acceptance_decision_recorded"] is False
        assert after["human_acceptance_effective"] is False
        assert after["human_accepted"] is False


def test_formal_publish_is_idempotent_after_stale_lease_replay(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)

    def crash(phase: str) -> None:
        if phase == "after_formal_publish":
            raise SystemExit("simulated hard crash")

    with pytest.raises(SystemExit, match="simulated hard crash"):
        context.processor(crash, worker_id="projector-a").process_one()
    first_write_count = context.formal_store.new_writes
    assert first_write_count > 0
    _expire_lease(context)
    result = context.processor(worker_id="projector-b").process_one()
    assert result["status"] == "applied"
    assert context.formal_store.new_writes == first_write_count
    with context.material_factory() as db:
        assert (
            db.query(MaterialOutput)
            .filter(MaterialOutput.origin == "worker_v3")
            .count()
            == 1
        )


def test_formal_target_is_frozen_before_publish_and_survives_config_drift(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)

    def crash(phase: str) -> None:
        if phase == "after_formal_publish":
            raise SystemExit("simulated hard crash")

    with pytest.raises(SystemExit, match="simulated hard crash"):
        context.processor(crash, worker_id="projector-a").process_one()
    _expire_lease(context)
    result = context.processor(
        worker_id="projector-b",
        formal_prefix="changed-prefix",
    ).process_one()
    assert result["status"] == "applied"
    manifests = [
        key
        for key in context.formal_store.objects
        if key[1].endswith("/manifest.json")
    ]
    assert manifests == [
        (
            "eduassets-elegantbook",
            (
                "elegantbook/pdf-projection-test/popo-run-test/"
                f"{context.job_id}/manifest.json"
            ),
        )
    ]
    with context.workflow_factory() as db:
        outbox = db.get(WorkflowV3ProjectionOutbox, context.final_outbox_id)
        assert outbox.formal_target_bucket == "eduassets-elegantbook"
        assert outbox.formal_target_prefix.startswith("elegantbook/")
        assert outbox.projected_manifest_object == (
            f"{outbox.formal_target_prefix}/manifest.json"
        )


def test_material_commit_crash_replays_without_duplicate_output(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)

    def crash(phase: str) -> None:
        if phase == "after_material_commit":
            raise SystemExit("simulated post-commit crash")

    with pytest.raises(SystemExit, match="post-commit"):
        context.processor(crash, worker_id="projector-a").process_one()
    with context.material_factory() as db:
        assert (
            db.query(MaterialOutput)
            .filter(MaterialOutput.origin == "worker_v3")
            .count()
            == 1
        )
    _expire_lease(context)
    result = context.processor(worker_id="projector-b").process_one()
    assert result["status"] == "applied"
    with context.material_factory() as db:
        assert (
            db.query(MaterialOutput)
            .filter(MaterialOutput.origin == "worker_v3")
            .count()
            == 1
        )
    with context.workflow_factory() as db:
        outbox = db.get(WorkflowV3ProjectionOutbox, context.final_outbox_id)
        assert outbox.attempt_count == 2
        assert outbox.status == "applied"


def test_transient_storage_failure_is_delayed_and_bounded(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)

    def timeout_after_publish(phase: str) -> None:
        if phase == "after_formal_publish":
            raise TimeoutError("temporary object-store timeout")

    result = context.processor(timeout_after_publish).process_one(
        max_attempts=2
    )
    assert result["status"] == "retry_scheduled"
    assert result["retry_after_seconds"] == 5
    with context.workflow_factory() as db:
        outbox = db.get(WorkflowV3ProjectionOutbox, context.final_outbox_id)
        assert outbox.status == "processing"
        assert outbox.lease_owner == ""
        assert outbox.lease_expires_at > datetime.utcnow()
        assert "temporary object-store timeout" in outbox.last_error

    assert context.processor().process_one(max_attempts=2)["status"] == "idle"
    _expire_lease(context)
    assert context.processor().process_one(max_attempts=2)["status"] == "applied"


def test_inner_hash_drift_fails_before_formal_or_material_projection(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path, corrupt_page_binding=True)
    result = context.processor().process_one()
    assert result["status"] == "failed"
    assert result["error_code"] == "projection_validation_failed"
    assert context.formal_store.objects == {}
    with context.material_factory() as db:
        assert (
            db.query(MaterialOutput)
            .filter(MaterialOutput.origin == "worker_v3")
            .count()
            == 0
        )
    with context.workflow_factory() as db:
        outbox = db.get(WorkflowV3ProjectionOutbox, context.final_outbox_id)
        assert outbox.status == "failed"
        assert outbox.attempt_count == 1
    assert context.processor().process_one()["status"] == "idle"


def test_admin_service_requeues_only_one_failed_projection(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path, corrupt_page_binding=True)
    assert context.processor().process_one()["status"] == "failed"
    with context.workflow_factory() as db:
        row = retry_projection_outbox(
            db,
            public_id=context.job_id,
            outbox_id=context.final_outbox_id,
            requested_by="pipeline-admin",
        )
        db.commit()
        assert row.status == "pending"
        assert row.attempt_count == 1
        assert "manual retry requested by pipeline-admin" in row.last_error
        with pytest.raises(ValueError, match="only a failed projection"):
            retry_projection_outbox(
                db,
                public_id=context.job_id,
                outbox_id=context.final_outbox_id,
                requested_by="pipeline-admin",
            )


@pytest.mark.parametrize(
    "context_options",
    [
        {"volume_count": 2, "reverse_recompile_order": True},
        {"misbind_compiled_pdf": True},
    ],
)
def test_volume_order_and_inner_role_paths_fail_closed(
    tmp_path: Path,
    context_options: dict[str, Any],
) -> None:
    context = _context(tmp_path, **context_options)
    result = context.processor().process_one()
    assert result["status"] == "failed"
    assert result["error_code"] == "projection_validation_failed"
    assert context.formal_store.objects == {}


def test_exact_review_asset_manifest_drift_fails_before_formal_publish(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    with context.material_factory() as db:
        review = db.query(ReviewAsset).one()
        review.manifest_bucket = "other-bucket"
        review.manifest_object = "wrong/run/manifest.json"
        db.commit()
    result = context.processor().process_one()
    assert result["status"] == "failed"
    assert result["error_code"] == "projection_validation_failed"
    assert context.formal_store.objects == {}


def test_shadow_final_ready_is_a_noop(tmp_path: Path) -> None:
    context = _context(tmp_path, shadow=True)
    result = context.processor().process_one()
    assert result == {"ok": True, "status": "idle"}
    assert context.formal_store.objects == {}
    with context.workflow_factory() as db:
        outbox = db.get(WorkflowV3ProjectionOutbox, context.final_outbox_id)
        assert outbox.status == "suppressed"
        assert outbox.attempt_count == 0


def test_human_acceptance_waits_for_applied_final_ready(tmp_path: Path) -> None:
    context = _context(tmp_path)
    with context.workflow_factory() as db:
        with pytest.raises(
            WorkflowV3TransitionError,
            match="exact applied formal output",
        ):
            record_human_acceptance(
                db,
                context.job_id,
                accepted=True,
                decided_by="human-reviewer",
                output_id=1,
                manifest_sha256="0" * 64,
            )
        db.rollback()
        job = db.query(WorkflowV3Job).filter(
            WorkflowV3Job.public_id == context.job_id
        ).one()
        assert job.human_acceptance_status == "pending"


@pytest.mark.parametrize("accepted", [True, False])
def test_dual_volume_acceptance_and_rejection_preserve_promotion_boundary(
    tmp_path: Path,
    accepted: bool,
) -> None:
    context = _context(tmp_path, volume_count=2)
    assert context.processor().process_one()["status"] == "applied"
    manifest_object, manifest = _formal_manifest(context)
    assert manifest["volume_count"] == 2
    assert manifest["human_acceptance_status"] == "pending"
    assert "compiled_pdf" not in manifest["objects"]
    assert "package_zip" not in manifest["objects"]
    assert len(manifest["volumes"]) == 2
    assert all(
        isinstance(row["objects"]["compiled_pdf"], str)
        and isinstance(row["objects"]["package_zip"], str)
        for row in manifest["volumes"]
    )
    with context.workflow_factory() as db:
        final = db.get(WorkflowV3ProjectionOutbox, context.final_outbox_id)
        job = record_human_acceptance(
            db,
            context.job_id,
            accepted=accepted,
            decided_by="human-reviewer",
            output_id=final.projected_output_id,
            manifest_sha256=final.projected_manifest_sha256,
            reason="review decision",
        )
        db.commit()
        assert job.human_acceptance_status == (
            "accepted" if accepted else "rejected"
        )
    result = context.processor(formal_prefix="changed-prefix").process_one()
    assert result["event_kind"] == "human_acceptance"
    assert result["status"] == "applied"
    with context.material_factory() as db:
        old = db.get(MaterialOutput, context.old_output_id)
        projected = (
            db.query(MaterialOutput)
            .filter(MaterialOutput.origin == "worker_v3")
            .one()
        )
        acceptance = projected.metadata_dict()["human_acceptance"]
        commit = acceptance["commit"]
        commit_payload = json.loads(
            context.formal_store.objects[(commit["bucket"], commit["object"])]
        )
        assert _sha_bytes(
            context.formal_store.objects[(commit["bucket"], commit["object"])]
        ) == commit["sha256"]
        assert commit_payload["formal_output"]["output_id"] == str(projected.id)
        assert (
            commit_payload["schema"]
            == "luceon.worker-v3-human-acceptance-commit/v1"
        )
        assert (
            commit_payload["formal_output"]["manifest"]["sha256"]
            == _sha_bytes(
                context.formal_store.objects[
                    ("eduassets-elegantbook", manifest_object)
                ]
            )
        )
        assert commit_payload["decision"] == (
            "accepted" if accepted else "rejected"
        )
        material = db.get(Material, context.material_pk)
        if accepted:
            assert projected.status == "promoted"
            assert projected.quality_status == "passed"
            assert projected.is_current is True
            assert old.is_current is False
            assert material.latex_manifest_object == manifest_object
        else:
            assert projected.status == "candidate"
            assert projected.quality_status == "rejected"
            assert projected.is_current is False
            assert old.is_current is True
            assert material.latex_manifest_object == old.manifest_object


@pytest.mark.parametrize(
    "crash_phase",
    ["after_acceptance_publish", "after_material_commit"],
)
def test_acceptance_commit_replays_after_lost_response_or_db_commit_crash(
    tmp_path: Path,
    crash_phase: str,
) -> None:
    context = _context(tmp_path)
    assert context.processor().process_one()["status"] == "applied"
    with context.workflow_factory() as db:
        final = db.get(WorkflowV3ProjectionOutbox, context.final_outbox_id)
        record_human_acceptance(
            db,
            context.job_id,
            accepted=True,
            decided_by="human-reviewer",
            output_id=final.projected_output_id,
            manifest_sha256=final.projected_manifest_sha256,
            reason="review decision",
        )
        db.commit()
        acceptance_outbox_id = (
            db.query(WorkflowV3ProjectionOutbox.id)
            .filter(
                WorkflowV3ProjectionOutbox.workflow_job_id
                == final.workflow_job_id,
                WorkflowV3ProjectionOutbox.event_kind == "human_acceptance",
            )
            .scalar()
        )

    def crash(phase: str) -> None:
        if phase == crash_phase:
            raise SystemExit(f"simulated {crash_phase}")

    with pytest.raises(SystemExit, match=crash_phase):
        context.processor(crash, worker_id="projector-a").process_one()
    writes_after_crash = context.formal_store.new_writes
    acceptance_objects = [
        key
        for key in context.formal_store.objects
        if "/acceptance/" in key[1]
    ]
    assert len(acceptance_objects) == 1
    _expire_lease(context, acceptance_outbox_id)
    result = context.processor(worker_id="projector-b").process_one()
    assert result["event_kind"] == "human_acceptance"
    assert result["status"] == "applied"
    assert context.formal_store.new_writes == writes_after_crash
    with context.material_factory() as db:
        projected = (
            db.query(MaterialOutput)
            .filter(MaterialOutput.origin == "worker_v3")
            .one()
        )
        assert projected.is_current is True
        assert (
            projected.metadata_dict()["human_acceptance"]["commit"]["object"]
            == acceptance_objects[0][1]
        )
    with context.workflow_factory() as db:
        outbox = db.get(WorkflowV3ProjectionOutbox, acceptance_outbox_id)
        assert outbox.status == "applied"
        assert outbox.attempt_count == 2
