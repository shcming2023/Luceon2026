from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.material import Material, MaterialOutput
from app.models.review_asset import ReviewAsset
from app.workflow_v3.contracts import WORKFLOW_VERSION, contracts_for_version
from app.workflow_v3.models import (
    WorkflowV3Base,
    WorkflowV3Candidate,
    WorkflowV3Evaluation,
    WorkflowV3Execution,
    WorkflowV3Job,
    WorkflowV3ModelCall,
    WorkflowV3OperationAttempt,
    WorkflowV3ProjectionOutbox,
    WorkflowV3Promotion,
    WorkflowV3SkillRelease,
    WorkflowV3StageRun,
    WorkflowV3WorkerHeartbeat,
)
from app.workflow_v3.uat_evidence import (
    MinioEvidenceReader,
    ObjectCheck,
    RUNTIME_SNAPSHOT_SCHEMA,
    UI_SNAPSHOT_SCHEMA,
    WorkerV3UatEvidenceCollector,
    render_markdown,
)


NOW = datetime(2026, 7, 26, 8, 0, tzinfo=timezone.utc)
RELEASE_SHA = "a" * 64
TEMPLATE_SHA = "b" * 64
RUNTIME_SHA = "c" * 64


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class MemoryReader:
    def __init__(self, objects: dict[tuple[str, str], bytes]):
        self.objects = objects

    def verify(
        self,
        *,
        bucket: str,
        object_name: str,
        expected_sha256: str,
        expected_size_bytes: int | None = None,
        capture: bool = False,
    ) -> ObjectCheck:
        payload = self.objects.get((bucket, object_name))
        if payload is None:
            return ObjectCheck(
                bucket,
                object_name,
                expected_sha256,
                expected_size_bytes,
                "",
                0,
                False,
                False,
                "memory",
                error="missing",
            )
        actual_sha = _sha(payload)
        valid = actual_sha == expected_sha256 and (
            expected_size_bytes is None or len(payload) == expected_size_bytes
        )
        return ObjectCheck(
            bucket,
            object_name,
            expected_sha256,
            expected_size_bytes,
            actual_sha,
            len(payload),
            True,
            valid,
            "memory",
            payload=payload if capture else None,
            error="" if valid else "drift",
        )


@dataclass
class Fixture:
    workflow_db: object
    material_db: object
    job: WorkflowV3Job
    objects: dict[tuple[str, str], bytes]
    ui: dict
    runtime: dict


def _session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    return engine, sessionmaker(bind=engine, expire_on_commit=False)()


def _binding(path: str, payload: bytes) -> dict:
    return {"path": path, "sha256": _sha(payload), "size_bytes": len(payload)}


