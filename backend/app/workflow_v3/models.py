from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import declarative_base


WorkflowV3Base = declarative_base()


class JsonMixin:
    @staticmethod
    def dump(value) -> str:
        return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def load(value: str | None, default):
        try:
            return json.loads(value or "")
        except (TypeError, json.JSONDecodeError):
            return default


class WorkflowV3SkillRelease(WorkflowV3Base, JsonMixin):
    __tablename__ = "workflow_v3_skill_releases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    release_version = Column(String(64), nullable=False, unique=True, index=True)
    manifest_sha256 = Column(String(64), nullable=False, unique=True, index=True)
    package_bucket = Column(String(128), nullable=False)
    package_object = Column(String(1024), nullable=False)
    package_sha256 = Column(String(64), nullable=False, unique=True, index=True)
    workflow_version = Column(String(64), nullable=False, index=True)
    template_sha256 = Column(String(64), nullable=False)
    runtime_identity_sha256 = Column(String(64), nullable=False)
    manifest_json = Column(Text, nullable=False)
    status = Column(String(16), nullable=False, default="registered", index=True)
    registered_by = Column(String(128), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        CheckConstraint(
            "status IN ('registered','qualification','retired')",
            name="ck_v3_skill_release_status",
        ),
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "release_version": self.release_version,
            "manifest_sha256": self.manifest_sha256,
            "package": {
                "bucket": self.package_bucket,
                "object": self.package_object,
                "sha256": self.package_sha256,
            },
            "workflow_version": self.workflow_version,
            "template_sha256": self.template_sha256,
            "runtime_identity_sha256": self.runtime_identity_sha256,
            "manifest": self.load(self.manifest_json, {}),
            "status": self.status,
            "registered_by": self.registered_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class WorkflowV3Job(WorkflowV3Base, JsonMixin):
    __tablename__ = "workflow_v3_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    public_id = Column(String(36), nullable=False, unique=True, index=True)
    idempotency_key = Column(String(64), nullable=False, unique=True, index=True)
    user_id = Column(String(64), nullable=False, index=True)
    material_pk = Column(Integer, nullable=False, index=True)
    material_id = Column(String(128), nullable=False, index=True)
    source_popo_bucket = Column(String(128), nullable=False)
    source_popo_object = Column(String(1024), nullable=False)
    source_popo_sha256 = Column(String(64), nullable=False)
    workflow_version = Column(String(64), nullable=False, index=True)
    skill_release_id = Column(Integer, ForeignKey("workflow_v3_skill_releases.id"), nullable=False, index=True)
    skill_release_version = Column(String(64), nullable=False)
    skill_release_sha256 = Column(String(64), nullable=False, index=True)
    template_sha256 = Column(String(64), nullable=False)
    machine_status = Column(String(32), nullable=False, default="queued", index=True)
    spec_status = Column(String(32), nullable=False, default="not_evaluated", index=True)
    readiness_status = Column(String(32), nullable=False, default="not_ready", index=True)
    human_acceptance_status = Column(String(32), nullable=False, default="pending", index=True)
    current_stage_key = Column(String(64), nullable=False)
    current_generation = Column(Integer, nullable=False, default=1)
    priority = Column(Integer, nullable=False, default=100)
    payload_json = Column(Text, nullable=False, default="{}")
    error_code = Column(String(128), nullable=False, default="")
    error_message = Column(Text, nullable=False, default="")
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        CheckConstraint(
            "machine_status IN ('queued','running','needs_review','failed','cancelled','succeeded')",
            name="ck_v3_job_machine_status",
        ),
        CheckConstraint(
            "spec_status IN ('not_evaluated','in_progress','needs_review','failed','passed')",
            name="ck_v3_job_spec_status",
        ),
        CheckConstraint(
            "readiness_status IN ('not_ready','ready')",
            name="ck_v3_job_readiness_status",
        ),
        CheckConstraint(
            "human_acceptance_status IN ('pending','accepted','rejected')",
            name="ck_v3_job_human_acceptance_status",
        ),
        Index("idx_v3_job_material_status", "user_id", "material_id", "machine_status"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.public_id,
            "user_id": self.user_id,
            "material_pk": str(self.material_pk),
            "material_id": self.material_id,
            "source_popo_manifest": {
                "bucket": self.source_popo_bucket,
                "object": self.source_popo_object,
                "sha256": self.source_popo_sha256,
            },
            "workflow_version": self.workflow_version,
            "skill_release": {
                "version": self.skill_release_version,
                "sha256": self.skill_release_sha256,
            },
            "template_sha256": self.template_sha256,
            "machine_status": self.machine_status,
            "spec_status": self.spec_status,
            "readiness_status": self.readiness_status,
            "human_acceptance_status": self.human_acceptance_status,
            "spec_passed": self.spec_status == "passed",
            "ready_for_user_acceptance": self.readiness_status == "ready",
            "human_accepted": self.human_acceptance_status == "accepted",
            "current_stage_key": self.current_stage_key,
            "current_generation": self.current_generation,
            "priority": self.priority,
            "payload": self.load(self.payload_json, {}),
            "error": {"code": self.error_code, "message": self.error_message},
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class WorkflowV3StageRun(WorkflowV3Base, JsonMixin):
    __tablename__ = "workflow_v3_stage_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workflow_job_id = Column(Integer, ForeignKey("workflow_v3_jobs.id"), nullable=False, index=True)
    stage_key = Column(String(64), nullable=False, index=True)
    stage_version = Column(String(64), nullable=False)
    attempt = Column(Integer, nullable=False, default=1)
    generation = Column(Integer, nullable=False, default=1, index=True)
    review_resolution_id = Column(
        Integer,
        ForeignKey("workflow_v3_review_resolutions.id"),
        nullable=True,
        index=True,
    )
    review_resolution_sha256 = Column(String(64), nullable=False, default="")
    machine_status = Column(String(32), nullable=False, default="pending", index=True)
    spec_status = Column(String(32), nullable=False, default="not_evaluated", index=True)
    owner = Column(String(32), nullable=False)
    input_kind = Column(String(32), nullable=False)
    input_promotion_id = Column(Integer, nullable=True, index=True)
    input_artifact_sha256 = Column(String(64), nullable=False)
    promoted_candidate_id = Column(Integer, nullable=True, index=True)
    promotion_id = Column(Integer, nullable=True, index=True)
    promoted_artifact_sha256 = Column(String(64), nullable=False, default="")
    error_code = Column(String(128), nullable=False, default="")
    error_message = Column(Text, nullable=False, default="")
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("workflow_job_id", "stage_key", "attempt", name="uq_v3_stage_attempt"),
        CheckConstraint(
            "machine_status IN ('pending','queued','running','awaiting_evaluation','awaiting_promotion','needs_review','failed','cancelled','succeeded')",
            name="ck_v3_stage_machine_status",
        ),
        CheckConstraint(
            "spec_status IN ('not_evaluated','needs_review','failed','passed')",
            name="ck_v3_stage_spec_status",
        ),
        CheckConstraint(
            "input_kind IN ('frozen_source','promoted_artifact')",
            name="ck_v3_stage_input_kind",
        ),
        Index("idx_v3_stage_job_status", "workflow_job_id", "machine_status"),
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "stage_key": self.stage_key,
            "stage_version": self.stage_version,
            "attempt": self.attempt,
            "generation": self.generation,
            "review_resolution": {
                "id": str(self.review_resolution_id) if self.review_resolution_id else "",
                "sha256": self.review_resolution_sha256,
            },
            "machine_status": self.machine_status,
            "spec_status": self.spec_status,
            "owner": self.owner,
            "input": {
                "kind": self.input_kind,
                "promotion_id": str(self.input_promotion_id) if self.input_promotion_id else "",
                "sha256": self.input_artifact_sha256,
            },
            "promotion": {
                "id": str(self.promotion_id) if self.promotion_id else "",
                "candidate_id": str(self.promoted_candidate_id) if self.promoted_candidate_id else "",
                "sha256": self.promoted_artifact_sha256,
            },
            "error": {"code": self.error_code, "message": self.error_message},
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


class WorkflowV3Execution(WorkflowV3Base, JsonMixin):
    __tablename__ = "workflow_v3_executions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workflow_job_id = Column(Integer, ForeignKey("workflow_v3_jobs.id"), nullable=False, index=True)
    stage_run_id = Column(Integer, ForeignKey("workflow_v3_stage_runs.id"), nullable=False, unique=True, index=True)
    producer_identity = Column(String(128), nullable=False, index=True)
    idempotency_key = Column(String(128), nullable=False, unique=True, index=True)
    machine_status = Column(String(32), nullable=False, default="running", index=True)
    skill_release_sha256 = Column(String(64), nullable=False)
    runtime_identity_sha256 = Column(String(64), nullable=False)
    generation = Column(Integer, nullable=False, default=1)
    review_resolution_sha256 = Column(String(64), nullable=False, default="")
    metrics_json = Column(Text, nullable=False, default="{}")
    error_code = Column(String(128), nullable=False, default="")
    error_message = Column(Text, nullable=False, default="")
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    heartbeat_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    finished_at = Column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "machine_status IN ('running','failed','cancelled','succeeded')",
            name="ck_v3_execution_machine_status",
        ),
    )


