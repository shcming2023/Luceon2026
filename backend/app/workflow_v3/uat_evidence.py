from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Any, Mapping, Protocol, Sequence

from sqlalchemy.orm import Session

from app.models.material import Material, MaterialOutput
from app.models.review_asset import ReviewAsset
from app.workflow_v3.contracts import contracts_for_version
from app.workflow_v3.pricing import aggregate_model_costs
from app.workflow_v3.models import (
    WorkflowV3Candidate,
    WorkflowV3Evaluation,
    WorkflowV3Event,
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


REPORT_SCHEMA = "luceon.worker-v3-uat-evidence/v1"
UI_SNAPSHOT_SCHEMA = "luceon.worker-v3-ui-snapshot/v1"
RUNTIME_SNAPSHOT_SCHEMA = "luceon.worker-v3-runtime-snapshot/v1"
_ACTIVE_JOBS = frozenset({"queued", "running"})
_SHA256_CHARS = frozenset("0123456789abcdef")


@dataclass(frozen=True)
class ObjectCheck:
    bucket: str
    object_name: str
    expected_sha256: str
    expected_size_bytes: int | None
    actual_sha256: str
    actual_size_bytes: int
    exists: bool
    verified: bool
    method: str
    payload: bytes | None = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "bucket": self.bucket,
            "object": self.object_name,
            "expected_sha256": self.expected_sha256,
            "expected_size_bytes": self.expected_size_bytes,
            "actual_sha256": self.actual_sha256,
            "actual_size_bytes": self.actual_size_bytes,
            "exists": self.exists,
            "verified": self.verified,
            "verification_method": self.method,
            "error": self.error,
        }


def _projection_is_applied(
    row: WorkflowV3ProjectionOutbox | None,
) -> bool:
    return bool(
        row
        and row.status == "applied"
        and len(row.applied_identity or "") == 64
        and all(char in _SHA256_CHARS for char in row.applied_identity)
        and row.projected_output_id
        and row.projected_manifest_bucket
        and row.projected_manifest_object
        and len(row.projected_manifest_sha256 or "") == 64
        and all(
            char in _SHA256_CHARS
            for char in row.projected_manifest_sha256
        )
    )


def _projection_for(
    projections: Sequence[WorkflowV3ProjectionOutbox],
    event_kind: str,
) -> WorkflowV3ProjectionOutbox | None:
    return next(
        (row for row in projections if row.event_kind == event_kind),
        None,
    )


class EvidenceObjectReader(Protocol):
    def verify(
        self,
        *,
        bucket: str,
        object_name: str,
        expected_sha256: str,
        expected_size_bytes: int | None = None,
        capture: bool = False,
    ) -> ObjectCheck:
        ...


class MinioEvidenceReader:
    """Read-only exact-object verifier with immutable metadata fast-path.

    Worker V3 candidate/formal writers bind ``x-amz-meta-luceon-sha256`` only
    after byte verification.  The reader accepts that immutable metadata plus
    exact size as evidence; objects without that metadata are streamed and
    hashed.  JSON captures are always streamed and hashed.
    """

    def __init__(self, client, *, capture_limit_bytes: int = 16 * 1024 * 1024):
        self.client = client
        self.capture_limit_bytes = int(capture_limit_bytes)
        self._cache: dict[tuple[str, str, str, int | None, bool], ObjectCheck] = {}

    def verify(
        self,
        *,
        bucket: str,
        object_name: str,
        expected_sha256: str,
        expected_size_bytes: int | None = None,
        capture: bool = False,
    ) -> ObjectCheck:
        key = (
            str(bucket),
            str(object_name),
            str(expected_sha256),
            expected_size_bytes,
            bool(capture),
        )
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        try:
            stat = self.client.stat_object(bucket, object_name)
        except Exception as exc:
            result = ObjectCheck(
                bucket,
                object_name,
                expected_sha256,
                expected_size_bytes,
                "",
                0,
                False,
                False,
                "missing",
                error=str(exc),
            )
            self._cache[key] = result
            return result

        actual_size = int(getattr(stat, "size", -1))
        metadata = {
            str(key).lower(): value
            for key, value in (getattr(stat, "metadata", {}) or {}).items()
        }
        metadata_sha = str(
            metadata.get("x-amz-meta-luceon-sha256")
            or metadata.get("luceon-sha256")
            or ""
        ).lower()
        size_matches = (
            expected_size_bytes is None or actual_size == int(expected_size_bytes)
        )
        if (
            not capture
            and _is_sha256(metadata_sha)
            and metadata_sha == expected_sha256
            and size_matches
        ):
            result = ObjectCheck(
                bucket,
                object_name,
                expected_sha256,
                expected_size_bytes,
                metadata_sha,
                actual_size,
                True,
                True,
                "immutable_metadata_and_size",
            )
            self._cache[key] = result
            return result

        digest = hashlib.sha256()
        size = 0
        chunks: list[bytes] | None = [] if capture else None
        response = None
        try:
            response = self.client.get_object(bucket, object_name)
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
                if chunks is not None:
                    if size > self.capture_limit_bytes:
                        raise ValueError("captured object exceeds the JSON evidence limit")
                    chunks.append(chunk)
        except Exception as exc:
            result = ObjectCheck(
                bucket,
                object_name,
                expected_sha256,
                expected_size_bytes,
                digest.hexdigest() if size else "",
                size,
                True,
                False,
                "stream_sha256",
                error=str(exc),
            )
        else:
            actual_sha = digest.hexdigest()
            result = ObjectCheck(
                bucket,
                object_name,
                expected_sha256,
                expected_size_bytes,
                actual_sha,
                size,
                True,
                actual_sha == expected_sha256
                and size_matches
                and size == actual_size,
                "stream_sha256",
                payload=b"".join(chunks) if chunks is not None else None,
                error=(
                    ""
                    if actual_sha == expected_sha256
                    and size_matches
                    and size == actual_size
                    else "object SHA-256 or size differs from its persisted identity"
                ),
            )
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
                try:
                    release = getattr(response, "release_conn", None)
                    if callable(release):
                        release()
                except Exception:
                    pass
        self._cache[key] = result
        return result


@dataclass(frozen=True)
class UatEvidencePolicy:
    stale_after_seconds: int = 900
    require_ui_snapshot: bool = True
    require_runtime_snapshot: bool = True