def _complete_fixture() -> Fixture:
    workflow_engine, workflow_db = _session()
    material_engine, material_db = _session()
    WorkflowV3Base.metadata.create_all(workflow_engine)
    Base.metadata.create_all(material_engine)

    material = Material(
        user_id="uat-user",
        material_id="pdf-generic-identity",
        title="Generic",
        filename="generic.pdf",
        input_bucket="eduassets-input",
        input_object="pdf/generic.pdf",
        input_sha256="d" * 64,
        size_bytes=100,
        page_count=10,
        stage_status="popo_done",
        pipeline_status="succeeded",
        mineru_manifest_bucket="eduassets-mineru",
        mineru_manifest_object="mineru/generic/run/manifest.json",
        mineru_run_id="mineru-run",
        popo_manifest_bucket="eduassets-minerupopo",
        popo_manifest_object="minerupopo/generic/popo-run/manifest.json",
        popo_run_id="popo-run",
    )
    material_db.add(material)
    material_db.flush()
    review = ReviewAsset(
        user_id=material.user_id,
        title=material.title,
        input_filename=material.filename,
        review_stage="popo",
        material_id=material.material_id,
        run_id=material.popo_run_id,
        manifest_bucket=material.popo_manifest_bucket,
        manifest_object=material.popo_manifest_object,
        input_pdf_bucket=material.input_bucket,
        input_pdf_object=material.input_object,
        review_status="approved",
        manifest_json="{}",
    )
    material_db.add(review)
    material_db.flush()
    material.review_asset_id = review.id

    source_payload = b'{"schema":"frozen-popo"}\n'
    objects: dict[tuple[str, str], bytes] = {
        (material.popo_manifest_bucket, material.popo_manifest_object): source_payload,
    }
    release_payload = b"immutable-worker-v3-release"
    release = WorkflowV3SkillRelease(
        release_version="3.0.0-rc1",
        manifest_sha256=RELEASE_SHA,
        package_bucket="worker-v3-releases",
        package_object="worker-v3/3.0.0-rc1.tar.gz",
        package_sha256=_sha(release_payload),
        workflow_version=WORKFLOW_VERSION,
        template_sha256=TEMPLATE_SHA,
        runtime_identity_sha256=RUNTIME_SHA,
        manifest_json="{}",
        status="registered",
        registered_by="release-controller",
    )
    workflow_db.add(release)
    workflow_db.flush()
    objects[(release.package_bucket, release.package_object)] = release_payload
    job = WorkflowV3Job(
        public_id="00000000-0000-4000-8000-000000000001",
        idempotency_key="f" * 64,
        user_id=material.user_id,
        material_pk=material.id,
        material_id=material.material_id,
        source_popo_bucket=material.popo_manifest_bucket,
        source_popo_object=material.popo_manifest_object,
        source_popo_sha256=_sha(source_payload),
        workflow_version=WORKFLOW_VERSION,
        skill_release_id=release.id,
        skill_release_version=release.release_version,
        skill_release_sha256=RELEASE_SHA,
        template_sha256=TEMPLATE_SHA,
        machine_status="succeeded",
        spec_status="passed",
        readiness_status="ready",
        human_acceptance_status="pending",
        current_stage_key="ready_for_user_acceptance",
        payload_json=WorkflowV3Job.dump(
            {
                "cohort_id": "cohort-generic",
                "source_evidence": {
                    "review_asset": {
                        "id": str(review.id),
                        "bucket": review.manifest_bucket,
                        "object": review.manifest_object,
                        "sha256": _sha(source_payload),
                    },
                    "artifacts": [
                        {
                            "role": "frozen_source",
                            "bucket": material.popo_manifest_bucket,
                            "object": material.popo_manifest_object,
                            "sha256": _sha(source_payload),
                            "size_bytes": len(source_payload),
                        }
                    ],
                },
            }
        ),
        started_at=NOW - timedelta(hours=1),
        finished_at=NOW - timedelta(minutes=1),
        created_at=NOW - timedelta(hours=1),
        updated_at=NOW - timedelta(minutes=1),
    )
    workflow_db.add(job)
    workflow_db.flush()

    previous_sha = job.source_popo_sha256
    final_promotion = None
    for index, contract in enumerate(contracts_for_version(WORKFLOW_VERSION), start=1):
        stage = WorkflowV3StageRun(
            workflow_job_id=job.id,
            stage_key=contract.key,
            stage_version=contract.stage_version,
            attempt=1,
            machine_status="succeeded",
            spec_status="passed",
            owner=contract.owner,
            input_kind="frozen_source" if index == 1 else "promoted_artifact",
            input_promotion_id=final_promotion.id if final_promotion else None,
            input_artifact_sha256=previous_sha,
            started_at=NOW - timedelta(minutes=60 - index),
            finished_at=NOW - timedelta(minutes=59 - index),
            created_at=NOW - timedelta(hours=1),
            updated_at=NOW - timedelta(minutes=1),
        )
        workflow_db.add(stage)
        workflow_db.flush()
        execution = WorkflowV3Execution(
            workflow_job_id=job.id,
            stage_run_id=stage.id,
            producer_identity=f"producer-{index}",
            idempotency_key=f"execution-{index}",
            machine_status="succeeded",
            skill_release_sha256=RELEASE_SHA,
            runtime_identity_sha256=RUNTIME_SHA,
            metrics_json="{}",
            started_at=NOW - timedelta(minutes=60 - index),
            heartbeat_at=NOW - timedelta(minutes=59 - index),
            finished_at=NOW - timedelta(minutes=59 - index),
        )
        workflow_db.add(execution)
        workflow_db.flush()
        candidate_payload = f"candidate:{contract.key}".encode()
        candidate_sha = _sha(candidate_payload)
        candidate_object = (
            f"jobs/{job.public_id}/{contract.key}/{candidate_sha}/candidate.tar.gz"
        )
        objects[("worker-v3-candidates", candidate_object)] = candidate_payload
        candidate = WorkflowV3Candidate(
            workflow_job_id=job.id,
            stage_run_id=stage.id,
            execution_id=execution.id,
            idempotency_key=f"candidate-{index}",
            artifact_kind=f"worker-v3-{contract.key}-candidate",
            bucket="worker-v3-candidates",
            object_name=candidate_object,
            object_identity_hash=_sha(candidate_object.encode()),
            sha256=candidate_sha,
            size_bytes=len(candidate_payload),
            immutable=True,
            status="promoted",
            metadata_json="{}",
            created_at=NOW - timedelta(minutes=59 - index),
        )
        workflow_db.add(candidate)
        workflow_db.flush()
        evaluation = WorkflowV3Evaluation(
            workflow_job_id=job.id,
            stage_run_id=stage.id,
            candidate_id=candidate.id,
            idempotency_key=f"evaluation-{index}",
            evaluator_identity=f"evaluator-{index}",
            evaluator_version="evaluator-v1",
            policy_sha256="1" * 64,
            decision="passed",
            spec_passed=True,
            gate_results_json=WorkflowV3Evaluation.dump(
                {gate: True for gate in contract.acceptance_gates}
            ),
            findings_json="[]",
            created_at=NOW - timedelta(minutes=58 - index),
        )
        workflow_db.add(evaluation)
        workflow_db.flush()
        promotion = WorkflowV3Promotion(
            workflow_job_id=job.id,
            stage_run_id=stage.id,
            candidate_id=candidate.id,
            evaluation_id=evaluation.id,
            idempotency_key=f"promotion-{index}",
            artifact_sha256=candidate_sha,
            promoted_by="promotion-controller",
            created_at=NOW - timedelta(minutes=57 - index),
        )
        workflow_db.add(promotion)
        workflow_db.flush()
        stage.promoted_candidate_id = candidate.id
        stage.promotion_id = promotion.id
        stage.promoted_artifact_sha256 = candidate_sha
        final_promotion = promotion
        previous_sha = candidate_sha

    package_zip = b"zip-bytes"
    compiled_pdf = b"%PDF-1.7\ncompiled\n"
    compile_log = b"Latexmk: All targets are up-to-date\n"
    compile_report = json.dumps(
        {
            "schema": "luceon.worker-v3-compile-report/v1",
            "status": "succeeded",
            "engine": "latexmk-xelatex",
            "volumes": [{"volume_id": "volume-1"}],
        },
        sort_keys=True,
    ).encode()
    formal_files = {
        "files/latex-project.zip": package_zip,
        "files/main.pdf": compiled_pdf,
        "files/main.log": compile_log,
        "files/compile-report.json": compile_report,
    }
    manifest = {
        "schema": "luceon.workflow.artifact-manifest/v1",
        "schema_version": "luceon.worker-v3-formal-output/v1",
        "origin": "worker_v3",
        "workflow_job_id": job.public_id,
        "workflow_version": job.workflow_version,
        "material_id": job.material_id,
        "status": "ready_for_user_acceptance",
        "template_sha256": job.template_sha256,
        "release": {
            "version": job.skill_release_version,
            "manifest_sha256": job.skill_release_sha256,
        },
        "source_popo_manifest": {
            "bucket": job.source_popo_bucket,
            "object": job.source_popo_object,
            "sha256": job.source_popo_sha256,
        },
        "volumes": [
            {
                "volume_id": "volume-1",
                "artifacts": {
                    "package_zip": _binding("files/latex-project.zip", package_zip),
                    "compiled_pdf": _binding("files/main.pdf", compiled_pdf),
                    "compile_log": _binding("files/main.log", compile_log),
                    "compile_report": _binding(
                        "files/compile-report.json", compile_report
                    ),
                },
            }
        ],
        "files": [
            _binding(path, payload) for path, payload in sorted(formal_files.items())
        ],
    }
    manifest_payload = json.dumps(manifest, sort_keys=True).encode()
    formal_bucket = "worker-v3-formal"
    formal_prefix = f"outputs/{job.public_id}"
    manifest_object = f"{formal_prefix}/manifest.json"
    objects[(formal_bucket, manifest_object)] = manifest_payload
    for path, payload in formal_files.items():
        objects[(formal_bucket, f"{formal_prefix}/{path}")] = payload

    output = MaterialOutput(
        user_id=job.user_id,
        material_pk=material.id,
        material_id=material.material_id,
        review_asset_id=review.id,
        output_type="elegantbook",
        origin="worker_v3",
        status="candidate",
        quality_status="ready_for_user_acceptance",
        is_current=False,
        manifest_bucket=formal_bucket,
        manifest_object=manifest_object,
        output_run_id=job.public_id,
        popo_run_id=material.popo_run_id,
        skill_name="luceon-popo-to-refined-elegantbook",
        skill_version=release.release_version,
        metadata_json=json.dumps(
            {
                "workflow_v3_job_id": job.public_id,
                "manifest_sha256": _sha(manifest_payload),
            },
            sort_keys=True,
        ),
    )
    material_db.add(output)
    material_db.flush()
    workflow_db.add(
        WorkflowV3ProjectionOutbox(
            workflow_job_id=job.id,
            final_promotion_id=final_promotion.id,
            idempotency_key="projection-final-ready",
            event_kind="final_ready",
            status="applied",
            target_kind="material_output",
            payload_json="{}",
            attempt_count=1,
            formal_target_bucket=formal_bucket,
            formal_target_prefix=formal_prefix,
            formal_target_manifest_object=manifest_object,
            applied_identity="2" * 64,
            projected_output_id=output.id,
            projected_manifest_bucket=formal_bucket,
            projected_manifest_object=manifest_object,
            projected_manifest_sha256=_sha(manifest_payload),
            created_at=NOW - timedelta(minutes=3),
            updated_at=NOW - timedelta(minutes=2),
            applied_at=NOW - timedelta(minutes=2),
        )
    )
    workflow_db.add(
        WorkflowV3WorkerHeartbeat(
            worker_id="worker-v3-producer-1",
            role="producer",
            status="idle",
            runtime_identity_sha256=RUNTIME_SHA,
            metrics_json="{}",
            started_at=NOW - timedelta(hours=2),
            heartbeat_at=NOW - timedelta(seconds=15),
            updated_at=NOW - timedelta(seconds=15),
        )
    )
    workflow_db.commit()
    material_db.commit()
    ui = {
        "schema": UI_SNAPSHOT_SCHEMA,
        "jobs": [
            {
                "id": job.public_id,
                "material_pk": str(job.material_pk),
                "material_id": job.material_id,
                "filename": material.filename,
                "popo_run_id": review.run_id,
                "skill_release_version": job.skill_release_version,
                "machine_status": "succeeded",
                "spec_status": "passed",
                "readiness_status": "ready",
                "human_acceptance_status": "pending",
                "current_stage_key": "ready_for_user_acceptance",
            }
        ],
    }
    runtime = {
        "schema": RUNTIME_SNAPSHOT_SCHEMA,
        "containers": [
            {
                "name": "worker-v3-producer",
                "status": "running",
                "health": "healthy",
                "restart_count": 0,
                "restart_delta": 0,
                "oom_killed": False,
            }
        ],
    }
    return Fixture(workflow_db, material_db, job, objects, ui, runtime)