class WorkflowV3Candidate(WorkflowV3Base, JsonMixin):
    __tablename__ = "workflow_v3_candidates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workflow_job_id = Column(Integer, ForeignKey("workflow_v3_jobs.id"), nullable=False, index=True)
    stage_run_id = Column(Integer, ForeignKey("workflow_v3_stage_runs.id"), nullable=False, index=True)
    execution_id = Column(Integer, ForeignKey("workflow_v3_executions.id"), nullable=False, unique=True, index=True)
    idempotency_key = Column(String(128), nullable=False, unique=True, index=True)
    artifact_kind = Column(String(128), nullable=False)
    bucket = Column(String(128), nullable=False)
    object_name = Column(String(1024), nullable=False)
    object_identity_hash = Column(String(64), nullable=False, unique=True, index=True)
    sha256 = Column(String(64), nullable=False, index=True)
    size_bytes = Column(Integer, nullable=False, default=0)
    immutable = Column(Boolean, nullable=False, default=True)
    generation = Column(Integer, nullable=False, default=1)
    review_resolution_sha256 = Column(String(64), nullable=False, default="")
    status = Column(String(32), nullable=False, default="candidate", index=True)
    metadata_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        CheckConstraint(
            "status IN ('candidate','evaluated_passed','needs_review','rejected','promoted')",
            name="ck_v3_candidate_status",
        ),
    )