class WorkerV3UatEvidenceCollector:
    def __init__(
        self,
        *,
        workflow_db: Session,
        material_db: Session,
        object_reader: EvidenceObjectReader | None,
        now: datetime | None = None,
        policy: UatEvidencePolicy | None = None,
    ):
        self.workflow_db = workflow_db
        self.material_db = material_db
        self.object_reader = object_reader
        current = now or datetime.now(timezone.utc)
        self.now = _aware_utc(current)
        self.policy = policy or UatEvidencePolicy()

    def collect(
        self,
        *,
        job_ids: Sequence[str] = (),
        cohort_id: str = "",
        cohort_field: str = "cohort_id",
        ui_snapshot: Mapping[str, Any] | None = None,
        runtime_snapshot: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        _assert_clean_read_session(self.workflow_db, "workflow_db")
        _assert_clean_read_session(self.material_db, "material_db")
        jobs = self._select_jobs(
            job_ids=job_ids,
            cohort_id=cohort_id,
            cohort_field=cohort_field,
        )
        ui_jobs, ui_error = _indexed_ui_jobs(ui_snapshot)
        runtime, runtime_findings = self._runtime_evidence(
            runtime_snapshot,
            selected_job_ids={job.public_id for job in jobs},
        )
        job_rows = [
            self._collect_job(job, ui_jobs=ui_jobs, ui_available=ui_error is None)
            for job in jobs
        ]
        findings: list[dict[str, Any]] = list(runtime_findings)
        if ui_error:
            severity = "blocker" if self.policy.require_ui_snapshot else "warning"
            findings.append(
                _finding(
                    "ui_snapshot_missing_or_invalid",
                    "ui",
                    severity,
                    ui_error,
                    category="evidence_gap",
                )
            )
        cohort_defect_blocker = any(
            row["severity"] == "blocker" and row["category"] == "defect"
            for row in findings
        )
        cohort_evidence_blocker = any(
            row["severity"] == "blocker" and row["category"] == "evidence_gap"
            for row in findings
        )
        for row in job_rows:
            ui_supplied = (
                ui_error is None and row["job"]["id"] in ui_jobs
            )
            runtime_supplied = not any(
                finding["severity"] == "blocker"
                and finding["category"] == "evidence_gap"
                and finding["layer"] == "runtime"
                for finding in runtime_findings
            )
            row["evidence"]["ui_snapshot_supplied"] = ui_supplied
            row["evidence"]["ui_db_consistent"] = ui_supplied and not any(
                finding["severity"] == "blocker"
                and finding["layer"] == "ui"
                for finding in row["findings"]
            )
            row["evidence"]["runtime_snapshot_supplied"] = runtime_supplied
            row["evidence"]["runtime_healthy"] = runtime_supplied and not any(
                finding["severity"] == "blocker"
                and finding["category"] == "defect"
                and finding["layer"] == "runtime"
                for finding in runtime_findings
            )
            if cohort_defect_blocker:
                row["acceptance"]["uat_status"] = "failed"
            elif (
                cohort_evidence_blocker
                and row["acceptance"]["uat_status"] == "passed"
            ):
                row["acceptance"]["uat_status"] = "incomplete"
        for row in job_rows:
            findings.extend(row["findings"])
        defect_blockers = [
            row
            for row in findings
            if row["severity"] == "blocker" and row["category"] == "defect"
        ]
        evidence_blockers = [
            row
            for row in findings
            if row["severity"] == "blocker" and row["category"] == "evidence_gap"
        ]
        status = (
            "failed"
            if defect_blockers
            else "incomplete"
            if evidence_blockers
            else "passed"
        )
        return {
            "schema": REPORT_SCHEMA,
            "generated_at": self.now.isoformat().replace("+00:00", "Z"),
            "selection": {
                "job_ids": list(job_ids),
                "cohort_id": cohort_id,
                "cohort_field": cohort_field if cohort_id else "",
                "matched_job_count": len(jobs),
            },
            "policy": {
                "stale_after_seconds": self.policy.stale_after_seconds,
                "require_ui_snapshot": self.policy.require_ui_snapshot,
                "require_runtime_snapshot": self.policy.require_runtime_snapshot,
                "read_only": True,
            },
            "summary": {
                "status": status,
                "job_count": len(job_rows),
                "passed_job_count": sum(
                    row["acceptance"]["uat_status"] == "passed" for row in job_rows
                ),
                "defect_blocker_count": len(defect_blockers),
                "evidence_gap_blocker_count": len(evidence_blockers),
                "warning_count": sum(
                    row["severity"] == "warning" for row in findings
                ),
            },
            "runtime": runtime,
            "jobs": job_rows,
            "findings": findings,
        }

    def _select_jobs(
        self,
        *,
        job_ids: Sequence[str],
        cohort_id: str,
        cohort_field: str,
    ) -> list[WorkflowV3Job]:
        normalized_ids = [str(value).strip() for value in job_ids if str(value).strip()]
        if bool(normalized_ids) == bool(str(cohort_id).strip()):
            raise ValueError("select exactly one of job_ids or cohort_id")
        query = self.workflow_db.query(WorkflowV3Job)
        if normalized_ids:
            rows = query.filter(WorkflowV3Job.public_id.in_(normalized_ids)).all()
            found = {row.public_id for row in rows}
            missing = sorted(set(normalized_ids) - found)
            if missing:
                raise ValueError("unknown Worker V3 job IDs: " + ", ".join(missing))
            by_id = {row.public_id: row for row in rows}
            return [by_id[value] for value in normalized_ids]
        if not cohort_field or any(
            not component
            for component in str(cohort_field).strip().split(".")
        ):
            raise ValueError("cohort_field must be a non-empty dotted payload path")
        rows = []
        for job in query.order_by(WorkflowV3Job.id.asc()).all():
            payload = job.load(job.payload_json, {})
            if str(_dotted_value(payload, cohort_field) or "") == str(cohort_id):
                rows.append(job)
        if not rows:
            raise ValueError(
                f"no Worker V3 jobs match payload {cohort_field}={cohort_id!r}"
            )
        return rows

    def _collect_job(
        self,
        job: WorkflowV3Job,
        *,
        ui_jobs: Mapping[str, Mapping[str, Any]],
        ui_available: bool,
    ) -> dict[str, Any]:
        findings: list[dict[str, Any]] = []
        stages = (
            self.workflow_db.query(WorkflowV3StageRun)
            .filter(WorkflowV3StageRun.workflow_job_id == job.id)
            .order_by(WorkflowV3StageRun.id.asc())
            .all()
        )
        executions = self._job_rows(WorkflowV3Execution, job.id)
        candidates = self._job_rows(WorkflowV3Candidate, job.id)
        evaluations = self._job_rows(WorkflowV3Evaluation, job.id)
        promotions = self._job_rows(WorkflowV3Promotion, job.id)
        operations = self._job_rows(WorkflowV3OperationAttempt, job.id)
        model_calls = self._job_rows(WorkflowV3ModelCall, job.id)
        events = self._job_rows(WorkflowV3Event, job.id)
        projections = self._job_rows(WorkflowV3ProjectionOutbox, job.id)
        release = self.workflow_db.get(
            WorkflowV3SkillRelease, job.skill_release_id
        )
        material = self.material_db.get(Material, job.material_pk)
        review_asset = self._review_asset(job)
        outputs = (
            self.material_db.query(MaterialOutput)
            .filter(
                MaterialOutput.user_id == job.user_id,
                MaterialOutput.material_pk == job.material_pk,
            )
            .order_by(MaterialOutput.id.asc())
            .all()
        )
        self._check_control_plane(
            job,
            release=release,
            stages=stages,
            executions=executions,
            candidates=candidates,
            evaluations=evaluations,
            promotions=promotions,
            operations=operations,
            model_calls=model_calls,
            projections=projections,
            findings=findings,
        )
        self._check_material_lineage(
            job,
            material=material,
            review_asset=review_asset,
            outputs=outputs,
            projections=projections,
            findings=findings,
        )
        artifact_checks = self._check_objects(
            job,
            release=release,
            candidates=candidates,
            projections=projections,
            findings=findings,
        )
        if ui_available:
            self._check_ui(
                job,
                material=material,
                review_asset=review_asset,
                ui=ui_jobs.get(job.public_id),
                findings=findings,
            )
        job_findings = [
            row for row in findings if row.get("job_id") == job.public_id
        ]
        defect_blocker = any(
            row["severity"] == "blocker" and row["category"] == "defect"
            for row in job_findings
        )
        evidence_blocker = any(
            row["severity"] == "blocker" and row["category"] == "evidence_gap"
            for row in job_findings
        )
        any_blocker = defect_blocker or evidence_blocker
        final_projection = _projection_for(projections, "final_ready")
        acceptance_projection = _projection_for(
            projections,
            "human_acceptance",
        )
        formal_projection_applied = _projection_is_applied(final_projection)
        acceptance_projection_applied = _projection_is_applied(
            acceptance_projection
        )
        human_decision_recorded = job.human_acceptance_status in {
            "accepted",
            "rejected",
        }
        delivery_status = (
            "projected"
            if formal_projection_applied
            else "projection_failed"
            if final_projection is not None
            and final_projection.status in {"failed", "suppressed"}
            else "projecting"
        )
        return {
            "job": _job_identity(job),
            "skill_release": release.to_dict() if release else None,
            "material": material.to_dict() if material else None,
            "review_asset": review_asset.to_dict() if review_asset else None,
            "states": {
                "machine": job.machine_status,
                "spec": job.spec_status,
                "readiness": job.readiness_status,
                "human_acceptance": job.human_acceptance_status,
                "delivery": delivery_status,
                "human_acceptance_projection": (
                    acceptance_projection.status
                    if acceptance_projection is not None
                    else "not_recorded"
                ),
            },
            "stages": [row.to_dict() for row in stages],
            "executions": [_execution_dict(row) for row in executions],
            "operation_attempts": [row.to_dict() for row in operations],
            "model_calls": [row.to_dict() for row in model_calls],
            "model_costs": aggregate_model_costs(
                model_calls,
                stage_key_by_id={
                    row.id: row.stage_key
                    for row in stages
                },
            ),
            "evaluations": [_evaluation_dict(row) for row in evaluations],
            "promotions": [_promotion_dict(row) for row in promotions],
            "projection": [row.to_dict() for row in projections],
            "legacy_outputs": [row.to_dict() for row in outputs],
            "events": [row.to_dict() for row in events],
            "objects": artifact_checks,
            "evidence": {
                "minio_exact_objects_verified": bool(artifact_checks)
                and all(row["verified"] for row in artifact_checks),
                "legacy_lineage_present": material is not None
                and review_asset is not None,
            },
            "acceptance": {
                "machine_status": job.machine_status,
                "spec_status": job.spec_status,
                "delivery_status": delivery_status,
                "ready_for_user_acceptance": (
                    formal_projection_applied and not any_blocker
                ),
                "human_decision_recorded": human_decision_recorded,
                "human_decision": (
                    job.human_acceptance_status
                    if human_decision_recorded
                    else "pending"
                ),
                "human_acceptance_effective": (
                    human_decision_recorded
                    and acceptance_projection_applied
                    and not any_blocker
                ),
                "human_accepted": (
                    job.human_acceptance_status == "accepted"
                    and acceptance_projection_applied
                    and not any_blocker
                ),
                "uat_status": (
                    "failed"
                    if defect_blocker
                    else "incomplete"
                    if evidence_blocker
                    else "passed"
                ),
            },
            "findings": job_findings,
        }

    def _job_rows(self, model, job_pk: int) -> list:
        return (
            self.workflow_db.query(model)
            .filter(model.workflow_job_id == job_pk)
            .order_by(model.id.asc())
            .all()
        )

    def _review_asset(self, job: WorkflowV3Job) -> ReviewAsset | None:
        source = job.load(job.payload_json, {}).get("source_evidence", {})
        raw = source.get("review_asset", {}) if isinstance(source, dict) else {}
        review_asset_id = raw.get("id") if isinstance(raw, dict) else None
        if str(review_asset_id or "").isdigit():
            return self.material_db.get(ReviewAsset, int(review_asset_id))
        return (
            self.material_db.query(ReviewAsset)
            .filter(
                ReviewAsset.user_id == job.user_id,
                ReviewAsset.material_id == job.material_id,
                ReviewAsset.manifest_bucket == job.source_popo_bucket,
                ReviewAsset.manifest_object == job.source_popo_object,
            )
            .first()
        )

    def _check_control_plane(
        self,
        job: WorkflowV3Job,
        *,
        release: WorkflowV3SkillRelease | None,
        stages: Sequence[WorkflowV3StageRun],
        executions: Sequence[WorkflowV3Execution],
        candidates: Sequence[WorkflowV3Candidate],
        evaluations: Sequence[WorkflowV3Evaluation],
        promotions: Sequence[WorkflowV3Promotion],
        operations: Sequence[WorkflowV3OperationAttempt],
        model_calls: Sequence[WorkflowV3ModelCall],
        projections: Sequence[WorkflowV3ProjectionOutbox],
        findings: list[dict[str, Any]],
    ) -> None:
        if (
            release is None
            or release.status != "registered"
            or release.release_version != job.skill_release_version
            or release.manifest_sha256 != job.skill_release_sha256
            or release.workflow_version != job.workflow_version
            or release.template_sha256 != job.template_sha256
        ):
            findings.append(
                _job_finding(
                    job,
                    "skill_release_binding_mismatch",
                    "db",
                    "blocker",
                    "job is not bound to one active immutable skill release identity",
                )
            )
        try:
            contracts = list(contracts_for_version(job.workflow_version))
            expected_keys = [contract.key for contract in contracts]
        except Exception:
            contracts = []
            expected_keys = []
        contract_by_key = {row.key: row for row in contracts}
        stages_by_key: dict[str, list[WorkflowV3StageRun]] = {}
        for stage in stages:
            stages_by_key.setdefault(stage.stage_key, []).append(stage)
        first_attempt_keys = [
            row.stage_key
            for row in sorted(
                (min(rows, key=lambda item: item.id) for rows in stages_by_key.values()),
                key=lambda item: item.id,
            )
        ]
        if expected_keys and first_attempt_keys != expected_keys:
            findings.append(
                _job_finding(
                    job,
                    "stage_contract_sequence_mismatch",
                    "db",
                    "blocker",
                    "persisted stage sequence differs from the registered workflow contract",
                    evidence={"expected": expected_keys, "actual": first_attempt_keys},
                )
            )
        for stage_key, rows in stages_by_key.items():
            attempts = sorted(row.attempt for row in rows)
            if attempts != list(range(1, max(attempts, default=0) + 1)):
                findings.append(
                    _job_finding(
                        job,
                        "stage_attempt_sequence_gap",
                        "db",
                        "blocker",
                        f"stage {stage_key} attempts are not contiguous",
                        evidence={"attempts": attempts},
                    )
                )
        latest_stages = [
            max(stages_by_key[key], key=lambda item: (item.attempt, item.id))
            for key in expected_keys
            if key in stages_by_key
        ]
        candidate_by_id = {row.id: row for row in candidates}
        evaluation_by_id = {row.id: row for row in evaluations}
        promotion_by_stage = {row.stage_run_id: row for row in promotions}
        evaluations_by_stage: dict[int, list[WorkflowV3Evaluation]] = {}
        for row in evaluations:
            evaluations_by_stage.setdefault(row.stage_run_id, []).append(row)
        operations_by_stage: dict[int, list[WorkflowV3OperationAttempt]] = {}
        for row in operations:
            operations_by_stage.setdefault(row.stage_run_id, []).append(row)
        candidates_by_stage: dict[int, list[WorkflowV3Candidate]] = {}
        for row in candidates:
            candidates_by_stage.setdefault(row.stage_run_id, []).append(row)

        for stage in stages:
            promotion = promotion_by_stage.get(stage.id)
            passed_evaluations = [
                row
                for row in evaluations_by_stage.get(stage.id, [])
                if row.decision == "passed" and row.spec_passed is True
            ]
            contract = contract_by_key.get(stage.stage_key)
            gate_closed = bool(
                passed_evaluations
                and contract is not None
                and any(
                    all(
                        evaluation.load(evaluation.gate_results_json, {}).get(gate)
                        is True
                        for gate in contract.acceptance_gates
                    )
                    for evaluation in passed_evaluations
                )
            )
            if stage.machine_status == "succeeded":
                if (
                    promotion is None
                    or stage.spec_status != "passed"
                    or not gate_closed
                    or stage.promotion_id != promotion.id
                    or stage.promoted_candidate_id != promotion.candidate_id
                    or stage.promoted_artifact_sha256 != promotion.artifact_sha256
                ):
                    findings.append(
                        _stage_finding(
                            job,
                            stage,
                            "stage_false_success",
                            "db",
                            "blocker",
                            "succeeded stage lacks a matching passed evaluation and promotion",
                        )
                    )
            if promotion is not None:
                candidate = candidate_by_id.get(promotion.candidate_id)
                evaluation = evaluation_by_id.get(promotion.evaluation_id)
                if (
                    candidate is None
                    or evaluation is None
                    or candidate.stage_run_id != stage.id
                    or candidate.sha256 != promotion.artifact_sha256
                    or evaluation.candidate_id != candidate.id
                    or evaluation.decision != "passed"
                    or evaluation.spec_passed is not True
                ):
                    findings.append(
                        _stage_finding(
                            job,
                            stage,
                            "promotion_chain_mismatch",
                            "db",
                            "blocker",
                            "promotion does not bind one passed evaluation to the exact candidate",
                        )
                    )
            if (
                stage.machine_status == "awaiting_evaluation"
                and _is_stale(stage.updated_at, self.now, self.policy.stale_after_seconds)
                and candidates_by_stage.get(stage.id)
                and not any(
                    row.status == "running"
                    and not _is_expired(row.lease_expires_at, self.now)
                    for row in operations_by_stage.get(stage.id, [])
                    if row.operation == "evaluation"
                )
            ):
                findings.append(
                    _stage_finding(
                        job,
                        stage,
                        "orphaned_candidate_lock",
                        "runtime",
                        "blocker",
                        "candidate is awaiting evaluation without a live evaluator lease",
                    )
                )

        for row in executions:
            if (
                row.skill_release_sha256 != job.skill_release_sha256
                or (
                    release is not None
                    and row.runtime_identity_sha256
                    != release.runtime_identity_sha256
                )
            ):
                findings.append(
                    _job_finding(
                        job,
                        "execution_release_runtime_mismatch",
                        "db",
                        "blocker",
                        "producer execution release/runtime identity differs from the job",
                        stage_run_id=row.stage_run_id,
                    )
                )
            if (
                row.machine_status == "running"
                and _is_stale(row.heartbeat_at, self.now, self.policy.stale_after_seconds)
            ):
                findings.append(
                    _job_finding(
                        job,
                        "stale_producer_heartbeat",
                        "runtime",
                        "blocker",
                        "producer execution heartbeat is stale",
                        stage_run_id=row.stage_run_id,
                        evidence={"heartbeat_at": _iso(row.heartbeat_at)},
                    )
                )
        for row in operations:
            if row.status == "running" and _is_expired(row.lease_expires_at, self.now):
                findings.append(
                    _job_finding(
                        job,
                        "expired_operation_lease",
                        "runtime",
                        "blocker",
                        f"{row.operation} lease expired while still marked running",
                        stage_run_id=row.stage_run_id,
                        evidence={"lease_expires_at": _iso(row.lease_expires_at)},
                    )
                )
        for row in model_calls:
            if row.release_sha256 != job.skill_release_sha256:
                findings.append(
                    _job_finding(
                        job,
                        "model_release_binding_mismatch",
                        "db",
                        "blocker",
                        "model call release SHA differs from its job",
                        stage_run_id=row.stage_run_id,
                    )
                )
            if (
                row.machine_status == "running"
                and _is_stale(row.started_at, self.now, self.policy.stale_after_seconds)
            ):
                findings.append(
                    _job_finding(
                        job,
                        "stale_model_call",
                        "runtime",
                        "blocker",
                        "model call remains running beyond the evidence threshold",
                        stage_run_id=row.stage_run_id,
                    )
                )
            if row.machine_status == "succeeded" and not all(
                _is_sha256(value)
                for value in (
                    row.request_sha256,
                    row.raw_response_sha256,
                    row.output_sha256,
                )
            ):
                findings.append(
                    _job_finding(
                        job,
                        "model_call_audit_incomplete",
                        "db",
                        "blocker",
                        "successful model call lacks request/raw-response/output hashes",
                        stage_run_id=row.stage_run_id,
                    )
                )
            if row.machine_status == "succeeded" and (
                row.cost_status != "charged"
                or not _is_sha256(row.pricing_snapshot_sha256)
                or not row.cost_currency
                or row.cost_micro_units is None
                or not row.load(row.cost_breakdown_json, {})
            ):
                findings.append(
                    _job_finding(
                        job,
                        "model_call_cost_unaccounted",
                        "db",
                        "blocker",
                        "successful model call lacks release-bound attributable cost",
                        stage_run_id=row.stage_run_id,
                    )
                )
            if (
                row.machine_status in {"failed", "cancelled"}
                and row.cost_status in {"", "pending", "legacy_unaccounted"}
            ):
                findings.append(
                    _job_finding(
                        job,
                        "model_call_cost_terminal_status_missing",
                        "db",
                        "blocker",
                        "terminal model call lacks an explicit cost outcome",
                        stage_run_id=row.stage_run_id,
                    )
                )
        for row in projections:
            if (
                row.status == "processing"
                and (
                    row.lease_expires_at is None
                    or _is_expired(row.lease_expires_at, self.now)
                )
            ):
                findings.append(
                    _job_finding(
                        job,
                        "expired_projection_lease",
                        "runtime",
                        "blocker",
                        "projection remains processing without a live lease",
                    )
                )
            if (
                row.event_kind == "final_ready"
                and row.status == "applied"
                and (
                    not latest_stages
                    or row.final_promotion_id != latest_stages[-1].promotion_id
                )
            ):
                findings.append(
                    _job_finding(
                        job,
                        "final_projection_chain_mismatch",
                        "db",
                        "blocker",
                        "applied final projection does not reference the latest stage-12 promotion",
                    )
                )
        if job.machine_status == "succeeded" and (
            not latest_stages
            or any(
                row.machine_status != "succeeded" or row.spec_status != "passed"
                for row in latest_stages
            )
            or job.spec_status != "passed"
        ):
            findings.append(
                _job_finding(
                    job,
                    "job_false_success",
                    "db",
                    "blocker",
                    "job is succeeded but its stage/spec chain is not fully passed",
                )
            )
        if job.readiness_status == "ready" and (
            job.machine_status != "succeeded" or job.spec_status != "passed"
        ):
            findings.append(
                _job_finding(
                    job,
                    "readiness_state_conflict",
                    "db",
                    "blocker",
                    "ready state requires machine succeeded and spec passed",
                )
            )
        if job.human_acceptance_status == "accepted" and job.readiness_status != "ready":
            findings.append(
                _job_finding(
                    job,
                    "human_acceptance_state_conflict",
                    "db",
                    "blocker",
                    "human acceptance cannot precede readiness",
                )
            )
        if job.human_acceptance_status == "rejected":
            findings.append(
                _job_finding(
                    job,
                    "human_acceptance_rejected",
                    "db",
                    "blocker",
                    "human rejection is an explicit non-acceptance outcome",
                )
            )
        if job.machine_status in _ACTIVE_JOBS:
            findings.append(
                _job_finding(
                    job,
                    "job_not_terminal",
                    "db",
                    "blocker",
                    "UAT cannot close while the Worker V3 job is still active",
                    category="evidence_gap",
                )
            )
        elif job.machine_status != "succeeded":
            findings.append(
                _job_finding(
                    job,
                    "job_terminal_not_ready",
                    "db",
                    "blocker",
                    "Worker V3 ended without a technically ready result",
                )
            )
        elif job.spec_status != "passed" or job.readiness_status != "ready":
            findings.append(
                _job_finding(
                    job,
                    "technical_readiness_incomplete",
                    "db",
                    "blocker",
                    "succeeded machine state lacks passed spec/readiness",
                )
            )

    def _check_material_lineage(
        self,
        job: WorkflowV3Job,
        *,
        material: Material | None,
        review_asset: ReviewAsset | None,
        outputs: Sequence[MaterialOutput],
        projections: Sequence[WorkflowV3ProjectionOutbox],
        findings: list[dict[str, Any]],
    ) -> None:
        if material is None:
            findings.append(
                _job_finding(
                    job,
                    "material_missing",
                    "db",
                    "blocker",
                    "legacy/material DB has no exact material_pk",
                )
            )
        elif (
            material.user_id != job.user_id
            or material.material_id != job.material_id
            or material.popo_manifest_bucket != job.source_popo_bucket
            or material.popo_manifest_object != job.source_popo_object
        ):
            findings.append(
                _job_finding(
                    job,
                    "material_lineage_mismatch",
                    "db",
                    "blocker",
                    "material identity or Popo manifest differs from the V3 job",
                )
            )
        if review_asset is None:
            findings.append(
                _job_finding(
                    job,
                    "review_asset_missing",
                    "db",
                    "blocker",
                    "no exact frozen Popo ReviewAsset is bound to this job",
                )
            )
        elif (
            review_asset.user_id != job.user_id
            or review_asset.material_id != job.material_id
            or review_asset.manifest_bucket != job.source_popo_bucket
            or review_asset.manifest_object != job.source_popo_object
        ):
            findings.append(
                _job_finding(
                    job,
                    "review_asset_lineage_mismatch",
                    "db",
                    "blocker",
                    "ReviewAsset differs from the job Popo lineage",
                )
            )
        acceptance_projection_applied = _projection_is_applied(
            _projection_for(projections, "human_acceptance")
        )
        for projection in projections:
            if projection.status != "applied":
                continue
            output = next(
                (row for row in outputs if row.id == projection.projected_output_id),
                None,
            )
            if (
                output is None
                or output.material_id != job.material_id
                or output.manifest_bucket != projection.projected_manifest_bucket
                or output.manifest_object != projection.projected_manifest_object
            ):
                findings.append(
                    _job_finding(
                        job,
                        "projection_material_output_mismatch",
                        "db",
                        "blocker",
                        "applied projection has no matching legacy MaterialOutput",
                    )
                )
                continue
            metadata = output.metadata_dict()
            expected_output_state = (
                ("promoted", "passed", True)
                if (
                    job.human_acceptance_status == "accepted"
                    and acceptance_projection_applied
                )
                else ("candidate", "rejected", False)
                if (
                    job.human_acceptance_status == "rejected"
                    and acceptance_projection_applied
                )
                else ("candidate", "ready_for_user_acceptance", False)
            )
            if (
                output.origin != "worker_v3"
                or output.output_run_id != job.public_id
                or output.popo_run_id
                != (review_asset.run_id if review_asset is not None else None)
                or output.review_asset_id
                != (review_asset.id if review_asset is not None else None)
                or (
                    output.status,
                    output.quality_status,
                    bool(output.is_current),
                )
                != expected_output_state
                or metadata.get("workflow_v3_job_id") != job.public_id
                or metadata.get("manifest_sha256")
                != projection.projected_manifest_sha256
            ):
                findings.append(
                    _job_finding(
                        job,
                        "legacy_output_semantics_mismatch",
                        "db",
                        "blocker",
                        "MaterialOutput state/metadata does not match V3 readiness or acceptance",
                    )
                )
        if job.human_acceptance_status == "accepted" and not any(
            row.event_kind == "human_acceptance" and row.status == "applied"
            for row in projections
        ):
            findings.append(
                _job_finding(
                    job,
                    "acceptance_projection_missing",
                    "db",
                    "blocker",
                    "accepted job lacks an applied human-acceptance projection",
                )
            )

    def _check_objects(
        self,
        job: WorkflowV3Job,
        *,
        release: WorkflowV3SkillRelease | None,
        candidates: Sequence[WorkflowV3Candidate],
        projections: Sequence[WorkflowV3ProjectionOutbox],
        findings: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        checks: list[ObjectCheck] = []
        if self.object_reader is None:
            findings.append(
                _job_finding(
                    job,
                    "minio_reader_unavailable",
                    "minio",
                    "blocker",
                    "MinIO exact-object verification was not configured",
                    category="evidence_gap",
                )
            )
            return []
        source = job.load(job.payload_json, {}).get("source_evidence", {})
        source_artifacts = source.get("artifacts", []) if isinstance(source, dict) else []
        refs: list[tuple[str, str, str, int | None, str]] = []
        if release is not None:
            refs.append(
                (
                    release.package_bucket,
                    release.package_object,
                    release.package_sha256,
                    None,
                    "skill_release_package",
                )
            )
        if isinstance(source_artifacts, list):
            for raw in source_artifacts:
                parsed = _artifact_tuple(raw)
                if parsed is not None:
                    refs.append((*parsed, str(raw.get("role") or "source")))
                else:
                    findings.append(
                        _job_finding(
                            job,
                            "source_artifact_identity_invalid",
                            "db",
                            "blocker",
                            "source evidence contains an incomplete object identity",
                        )
                    )
        if not refs:
            refs.append(
                (
                    job.source_popo_bucket,
                    job.source_popo_object,
                    job.source_popo_sha256,
                    None,
                    "frozen_source",
                )
            )
        for row in candidates:
            refs.append(
                (
                    row.bucket,
                    row.object_name,
                    row.sha256,
                    int(row.size_bytes),
                    f"candidate:{row.id}",
                )
            )
        seen: set[tuple[str, str, str, int | None]] = set()
        for bucket, object_name, sha256, size_bytes, role in refs:
            identity = (bucket, object_name, sha256, size_bytes)
            if identity in seen:
                continue
            seen.add(identity)
            check = self.object_reader.verify(
                bucket=bucket,
                object_name=object_name,
                expected_sha256=sha256,
                expected_size_bytes=size_bytes,
            )
            checks.append(check)
            if not check.verified:
                findings.append(
                    _job_finding(
                        job,
                        "minio_object_identity_mismatch",
                        "minio",
                        "blocker",
                        f"{role} object is missing or differs from its exact identity",
                        evidence=check.to_dict(),
                    )
                )
        for projection in projections:
            if projection.status != "applied" or projection.event_kind != "final_ready":
                continue
            manifest_check = self.object_reader.verify(
                bucket=projection.projected_manifest_bucket,
                object_name=projection.projected_manifest_object,
                expected_sha256=projection.projected_manifest_sha256,
                capture=True,
            )
            checks.append(manifest_check)
            if not manifest_check.verified:
                findings.append(
                    _job_finding(
                        job,
                        "formal_manifest_identity_mismatch",
                        "minio",
                        "blocker",
                        "formal output manifest is missing or drifted",
                        evidence=manifest_check.to_dict(),
                    )
                )
                continue
            try:
                manifest = json.loads((manifest_check.payload or b"").decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                findings.append(
                    _job_finding(
                        job,
                        "formal_manifest_invalid",
                        "minio",
                        "blocker",
                        "formal output manifest is not valid JSON",
                    )
                )
                continue
            self._check_formal_manifest(
                job,
                projection=projection,
                manifest=manifest,
                checks=checks,
                findings=findings,
            )
        if job.readiness_status == "ready" and not any(
            row.event_kind == "final_ready" and row.status == "applied"
            for row in projections
        ):
            observed_failure = any(
                row.event_kind == "final_ready"
                and row.status in {"failed", "suppressed"}
                for row in projections
            )
            findings.append(
                _job_finding(
                    job,
                    (
                        "formal_projection_failed"
                        if observed_failure
                        else "formal_projection_missing"
                    ),
                    "delivery",
                    "blocker",
                    (
                        "ready job has a failed/suppressed formal-output projection"
                        if observed_failure
                        else "ready job has no applied formal-output projection"
                    ),
                    category="defect" if observed_failure else "evidence_gap",
                )
            )
        return [row.to_dict() for row in checks]

    def _check_formal_manifest(
        self,
        job: WorkflowV3Job,
        *,
        projection: WorkflowV3ProjectionOutbox,
        manifest: Mapping[str, Any],
        checks: list[ObjectCheck],
        findings: list[dict[str, Any]],
    ) -> None:
        if (
            manifest.get("origin") != "worker_v3"
            or manifest.get("workflow_job_id") != job.public_id
            or manifest.get("workflow_version") != job.workflow_version
            or manifest.get("material_id") != job.material_id
            or manifest.get("status") != "ready_for_user_acceptance"
            or manifest.get("template_sha256") != job.template_sha256
            or not isinstance(manifest.get("release"), Mapping)
            or manifest["release"].get("version") != job.skill_release_version
            or manifest["release"].get("manifest_sha256")
            != job.skill_release_sha256
            or not isinstance(manifest.get("source_popo_manifest"), Mapping)
            or manifest["source_popo_manifest"].get("bucket")
            != job.source_popo_bucket
            or manifest["source_popo_manifest"].get("object")
            != job.source_popo_object
            or manifest["source_popo_manifest"].get("sha256")
            != job.source_popo_sha256
        ):
            findings.append(
                _job_finding(
                    job,
                    "formal_manifest_lineage_mismatch",
                    "minio",
                    "blocker",
                    "formal manifest identity/status differs from V3 control-plane truth",
                )
            )
        files = manifest.get("files")
        volumes = manifest.get("volumes")
        if not isinstance(files, list) or not isinstance(volumes, list) or not volumes:
            findings.append(
                _job_finding(
                    job,
                    "delivery_manifest_incomplete",
                    "delivery",
                    "blocker",
                    "formal manifest has no closed file/volume inventory",
                )
            )
            return
        by_path = {
            str(row.get("path") or ""): row
            for row in files
            if isinstance(row, dict) and row.get("path")
        }
        if len(by_path) != len(files):
            findings.append(
                _job_finding(
                    job,
                    "delivery_file_inventory_duplicate_or_invalid",
                    "delivery",
                    "blocker",
                    "formal file inventory contains duplicate or invalid paths",
                )
            )
        required_paths: set[str] = set(by_path)
        required_paths.add("files/compile-report.json")
        for volume in volumes:
            artifacts = volume.get("artifacts", {}) if isinstance(volume, dict) else {}
            for role in ("package_zip", "compiled_pdf", "compile_log", "compile_report"):
                raw = artifacts.get(role) if isinstance(artifacts, dict) else None
                path = str(raw.get("path") or "") if isinstance(raw, dict) else ""
                if not path:
                    findings.append(
                        _job_finding(
                            job,
                            f"delivery_{role}_missing",
                            "delivery",
                            "blocker",
                            f"formal volume lacks {role} evidence",
                        )
                    )
                else:
                    required_paths.add(path)
        prefix = PurePosixPath(projection.projected_manifest_object).parent
        for path in sorted(required_paths):
            if not _safe_relative_object_path(path):
                findings.append(
                    _job_finding(
                        job,
                        "delivery_file_path_unsafe",
                        "delivery",
                        "blocker",
                        f"formal file inventory contains an unsafe path: {path}",
                    )
                )
                continue
            raw = by_path.get(path)
            parsed = _formal_file_tuple(raw)
            if parsed is None:
                findings.append(
                    _job_finding(
                        job,
                        "delivery_file_inventory_mismatch",
                        "delivery",
                        "blocker",
                        f"formal file inventory has no exact identity for {path}",
                    )
                )
                continue
            sha256, size_bytes = parsed
            capture = path == "files/compile-report.json"
            check = self.object_reader.verify(
                bucket=projection.projected_manifest_bucket,
                object_name=(prefix / path).as_posix(),
                expected_sha256=sha256,
                expected_size_bytes=size_bytes,
                capture=capture,
            )
            checks.append(check)
            if not check.verified:
                findings.append(
                    _job_finding(
                        job,
                        "delivery_object_identity_mismatch",
                        "minio",
                        "blocker",
                        f"formal delivery object is missing or drifted: {path}",
                        evidence=check.to_dict(),
                    )
                )
            if capture and check.verified:
                try:
                    report = json.loads((check.payload or b"").decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    report = {}
                if (
                    report.get("schema") != "luceon.worker-v3-compile-report/v1"
                    or report.get("status") != "succeeded"
                    or report.get("engine") != "latexmk-xelatex"
                    or not isinstance(report.get("volumes"), list)
                    or len(report["volumes"]) != len(volumes)
                ):
                    findings.append(
                        _job_finding(
                            job,
                            "independent_recompile_evidence_invalid",
                            "delivery",
                            "blocker",
                            "compile report does not prove successful XeLaTeX recompile",
                        )
                    )

    def _check_ui(
        self,
        job: WorkflowV3Job,
        *,
        material: Material | None,
        review_asset: ReviewAsset | None,
        ui: Mapping[str, Any] | None,
        findings: list[dict[str, Any]],
    ) -> None:
        if ui is None:
            findings.append(
                _job_finding(
                    job,
                    "job_missing_from_ui_snapshot",
                    "ui",
                    "blocker",
                    "browser-visible snapshot has no row for this job",
                    category="evidence_gap",
                )
            )
            return
        expected = {
            "material_pk": str(job.material_pk),
            "material_id": job.material_id,
            "filename": material.filename if material is not None else None,
            "popo_run_id": review_asset.run_id if review_asset is not None else None,
            "skill_release_version": job.skill_release_version,
            "machine_status": job.machine_status,
            "spec_status": job.spec_status,
            "readiness_status": job.readiness_status,
            "human_acceptance_status": job.human_acceptance_status,
            "current_stage_key": job.current_stage_key,
        }
        mismatches = {
            key: {"db": value, "ui": ui.get(key)}
            for key, value in expected.items()
            if (
                str(ui.get(key)) != value
                if key == "material_pk"
                else ui.get(key) != value
            )
        }
        if mismatches:
            findings.append(
                _job_finding(
                    job,
                    "ui_db_state_mismatch",
                    "ui",
                    "blocker",
                    "browser-visible job identity/status differs from DB truth",
                    evidence=mismatches,
                )
            )

    def _runtime_evidence(
        self,
        snapshot: Mapping[str, Any] | None,
        *,
        selected_job_ids: set[str],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        findings: list[dict[str, Any]] = []
        workers = (
            self.workflow_db.query(WorkflowV3WorkerHeartbeat)
            .order_by(WorkflowV3WorkerHeartbeat.worker_id.asc())
            .all()
        )
        worker_rows = []
        if not workers:
            findings.append(
                _finding(
                    "worker_heartbeat_evidence_missing",
                    "runtime",
                    "blocker",
                    "dedicated Worker V3 DB contains no worker heartbeat evidence",
                    category="evidence_gap",
                )
            )
        for row in workers:
            value = row.to_dict()
            value["stale"] = _is_stale(
                row.heartbeat_at, self.now, self.policy.stale_after_seconds
            )
            worker_rows.append(value)
            if (
                row.status in {"starting", "idle", "busy", "degraded"}
                and value["stale"]
            ):
                selected_or_busy = (
                    row.status == "busy"
                    or row.current_job_public_id in selected_job_ids
                )
                findings.append(
                    _finding(
                        (
                            "stale_worker_heartbeat"
                            if selected_or_busy
                            else "stale_worker_registry_row"
                        ),
                        "runtime",
                        "blocker" if selected_or_busy else "warning",
                        f"worker {row.worker_id} heartbeat is stale",
                        evidence={
                            "worker_id": row.worker_id,
                            "role": row.role,
                            "heartbeat_at": _iso(row.heartbeat_at),
                        },
                    )
                )
        containers: list[dict[str, Any]] = []
        if not isinstance(snapshot, Mapping) or snapshot.get("schema") != RUNTIME_SNAPSHOT_SCHEMA:
            severity = "blocker" if self.policy.require_runtime_snapshot else "warning"
            findings.append(
                _finding(
                    "runtime_snapshot_missing_or_invalid",
                    "runtime",
                    severity,
                    "container OOM/restart evidence was not supplied in the canonical schema",
                    category="evidence_gap",
                )
            )
        else:
            raw_containers = snapshot.get("containers")
            if not isinstance(raw_containers, list) or not raw_containers:
                findings.append(
                    _finding(
                        "runtime_container_inventory_missing",
                        "runtime",
                        "blocker",
                        "runtime snapshot contains no container inventory",
                        category="evidence_gap",
                    )
                )
            else:
                for raw in raw_containers:
                    if not isinstance(raw, Mapping):
                        continue
                    row = {
                        key: raw.get(key)
                        for key in (
                            "name",
                            "image_id",
                            "status",
                            "health",
                            "restart_count",
                            "restart_delta",
                            "oom_killed",
                        )
                        if key in raw
                    }
                    containers.append(row)
                    if row.get("oom_killed") is True:
                        findings.append(
                            _finding(
                                "container_oom_killed",
                                "runtime",
                                "blocker",
                                f"container {row.get('name') or '?'} was OOM-killed",
                                evidence=row,
                            )
                        )
                    restart_delta = row.get("restart_delta", 0)
                    if (
                        isinstance(restart_delta, int)
                        and not isinstance(restart_delta, bool)
                        and restart_delta > 0
                    ):
                        findings.append(
                            _finding(
                                "container_restart_during_uat",
                                "runtime",
                                "blocker",
                                f"container {row.get('name') or '?'} restarted during UAT",
                                evidence=row,
                            )
                        )
                    if row.get("health") in {"unhealthy", "dead"} or row.get(
                        "status"
                    ) in {"dead", "exited", "restarting"}:
                        findings.append(
                            _finding(
                                "container_unhealthy",
                                "runtime",
                                "blocker",
                                f"container {row.get('name') or '?'} is unhealthy",
                                evidence=row,
                            )
                        )
        return {
            "worker_heartbeats": worker_rows,
            "containers": containers,
            "snapshot_supplied": isinstance(snapshot, Mapping),
        }, findings


def render_markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary", {})
    lines = [
        "# Worker V3 UAT 证据报告",
        "",
        f"- 生成时间：`{report.get('generated_at', '')}`",
        f"- 总体结论：**{summary.get('status', 'unknown')}**",
        f"- 任务数：{summary.get('job_count', 0)}",
        f"- 缺陷阻断：{summary.get('defect_blocker_count', 0)}",
        f"- 证据缺口：{summary.get('evidence_gap_blocker_count', 0)}",
        f"- 警告：{summary.get('warning_count', 0)}",
        "",
        "## 逐本状态",
        "",
        "| Job | material_id | Machine | Spec | Readiness | Human | UAT |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in report.get("jobs", []):
        job = row.get("job", {})
        states = row.get("states", {})
        acceptance = row.get("acceptance", {})
        lines.append(
            "| {id} | {material} | {machine} | {spec} | {ready} | {human} | {uat} |".format(
                id=job.get("id", ""),
                material=job.get("material_id", ""),
                machine=states.get("machine", ""),
                spec=states.get("spec", ""),
                ready=states.get("readiness", ""),
                human=states.get("human_acceptance", ""),
                uat=acceptance.get("uat_status", ""),
            )
        )
    lines.extend(["", "## 发现", ""])
    findings = report.get("findings", [])
    if not findings:
        lines.append("- 无。")
    else:
        for finding in findings:
            scope = finding.get("job_id") or "cohort"
            stage = finding.get("stage_key") or finding.get("stage_run_id") or ""
            suffix = f" / {stage}" if stage else ""
            lines.append(
                f"- **{finding.get('severity')}** "
                f"`{finding.get('code')}` ({finding.get('layer')}; {scope}{suffix})："
                f"{finding.get('message')}"
            )
    lines.extend(
        [
            "",
            "## 判定边界",
            "",
            "- `machine/spec/readiness/human acceptance` 独立呈现，不互相替代。",
            "- 本报告只读；未调用状态变更 API，也未写入 DB/MinIO。",
            "- MinIO 对象以不可变 SHA 元数据+大小或流式 SHA-256 核验。",
            "- UI 或容器证据缺失时结论为 `incomplete`，不会制造通过。",
            "",
        ]
    )
    return "\n".join(lines)


def _indexed_ui_jobs(
    snapshot: Mapping[str, Any] | None,
) -> tuple[dict[str, Mapping[str, Any]], str | None]:
    if not isinstance(snapshot, Mapping):
        return {}, "UI snapshot was not supplied"
    if snapshot.get("schema") != UI_SNAPSHOT_SCHEMA:
        return {}, f"UI snapshot schema must be {UI_SNAPSHOT_SCHEMA}"
    rows = snapshot.get("jobs")
    if not isinstance(rows, list):
        return {}, "UI snapshot jobs must be a list"
    indexed: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or not str(row.get("id") or ""):
            return {}, "every UI job row requires a canonical id"
        identifier = str(row["id"])
        if identifier in indexed:
            return {}, f"duplicate UI job row: {identifier}"
        indexed[identifier] = row
    return indexed, None


def _assert_clean_read_session(session: Session, label: str) -> None:
    if session.new or session.dirty or session.deleted:
        raise ValueError(
            f"{label} contains pending writes; refusing a read-only evidence run"
        )


def _job_identity(job: WorkflowV3Job) -> dict[str, Any]:
    return {
        "id": job.public_id,
        "material_pk": str(job.material_pk),
        "material_id": job.material_id,
        "user_id": job.user_id,
        "workflow_version": job.workflow_version,
        "skill_release_version": job.skill_release_version,
        "skill_release_sha256": job.skill_release_sha256,
        "template_sha256": job.template_sha256,
        "source_popo_manifest": {
            "bucket": job.source_popo_bucket,
            "object": job.source_popo_object,
            "sha256": job.source_popo_sha256,
        },
        "created_at": _iso(job.created_at),
        "updated_at": _iso(job.updated_at),
    }


def _execution_dict(row: WorkflowV3Execution) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "stage_run_id": str(row.stage_run_id),
        "producer_identity": row.producer_identity,
        "machine_status": row.machine_status,
        "runtime_identity_sha256": row.runtime_identity_sha256,
        "heartbeat_at": _iso(row.heartbeat_at),
        "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
        "error": {"code": row.error_code, "message": row.error_message},
    }


def _evaluation_dict(row: WorkflowV3Evaluation) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "stage_run_id": str(row.stage_run_id),
        "candidate_id": str(row.candidate_id),
        "decision": row.decision,
        "spec_passed": bool(row.spec_passed),
        "evaluator_identity": row.evaluator_identity,
        "evaluator_version": row.evaluator_version,
        "policy_sha256": row.policy_sha256,
        "gate_results": row.load(row.gate_results_json, {}),
        "findings": row.load(row.findings_json, []),
    }


def _promotion_dict(row: WorkflowV3Promotion) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "stage_run_id": str(row.stage_run_id),
        "candidate_id": str(row.candidate_id),
        "evaluation_id": str(row.evaluation_id),
        "artifact_sha256": row.artifact_sha256,
        "promoted_by": row.promoted_by,
        "created_at": _iso(row.created_at),
    }


def _artifact_tuple(raw: Any) -> tuple[str, str, str, int | None] | None:
    if not isinstance(raw, Mapping):
        return None
    bucket = str(raw.get("bucket") or "")
    object_name = str(raw.get("object") or "")
    sha256 = str(raw.get("sha256") or "").lower()
    size = raw.get("size_bytes")
    if (
        not bucket
        or not object_name
        or not _is_sha256(sha256)
        or (
            size is not None
            and (
                not isinstance(size, int)
                or isinstance(size, bool)
                or size < 0
            )
        )
    ):
        return None
    return bucket, object_name, sha256, size


def _formal_file_tuple(raw: Any) -> tuple[str, int] | None:
    if not isinstance(raw, Mapping):
        return None
    sha256 = str(raw.get("sha256") or "").lower()
    size = raw.get("size_bytes")
    if (
        not _is_sha256(sha256)
        or not isinstance(size, int)
        or isinstance(size, bool)
        or size < 0
    ):
        return None
    return sha256, size


def _dotted_value(payload: Any, path: str) -> Any:
    current = payload
    for component in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(component)
    return current


def _safe_relative_object_path(value: str) -> bool:
    path = PurePosixPath(str(value))
    return bool(value) and not path.is_absolute() and all(
        part not in {"", ".", ".."} for part in path.parts
    )


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _is_sha256(value: Any) -> bool:
    text = str(value or "").lower()
    return len(text) == 64 and set(text) <= _SHA256_CHARS


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _is_stale(value: datetime | None, now: datetime, seconds: int) -> bool:
    return value is None or _aware_utc(value) < now - timedelta(seconds=seconds)


def _is_expired(value: datetime | None, now: datetime) -> bool:
    return value is None or _aware_utc(value) < now


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _aware_utc(value).isoformat().replace("+00:00", "Z")


def _finding(
    code: str,
    layer: str,
    severity: str,
    message: str,
    *,
    category: str = "defect",
    evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "layer": layer,
        "severity": severity,
        "category": category,
        "message": message,
        "evidence": dict(evidence or {}),
    }


def _job_finding(
    job: WorkflowV3Job,
    code: str,
    layer: str,
    severity: str,
    message: str,
    *,
    category: str = "defect",
    stage_run_id: int | None = None,
    evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    row = _finding(
        code,
        layer,
        severity,
        message,
        category=category,
        evidence=evidence,
    )
    row["job_id"] = job.public_id
    row["material_id"] = job.material_id
    if stage_run_id is not None:
        row["stage_run_id"] = str(stage_run_id)
    return row


def _stage_finding(
    job: WorkflowV3Job,
    stage: WorkflowV3StageRun,
    code: str,
    layer: str,
    severity: str,
    message: str,
) -> dict[str, Any]:
    row = _job_finding(
        job,
        code,
        layer,
        severity,
        message,
        stage_run_id=stage.id,
    )
    row["stage_key"] = stage.stage_key
    return row