def _collect(fixture: Fixture):
    return WorkerV3UatEvidenceCollector(
        workflow_db=fixture.workflow_db,
        material_db=fixture.material_db,
        object_reader=MemoryReader(fixture.objects),
        now=NOW,
    ).collect(
        job_ids=[fixture.job.public_id],
        ui_snapshot=fixture.ui,
        runtime_snapshot=fixture.runtime,
    )


def test_complete_read_only_evidence_separates_statuses_and_passes():
    fixture = _complete_fixture()
    report = _collect(fixture)

    assert report["summary"] == {
        "status": "passed",
        "job_count": 1,
        "passed_job_count": 1,
        "defect_blocker_count": 0,
        "evidence_gap_blocker_count": 0,
        "warning_count": 0,
    }
    states = report["jobs"][0]["states"]
    assert states == {
        "machine": "succeeded",
        "spec": "passed",
        "readiness": "ready",
        "human_acceptance": "pending",
        "delivery": "projected",
        "human_acceptance_projection": "not_recorded",
    }
    acceptance = report["jobs"][0]["acceptance"]
    assert acceptance["delivery_status"] == "projected"
    assert acceptance["ready_for_user_acceptance"] is True
    assert acceptance["human_decision_recorded"] is False
    assert acceptance["human_acceptance_effective"] is False
    assert acceptance["human_accepted"] is False
    assert all(row["verified"] for row in report["jobs"][0]["objects"])
    markdown = render_markdown(report)
    assert "本报告只读" in markdown
    assert "不会制造通过" in markdown
    assert not fixture.workflow_db.new
    assert not fixture.workflow_db.dirty
    assert not fixture.workflow_db.deleted
    assert not fixture.material_db.new
    assert not fixture.material_db.dirty
    assert not fixture.material_db.deleted