class WorkflowV3Evaluation(WorkflowV3Base, JsonMixin):
    __tablename__ = "workflow_v3_evaluations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workflow_job_id = Column(Integer, ForeignKey("workflow_v3_jobs.id"), nullable=False, index=True)
    stage_run_id = Column(Integer, ForeignKey("workflow_v3_stage_runs.id"), nullable=False, index=True)
    candidate_id = Column(Integer, ForeignKey("workflow_v3_candidates.id"), nullable=False, index=True)
    idempotency_key = Column(String(128), nullable=False, unique=True, index=True)
    evaluator_identity = Column(String(128), nullable=False, index=True)
    evaluator_version = Column(String(128), nullable=False)
    policy_sha256 = Column(String(64), nullable=False)
    decision = Column(String(16), nullable=False, index=True)
    spec_passed = Column(Boolean, nullable=False, default=False)
    gate_results_json = Column(Text, nullable=False, default="{}")
    findings_json = Column(Text, nullable=False, default="[]")
    generation = Column(Integer, nullable=False, default=1)
    review_resolution_sha256 = Column(String(64), nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        CheckConstraint(
            "decision IN ('passed','needs_review','failed')",
            name="ck_v3_evaluation_decision",
        ),
    )


class WorkflowV3Promotion(WorkflowV3Base):
    __tablename__ = "workflow_v3_promotions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workflow_job_id = Column(Integer, ForeignKey("workflow_v3_jobs.id"), nullable=False, index=True)
    stage_run_id = Column(Integer, ForeignKey("workflow_v3_stage_runs.id"), nullable=False, unique=True, index=True)
    candidate_id = Column(Integer, ForeignKey("workflow_v3_candidates.id"), nullable=False, unique=True, index=True)
    evaluation_id = Column(Integer, ForeignKey("workflow_v3_evaluations.id"), nullable=False, unique=True, index=True)
    idempotency_key = Column(String(128), nullable=False, unique=True, index=True)
    artifact_sha256 = Column(String(64), nullable=False, index=True)
    promoted_by = Column(String(128), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class WorkflowV3ReviewResolution(WorkflowV3Base, JsonMixin):
    __tablename__ = "workflow_v3_review_resolutions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workflow_job_id = Column(
        Integer,
        ForeignKey("workflow_v3_jobs.id"),
        nullable=False,
        index=True,
    )
    evaluation_id = Column(
        Integer,
        ForeignKey("workflow_v3_evaluations.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    idempotency_key = Column(String(128), nullable=False, unique=True, index=True)
    evaluation_sha256 = Column(String(64), nullable=False)
    finding_fingerprints_json = Column(Text, nullable=False)
    authorized_by = Column(String(128), nullable=False, index=True)
    recovery_stage_key = Column(String(64), nullable=False, index=True)
    source_generation = Column(Integer, nullable=False)
    recovery_generation = Column(Integer, nullable=False)
    manifest_bucket = Column(String(128), nullable=False)
    manifest_object = Column(String(1024), nullable=False)
    manifest_sha256 = Column(String(64), nullable=False, unique=True, index=True)
    manifest_size_bytes = Column(Integer, nullable=False)
    manifest_json = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        CheckConstraint(
            "source_generation > 0 AND recovery_generation > source_generation",
            name="ck_v3_review_resolution_generations",
        ),
        CheckConstraint(
            "manifest_size_bytes > 0",
            name="ck_v3_review_resolution_manifest_size",
        ),
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "evaluation_id": str(self.evaluation_id),
            "evaluation_sha256": self.evaluation_sha256,
            "finding_fingerprints": self.load(
                self.finding_fingerprints_json,
                [],
            ),
            "authorized_by": self.authorized_by,
            "recovery_stage": self.recovery_stage_key,
            "source_generation": self.source_generation,
            "recovery_generation": self.recovery_generation,
            "manifest": {
                "bucket": self.manifest_bucket,
                "object": self.manifest_object,
                "sha256": self.manifest_sha256,
                "size_bytes": self.manifest_size_bytes,
            },
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@event.listens_for(WorkflowV3ReviewResolution, "before_update")
@event.listens_for(WorkflowV3ReviewResolution, "before_delete")
def _reject_review_resolution_mutation(*_args) -> None:
    raise ValueError("WorkflowV3ReviewResolution is immutable")


class WorkflowV3OperationAttempt(WorkflowV3Base, JsonMixin):
    """A leased evaluator or promotion-controller operation.

    Producer executions have their own durable lease table.  Evaluations and
    promotions need the same crash-recovery boundary without conflating a
    technical operation failure with a specification decision.
    """

    __tablename__ = "workflow_v3_operation_attempts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workflow_job_id = Column(
        Integer,
        ForeignKey("workflow_v3_jobs.id"),
        nullable=False,
        index=True,
    )
    stage_run_id = Column(
        Integer,
        ForeignKey("workflow_v3_stage_runs.id"),
        nullable=False,
        index=True,
    )
    operation = Column(String(16), nullable=False, index=True)
    target_id = Column(Integer, nullable=False, index=True)
    attempt = Column(Integer, nullable=False)
    status = Column(String(16), nullable=False, default="running", index=True)
    owner_identity = Column(String(128), nullable=False, index=True)
    owner_token_sha256 = Column(String(64), nullable=False, unique=True)
    max_attempts = Column(Integer, nullable=False, default=3)
    lease_seconds = Column(Integer, nullable=False, default=300)
    lease_expires_at = Column(DateTime, nullable=False, index=True)
    heartbeat_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    error_code = Column(String(128), nullable=False, default="")
    error_message = Column(Text, nullable=False, default="")
    metadata_json = Column(Text, nullable=False, default="{}")
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "operation IN ('evaluation','promotion')",
            name="ck_v3_operation_attempt_operation",
        ),
        CheckConstraint(
            "status IN ('running','succeeded','failed','cancelled')",
            name="ck_v3_operation_attempt_status",
        ),
        CheckConstraint(
            "attempt > 0 AND max_attempts > 0 AND lease_seconds > 0",
            name="ck_v3_operation_attempt_limits",
        ),
        UniqueConstraint(
            "operation",
            "target_id",
            "attempt",
            name="uq_v3_operation_target_attempt",
        ),
        Index(
            "idx_v3_operation_target_status",
            "operation",
            "target_id",
            "status",
        ),
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "stage_run_id": str(self.stage_run_id),
            "operation": self.operation,
            "target_id": str(self.target_id),
            "attempt": self.attempt,
            "status": self.status,
            "owner_identity": self.owner_identity,
            "max_attempts": self.max_attempts,
            "lease_seconds": self.lease_seconds,
            "lease_expires_at": (
                self.lease_expires_at.isoformat()
                if self.lease_expires_at
                else None
            ),
            "heartbeat_at": (
                self.heartbeat_at.isoformat() if self.heartbeat_at else None
            ),
            "error": {
                "code": self.error_code,
                "message": self.error_message,
            },
            "metadata": self.load(self.metadata_json, {}),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


