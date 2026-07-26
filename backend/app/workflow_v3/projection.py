from __future__ import annotations

import errno
import hashlib
import json
import mimetypes
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Protocol

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models.material import Material, MaterialOutput
from app.models.review_asset import ReviewAsset
from app.services.material_outputs import promote_material_output
from app.workflow_v3.contracts import contracts_for_version
from app.workflow_v3.executor import (
    ArtifactIntegrityError,
    ArtifactRef,
    ReleaseResolver,
    RuntimeBindingGuardProtocol,
)
from app.workflow_v3.models import (
    WorkflowV3Candidate,
    WorkflowV3Evaluation,
    WorkflowV3Job,
    WorkflowV3ProjectionOutbox,
    WorkflowV3Promotion,
    WorkflowV3SkillRelease,
    WorkflowV3StageRun,
)
from app.workflow_v3.stage_evaluators import (
    PDF_RASTER_PROFILE,
    _pdf_page_raster_sha256,
)
from app.workflow_v3.stage_entrypoint import (
    BUNDLE_PROTOCOL,
    _safe_extract_candidate_bundle,
)


FINAL_READY_SCHEMA = "luceon.worker-v3-final-projection/v1"
HUMAN_ACCEPTANCE_SCHEMA = "luceon.worker-v3-human-acceptance-projection/v1"
FORMAL_MANIFEST_SCHEMA = "luceon.workflow.artifact-manifest/v1"
FORMAL_OUTPUT_SCHEMA = "luceon.worker-v3-formal-output/v1"
READY_QUALITY_STATUS = "ready_for_user_acceptance"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MAX_ERROR_CHARS = 8_000
_TRANSIENT_STORAGE_CODES = frozenset(
    {
        "InternalError",
        "RequestTimeout",
        "RequestTimeoutException",
        "ServiceUnavailable",
        "SlowDown",
    }
)
_TRANSIENT_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
_TRANSIENT_ERRNOS = frozenset(
    {
        errno.EAGAIN,
        errno.EBUSY,
        errno.ECONNABORTED,
        errno.ECONNREFUSED,
        errno.ECONNRESET,
        errno.EHOSTUNREACH,
        errno.ENETUNREACH,
        errno.EPIPE,
        errno.ETIMEDOUT,
    }
)
_REQUIRED_FINAL_STAGE_KEYS = (
    "intake_snapshot",
    "source_scope_and_order",
    "canonical_block_ledger",
    "outline_reconstruction",
    "semantic_annotation",
    "template_construct_binding",
    "frozen_render_plan",
    "deterministic_elegantbook",
    "readonly_latex_audit",
    "independent_full_page_review",
    "delivery_recompile",
    "ready_for_user_acceptance",
)


class ProjectionError(RuntimeError):
    code = "projection_failed"


class ProjectionValidationError(ProjectionError):
    code = "projection_validation_failed"


class ProjectionStateError(ProjectionError):
    code = "projection_state_conflict"


class CandidateReader(Protocol):
    def materialize(self, artifact: ArtifactRef, destination: Path) -> ArtifactRef:
        ...


class FormalWriter(Protocol):
    def put_formal(
        self,
        source: Path,
        *,
        bucket: str,
        object_name: str,
        expected_sha256: str,
        content_type: str = "application/octet-stream",
    ) -> ArtifactRef:
        ...


@dataclass(frozen=True)
class ClaimedProjection:
    outbox_id: int
    event_kind: str
    attempt_count: int


@dataclass(frozen=True)
class ChainCandidate:
    stage_key: str
    stage_version: str
    stage_run_id: int
    promotion_id: int
    candidate_id: int
    artifact: ArtifactRef


@dataclass(frozen=True)
class FinalReadySnapshot:
    outbox_id: int
    idempotency_key: str
    workflow_job_pk: int
    workflow_job_id: str
    workflow_version: str
    user_id: str
    material_pk: int
    material_id: str
    popo_run_id: str
    source_popo_bucket: str
    source_popo_object: str
    source_popo_sha256: str
    review_asset_id: int
    review_manifest_bucket: str
    review_manifest_object: str
    review_manifest_sha256: str
    input_set_sha256: str
    release_version: str
    release_manifest_sha256: str
    template_sha256: str
    finished_at: datetime
    chain: tuple[ChainCandidate, ...]
    final_candidate: ArtifactRef


@dataclass(frozen=True)
class BoundArtifact:
    path: Path
    relative_path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class ValidatedVolume:
    volume_id: str
    delivery_zip: BoundArtifact
    compiled_pdf: BoundArtifact
    compile_log: BoundArtifact
    audit_report: BoundArtifact
    reviewed_pdf: BoundArtifact
    page_count: int


@dataclass(frozen=True)
class ValidatedDelivery:
    root: Path
    volumes: tuple[ValidatedVolume, ...]
    delivery_set: Mapping[str, Any]
    audit: Mapping[str, Any]
    page_review: Mapping[str, Any]
    recompile: Mapping[str, Any]
    readiness: Mapping[str, Any]
    promotion_chain: Mapping[str, Any]
    lineage_attestation: Mapping[str, Any]


@dataclass(frozen=True)
class FormalPublication:
    bucket: str
    manifest_object: str
    manifest_sha256: str
    output_run_id: str
    applied_identity: str
    manifest: Mapping[str, Any]


@dataclass(frozen=True)
class FormalTarget:
    bucket: str
    prefix: str
    manifest_object: str


@dataclass(frozen=True)
class AcceptanceSnapshot:
    outbox_id: int
    idempotency_key: str
    workflow_job_id: str
    user_id: str
    material_pk: int
    material_id: str
    popo_run_id: str
    review_asset_id: int
    review_manifest_bucket: str
    review_manifest_object: str
    review_manifest_sha256: str
    accepted: bool
    decided_by: str
    reason: str
    final_ready_applied_identity: str
    projected_output_id: int
    projected_manifest_bucket: str
    projected_manifest_object: str
    projected_manifest_sha256: str
    decided_at: datetime


@dataclass(frozen=True)
class AcceptancePublication:
    bucket: str
    object_name: str
    sha256: str
    size_bytes: int
    payload: Mapping[str, Any]


def claim_projection_outbox(
    db: Session,
    *,
    worker_id: str,
    lease_seconds: int = 900,
    now: datetime | None = None,
    include_failed: bool = False,
    max_attempts: int = 5,
    outbox_id: int | None = None,
    runtime_identity_sha256: str | None = None,
) -> ClaimedProjection | None:
    """Atomically lease the next applicable projection.

    A stale ``processing`` row is replayable. Failed rows require the explicit
    ``include_failed`` switch so deterministic validation failures do not
    become a hot loop.
    """

    if not worker_id:
        raise ValueError("projection worker_id is required")
    if lease_seconds < 30:
        raise ValueError("projection lease must be at least 30 seconds")
    if runtime_identity_sha256 is not None and (
        len(runtime_identity_sha256) != 64
        or any(
            char not in "0123456789abcdef"
            for char in runtime_identity_sha256
        )
    ):
        raise ValueError(
            "runtime_identity_sha256 must be a lowercase SHA-256"
        )
    timestamp = now or datetime.utcnow()
    retryable_statuses = ["pending"]
    if include_failed:
        retryable_statuses.append("failed")
    eligible = or_(
        WorkflowV3ProjectionOutbox.status.in_(retryable_statuses),
        and_(
            WorkflowV3ProjectionOutbox.status == "processing",
            or_(
                WorkflowV3ProjectionOutbox.lease_expires_at.is_(None),
                WorkflowV3ProjectionOutbox.lease_expires_at < timestamp,
            ),
        ),
    )
    query = db.query(WorkflowV3ProjectionOutbox).filter(
        eligible,
        WorkflowV3ProjectionOutbox.attempt_count < max_attempts,
    )
    if runtime_identity_sha256 is not None:
        query = (
            query.join(
                WorkflowV3Job,
                WorkflowV3Job.id
                == WorkflowV3ProjectionOutbox.workflow_job_id,
            )
            .join(
                WorkflowV3SkillRelease,
                WorkflowV3SkillRelease.id
                == WorkflowV3Job.skill_release_id,
            )
            .filter(
                WorkflowV3SkillRelease.runtime_identity_sha256
                == runtime_identity_sha256
            )
        )
    if outbox_id is not None:
        query = query.filter(WorkflowV3ProjectionOutbox.id == int(outbox_id))
    rows = query.order_by(
        WorkflowV3ProjectionOutbox.created_at.asc(),
        WorkflowV3ProjectionOutbox.id.asc(),
    ).all()
    for row in rows:
        if row.event_kind == "human_acceptance" and not _acceptance_dependency_applied(
            db, row
        ):
            continue
        previous_status = row.status
        previous_lease = row.lease_expires_at
        compare = db.query(WorkflowV3ProjectionOutbox).filter(
            WorkflowV3ProjectionOutbox.id == row.id,
            WorkflowV3ProjectionOutbox.status == previous_status,
        )
        if previous_status == "processing":
            if previous_lease is None:
                compare = compare.filter(
                    WorkflowV3ProjectionOutbox.lease_expires_at.is_(None)
                )
            else:
                compare = compare.filter(
                    WorkflowV3ProjectionOutbox.lease_expires_at == previous_lease
                )
        updated = compare.update(
            {
                WorkflowV3ProjectionOutbox.status: "processing",
                WorkflowV3ProjectionOutbox.lease_owner: worker_id,
                WorkflowV3ProjectionOutbox.lease_expires_at: timestamp
                + timedelta(seconds=lease_seconds),
                WorkflowV3ProjectionOutbox.attempt_count:
                    WorkflowV3ProjectionOutbox.attempt_count + 1,
                WorkflowV3ProjectionOutbox.updated_at: timestamp,
            },
            synchronize_session=False,
        )
        if updated != 1:
            db.expire_all()
            continue
        db.flush()
        current = db.get(WorkflowV3ProjectionOutbox, row.id)
        return ClaimedProjection(
            outbox_id=row.id,
            event_kind=row.event_kind,
            attempt_count=int(current.attempt_count),
        )
    return None