def test_cohort_selection_and_missing_observability_fail_closed():
    fixture = _complete_fixture()
    report = WorkerV3UatEvidenceCollector(
        workflow_db=fixture.workflow_db,
        material_db=fixture.material_db,
        object_reader=MemoryReader(fixture.objects),
        now=NOW,
    ).collect(cohort_id="cohort-generic", cohort_field="cohort_id")

    assert report["selection"]["matched_job_count"] == 1
    assert report["summary"]["status"] == "incomplete"
    assert {
        row["code"] for row in report["findings"] if row["category"] == "evidence_gap"
    } >= {
        "ui_snapshot_missing_or_invalid",
        "runtime_snapshot_missing_or_invalid",
    }


@pytest.mark.parametrize(
    ("projection_status", "expected_delivery", "expected_report"),
    [
        ("pending", "projecting", "incomplete"),
        ("failed", "projection_failed", "failed"),
    ],
)
def test_uat_readiness_requires_applied_projection_and_keeps_error_visible(
    projection_status: str,
    expected_delivery: str,
    expected_report: str,
):
    fixture = _complete_fixture()
    projection = (
        fixture.workflow_db.query(WorkflowV3ProjectionOutbox)
        .filter(
            WorkflowV3ProjectionOutbox.workflow_job_id == fixture.job.id,
            WorkflowV3ProjectionOutbox.event_kind == "final_ready",
        )
        .one()
    )
    projection.status = projection_status
    projection.last_error = "formal projection evidence"
    projection.applied_identity = ""
    projection.projected_output_id = None
    projection.projected_manifest_bucket = ""
    projection.projected_manifest_object = ""
    projection.projected_manifest_sha256 = ""
    projection.applied_at = None
    fixture.workflow_db.commit()

    report = _collect(fixture)
    job = report["jobs"][0]
    assert report["summary"]["status"] == expected_report
    assert job["acceptance"]["delivery_status"] == expected_delivery
    assert job["acceptance"]["ready_for_user_acceptance"] is False
    assert any(
        row["last_error"] == "formal projection evidence"
        for row in job["projection"]
    )
    assert any(
        row["severity"] == "blocker" for row in job["findings"]
    )