class WorkflowV3ProjectionOutbox(WorkflowV3Base, JsonMixin):
    __tablename__ = "workflow_v3_projection_outbox"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workflow_job_id = Column(
        Integer,
        ForeignKey("workflow_v3_jobs.id"),
        nullable=False,
        index=True,
    )
    final_promotion_id = Column(
        Integer,
        ForeignKey("workflow_v3_promotions.id"),
        nullable=False,
        index=True,
    )
    idempotency_key = Column(String(128), nullable=False, unique=True, index=True)
    event_kind = Column(String(32), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="pending", index=True)
    target_kind = Column(String(64), nullable=False, default="material_output")
    payload_json = Column(Text, nullable=False)
    attempt_count = Column(Integer, nullable=False, default=0)
    lease_owner = Column(String(128), nullable=False, default="")
    lease_expires_at = Column(DateTime, nullable=True, index=True)
    last_error = Column(Text, nullable=False, default="")
    formal_target_bucket = Column(String(128), nullable=False, default="")
    formal_target_prefix = Column(String(1024), nullable=False, default="")
    formal_target_manifest_object = Column(String(1024), nullable=False, default="")
    applied_identity = Column(String(256), nullable=False, default="")
    projected_output_id = Column(Integer, nullable=True, index=True)
    projected_manifest_bucket = Column(String(128), nullable=False, default="")
    projected_manifest_object = Column(String(1024), nullable=False, default="")
    projected_manifest_sha256 = Column(String(64), nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    applied_at = Column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','processing','applied','failed','suppressed')",
            name="ck_v3_projection_outbox_status",
        ),
        CheckConstraint(
            "target_kind IN ('material_output')",
            name="ck_v3_projection_outbox_target_kind",
        ),
        CheckConstraint(
            "event_kind IN ('final_ready','human_acceptance')",
            name="ck_v3_projection_outbox_event_kind",
        ),
        UniqueConstraint(
            "workflow_job_id",
            "event_kind",
            name="uq_v3_projection_outbox_job_event",
        ),
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "final_promotion_id": str(self.final_promotion_id),
            "event_kind": self.event_kind,
            "status": self.status,
            "target_kind": self.target_kind,
            "payload": self.load(self.payload_json, {}),
            "attempt_count": self.attempt_count,
            "lease_owner": self.lease_owner,
            "lease_expires_at": (
                self.lease_expires_at.isoformat() if self.lease_expires_at else None
            ),
            "last_error": self.last_error,
            "formal_target": {
                "bucket": self.formal_target_bucket,
                "prefix": self.formal_target_prefix,
                "manifest_object": self.formal_target_manifest_object,
            },
            "applied_identity": self.applied_identity,
            "projected_output_id": (
                str(self.projected_output_id) if self.projected_output_id else ""
            ),
            "projected_manifest": {
                "bucket": self.projected_manifest_bucket,
                "object": self.projected_manifest_object,
                "sha256": self.projected_manifest_sha256,
            },
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "applied_at": self.applied_at.isoformat() if self.applied_at else None,
        }