def renew_projection_lease(
    db: Session,
    outbox_id: int,
    *,
    worker_id: str,
    lease_seconds: int = 900,
    now: datetime | None = None,
) -> None:
    timestamp = now or datetime.utcnow()
    updated = (
        db.query(WorkflowV3ProjectionOutbox)
        .filter(
            WorkflowV3ProjectionOutbox.id == int(outbox_id),
            WorkflowV3ProjectionOutbox.status == "processing",
            WorkflowV3ProjectionOutbox.lease_owner == worker_id,
        )
        .update(
            {
                WorkflowV3ProjectionOutbox.lease_expires_at: timestamp
                + timedelta(seconds=lease_seconds),
                WorkflowV3ProjectionOutbox.updated_at: timestamp,
            },
            synchronize_session=False,
        )
    )
    if updated != 1:
        raise ProjectionStateError("projection lease is no longer owned by this worker")
    db.flush()


def mark_projection_applied(
    db: Session,
    outbox_id: int,
    *,
    worker_id: str,
    applied_identity: str,
    projected_output_id: int,
    projected_manifest_bucket: str,
    projected_manifest_object: str,
    projected_manifest_sha256: str,
    now: datetime | None = None,
) -> None:
    identity = _require_sha256(applied_identity, "applied_identity")
    if (
        not isinstance(projected_output_id, int)
        or isinstance(projected_output_id, bool)
        or projected_output_id <= 0
    ):
        raise ProjectionValidationError(
            "projected_output_id must be a positive integer"
        )
    _required_text(projected_manifest_bucket, "projected_manifest_bucket")
    _safe_relative(projected_manifest_object, "projected_manifest_object")
    _require_sha256(
        projected_manifest_sha256,
        "projected_manifest_sha256",
    )
    timestamp = now or datetime.utcnow()
    updated = (
        db.query(WorkflowV3ProjectionOutbox)
        .filter(
            WorkflowV3ProjectionOutbox.id == int(outbox_id),
            WorkflowV3ProjectionOutbox.status == "processing",
            WorkflowV3ProjectionOutbox.lease_owner == worker_id,
        )
        .update(
            {
                WorkflowV3ProjectionOutbox.status: "applied",
                WorkflowV3ProjectionOutbox.lease_owner: "",
                WorkflowV3ProjectionOutbox.lease_expires_at: None,
                WorkflowV3ProjectionOutbox.last_error: "",
                WorkflowV3ProjectionOutbox.applied_identity: identity,
                WorkflowV3ProjectionOutbox.projected_output_id: projected_output_id,
                WorkflowV3ProjectionOutbox.projected_manifest_bucket:
                    projected_manifest_bucket,
                WorkflowV3ProjectionOutbox.projected_manifest_object:
                    projected_manifest_object,
                WorkflowV3ProjectionOutbox.projected_manifest_sha256:
                    projected_manifest_sha256,
                WorkflowV3ProjectionOutbox.applied_at: timestamp,
                WorkflowV3ProjectionOutbox.updated_at: timestamp,
            },
            synchronize_session=False,
        )
    )
    if updated != 1:
        raise ProjectionStateError("projection cannot be marked applied without its lease")
    db.flush()


def mark_projection_failed(
    db: Session,
    outbox_id: int,
    *,
    worker_id: str,
    error: str,
    now: datetime | None = None,
) -> None:
    timestamp = now or datetime.utcnow()
    updated = (
        db.query(WorkflowV3ProjectionOutbox)
        .filter(
            WorkflowV3ProjectionOutbox.id == int(outbox_id),
            WorkflowV3ProjectionOutbox.status == "processing",
            WorkflowV3ProjectionOutbox.lease_owner == worker_id,
        )
        .update(
            {
                WorkflowV3ProjectionOutbox.status: "failed",
                WorkflowV3ProjectionOutbox.lease_owner: "",
                WorkflowV3ProjectionOutbox.lease_expires_at: None,
                WorkflowV3ProjectionOutbox.last_error: str(error)[-_MAX_ERROR_CHARS:],
                WorkflowV3ProjectionOutbox.updated_at: timestamp,
            },
            synchronize_session=False,
        )
    )
    if updated != 1:
        raise ProjectionStateError("projection cannot be marked failed without its lease")
    db.flush()


def schedule_projection_retry(
    db: Session,
    outbox_id: int,
    *,
    worker_id: str,
    error: str,
    retry_after_seconds: int,
    now: datetime | None = None,
) -> None:
    """Release a transient failure into a bounded delayed retry."""

    timestamp = now or datetime.utcnow()
    updated = (
        db.query(WorkflowV3ProjectionOutbox)
        .filter(
            WorkflowV3ProjectionOutbox.id == int(outbox_id),
            WorkflowV3ProjectionOutbox.status == "processing",
            WorkflowV3ProjectionOutbox.lease_owner == worker_id,
        )
        .update(
            {
                WorkflowV3ProjectionOutbox.status: "processing",
                WorkflowV3ProjectionOutbox.lease_owner: "",
                WorkflowV3ProjectionOutbox.lease_expires_at: timestamp
                + timedelta(seconds=max(1, int(retry_after_seconds))),
                WorkflowV3ProjectionOutbox.last_error:
                    str(error)[-_MAX_ERROR_CHARS:],
                WorkflowV3ProjectionOutbox.updated_at: timestamp,
            },
            synchronize_session=False,
        )
    )
    if updated != 1:
        raise ProjectionStateError(
            "projection cannot schedule retry without its lease"
        )
    db.flush()