def test_human_decision_is_not_effective_until_acceptance_projection_applies():
    fixture = _complete_fixture()
    final = (
        fixture.workflow_db.query(WorkflowV3ProjectionOutbox)
        .filter(
            WorkflowV3ProjectionOutbox.workflow_job_id == fixture.job.id,
            WorkflowV3ProjectionOutbox.event_kind == "final_ready",
        )
        .one()
    )
    fixture.job.human_acceptance_status = "accepted"
    fixture.workflow_db.add(
        WorkflowV3ProjectionOutbox(
            workflow_job_id=fixture.job.id,
            final_promotion_id=final.final_promotion_id,
            idempotency_key="projection-human-acceptance-pending",
            event_kind="human_acceptance",
            status="pending",
            target_kind="material_output",
            payload_json="{}",
            last_error="awaiting acceptance projection",
        )
    )
    fixture.workflow_db.commit()

    report = _collect(fixture)
    acceptance = report["jobs"][0]["acceptance"]
    assert acceptance["human_decision_recorded"] is True
    assert acceptance["human_decision"] == "accepted"
    assert acceptance["human_acceptance_effective"] is False
    assert acceptance["human_accepted"] is False
    assert acceptance["ready_for_user_acceptance"] is False
    assert "acceptance_projection_missing" in {
        row["code"] for row in report["jobs"][0]["findings"]
    }