class WorkflowV3SchemaRevision(WorkflowV3Base):
    __tablename__ = "workflow_v3_schema_revisions"

    revision = Column(String(64), primary_key=True)
    description = Column(String(256), nullable=False)
    applied_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class WorkflowV3Event(WorkflowV3Base, JsonMixin):
    __tablename__ = "workflow_v3_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workflow_job_id = Column(Integer, ForeignKey("workflow_v3_jobs.id"), nullable=False, index=True)
    stage_run_id = Column(Integer, ForeignKey("workflow_v3_stage_runs.id"), nullable=True, index=True)
    event_type = Column(String(64), nullable=False, index=True)
    level = Column(String(16), nullable=False, default="info")
    message = Column(Text, nullable=False)
    payload_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "stage_run_id": str(self.stage_run_id) if self.stage_run_id else "",
            "event_type": self.event_type,
            "level": self.level,
            "message": self.message,
            "payload": self.load(self.payload_json, {}),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class WorkflowV3ModelCall(WorkflowV3Base, JsonMixin):
    __tablename__ = "workflow_v3_model_calls"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workflow_job_id = Column(Integer, ForeignKey("workflow_v3_jobs.id"), nullable=False, index=True)
    stage_run_id = Column(Integer, ForeignKey("workflow_v3_stage_runs.id"), nullable=False, index=True)
    call_id = Column(String(128), nullable=False, unique=True, index=True)
    attempt = Column(Integer, nullable=False, default=1)
    provider = Column(String(128), nullable=False)
    model = Column(String(256), nullable=False)
    prompt_id = Column(String(128), nullable=False)
    prompt_version = Column(String(64), nullable=False)
    prompt_sha256 = Column(String(64), nullable=False)
    schema_id = Column(String(128), nullable=False)
    schema_version = Column(String(64), nullable=False)
    schema_sha256 = Column(String(64), nullable=False)
    input_sha256 = Column(String(64), nullable=False)
    release_sha256 = Column(String(64), nullable=False)
    request_sha256 = Column(String(64), nullable=False, default="")
    response_id = Column(String(256), nullable=False, default="")
    raw_response_sha256 = Column(String(64), nullable=False, default="")
    output_sha256 = Column(String(64), nullable=False, default="")
    machine_status = Column(String(16), nullable=False, default="running", index=True)
    retryable = Column(Boolean, nullable=False, default=False)
    error_code = Column(String(128), nullable=False, default="")
    parameters_json = Column(Text, nullable=False, default="{}")
    usage_json = Column(Text, nullable=False, default="{}")
    latency_ms = Column(Integer, nullable=True)
    pricing_snapshot_sha256 = Column(String(64), nullable=False, default="")
    cost_status = Column(
        String(48),
        nullable=False,
        default="pending",
        index=True,
    )
    cost_currency = Column(String(8), nullable=False, default="")
    cost_micro_units = Column(Integer, nullable=True)
    cost_breakdown_json = Column(Text, nullable=False, default="{}")
    # Deprecated V3 compatibility field. New release-bound accounting uses
    # cost_currency/cost_micro_units and never stores CNY as micro-USD.
    estimated_cost_microusd = Column(Integer, nullable=True)
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "machine_status IN ('running','succeeded','failed','cancelled')",
            name="ck_v3_model_call_status",
        ),
        CheckConstraint(
            "cost_status IN ("
            "'pending','charged','charged_failed_call',"
            "'failed_no_attributable_usage',"
            "'cancelled_no_attributable_usage','legacy_unaccounted'"
            ")",
            name="ck_v3_model_call_cost_status",
        ),
        UniqueConstraint(
            "workflow_job_id",
            "stage_run_id",
            "call_id",
            name="uq_v3_model_call_job_stage",
        ),
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "kind": "bounded_llm",
            "stage_run_id": str(self.stage_run_id),
            "call_id": self.call_id,
            "attempt": self.attempt,
            "provider": self.provider,
            "model": self.model,
            "prompt": {
                "id": self.prompt_id,
                "version": self.prompt_version,
                "sha256": self.prompt_sha256,
            },
            "schema": {
                "id": self.schema_id,
                "version": self.schema_version,
                "sha256": self.schema_sha256,
            },
            "input_sha256": self.input_sha256,
            "release_sha256": self.release_sha256,
            "request_sha256": self.request_sha256,
            "response_id": self.response_id,
            "raw_response_sha256": self.raw_response_sha256,
            "output_sha256": self.output_sha256,
            "status": self.machine_status,
            "retryable": bool(self.retryable),
            "error_code": self.error_code,
            "parameters": self.load(self.parameters_json, {}),
            "usage": self.load(self.usage_json, {}),
            "latency_ms": self.latency_ms,
            "pricing_snapshot_sha256": self.pricing_snapshot_sha256,
            "cost": {
                "status": self.cost_status,
                "currency": self.cost_currency,
                "micro_units": self.cost_micro_units,
                "breakdown": self.load(self.cost_breakdown_json, {}),
            },
            "estimated_cost_microusd": self.estimated_cost_microusd,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