class WorkflowV3ProjectionProcessor:
    """Replayable V3 outbox projector across the V3 DB, MinIO, and material DB."""

    def __init__(
        self,
        *,
        workflow_session_factory,
        material_session_factory,
        candidate_store: CandidateReader,
        formal_store: FormalWriter,
        work_root: str | Path,
        worker_id: str,
        formal_bucket: str = "eduassets-elegantbook",
        formal_prefix: str = "elegantbook/v3",
        lease_seconds: int = 900,
        phase_hook: Callable[[str], None] | None = None,
        release_resolver: ReleaseResolver | None = None,
        runtime_guard: RuntimeBindingGuardProtocol | None = None,
    ):
        if not worker_id:
            raise ValueError("projection worker_id is required")
        self.workflow_session_factory = workflow_session_factory
        self.material_session_factory = material_session_factory
        self.candidate_store = candidate_store
        self.formal_store = formal_store
        self.work_root = Path(work_root).resolve()
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.worker_id = worker_id
        self.formal_bucket = formal_bucket
        self.formal_prefix = _safe_prefix(formal_prefix)
        self.lease_seconds = max(30, int(lease_seconds))
        self.phase_hook = phase_hook or (lambda _phase: None)
        self.release_resolver = release_resolver
        self.runtime_guard = runtime_guard
        if (release_resolver is None) != (runtime_guard is None):
            raise ValueError(
                "projector release resolver and runtime guard must be supplied together"
            )

    def process_one(
        self,
        *,
        include_failed: bool = False,
        max_attempts: int = 5,
        outbox_id: int | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        db = self.workflow_session_factory()
        try:
            claim = claim_projection_outbox(
                db,
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
                include_failed=include_failed,
                max_attempts=max_attempts,
                outbox_id=outbox_id,
                now=now,
                runtime_identity_sha256=(
                    self.runtime_guard.runtime_identity_sha256
                    if self.runtime_guard is not None
                    else None
                ),
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        if claim is None:
            return {"ok": True, "status": "idle"}

        try:
            self._assert_runtime_binding(claim.outbox_id)
            if claim.event_kind == "final_ready":
                identity = self._process_final_ready(claim.outbox_id)
            elif claim.event_kind == "human_acceptance":
                identity = self._process_human_acceptance(claim.outbox_id)
            else:
                raise ProjectionValidationError(
                    f"unsupported projection event kind: {claim.event_kind!r}"
                )
        except Exception as exc:
            retryable = (
                claim.attempt_count < max_attempts
                and _is_transient_projection_error(exc)
            )
            if retryable:
                retry_after_seconds = min(
                    300,
                    5 * (2 ** max(0, claim.attempt_count - 1)),
                )
                self._schedule_retry(
                    claim.outbox_id,
                    exc,
                    retry_after_seconds=retry_after_seconds,
                )
                return {
                    "ok": False,
                    "status": "retry_scheduled",
                    "outbox_id": str(claim.outbox_id),
                    "event_kind": claim.event_kind,
                    "retry_after_seconds": retry_after_seconds,
                    "error_code": getattr(
                        exc,
                        "code",
                        "projection_transient_error",
                    ),
                    "error": str(exc),
                }
            self._mark_failed(claim.outbox_id, exc)
            return {
                "ok": False,
                "status": "failed",
                "outbox_id": str(claim.outbox_id),
                "event_kind": claim.event_kind,
                "error_code": getattr(exc, "code", "projection_unhandled_error"),
                "error": str(exc),
            }
        return {
            "ok": True,
            "status": "applied",
            "outbox_id": str(claim.outbox_id),
            "event_kind": claim.event_kind,
            "applied_identity": identity,
        }

    def _assert_runtime_binding(self, outbox_id: int) -> None:
        if self.runtime_guard is None or self.release_resolver is None:
            return
        db = self.workflow_session_factory()
        try:
            outbox = db.get(WorkflowV3ProjectionOutbox, int(outbox_id))
            if outbox is None:
                raise ProjectionValidationError(
                    "projection outbox disappeared after claim"
                )
            job = db.get(WorkflowV3Job, outbox.workflow_job_id)
            if job is None:
                raise ProjectionValidationError(
                    "projection job is missing"
                )
            release = db.get(WorkflowV3SkillRelease, job.skill_release_id)
            if release is None:
                raise ProjectionValidationError(
                    "projection release is missing"
                )
            self.runtime_guard.assert_bound(
                self.release_resolver.resolve(release),
                job=job,
                release=release,
            )
        finally:
            db.close()

    def _process_final_ready(self, outbox_id: int) -> str:
        snapshot = self._final_ready_snapshot(outbox_id)
        self._assert_material_binding(snapshot)
        target = self._freeze_formal_target(snapshot)
        self._renew(outbox_id)
        with tempfile.TemporaryDirectory(
            prefix=f"projection-{outbox_id}-",
            dir=self.work_root,
        ) as raw_work:
            work = Path(raw_work)
            archive = work / "candidate.tar.gz"
            actual = self.candidate_store.materialize(
                snapshot.final_candidate,
                archive,
            )
            _assert_ref(snapshot.final_candidate, actual)
            bundle = work / "bundle"
            bundle.mkdir(mode=0o700)
            _safe_extract_candidate_bundle(archive, bundle)
            delivery = _validate_final_bundle(snapshot, bundle)
            self._renew(outbox_id)
            publication = self._publish_formal(
                snapshot,
                delivery,
                work / "formal",
                target,
            )
            self.phase_hook("after_formal_publish")
            self._renew(outbox_id)
            output_id = self._register_candidate(snapshot, publication)
            self.phase_hook("after_material_commit")
        applied_identity = _projection_identity(
            publication.bucket,
            publication.manifest_object,
            publication.manifest_sha256,
            str(output_id),
        )
        self._mark_applied(
            outbox_id,
            applied_identity,
            projected_output_id=output_id,
            projected_manifest_bucket=publication.bucket,
            projected_manifest_object=publication.manifest_object,
            projected_manifest_sha256=publication.manifest_sha256,
        )
        return applied_identity

    def _process_human_acceptance(self, outbox_id: int) -> str:
        snapshot = self._acceptance_snapshot(outbox_id)
        self._assert_material_binding(snapshot)
        self._renew(outbox_id)
        with tempfile.TemporaryDirectory(
            prefix=f"acceptance-{outbox_id}-",
            dir=self.work_root,
        ) as raw_work:
            publication = self._publish_acceptance_commit(
                snapshot,
                Path(raw_work),
            )
        self.phase_hook("after_acceptance_publish")
        self._renew(outbox_id)
        output_id = self._apply_acceptance(snapshot, publication)
        self.phase_hook("after_material_commit")
        identity = _projection_identity(
            snapshot.final_ready_applied_identity,
            str(output_id),
            "accepted" if snapshot.accepted else "rejected",
            publication.sha256,
        )
        self._mark_applied(
            outbox_id,
            identity,
            projected_output_id=snapshot.projected_output_id,
            projected_manifest_bucket=snapshot.projected_manifest_bucket,
            projected_manifest_object=snapshot.projected_manifest_object,
            projected_manifest_sha256=snapshot.projected_manifest_sha256,
        )
        return identity

    def _final_ready_snapshot(self, outbox_id: int) -> FinalReadySnapshot:
        db = self.workflow_session_factory()
        try:
            row = _leased_outbox(db, outbox_id, self.worker_id, "final_ready")
            return _validate_final_ready_outbox(db, row)
        finally:
            db.close()

    def _acceptance_snapshot(self, outbox_id: int) -> AcceptanceSnapshot:
        db = self.workflow_session_factory()
        try:
            row = _leased_outbox(db, outbox_id, self.worker_id, "human_acceptance")
            payload = _json_payload(row, HUMAN_ACCEPTANCE_SCHEMA)
            final_id = _positive_int(
                payload.get("final_ready_outbox_id"),
                "final_ready_outbox_id",
            )
            final = db.get(WorkflowV3ProjectionOutbox, final_id)
            if (
                final is None
                or final.workflow_job_id != row.workflow_job_id
                or final.event_kind != "final_ready"
                or final.status != "applied"
                or not _SHA256_RE.fullmatch(final.applied_identity or "")
            ):
                raise ProjectionStateError(
                    "human acceptance requires an applied final-ready projection"
                )
            final_payload = _json_payload(final, FINAL_READY_SCHEMA)
            job = db.get(WorkflowV3Job, row.workflow_job_id)
            if job is None:
                raise ProjectionValidationError("acceptance job is missing")
            _require_payload_job_identity(payload, job)
            if payload.get("shadow") is not False:
                raise ProjectionValidationError(
                    "shadow human acceptance cannot be projected"
                )
            if str(payload.get("final_promotion_id") or "") != str(
                final.final_promotion_id
            ):
                raise ProjectionValidationError(
                    "acceptance final promotion differs from final readiness"
                )
            accepted = payload.get("accepted")
            if not isinstance(accepted, bool):
                raise ProjectionValidationError("acceptance decision must be boolean")
            expected_job_status = "accepted" if accepted else "rejected"
            if job.human_acceptance_status != expected_job_status:
                raise ProjectionValidationError(
                    "acceptance outbox differs from the immutable job decision"
                )
            release = payload.get("release")
            if (
                not isinstance(release, dict)
                or release.get("version") != job.skill_release_version
                or release.get("manifest_sha256") != job.skill_release_sha256
            ):
                raise ProjectionValidationError(
                    "acceptance release identity differs from its job"
                )
            expected_identity = hashlib.sha256(
                (
                    f"{job.public_id}\nhuman_acceptance\n{accepted}\n"
                    f"{final.final_promotion_id}\n{job.skill_release_sha256}"
                ).encode("utf-8")
            ).hexdigest()
            if row.idempotency_key != expected_identity:
                raise ProjectionValidationError(
                    "acceptance projection idempotency identity drifted"
                )
            if (
                not isinstance(final.projected_output_id, int)
                or final.projected_output_id <= 0
                or not final.projected_manifest_bucket
                or not final.projected_manifest_object
                or not _SHA256_RE.fullmatch(
                    final.projected_manifest_sha256 or ""
                )
            ):
                raise ProjectionStateError(
                    "final-ready projection has no exact formal output binding"
                )
            if (
                final.formal_target_bucket != final.projected_manifest_bucket
                or final.formal_target_manifest_object
                != final.projected_manifest_object
                or f"{final.formal_target_prefix}/manifest.json"
                != final.projected_manifest_object
            ):
                raise ProjectionStateError(
                    "final-ready applied output differs from its frozen formal target"
                )
            source_evidence = _source_evidence(job)
            popo_run_id = _required_text(
                source_evidence.get("run_id"),
                "source_evidence.run_id",
            )
            review_asset = _review_asset_evidence(source_evidence, job)
            if final_payload.get("job_id") != job.public_id:
                raise ProjectionValidationError(
                    "acceptance references another final-ready job"
                )
            return AcceptanceSnapshot(
                outbox_id=row.id,
                idempotency_key=row.idempotency_key,
                workflow_job_id=job.public_id,
                user_id=job.user_id,
                material_pk=job.material_pk,
                material_id=job.material_id,
                popo_run_id=popo_run_id,
                review_asset_id=review_asset["id"],
                review_manifest_bucket=review_asset["bucket"],
                review_manifest_object=review_asset["object"],
                review_manifest_sha256=review_asset["sha256"],
                accepted=accepted,
                decided_by=_required_text(payload.get("decided_by"), "decided_by"),
                reason=str(payload.get("reason") or ""),
                final_ready_applied_identity=final.applied_identity,
                projected_output_id=final.projected_output_id,
                projected_manifest_bucket=final.projected_manifest_bucket,
                projected_manifest_object=final.projected_manifest_object,
                projected_manifest_sha256=final.projected_manifest_sha256,
                decided_at=row.created_at,
            )
        finally:
            db.close()

    def _freeze_formal_target(
        self,
        snapshot: FinalReadySnapshot,
    ) -> FormalTarget:
        db = self.workflow_session_factory()
        try:
            row = _leased_outbox(
                db,
                snapshot.outbox_id,
                self.worker_id,
                "final_ready",
            )
            existing = (
                row.formal_target_bucket,
                row.formal_target_prefix,
                row.formal_target_manifest_object,
            )
            if any(existing):
                if not all(existing):
                    raise ProjectionStateError(
                        "formal projection target is only partially frozen"
                    )
                return _validated_formal_target(
                    bucket=existing[0],
                    prefix=existing[1],
                    manifest_object=existing[2],
                    snapshot=snapshot,
                )

            material_component = _safe_component(
                snapshot.material_id,
                "material_id",
            )
            popo_component = _safe_component(snapshot.popo_run_id, "popo_run_id")
            job_component = _safe_component(
                snapshot.workflow_job_id,
                "workflow_job_id",
            )
            prefix = (
                f"{self.formal_prefix}/{material_component}/{popo_component}/"
                f"{job_component}"
            )
            target = _validated_formal_target(
                bucket=self.formal_bucket,
                prefix=prefix,
                manifest_object=f"{prefix}/manifest.json",
                snapshot=snapshot,
            )
            updated = (
                db.query(WorkflowV3ProjectionOutbox)
                .filter(
                    WorkflowV3ProjectionOutbox.id == snapshot.outbox_id,
                    WorkflowV3ProjectionOutbox.status == "processing",
                    WorkflowV3ProjectionOutbox.lease_owner == self.worker_id,
                    WorkflowV3ProjectionOutbox.formal_target_bucket == "",
                    WorkflowV3ProjectionOutbox.formal_target_prefix == "",
                    WorkflowV3ProjectionOutbox.formal_target_manifest_object == "",
                )
                .update(
                    {
                        WorkflowV3ProjectionOutbox.formal_target_bucket:
                            target.bucket,
                        WorkflowV3ProjectionOutbox.formal_target_prefix:
                            target.prefix,
                        WorkflowV3ProjectionOutbox.formal_target_manifest_object:
                            target.manifest_object,
                        WorkflowV3ProjectionOutbox.updated_at: datetime.utcnow(),
                    },
                    synchronize_session=False,
                )
            )
            if updated != 1:
                raise ProjectionStateError(
                    "formal projection target could not be frozen under its lease"
                )
            db.commit()
            return target
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _assert_material_binding(
        self,
        snapshot: FinalReadySnapshot | AcceptanceSnapshot,
    ) -> None:
        db = self.material_session_factory()
        try:
            _exact_material_and_review_asset(
                db,
                user_id=snapshot.user_id,
                material_pk=snapshot.material_pk,
                material_id=snapshot.material_id,
                popo_run_id=snapshot.popo_run_id,
                review_asset_id=snapshot.review_asset_id,
                source_manifest_bucket=snapshot.review_manifest_bucket,
                source_manifest_object=snapshot.review_manifest_object,
                source_manifest_sha256=snapshot.review_manifest_sha256,
            )
        finally:
            db.close()

    def _publish_acceptance_commit(
        self,
        snapshot: AcceptanceSnapshot,
        workdir: Path,
    ) -> AcceptancePublication:
        payload = {
            "schema": "luceon.worker-v3-human-acceptance-commit/v1",
            "workflow_job_id": snapshot.workflow_job_id,
            "material_id": snapshot.material_id,
            "popo_run_id": snapshot.popo_run_id,
            "decision": "accepted" if snapshot.accepted else "rejected",
            "accepted": snapshot.accepted,
            "decided_by": snapshot.decided_by,
            "reason": snapshot.reason,
            "decided_at": snapshot.decided_at.isoformat() + "Z",
            "formal_output": {
                "output_id": str(snapshot.projected_output_id),
                "manifest": {
                    "bucket": snapshot.projected_manifest_bucket,
                    "object": snapshot.projected_manifest_object,
                    "sha256": snapshot.projected_manifest_sha256,
                },
            },
            "projection": {
                "outbox_id": str(snapshot.outbox_id),
                "idempotency_key": snapshot.idempotency_key,
                "final_ready_applied_identity":
                    snapshot.final_ready_applied_identity,
            },
        }
        path = workdir / "human-acceptance.json"
        _write_json(path, payload)
        digest = _sha256_file(path)
        manifest_path = PurePosixPath(snapshot.projected_manifest_object)
        if manifest_path.name != "manifest.json":
            raise ProjectionValidationError(
                "final-ready projection manifest path is not canonical"
            )
        object_name = f"{manifest_path.parent.as_posix()}/acceptance/{digest}.json"
        uploaded = self._put_formal_file(
            path,
            bucket=snapshot.projected_manifest_bucket,
            object_name=object_name,
            expected_sha256=digest,
            content_type="application/json",
        )
        return AcceptancePublication(
            bucket=uploaded.bucket,
            object_name=uploaded.object_name,
            sha256=uploaded.sha256,
            size_bytes=uploaded.size_bytes,
            payload=payload,
        )

    def _publish_formal(
        self,
        snapshot: FinalReadySnapshot,
        delivery: ValidatedDelivery,
        output: Path,
        target: FormalTarget,
    ) -> FormalPublication:
        output.mkdir(mode=0o700)
        prefix = target.prefix
        published: list[dict[str, Any]] = []
        formal_volumes: list[dict[str, Any]] = []
        one_volume = len(delivery.volumes) == 1

        for volume in delivery.volumes:
            volume_component = _safe_component(volume.volume_id, "volume_id")
            base = "files" if one_volume else f"volumes/{volume_component}"
            bindings: dict[str, dict[str, Any]] = {}
            for key, source, filename in (
                ("package_zip", volume.delivery_zip, "latex-project.zip"),
                ("compiled_pdf", volume.reviewed_pdf, "main.pdf"),
                ("compile_log", volume.compile_log, "main.log"),
                ("audit_report", volume.audit_report, "latex-polish-report.json"),
            ):
                relative = f"{base}/{filename}"
                uploaded = self._put_formal_file(
                    source.path,
                    bucket=target.bucket,
                    object_name=f"{prefix}/{relative}",
                    expected_sha256=source.sha256,
                )
                binding = {
                    "path": relative,
                    "sha256": uploaded.sha256,
                    "size_bytes": uploaded.size_bytes,
                }
                bindings[key] = binding
                published.append({"path": relative, **binding})
            formal_volumes.append(
                {
                    "volume_id": volume.volume_id,
                    "label": (
                        "第 1 卷"
                        if one_volume
                        else f"第 {len(formal_volumes) + 1} 卷"
                    ),
                    "objects": {
                        key: value["path"]
                        for key, value in bindings.items()
                    },
                    "artifacts": bindings,
                    "compiled_page_count": volume.page_count,
                }
            )

        compact_reports = _compact_reports(snapshot, delivery, formal_volumes)
        report_bindings: dict[str, dict[str, Any]] = {}
        for relative, payload in compact_reports.items():
            local = output / relative
            _write_json(local, payload)
            digest = _sha256_file(local)
            uploaded = self._put_formal_file(
                local,
                bucket=target.bucket,
                object_name=f"{prefix}/{relative}",
                expected_sha256=digest,
                content_type="application/json",
            )
            binding = {
                "path": relative,
                "sha256": uploaded.sha256,
                "size_bytes": uploaded.size_bytes,
            }
            report_bindings[relative] = binding
            published.append({"path": relative, **binding})
        compile_report = report_bindings["files/compile-report.json"]
        for volume in formal_volumes:
            volume["objects"]["compile_report"] = compile_report["path"]
            volume["artifacts"]["compile_report"] = compile_report

        objects: dict[str, str] = {
            "compile_report": "files/compile-report.json",
            "final_review_report_json": "files/core-acceptance.json",
            "run_state": "files/run-state.json",
        }
        if one_volume:
            objects.update(
                {
                    "compiled_pdf": formal_volumes[0]["objects"]["compiled_pdf"],
                    "package_zip": formal_volumes[0]["objects"]["package_zip"],
                    "latex_project_zip": formal_volumes[0]["objects"]["package_zip"],
                }
            )
        created_at = snapshot.finished_at.isoformat() + "Z"
        manifest = {
            "schema": FORMAL_MANIFEST_SCHEMA,
            "schema_version": FORMAL_OUTPUT_SCHEMA,
            "stage": "elegantbook",
            "origin": "worker_v3",
            "material_id": snapshot.material_id,
            "popo_run_id": snapshot.popo_run_id,
            "output_run_id": snapshot.workflow_job_id,
            "workflow_job_id": snapshot.workflow_job_id,
            "workflow_version": snapshot.workflow_version,
            "created_at": created_at,
            "updated_at": created_at,
            "status": READY_QUALITY_STATUS,
            "human_acceptance_status": "pending",
            "skill_name": "luceon-popo-to-refined-elegantbook",
            "skill_version": snapshot.release_version,
            "release": {
                "version": snapshot.release_version,
                "manifest_sha256": snapshot.release_manifest_sha256,
            },
            "template_sha256": snapshot.template_sha256,
            "input_set_sha256": snapshot.input_set_sha256,
            "source_popo_manifest": {
                "bucket": snapshot.source_popo_bucket,
                "object": snapshot.source_popo_object,
                "sha256": snapshot.source_popo_sha256,
            },
            "final_projection": {
                "outbox_id": str(snapshot.outbox_id),
                "idempotency_key": snapshot.idempotency_key,
                "candidate": {
                    "bucket": snapshot.final_candidate.bucket,
                    "object": snapshot.final_candidate.object_name,
                    "sha256": snapshot.final_candidate.sha256,
                    "size_bytes": snapshot.final_candidate.size_bytes,
                },
                "promotion_chain": [
                    {
                        "stage_key": item.stage_key,
                        "stage_version": item.stage_version,
                        "promotion_id": str(item.promotion_id),
                        "candidate_sha256": item.artifact.sha256,
                    }
                    for item in snapshot.chain
                ],
            },
            "volume_count": len(formal_volumes),
            "volumes": formal_volumes,
            "objects": objects,
            "files": sorted(published, key=lambda row: row["path"]),
        }
        manifest_path = output / "manifest.json"
        _write_json(manifest_path, manifest)
        manifest_sha256 = _sha256_file(manifest_path)
        manifest_object = target.manifest_object
        uploaded_manifest = self._put_formal_file(
            manifest_path,
            bucket=target.bucket,
            object_name=manifest_object,
            expected_sha256=manifest_sha256,
            content_type="application/json",
        )
        applied_identity = _projection_identity(
            target.bucket,
            manifest_object,
            uploaded_manifest.sha256,
        )
        return FormalPublication(
            bucket=target.bucket,
            manifest_object=manifest_object,
            manifest_sha256=uploaded_manifest.sha256,
            output_run_id=snapshot.workflow_job_id,
            applied_identity=applied_identity,
            manifest=manifest,
        )

    def _put_formal_file(
        self,
        source: Path,
        *,
        bucket: str,
        object_name: str,
        expected_sha256: str,
        content_type: str | None = None,
    ) -> ArtifactRef:
        return self.formal_store.put_formal(
            source,
            bucket=bucket,
            object_name=object_name,
            expected_sha256=expected_sha256,
            content_type=content_type
            or mimetypes.guess_type(source.name)[0]
            or "application/octet-stream",
        )

    def _register_candidate(
        self,
        snapshot: FinalReadySnapshot,
        publication: FormalPublication,
    ) -> int:
        db = self.material_session_factory()
        try:
            material, review_asset = _exact_material_and_review_asset(
                db,
                user_id=snapshot.user_id,
                material_pk=snapshot.material_pk,
                material_id=snapshot.material_id,
                popo_run_id=snapshot.popo_run_id,
                review_asset_id=snapshot.review_asset_id,
                source_manifest_bucket=snapshot.review_manifest_bucket,
                source_manifest_object=snapshot.review_manifest_object,
                source_manifest_sha256=snapshot.review_manifest_sha256,
            )
            collision = (
                db.query(MaterialOutput)
                .filter(
                    MaterialOutput.user_id == snapshot.user_id,
                    MaterialOutput.output_run_id == publication.output_run_id,
                    or_(
                        MaterialOutput.manifest_bucket != publication.bucket,
                        MaterialOutput.manifest_object != publication.manifest_object,
                    ),
                )
                .first()
            )
            if collision:
                raise ProjectionValidationError(
                    "workflow job already identifies another formal MaterialOutput"
                )
            row = (
                db.query(MaterialOutput)
                .filter(
                    MaterialOutput.user_id == snapshot.user_id,
                    MaterialOutput.manifest_bucket == publication.bucket,
                    MaterialOutput.manifest_object == publication.manifest_object,
                )
                .one_or_none()
            )
            if row is None:
                row = MaterialOutput(
                    user_id=snapshot.user_id,
                    manifest_bucket=publication.bucket,
                    manifest_object=publication.manifest_object,
                )
                db.add(row)
            elif (
                row.material_pk not in {None, material.id}
                or row.material_id not in {None, "", snapshot.material_id}
                or row.output_run_id not in {None, "", publication.output_run_id}
                or row.popo_run_id not in {None, "", snapshot.popo_run_id}
            ):
                raise ProjectionValidationError(
                    "existing MaterialOutput differs from the immutable V3 identity"
                )
            row.material_pk = material.id
            row.material_id = snapshot.material_id
            row.review_asset_id = review_asset.id
            row.output_type = "elegantbook"
            row.origin = "worker_v3"
            row.status = "candidate"
            row.quality_status = READY_QUALITY_STATUS
            row.is_current = False
            row.output_run_id = publication.output_run_id
            row.popo_run_id = snapshot.popo_run_id
            row.skill_name = "luceon-popo-to-refined-elegantbook"
            row.skill_version = snapshot.release_version
            row.version_label = f"Worker V3 · {snapshot.workflow_job_id}"
            row.metadata_json = json.dumps(
                {
                    "workflow_v3_job_id": snapshot.workflow_job_id,
                    "projection_outbox_id": str(snapshot.outbox_id),
                    "projection_idempotency_key": snapshot.idempotency_key,
                    "manifest_sha256": publication.manifest_sha256,
                    "input_set_sha256": snapshot.input_set_sha256,
                    "release_manifest_sha256": snapshot.release_manifest_sha256,
                    "template_sha256": snapshot.template_sha256,
                    "volume_count": len(publication.manifest["volumes"]),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            db.flush()
            output_id = int(row.id)
            db.commit()
            return output_id
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _apply_acceptance(
        self,
        snapshot: AcceptanceSnapshot,
        publication: AcceptancePublication,
    ) -> int:
        db = self.material_session_factory()
        try:
            material, review_asset = _exact_material_and_review_asset(
                db,
                user_id=snapshot.user_id,
                material_pk=snapshot.material_pk,
                material_id=snapshot.material_id,
                popo_run_id=snapshot.popo_run_id,
                review_asset_id=snapshot.review_asset_id,
                source_manifest_bucket=snapshot.review_manifest_bucket,
                source_manifest_object=snapshot.review_manifest_object,
                source_manifest_sha256=snapshot.review_manifest_sha256,
            )
            row = (
                db.query(MaterialOutput)
                .filter(
                    MaterialOutput.user_id == snapshot.user_id,
                    MaterialOutput.manifest_bucket
                    == snapshot.projected_manifest_bucket,
                    MaterialOutput.manifest_object
                    == snapshot.projected_manifest_object,
                )
                .one_or_none()
            )
            if (
                row is None
                or row.material_pk != material.id
                or row.material_id != snapshot.material_id
                or row.popo_run_id != snapshot.popo_run_id
                or row.output_run_id != snapshot.workflow_job_id
                or row.origin != "worker_v3"
                or row.review_asset_id != review_asset.id
            ):
                raise ProjectionValidationError(
                    "human acceptance cannot resolve the exact V3 MaterialOutput"
                )
            metadata = row.metadata_dict()
            if (
                metadata.get("manifest_sha256")
                != snapshot.projected_manifest_sha256
            ):
                raise ProjectionValidationError(
                    "human acceptance MaterialOutput manifest SHA drifted"
                )
            acceptance_metadata = {
                "accepted": snapshot.accepted,
                "decision": "accepted" if snapshot.accepted else "rejected",
                "decided_by": snapshot.decided_by,
                "reason": snapshot.reason,
                "final_ready_applied_identity": snapshot.final_ready_applied_identity,
                "commit": {
                    "bucket": publication.bucket,
                    "object": publication.object_name,
                    "sha256": publication.sha256,
                    "size_bytes": publication.size_bytes,
                },
            }
            existing_acceptance = metadata.get("human_acceptance")
            if (
                existing_acceptance is not None
                and existing_acceptance != acceptance_metadata
            ):
                raise ProjectionStateError(
                    "MaterialOutput already records another acceptance commit"
                )
            metadata["human_acceptance"] = acceptance_metadata
            row.metadata_json = json.dumps(
                metadata,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if snapshot.accepted:
                promote_material_output(db, row, material)
            else:
                if row.is_current or row.quality_status == "passed":
                    raise ProjectionStateError(
                        "a rejected candidate is already promoted and cannot be rewritten"
                    )
                row.status = "candidate"
                row.quality_status = "rejected"
                row.is_current = False
            db.flush()
            output_id = int(row.id)
            db.commit()
            return output_id
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _renew(self, outbox_id: int) -> None:
        db = self.workflow_session_factory()
        try:
            renew_projection_lease(
                db,
                outbox_id,
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _mark_applied(
        self,
        outbox_id: int,
        identity: str,
        *,
        projected_output_id: int,
        projected_manifest_bucket: str,
        projected_manifest_object: str,
        projected_manifest_sha256: str,
    ) -> None:
        db = self.workflow_session_factory()
        try:
            mark_projection_applied(
                db,
                outbox_id,
                worker_id=self.worker_id,
                applied_identity=identity,
                projected_output_id=projected_output_id,
                projected_manifest_bucket=projected_manifest_bucket,
                projected_manifest_object=projected_manifest_object,
                projected_manifest_sha256=projected_manifest_sha256,
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _mark_failed(self, outbox_id: int, error: Exception) -> None:
        db = self.workflow_session_factory()
        try:
            mark_projection_failed(
                db,
                outbox_id,
                worker_id=self.worker_id,
                error=f"{getattr(error, 'code', 'projection_unhandled_error')}: {error}",
            )
            db.commit()
        except ProjectionStateError:
            db.rollback()
        finally:
            db.close()

    def _schedule_retry(
        self,
        outbox_id: int,
        error: Exception,
        *,
        retry_after_seconds: int,
    ) -> None:
        db = self.workflow_session_factory()
        try:
            schedule_projection_retry(
                db,
                outbox_id,
                worker_id=self.worker_id,
                error=(
                    f"{getattr(error, 'code', 'projection_transient_error')}: "
                    f"{error}"
                ),
                retry_after_seconds=retry_after_seconds,
            )
            db.commit()
        except ProjectionStateError:
            db.rollback()
        finally:
            db.close()


def _validate_final_ready_outbox(
    db: Session,
    row: WorkflowV3ProjectionOutbox,
) -> FinalReadySnapshot:
    payload = _json_payload(row, FINAL_READY_SCHEMA)
    if payload.get("shadow") is not False:
        raise ProjectionValidationError("suppressed or shadow output cannot be projected")
    job = db.get(WorkflowV3Job, row.workflow_job_id)
    if job is None:
        raise ProjectionValidationError("projection job is missing")
    _require_payload_job_identity(payload, job)
    if (
        job.machine_status != "succeeded"
        or job.spec_status != "passed"
        or job.readiness_status != "ready"
        or job.human_acceptance_status != "pending"
        or job.current_stage_key != _REQUIRED_FINAL_STAGE_KEYS[-1]
        or payload.get("stage_key") != _REQUIRED_FINAL_STAGE_KEYS[-1]
        or str(payload.get("final_promotion_id") or "")
        != str(row.final_promotion_id)
        or payload.get("human_acceptance_status") != "pending"
    ):
        raise ProjectionValidationError("job is not in a projectable ready state")
    contracts = contracts_for_version(job.workflow_version)
    expected_keys = tuple(contract.key for contract in contracts)
    if expected_keys != _REQUIRED_FINAL_STAGE_KEYS:
        raise ProjectionValidationError("workflow version is not the closed 12-stage V3 contract")
    raw_chain = payload.get("promoted_chain")
    if not isinstance(raw_chain, list) or len(raw_chain) != len(expected_keys):
        raise ProjectionValidationError("final projection must contain all 12 promotions")
    chain: list[ChainCandidate] = []
    for index, (contract, raw) in enumerate(zip(contracts, raw_chain)):
        expected_key = contract.key
        if not isinstance(raw, dict):
            raise ProjectionValidationError(f"promotion chain row {index} is invalid")
        candidate_payload = raw.get("candidate")
        evaluation_payload = raw.get("evaluation")
        if not isinstance(candidate_payload, dict):
            raise ProjectionValidationError(
                f"promotion chain row {index} has no candidate"
            )
        if not isinstance(evaluation_payload, dict):
            raise ProjectionValidationError(
                f"promotion chain row {index} has no evaluation"
            )
        stage_id = _positive_int(raw.get("stage_run_id"), "stage_run_id")
        promotion_id = _positive_int(raw.get("promotion_id"), "promotion_id")
        candidate_id = _positive_int(candidate_payload.get("id"), "candidate.id")
        evaluation_id = _positive_int(
            evaluation_payload.get("id"),
            "evaluation.id",
        )
        stage = db.get(WorkflowV3StageRun, stage_id)
        promotion = db.get(WorkflowV3Promotion, promotion_id)
        candidate = db.get(WorkflowV3Candidate, candidate_id)
        evaluation = db.get(WorkflowV3Evaluation, evaluation_id)
        if (
            stage is None
            or promotion is None
            or candidate is None
            or evaluation is None
            or stage.workflow_job_id != job.id
            or promotion.workflow_job_id != job.id
            or candidate.workflow_job_id != job.id
            or evaluation.workflow_job_id != job.id
            or stage.stage_key != expected_key
            or raw.get("stage_key") != expected_key
            or raw.get("stage_version") != stage.stage_version
            or stage.stage_version != contract.stage_version
            or stage.owner != contract.owner
            or stage.machine_status != "succeeded"
            or stage.spec_status != "passed"
            or candidate.stage_run_id != stage.id
            or candidate.status != "promoted"
            or candidate.immutable is not True
            or evaluation.stage_run_id != stage.id
            or evaluation.candidate_id != candidate.id
            or evaluation.decision != "passed"
            or evaluation.spec_passed is not True
            or promotion.stage_run_id != stage.id
            or promotion.candidate_id != candidate.id
            or promotion.evaluation_id != evaluation.id
            or promotion.artifact_sha256 != candidate.sha256
            or raw.get("promotion_idempotency_key") != promotion.idempotency_key
            or candidate_payload.get("metadata")
            != candidate.load(candidate.metadata_json, {})
            or evaluation_payload.get("decision") != evaluation.decision
            or evaluation_payload.get("spec_passed") is not True
            or evaluation_payload.get("evaluator_identity")
            != evaluation.evaluator_identity
            or evaluation_payload.get("evaluator_version")
            != evaluation.evaluator_version
            or evaluation_payload.get("policy_sha256") != evaluation.policy_sha256
            or evaluation_payload.get("gate_results")
            != evaluation.load(evaluation.gate_results_json, {})
            or evaluation_payload.get("findings")
            != evaluation.load(evaluation.findings_json, [])
            or any(
                evaluation.load(evaluation.gate_results_json, {}).get(gate)
                is not True
                for gate in contract.acceptance_gates
            )
            or stage.promotion_id != promotion.id
            or stage.promoted_candidate_id != candidate.id
            or stage.promoted_artifact_sha256 != candidate.sha256
        ):
            raise ProjectionValidationError(
                f"promotion chain row {index} differs from persisted control-plane truth"
            )
        artifact = _artifact_ref(candidate_payload)
        persisted = ArtifactRef(
            candidate.bucket,
            candidate.object_name,
            candidate.sha256,
            candidate.size_bytes,
        )
        _assert_ref(persisted, artifact)
        chain.append(
            ChainCandidate(
                stage_key=expected_key,
                stage_version=stage.stage_version,
                stage_run_id=stage.id,
                promotion_id=promotion.id,
                candidate_id=candidate.id,
                artifact=persisted,
            )
        )
    persisted_promotion_ids = {
        value
        for (value,) in (
            db.query(WorkflowV3Promotion.id)
            .filter(WorkflowV3Promotion.workflow_job_id == job.id)
            .all()
        )
    }
    if persisted_promotion_ids != {item.promotion_id for item in chain}:
        raise ProjectionValidationError(
            "persisted promotion set differs from the closed 12-stage chain"
        )
    if chain[-1].promotion_id != row.final_promotion_id:
        raise ProjectionValidationError("outbox final promotion is not chain stage 12")
    final_payload = payload.get("candidate")
    if not isinstance(final_payload, dict):
        raise ProjectionValidationError("final projection candidate is missing")
    _assert_ref(chain[-1].artifact, _artifact_ref(final_payload))
    final_candidate = db.get(WorkflowV3Candidate, chain[-1].candidate_id)
    if (
        final_candidate is None
        or final_candidate.artifact_kind
        != "worker-v3-ready-for-user-acceptance-candidate"
    ):
        raise ProjectionValidationError(
            "final projection candidate kind is not readiness"
        )
    source_evidence = _source_evidence(job)
    popo_run_id = _required_text(source_evidence.get("run_id"), "source_evidence.run_id")
    review_asset = _review_asset_evidence(source_evidence, job)
    popo_manifest = source_evidence.get("popo_manifest")
    if (
        not isinstance(popo_manifest, dict)
        or popo_manifest.get("bucket") != job.source_popo_bucket
        or popo_manifest.get("object") != job.source_popo_object
        or popo_manifest.get("sha256") != job.source_popo_sha256
    ):
        raise ProjectionValidationError("source Popo evidence differs from the job binding")
    input_set_sha256 = _require_sha256(
        source_evidence.get("input_set_sha256"),
        "input_set_sha256",
    )
    if payload.get("input_set_sha256") != input_set_sha256:
        raise ProjectionValidationError("projection input-set SHA differs from job evidence")
    release = payload.get("release")
    if (
        not isinstance(release, dict)
        or release.get("version") != job.skill_release_version
        or release.get("manifest_sha256") != job.skill_release_sha256
        or payload.get("template_sha256") != job.template_sha256
    ):
        raise ProjectionValidationError("projection release or template identity drifted")
    identity = hashlib.sha256(
        (
            f"{job.public_id}\n{row.final_promotion_id}\n"
            f"{chain[-1].artifact.bucket}\n{chain[-1].artifact.object_name}\n"
            f"{chain[-1].artifact.sha256}"
        ).encode("utf-8")
    ).hexdigest()
    if row.idempotency_key != identity:
        raise ProjectionValidationError("final projection idempotency identity drifted")
    return FinalReadySnapshot(
        outbox_id=row.id,
        idempotency_key=row.idempotency_key,
        workflow_job_pk=job.id,
        workflow_job_id=job.public_id,
        workflow_version=job.workflow_version,
        user_id=job.user_id,
        material_pk=job.material_pk,
        material_id=job.material_id,
        popo_run_id=popo_run_id,
        source_popo_bucket=job.source_popo_bucket,
        source_popo_object=job.source_popo_object,
        source_popo_sha256=job.source_popo_sha256,
        review_asset_id=review_asset["id"],
        review_manifest_bucket=review_asset["bucket"],
        review_manifest_object=review_asset["object"],
        review_manifest_sha256=review_asset["sha256"],
        input_set_sha256=input_set_sha256,
        release_version=job.skill_release_version,
        release_manifest_sha256=job.skill_release_sha256,
        template_sha256=job.template_sha256,
        finished_at=job.finished_at or job.updated_at or job.created_at,
        chain=tuple(chain),
        final_candidate=chain[-1].artifact,
    )


def _validate_final_bundle(
    snapshot: FinalReadySnapshot,
    root: Path,
) -> ValidatedDelivery:
    content = _read_json(root / "candidate-content-manifest.json", "candidate manifest")
    if (
        content.get("schema_version") != BUNDLE_PROTOCOL
        or content.get("job_id") != snapshot.workflow_job_id
        or content.get("stage_key") != "ready_for_user_acceptance"
        or content.get("stage_version") != snapshot.chain[-1].stage_version
        or _positive_int(content.get("attempt"), "candidate attempt") < 1
        or content.get("artifact_kind")
        != "worker-v3-ready-for-user-acceptance-candidate"
        or content.get("input_sha256") != snapshot.chain[-2].artifact.sha256
        or content.get("release_manifest_sha256")
        != snapshot.release_manifest_sha256
        or not _SHA256_RE.fullmatch(
            str(content.get("predecessor_promotion_sha256") or "")
        )
    ):
        raise ProjectionValidationError(
            "final candidate content manifest is not bound to stage 12"
        )

    delivery = _read_json(
        root / "spec05/manifests/delivery_set_manifest.json",
        "delivery set manifest",
    )
    raw_volumes = delivery.get("volumes")
    if (
        delivery.get("schema_version") != "spec05-delivery-set-manifest/1.2"
        or delivery.get("spec_status") != "passed"
        or not isinstance(raw_volumes, list)
        or len(raw_volumes) not in {1, 2}
        or delivery.get("volume_count") != len(raw_volumes)
    ):
        raise ProjectionValidationError("stage 8 delivery set is not a passed 1–2 volume set")
    delivery_zips: dict[str, BoundArtifact] = {}
    delivery_sequence: list[str] = []
    for row in raw_volumes:
        if not isinstance(row, dict):
            raise ProjectionValidationError("delivery volume is invalid")
        volume_id = _safe_component(
            str(row.get("volume_id") or ""),
            "volume_id",
        )
        if volume_id in delivery_zips:
            raise ProjectionValidationError("delivery volume IDs must be unique")
        delivery_zips[volume_id] = _bound_artifact(
            root / "spec05",
            row.get("delivery_zip"),
            f"delivery volume {volume_id} ZIP",
            allowed_prefix="delivery/",
            allowed_suffix=".zip",
        )
        delivery_sequence.append(volume_id)

    audit = _read_json(
        root / "manifests/readonly_latex_audit.json",
        "read-only LaTeX audit",
    )
    raw_audits = audit.get("volumes")
    if (
        audit.get("schema_version")
        != "luceon.worker-v3-readonly-latex-audit/v1"
        or audit.get("input_bytes_unchanged") is not True
        or audit.get("replacement_product_created") is not False
        or not isinstance(raw_audits, list)
        or len(raw_audits) != len(delivery_zips)
    ):
        raise ProjectionValidationError("stage 9 audit boundary is incomplete")
    audit_reports: dict[str, BoundArtifact] = {}
    audit_sequence: list[str] = []
    for row in raw_audits:
        if not isinstance(row, dict):
            raise ProjectionValidationError("stage 9 audit volume is invalid")
        volume_id = _safe_component(str(row.get("volume_id") or ""), "volume_id")
        if volume_id not in delivery_zips or volume_id in audit_reports:
            raise ProjectionValidationError("stage 9 volume mapping differs from stage 8")
        audit_zip = _bound_artifact(
            root,
            row.get("delivery_zip"),
            "audited ZIP",
            expected_relative_path=(
                f"spec05/{delivery_zips[volume_id].relative_path}"
            ),
        )
        if (
            audit_zip.sha256 != delivery_zips[volume_id].sha256
            or audit_zip.path != delivery_zips[volume_id].path
        ):
            raise ProjectionValidationError("stage 9 audited another ZIP")
        report = _bound_artifact(
            root,
            row.get("audit_report"),
            "audit report",
            expected_relative_path=(
                f"audit/{volume_id}/latex_polish_report.json"
            ),
        )
        report_json = _read_json(report.path, "audit report")
        if (
            report_json.get("mode") != "audit"
            or bool(report_json.get("changes"))
            or bool(report_json.get("replacement_zip"))
        ):
            raise ProjectionValidationError("stage 9 audit mutated or replaced the product")
        audit_reports[volume_id] = report
        audit_sequence.append(volume_id)
    if audit_sequence != delivery_sequence:
        raise ProjectionValidationError("stage 9 volume order differs from stage 8")

    full_page = _read_json(
        root / "manifests/full_page_review.json",
        "full-page review manifest",
    )
    page_review_artifact = _bound_artifact(
        root,
        full_page.get("page_review"),
        "full-page review report",
        expected_relative_path="reports/page_review.json",
    )
    page_review = _read_json(page_review_artifact.path, "full-page review report")
    review_volumes = page_review.get("volumes")
    source_pdf = _bound_artifact(
        root,
        page_review.get("source_pdf"),
        "source PDF",
        expected_relative_path="lineage/source.pdf",
    )
    source_page_count = _positive_int(
        page_review.get("source_page_count"),
        "source_page_count",
    )
    if (
        full_page.get("schema_version") != "luceon.worker-v3-full-page-review/v1"
        or full_page.get("source_pdf_sha256") != source_pdf.sha256
        or full_page.get("volume_count") != len(delivery_zips)
        or int(full_page.get("blocking_findings") or 0) != 0
        or page_review.get("schema_version")
        != "luceon.worker-v3-full-page-review-evidence/v1"
        or page_review.get("review_scope") != "all_pages_source_fidelity"
        or page_review.get("source_pdf_sha256") != source_pdf.sha256
        or page_review.get("human_accepted") is not False
        or int(page_review.get("blocking_findings") or 0) != 0
        or not isinstance(review_volumes, list)
        or len(review_volumes) != len(delivery_zips)
    ):
        raise ProjectionValidationError("stage 10 full-page review is not closed")
    reviewed_pdfs: dict[str, BoundArtifact] = {}
    reviewed_page_counts: dict[str, int] = {}
    review_sequence: list[str] = []
    for row in review_volumes:
        if not isinstance(row, dict):
            raise ProjectionValidationError("stage 10 review volume is invalid")
        volume_id = _safe_component(str(row.get("volume_id") or ""), "volume_id")
        if volume_id not in delivery_zips or volume_id in reviewed_pdfs:
            raise ProjectionValidationError("stage 10 volume mapping differs from stage 8")
        pdf = _bound_artifact(
            root,
            row.get("candidate_pdf"),
            "reviewed PDF",
            allowed_prefix="spec05/delivery/",
            allowed_suffix=".pdf",
        )
        if row.get("candidate_pdf_sha256") != pdf.sha256:
            raise ProjectionValidationError("stage 10 reviewed PDF hash is inconsistent")
        page_count = _positive_int(row.get("page_count"), "review page_count")
        pages = row.get("pages")
        if (
            not isinstance(pages, list)
            or len(pages) != page_count
            or [item.get("page") for item in pages if isinstance(item, dict)]
            != list(range(1, page_count + 1))
        ):
            raise ProjectionValidationError("stage 10 did not review every PDF page")
        for page in pages:
            if not isinstance(page, dict):
                raise ProjectionValidationError("stage 10 page evidence is invalid")
            image = _bound_artifact(
                root,
                page.get("image"),
                "page raster",
                allowed_prefix="review/pages/",
                allowed_suffix=".png",
            )
            findings = page.get("findings")
            source_evidence = page.get("source_evidence")
            if (
                page.get("image_sha256") != image.sha256
                or page.get("status") != "reviewed_passed"
                or not isinstance(source_evidence, list)
                or not source_evidence
                or any(
                    not isinstance(source, dict)
                    or not isinstance(source.get("source_page"), int)
                    or isinstance(source.get("source_page"), bool)
                    or source["source_page"] < 1
                    or source["source_page"] > source_page_count
                    for source in source_evidence
                )
                or not isinstance(findings, list)
                or any(
                    isinstance(finding, dict) and finding.get("blocking") is True
                    for finding in findings
                )
            ):
                raise ProjectionValidationError(
                    "stage 10 page evidence contains an unclosed finding"
                )
        reviewed_pdfs[volume_id] = pdf
        reviewed_page_counts[volume_id] = page_count
        review_sequence.append(volume_id)
    if review_sequence != delivery_sequence:
        raise ProjectionValidationError("stage 10 volume order differs from stage 8")

    recompile = _read_json(
        root / "manifests/delivery_recompile.json",
        "delivery recompile manifest",
    )
    raw_recompile = recompile.get("volumes")
    if (
        recompile.get("schema_version")
        != "luceon.worker-v3-delivery-recompile/v1"
        or recompile.get("compiler") != "latexmk-xelatex"
        or not _SHA256_RE.fullmatch(
            str(recompile.get("target_environment_sha256") or "")
        )
        or not isinstance(raw_recompile, list)
        or len(raw_recompile) != len(delivery_zips)
    ):
        raise ProjectionValidationError("stage 11 recompile manifest is incomplete")
    validated_volumes: list[ValidatedVolume] = []
    recompile_sequence: list[str] = []
    for row in raw_recompile:
        if not isinstance(row, dict):
            raise ProjectionValidationError("stage 11 recompile volume is invalid")
        volume_id = _safe_component(str(row.get("volume_id") or ""), "volume_id")
        if volume_id not in delivery_zips or volume_id in recompile_sequence:
            raise ProjectionValidationError("stage 11 volume mapping differs from stage 8")
        delivery_zip = _bound_artifact(
            root,
            row.get("delivery_zip"),
            "recompiled ZIP",
            expected_relative_path=(
                f"spec05/{delivery_zips[volume_id].relative_path}"
            ),
        )
        reviewed_pdf = _bound_artifact(
            root,
            row.get("reviewed_pdf"),
            "Stage 11 reviewed PDF binding",
            expected_relative_path=reviewed_pdfs[volume_id].relative_path,
        )
        compiled_pdf = _bound_artifact(
            root,
            row.get("compiled_pdf"),
            "compiled PDF",
            expected_relative_path=f"recompile/{volume_id}/main.pdf",
        )
        compile_log = _bound_artifact(
            root,
            row.get("compile_log"),
            "compile log",
            expected_relative_path=f"recompile/{volume_id}/main.log",
        )
        page_count = _positive_int(row.get("compiled_page_count"), "compiled_page_count")
        reviewed_rasters = _pdf_page_raster_sha256(reviewed_pdf.path)
        compiled_rasters = _pdf_page_raster_sha256(compiled_pdf.path)
        if (
            delivery_zip.sha256 != delivery_zips[volume_id].sha256
            or delivery_zip.path != delivery_zips[volume_id].path
            or row.get("delivery_zip_sha256") != delivery_zip.sha256
            or reviewed_pdf.sha256 != reviewed_pdfs[volume_id].sha256
            or row.get("reviewed_pdf_sha256") != reviewed_pdf.sha256
            or row.get("compiled_pdf_sha256") != compiled_pdf.sha256
            or row.get("compile_log_sha256") != compile_log.sha256
            or page_count != reviewed_page_counts[volume_id]
            or row.get("raster_profile") != dict(PDF_RASTER_PROFILE)
            or row.get("reviewed_page_raster_sha256") != reviewed_rasters
            or row.get("compiled_page_raster_sha256") != compiled_rasters
            or reviewed_rasters != compiled_rasters
            or row.get("visual_equivalent") is not True
            or not _required_text(row.get("xelatex_version"), "xelatex_version")
            or not _required_text(row.get("latexmk_version"), "latexmk_version")
        ):
            raise ProjectionValidationError("stage 11 artifact hashes are inconsistent")
        recompile_sequence.append(volume_id)
        validated_volumes.append(
            ValidatedVolume(
                volume_id=volume_id,
                delivery_zip=delivery_zips[volume_id],
                compiled_pdf=compiled_pdf,
                compile_log=compile_log,
                audit_report=audit_reports[volume_id],
                reviewed_pdf=reviewed_pdfs[volume_id],
                page_count=page_count,
            )
        )
    if recompile_sequence != delivery_sequence:
        raise ProjectionValidationError("stage 11 volume order differs from stage 8")

    readiness = _read_json(
        root / "manifests/ready_for_user_acceptance.json",
        "readiness manifest",
    )
    chain_artifact = _bound_artifact(
        root,
        readiness.get("promotion_chain"),
        "promotion chain",
        expected_relative_path="lineage/promotion_chain.json",
    )
    lineage_artifact = _bound_artifact(
        root,
        readiness.get("lineage_attestation"),
        "lineage attestation",
        expected_relative_path="lineage/lineage_attestation.json",
    )
    internal_chain = _read_json(chain_artifact.path, "promotion chain")
    lineage = _read_json(lineage_artifact.path, "lineage attestation")
    promotions = internal_chain.get("promotions")
    expected_prior = list(_REQUIRED_FINAL_STAGE_KEYS[:-1])
    if (
        readiness.get("schema_version")
        != "luceon.worker-v3-ready-for-user-acceptance/v1"
        or readiness.get("machine_status") != "succeeded"
        or readiness.get("spec_status") != "passed"
        or readiness.get("readiness") != "ready_for_user_acceptance"
        or readiness.get("lineage_consistent") is not True
        or readiness.get("promotion_chain_sha256") != chain_artifact.sha256
        or readiness.get("open_blockers") != []
        or readiness.get("human_accepted") is not False
        or readiness.get("user_acceptance_record") is not None
        or internal_chain.get("schema_version")
        != "luceon.worker-v3-promotion-chain/v1"
        or not isinstance(promotions, list)
        or len(promotions) != len(expected_prior)
        or lineage.get("schema_version")
        != "luceon.worker-v3-page-db-minio-lineage/v1"
        or lineage.get("consistent") is not True
        or lineage.get("open_blockers") != []
    ):
        raise ProjectionValidationError("stage 12 readiness or lineage is incomplete")
    for index, (stage_key, row) in enumerate(zip(expected_prior, promotions)):
        if (
            not isinstance(row, dict)
            or row.get("stage_key") != stage_key
            or row.get("evaluation_decision") != "passed"
            or row.get("promotion_status") != "promoted"
            or row.get("artifact_sha256") != snapshot.chain[index].artifact.sha256
        ):
            raise ProjectionValidationError(
                "stage 12 promotion chain differs from persisted promotions"
            )
    return ValidatedDelivery(
        root=root,
        volumes=tuple(validated_volumes),
        delivery_set=delivery,
        audit=audit,
        page_review=page_review,
        recompile=recompile,
        readiness=readiness,
        promotion_chain=internal_chain,
        lineage_attestation=lineage,
    )


def _compact_reports(
    snapshot: FinalReadySnapshot,
    delivery: ValidatedDelivery,
    volumes: list[dict[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    volume_reports = [
        {
            "volume_id": row["volume_id"],
            "package_zip": row["artifacts"]["package_zip"],
            "compiled_pdf": row["artifacts"]["compiled_pdf"],
            "compile_log": row["artifacts"]["compile_log"],
            "compiled_page_count": row["compiled_page_count"],
        }
        for row in volumes
    ]
    reviewed_volumes = []
    for row in delivery.page_review["volumes"]:
        reviewed_volumes.append(
            {
                "volume_id": row["volume_id"],
                "candidate_pdf_sha256": row["candidate_pdf_sha256"],
                "page_count": row["page_count"],
                "reviewed_pages": len(row["pages"]),
                "blocking_findings": sum(
                    1
                    for page in row["pages"]
                    for finding in page["findings"]
                    if isinstance(finding, dict) and finding.get("blocking") is True
                ),
            }
        )
    delivery_summary = {
        "schema": "luceon.worker-v3-formal-delivery-set/v1",
        "spec_status": "passed",
        "volume_count": len(volumes),
        "volumes": volume_reports,
    }
    page_review_summary = {
        "schema": "luceon.worker-v3-formal-page-review/v1",
        "review_scope": "all_pages_source_fidelity",
        "source_pdf_sha256": delivery.page_review["source_pdf_sha256"],
        "source_page_count": delivery.page_review["source_page_count"],
        "blocking_findings": 0,
        "human_accepted": False,
        "volumes": reviewed_volumes,
    }
    readiness_summary = {
        "schema": "luceon.worker-v3-formal-readiness/v1",
        "machine_status": "succeeded",
        "spec_status": "passed",
        "readiness": READY_QUALITY_STATUS,
        "lineage_consistent": True,
        "open_blockers": [],
        "human_accepted": False,
        "promotion_chain": [
            {
                "stage_key": item.stage_key,
                "promotion_id": str(item.promotion_id),
                "artifact_sha256": item.artifact.sha256,
            }
            for item in snapshot.chain
        ],
    }
    return {
        "files/compile-report.json": {
            "schema": "luceon.worker-v3-compile-report/v1",
            "status": "succeeded",
            "engine": "latexmk-xelatex",
            "volumes": volume_reports,
        },
        "files/core-acceptance.json": {
            "schema": "luceon.worker-v3-core-acceptance/v1",
            "status": READY_QUALITY_STATUS,
            "spec_status": "passed",
            "human_accepted": False,
            "blocking_findings": 0,
            "volume_count": len(volumes),
            "source_pdf_sha256": delivery.page_review.get("source_pdf_sha256"),
            "input_set_sha256": snapshot.input_set_sha256,
        },
        "files/run-state.json": {
            "schema": "luceon.worker-v3-run-state/v1",
            "workflow_job_id": snapshot.workflow_job_id,
            "machine_status": "succeeded",
            "spec_status": "passed",
            "readiness_status": "ready",
            "human_acceptance_status": "pending",
            "final_projection_outbox_id": str(snapshot.outbox_id),
        },
        "files/delivery-set.json": delivery_summary,
        "files/page-review.json": page_review_summary,
        "files/readiness.json": readiness_summary,
    }


def _exact_material_and_review_asset(
    db: Session,
    *,
    user_id: str,
    material_pk: int,
    material_id: str,
    popo_run_id: str,
    review_asset_id: int,
    source_manifest_bucket: str,
    source_manifest_object: str,
    source_manifest_sha256: str,
) -> tuple[Material, ReviewAsset]:
    _require_sha256(source_manifest_sha256, "source_manifest_sha256")
    material = (
        db.query(Material)
        .filter(
            Material.id == int(material_pk),
            Material.user_id == user_id,
            Material.material_id == material_id,
            Material.ignored.is_(False),
        )
        .one_or_none()
    )
    if material is None:
        raise ProjectionValidationError("formal projection material identity is missing")
    if str(material.popo_run_id or "") != popo_run_id:
        raise ProjectionValidationError("material Popo run differs from V3 lineage")
    if (
        material.review_asset_id != review_asset_id
        or material.popo_manifest_bucket != source_manifest_bucket
        or material.popo_manifest_object != source_manifest_object
    ):
        raise ProjectionValidationError(
            "material Popo manifest or ReviewAsset differs from V3 lineage"
        )
    review_asset = (
        db.query(ReviewAsset)
        .filter(
            ReviewAsset.id == review_asset_id,
            ReviewAsset.user_id == user_id,
            ReviewAsset.material_id == material_id,
            ReviewAsset.run_id == popo_run_id,
            ReviewAsset.manifest_bucket == source_manifest_bucket,
            ReviewAsset.manifest_object == source_manifest_object,
        )
        .one_or_none()
    )
    if review_asset is None:
        raise ProjectionValidationError(
            "formal projection requires the frozen exact Popo ReviewAsset"
        )
    return material, review_asset


def _acceptance_dependency_applied(
    db: Session,
    row: WorkflowV3ProjectionOutbox,
) -> bool:
    try:
        payload = row.load(row.payload_json, {})
        final_id = int(payload.get("final_ready_outbox_id"))
    except (TypeError, ValueError):
        return False
    final = db.get(WorkflowV3ProjectionOutbox, final_id)
    return bool(
        final
        and final.workflow_job_id == row.workflow_job_id
        and final.event_kind == "final_ready"
        and final.status == "applied"
        and _SHA256_RE.fullmatch(final.applied_identity or "")
    )


def _leased_outbox(
    db: Session,
    outbox_id: int,
    worker_id: str,
    event_kind: str,
) -> WorkflowV3ProjectionOutbox:
    row = db.get(WorkflowV3ProjectionOutbox, int(outbox_id))
    if (
        row is None
        or row.status != "processing"
        or row.lease_owner != worker_id
        or row.event_kind != event_kind
        or row.target_kind != "material_output"
    ):
        raise ProjectionStateError("projection is not leased for the requested event")
    return row


def _is_transient_projection_error(error: BaseException) -> bool:
    """Recognize infrastructure failures without retrying validation defects."""

    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ProjectionError):
            return False
        if isinstance(current, (TimeoutError, ConnectionError)):
            return True
        if (
            isinstance(current, OSError)
            and current.errno in _TRANSIENT_ERRNOS
        ):
            return True
        code = getattr(current, "code", None)
        if code in _TRANSIENT_STORAGE_CODES:
            return True
        status = getattr(current, "status", None)
        if status is None:
            status = getattr(current, "status_code", None)
        if status in _TRANSIENT_HTTP_STATUSES:
            return True
        if current.__class__.__name__ in {
            "ConnectTimeout",
            "ConnectionError",
            "MaxRetryError",
            "ProtocolError",
            "ReadTimeout",
        }:
            return True
        current = current.__cause__ or current.__context__
    return False


def _json_payload(
    row: WorkflowV3ProjectionOutbox,
    expected_schema: str,
) -> dict[str, Any]:
    payload = row.load(row.payload_json, None)
    if not isinstance(payload, dict) or payload.get("schema_version") != expected_schema:
        raise ProjectionValidationError("projection payload schema is invalid")
    return payload


def _require_payload_job_identity(payload: Mapping[str, Any], job: WorkflowV3Job) -> None:
    if (
        payload.get("job_id") != job.public_id
        or payload.get("workflow_version") != job.workflow_version
        or str(payload.get("material_pk") or "") != str(job.material_pk)
        or payload.get("material_id") != job.material_id
        or payload.get("user_id") != job.user_id
    ):
        raise ProjectionValidationError("projection payload differs from its job identity")


def _source_evidence(job: WorkflowV3Job) -> dict[str, Any]:
    payload = job.load(job.payload_json, {})
    evidence = payload.get("source_evidence") if isinstance(payload, dict) else None
    if not isinstance(evidence, dict):
        raise ProjectionValidationError("job has no frozen source evidence")
    return evidence


def _review_asset_evidence(
    source_evidence: Mapping[str, Any],
    job: WorkflowV3Job,
) -> dict[str, Any]:
    value = source_evidence.get("review_asset")
    if not isinstance(value, Mapping):
        raise ProjectionValidationError("job has no frozen ReviewAsset evidence")
    review_asset_id = _positive_int(value.get("id"), "review_asset.id")
    bucket = _required_text(value.get("bucket"), "review_asset.bucket")
    object_name = _safe_relative(value.get("object"), "review_asset.object")
    sha256 = _require_sha256(value.get("sha256"), "review_asset.sha256")
    if (
        bucket != job.source_popo_bucket
        or object_name != job.source_popo_object
        or sha256 != job.source_popo_sha256
    ):
        raise ProjectionValidationError(
            "frozen ReviewAsset differs from the job Popo manifest"
        )
    return {
        "id": review_asset_id,
        "bucket": bucket,
        "object": object_name,
        "sha256": sha256,
    }


def _artifact_ref(value: Mapping[str, Any]) -> ArtifactRef:
    return ArtifactRef(
        bucket=_required_text(value.get("bucket"), "artifact.bucket"),
        object_name=_safe_relative(value.get("object"), "artifact.object"),
        sha256=_require_sha256(value.get("sha256"), "artifact.sha256"),
        size_bytes=_nonnegative_int(value.get("size_bytes"), "artifact.size_bytes"),
    )


def _bound_artifact(
    root: Path,
    value: Any,
    label: str,
    *,
    expected_relative_path: str | None = None,
    allowed_prefix: str | None = None,
    allowed_suffix: str | None = None,
) -> BoundArtifact:
    if not isinstance(value, Mapping):
        raise ProjectionValidationError(f"{label} binding is missing")
    relative = _safe_relative(value.get("path"), f"{label}.path")
    if (
        expected_relative_path is not None
        and relative
        != _safe_relative(expected_relative_path, f"{label}.expected_path")
    ):
        raise ProjectionValidationError(f"{label} path is outside its exact role")
    if allowed_prefix is not None and not relative.startswith(allowed_prefix):
        raise ProjectionValidationError(f"{label} path is outside its role prefix")
    if allowed_suffix is not None and not relative.endswith(allowed_suffix):
        raise ProjectionValidationError(f"{label} path has the wrong role suffix")
    candidate = (root / relative).resolve()
    resolved_root = root.resolve()
    if (
        resolved_root not in candidate.parents
        or candidate.is_symlink()
        or not candidate.is_file()
    ):
        raise ProjectionValidationError(f"{label} file is missing or unsafe")
    expected_sha = _require_sha256(value.get("sha256"), f"{label}.sha256")
    expected_size = _nonnegative_int(value.get("size_bytes"), f"{label}.size_bytes")
    actual_sha = _sha256_file(candidate)
    actual_size = candidate.stat().st_size
    if actual_sha != expected_sha or actual_size != expected_size:
        raise ProjectionValidationError(f"{label} bytes differ from their binding")
    return BoundArtifact(
        path=candidate,
        relative_path=candidate.relative_to(resolved_root).as_posix(),
        sha256=actual_sha,
        size_bytes=actual_size,
    )


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProjectionValidationError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ProjectionValidationError(f"{label} must be a JSON object")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
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


def _safe_component(value: object, field: str) -> str:
    if not isinstance(value, str) or not _COMPONENT_RE.fullmatch(value):
        raise ProjectionValidationError(f"{field} is not a safe object-path component")
    return value


def _safe_relative(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith("/")
        or "\\" in value
    ):
        raise ProjectionValidationError(f"{field} is not a safe relative path")
    parsed = PurePosixPath(value)
    if str(parsed) != value or any(part in {"", ".", ".."} for part in parsed.parts):
        raise ProjectionValidationError(f"{field} is not a normalized relative path")
    return value


def _safe_prefix(value: str) -> str:
    normalized = str(value or "").strip().strip("/")
    _safe_relative(normalized, "formal_prefix")
    return normalized


def _validated_formal_target(
    *,
    bucket: object,
    prefix: object,
    manifest_object: object,
    snapshot: FinalReadySnapshot,
) -> FormalTarget:
    normalized_bucket = _required_text(bucket, "formal_target.bucket")
    normalized_prefix = _safe_relative(prefix, "formal_target.prefix")
    normalized_manifest = _safe_relative(
        manifest_object,
        "formal_target.manifest_object",
    )
    required_tail = (
        _safe_component(snapshot.material_id, "material_id"),
        _safe_component(snapshot.popo_run_id, "popo_run_id"),
        _safe_component(snapshot.workflow_job_id, "workflow_job_id"),
    )
    if (
        tuple(PurePosixPath(normalized_prefix).parts[-3:]) != required_tail
        or normalized_manifest != f"{normalized_prefix}/manifest.json"
    ):
        raise ProjectionValidationError(
            "formal target is not bound to the exact material, Popo run, and job"
        )
    return FormalTarget(
        bucket=normalized_bucket,
        prefix=normalized_prefix,
        manifest_object=normalized_manifest,
    )


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProjectionValidationError(f"{field} is required")
    return value.strip()


def _require_sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ProjectionValidationError(f"{field} must be a lowercase SHA-256")
    return value


def _positive_int(value: object, field: str) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ProjectionValidationError(f"{field} must be a positive integer") from exc
    if normalized <= 0:
        raise ProjectionValidationError(f"{field} must be a positive integer")
    return normalized


def _nonnegative_int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ProjectionValidationError(f"{field} must be a non-negative integer")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assert_ref(expected: ArtifactRef, actual: ArtifactRef) -> None:
    if expected != actual:
        raise ArtifactIntegrityError("artifact differs from its immutable reference")


def _projection_identity(*values: str) -> str:
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()