def test_collector_refuses_sessions_with_pending_writes():
    fixture = _complete_fixture()
    fixture.job.error_message = "uncommitted mutation"

    with pytest.raises(ValueError, match="pending writes"):
        _collect(fixture)


def test_ui_mismatch_runtime_oom_expired_lease_and_candidate_orphan_are_visible():
    fixture = _complete_fixture()
    first_stage = (
        fixture.workflow_db.query(WorkflowV3StageRun)
        .filter(WorkflowV3StageRun.workflow_job_id == fixture.job.id)
        .order_by(WorkflowV3StageRun.id.asc())
        .first()
    )
    fixture.job.machine_status = "running"
    fixture.job.spec_status = "in_progress"
    fixture.job.readiness_status = "not_ready"
    first_stage.machine_status = "awaiting_evaluation"
    first_stage.spec_status = "not_evaluated"
    first_stage.updated_at = NOW - timedelta(hours=1)
    fixture.workflow_db.add(
        WorkflowV3OperationAttempt(
            workflow_job_id=fixture.job.id,
            stage_run_id=first_stage.id,
            operation="evaluation",
            target_id=1,
            attempt=1,
            status="running",
            owner_identity="evaluator-stale",
            owner_token_sha256="3" * 64,
            max_attempts=3,
            lease_seconds=300,
            lease_expires_at=NOW - timedelta(minutes=20),
            heartbeat_at=NOW - timedelta(minutes=25),
            metadata_json="{}",
            started_at=NOW - timedelta(minutes=30),
        )
    )
    fixture.workflow_db.commit()
    fixture.ui["jobs"][0]["machine_status"] = "succeeded"
    fixture.runtime["containers"][0]["oom_killed"] = True

    report = _collect(fixture)
    codes = {row["code"] for row in report["findings"]}
    assert report["summary"]["status"] == "failed"
    assert {
        "ui_db_state_mismatch",
        "container_oom_killed",
        "expired_operation_lease",
        "orphaned_candidate_lock",
        "job_not_terminal",
    } <= codes