class WorkflowV3WorkerHeartbeat(WorkflowV3Base, JsonMixin):
    __tablename__ = "workflow_v3_worker_heartbeats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    worker_id = Column(String(128), nullable=False, unique=True, index=True)
    role = Column(String(32), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="starting", index=True)
    runtime_identity_sha256 = Column(String(64), nullable=False, default="")
    current_job_public_id = Column(String(36), nullable=False, default="", index=True)
    current_stage_key = Column(String(64), nullable=False, default="")
    last_error = Column(Text, nullable=False, default="")
    metrics_json = Column(Text, nullable=False, default="{}")
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    heartbeat_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    __table_args__ = (
        CheckConstraint(
            "role IN ('producer','evaluator','promoter','projector')",
            name="ck_v3_worker_heartbeat_role",
        ),
        CheckConstraint(
            "status IN ('starting','idle','busy','degraded','stopped')",
            name="ck_v3_worker_heartbeat_status",
        ),
        Index("idx_v3_worker_role_heartbeat", "role", "heartbeat_at"),
    )

    def to_dict(self) -> dict:
        return {
            "worker_id": self.worker_id,
            "role": self.role,
            "status": self.status,
            "runtime_identity_sha256": self.runtime_identity_sha256,
            "current_job_id": self.current_job_public_id,
            "current_stage_key": self.current_stage_key,
            "last_error": self.last_error,
            "metrics": self.load(self.metrics_json, {}),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "heartbeat_at": self.heartbeat_at.isoformat() if self.heartbeat_at else None,
        }