def test_minio_drift_and_missing_recompile_are_blockers():
    fixture = _complete_fixture()
    fixture.objects[
        ("eduassets-minerupopo", "minerupopo/generic/popo-run/manifest.json")
    ] = b"drifted"
    compile_path = (
        "worker-v3-formal",
        f"outputs/{fixture.job.public_id}/files/compile-report.json",
    )
    del fixture.objects[compile_path]

    report = _collect(fixture)
    codes = {row["code"] for row in report["findings"]}
    assert report["summary"]["status"] == "failed"
    assert "minio_object_identity_mismatch" in codes
    assert "delivery_object_identity_mismatch" in codes


def test_successful_model_call_without_full_hash_audit_fails_closed():
    fixture = _complete_fixture()
    first_stage = (
        fixture.workflow_db.query(WorkflowV3StageRun)
        .filter(WorkflowV3StageRun.workflow_job_id == fixture.job.id)
        .order_by(WorkflowV3StageRun.id.asc())
        .first()
    )
    fixture.workflow_db.add(
        WorkflowV3ModelCall(
            workflow_job_id=fixture.job.id,
            stage_run_id=first_stage.id,
            call_id="bounded-call-1",
            attempt=1,
            provider="fixture",
            model="bounded-model",
            prompt_id="prompt",
            prompt_version="1",
            prompt_sha256="4" * 64,
            schema_id="schema",
            schema_version="1",
            schema_sha256="5" * 64,
            input_sha256="6" * 64,
            release_sha256=RELEASE_SHA,
            request_sha256="7" * 64,
            raw_response_sha256="",
            output_sha256="8" * 64,
            machine_status="succeeded",
            parameters_json="{}",
            usage_json="{}",
            started_at=NOW - timedelta(minutes=5),
            finished_at=NOW - timedelta(minutes=4),
        )
    )
    fixture.workflow_db.commit()

    report = _collect(fixture)
    assert report["summary"]["status"] == "failed"
    assert "model_call_audit_incomplete" in {
        row["code"] for row in report["findings"]
    }


class _Response:
    def __init__(self, payload: bytes):
        self._stream = io.BytesIO(payload)

    def read(self, size: int = -1):
        return self._stream.read(size)

    def close(self):
        pass

    def release_conn(self):
        pass


class _Stat:
    def __init__(self, size: int, metadata: dict[str, str]):
        self.size = size
        self.metadata = metadata


class _Minio:
    def __init__(self, payload: bytes, *, metadata_sha: str = ""):
        self.payload = payload
        self.metadata_sha = metadata_sha
        self.get_count = 0

    def stat_object(self, _bucket, _object):
        return _Stat(
            len(self.payload),
            {"x-amz-meta-luceon-sha256": self.metadata_sha}
            if self.metadata_sha
            else {},
        )

    def get_object(self, _bucket, _object):
        self.get_count += 1
        return _Response(self.payload)


def test_minio_reader_uses_immutable_metadata_but_hashes_captured_json():
    payload = b'{"ok":true}'
    client = _Minio(payload, metadata_sha=_sha(payload))
    reader = MinioEvidenceReader(client)

    fast = reader.verify(
        bucket="worker-v3-formal",
        object_name="path/file.zip",
        expected_sha256=_sha(payload),
        expected_size_bytes=len(payload),
    )
    captured = reader.verify(
        bucket="worker-v3-formal",
        object_name="path/manifest.json",
        expected_sha256=_sha(payload),
        expected_size_bytes=len(payload),
        capture=True,
    )

    assert fast.verified is True
    assert fast.method == "immutable_metadata_and_size"
    assert captured.verified is True
    assert captured.method == "stream_sha256"
    assert captured.payload == payload
    assert client.get_count == 1
