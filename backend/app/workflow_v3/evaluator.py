from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Protocol

from sqlalchemy.orm import Session

from app.workflow_v3.contracts import contract_for, contracts_for_version
from app.workflow_v3.executor import (
    ArtifactIntegrityError,
    ArtifactRef,
    BoundRelease,
    CommandTransport,
    EntrypointProtocolError,
    ExternalCommandFailed,
    ReleaseResolver,
    RuntimeBindingGuardProtocol,
    SubprocessTransport,
    WorkerV3RuntimeError,
    _assert_artifact_identity,
    _idempotency_key,
    _read_json_object,
    _sha256_file,
    _write_json,
    select_formal_invocation,
    verify_bound_release,
)
from app.workflow_v3.release import require_qualification_environment
from app.workflow_v3.models import (
    WorkflowV3Candidate,
    WorkflowV3Evaluation,
    WorkflowV3Execution,
    WorkflowV3Job,
    WorkflowV3Promotion,
    WorkflowV3SkillRelease,
    WorkflowV3StageRun,
)
from app.workflow_v3.queue import claim_evaluation_item, claim_promotion_item
from app.workflow_v3.state_machine import (
    WorkflowV3TransitionError,
    assert_operation_attempt,
    finish_operation_attempt,
    promote_candidate,
    record_evaluation,
    recover_stale_operation_attempts,
    touch_operation_heartbeat,
)


EVALUATION_PROTOCOL = "luceon.worker-v3-stage-evaluation/v1"
EVALUATION_REQUEST_PROTOCOL = "luceon.worker-v3-evaluation-request/v1"
CONTROL_PLANE_CHAIN_PROTOCOL = "luceon.worker-v3-control-plane-chain/v1"
CONTROL_PLANE_CHAIN_PATH = "control-plane/promotion-chain.json"
READY_FOR_USER_ACCEPTANCE_STAGE = "ready_for_user_acceptance"
_EVALUATOR_SUCCESS = "evaluation_ready"


class EvaluationRuntimeError(WorkerV3RuntimeError):
    code = "evaluation_runtime_error"


class ReadonlyArtifactStore(Protocol):
    def materialize(self, artifact: ArtifactRef, destination: Path) -> ArtifactRef:
        ...

    def stat(self, artifact: ArtifactRef) -> ArtifactRef:
        ...


class WorkflowV3Evaluator:
    """Run an immutable read-only evaluator and persist its decision.

    This component has no promotion method or artifact-write capability.  The
    injected store is used only for ``stat`` and ``materialize``.
    """

    def __init__(
        self,
        *,
        session_factory,
        release_resolver: ReleaseResolver,
        artifact_store: ReadonlyArtifactStore,
        work_root: str | os.PathLike[str],
        evaluator_identity: str,
        transport: CommandTransport | None = None,
        qualification_mode: bool = False,
        runtime_guard: RuntimeBindingGuardProtocol | None = None,
    ):
        if not evaluator_identity:
            raise ValueError("evaluator_identity is required")
        self.session_factory = session_factory
        self.release_resolver = release_resolver
        self.artifact_store = artifact_store
        self.work_root = Path(work_root).resolve()
        self.evaluator_identity = evaluator_identity
        self.transport = transport or SubprocessTransport()
        if qualification_mode:
            require_qualification_environment()
        self.qualification_mode = qualification_mode
        self.runtime_guard = runtime_guard

    def evaluate(
        self,
        public_id: str,
        candidate_id: int,
        *,
        operation_attempt_id: int | None = None,
        owner_token: str = "",
        lease_seconds: int = 300,
        max_attempts: int = 3,
    ) -> dict:
        if operation_attempt_id is None:
            db: Session = self.session_factory()
            try:
                if self.runtime_guard is not None:
                    job = (
                        db.query(WorkflowV3Job)
                        .filter(WorkflowV3Job.public_id == public_id)
                        .one()
                    )
                    release = (
                        db.query(WorkflowV3SkillRelease)
                        .filter(
                            WorkflowV3SkillRelease.id
                            == job.skill_release_id
                        )
                        .one()
                    )
                    self.runtime_guard.assert_bound(
                        self.release_resolver.resolve(release),
                        job=job,
                        release=release,
                        qualification=self.qualification_mode,
                    )
                recover_stale_operation_attempts(db, operation="evaluation")
                claimed = claim_evaluation_item(
                    db,
                    public_id=public_id,
                    candidate_id=candidate_id,
                    owner_identity=self.evaluator_identity,
                    lease_seconds=lease_seconds,
                    max_attempts=max_attempts,
                )
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()
            operation_attempt_id = claimed.operation_attempt_id
            owner_token = claimed.owner_token
        if operation_attempt_id is None or not owner_token:
            raise EvaluationRuntimeError("evaluation operation claim is required")
        try:
            return self._evaluate_claimed(
                public_id,
                candidate_id,
                operation_attempt_id=operation_attempt_id,
                owner_token=owner_token,
            )
        except Exception as exc:
            self._fail_operation(
                public_id,
                candidate_id,
                operation_attempt_id=operation_attempt_id,
                owner_token=owner_token,
                error_code=getattr(exc, "code", type(exc).__name__),
                error_message=str(exc),
            )
            raise

    def _evaluate_claimed(
        self,
        public_id: str,
        candidate_id: int,
        *,
        operation_attempt_id: int,
        owner_token: str,
    ) -> dict:
        db: Session = self.session_factory()
        try:
            (
                job,
                release,
                stage,
                candidate,
                execution,
            ) = _evaluation_context(db, public_id, candidate_id)
            assert_operation_attempt(
                db,
                public_id,
                operation_attempt_id=operation_attempt_id,
                operation="evaluation",
                target_id=candidate_id,
                owner_identity=self.evaluator_identity,
                owner_token=owner_token,
            )
            if execution.producer_identity == self.evaluator_identity:
                raise EvaluationRuntimeError("producer and evaluator identities must differ")
            if self.runtime_guard is not None:
                bound = self.runtime_guard.assert_bound(
                    self.release_resolver.resolve(release),
                    job=job,
                    release=release,
                    qualification=self.qualification_mode,
                )
            else:
                bound = verify_bound_release(
                    self.release_resolver.resolve(release),
                    job=job,
                    release=release,
                    qualification=self.qualification_mode,
                )
            invocation = select_formal_invocation(
                bound.verification,
                stage_key=stage.stage_key,
                execution_role="evaluator",
                success_semantic=_EVALUATOR_SUCCESS,
                permission_envelope="read-only-evaluator",
                qualification=self.qualification_mode,
            )
            policy_sha256 = evaluation_policy_sha256(
                bound,
                entrypoint_id=invocation.entrypoint_id,
                definition=invocation.definition,
            )
            idempotency_key = _idempotency_key(
                "evaluation",
                public_id,
                str(candidate.id),
                policy_sha256,
            )
            duplicate = (
                db.query(WorkflowV3Evaluation)
                .filter(WorkflowV3Evaluation.idempotency_key == idempotency_key)
                .one_or_none()
            )
            if duplicate:
                return {
                    "ok": True,
                    "job_id": public_id,
                    "candidate_id": str(candidate.id),
                    "evaluation_id": str(duplicate.id),
                    "decision": duplicate.decision,
                    "spec_passed": duplicate.spec_passed,
                    "idempotent": True,
                }
            snapshot = {
                "job_id": public_id,
                "workflow_version": job.workflow_version,
                "stage_id": stage.id,
                "stage_key": stage.stage_key,
                "stage_version": stage.stage_version,
                "attempt": stage.attempt,
                "candidate_id": candidate.id,
                "candidate": ArtifactRef(
                    bucket=candidate.bucket,
                    object_name=candidate.object_name,
                    sha256=candidate.sha256,
                    size_bytes=candidate.size_bytes,
                ),
                "release_version": release.release_version,
                "release_manifest_sha256": bound.manifest_sha256,
                "policy_sha256": policy_sha256,
                "entrypoint_id": invocation.entrypoint_id,
                "evaluator_version": (
                    f"{bound.verification.release_id}:{invocation.entrypoint_id}"
                ),
                "required_gates": list(
                    contract_for(job.workflow_version, stage.stage_key).acceptance_gates
                ),
                "operation_attempt_id": operation_attempt_id,
                "control_plane_chain": (
                    _build_control_plane_chain_snapshot(db, job=job, stage=stage)
                    if stage.stage_key == READY_FOR_USER_ACCEPTANCE_STAGE
                    else None
                ),
            }
        finally:
            db.close()

        workdir = (
            self.work_root
            / public_id
            / snapshot["stage_key"]
            / f"attempt-{snapshot['attempt']}"
            / f"evaluation-operation-{snapshot['operation_attempt_id']}"
        )
        workdir.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            workdir.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise EvaluationRuntimeError(
                "evaluation workspace already exists without a persisted decision"
            ) from exc

        candidate_path = workdir / "candidate" / "artifact"
        materialized = self.artifact_store.materialize(snapshot["candidate"], candidate_path)
        _assert_artifact_identity(snapshot["candidate"], materialized)
        request = {
            "schema_version": EVALUATION_REQUEST_PROTOCOL,
            "mode": "evaluate",
            "job_id": public_id,
            "stage_key": snapshot["stage_key"],
            "stage_version": snapshot["stage_version"],
            "attempt": snapshot["attempt"],
            "candidate": {
                "id": str(candidate_id),
                "path": "candidate/artifact",
                "sha256": materialized.sha256,
                "size_bytes": materialized.size_bytes,
            },
            "release_manifest_sha256": snapshot["release_manifest_sha256"],
            "policy_sha256": snapshot["policy_sha256"],
            "required_gates": snapshot["required_gates"],
            "output_manifest": "evaluation-manifest.json",
        }
        if snapshot["control_plane_chain"] is not None:
            chain_path = workdir / CONTROL_PLANE_CHAIN_PATH
            chain_path.parent.mkdir(parents=True, mode=0o700)
            _write_json(chain_path, snapshot["control_plane_chain"])
            chain_path.chmod(0o444)
            chain_path.parent.chmod(0o555)
            snapshot["control_plane_chain_sha256"] = _sha256_file(chain_path)
            snapshot["control_plane_chain_size_bytes"] = chain_path.stat().st_size
            request["control_plane_chain"] = {
                "path": CONTROL_PLANE_CHAIN_PATH,
                "sha256": snapshot["control_plane_chain_sha256"],
                "size_bytes": snapshot["control_plane_chain_size_bytes"],
            }
        _write_json(workdir / "request.json", request)
        result = self.transport.run(
            invocation.argv,
            cwd=workdir,
            timeout_seconds=invocation.timeout_seconds,
            heartbeat=lambda: self._heartbeat(
                public_id,
                candidate_id,
                operation_attempt_id=operation_attempt_id,
                owner_token=owner_token,
            ),
            cancelled=lambda: self._cancelled(public_id),
        )
        if result.returncode != 0:
            raise ExternalCommandFailed(
                f"formal evaluator exited {result.returncode}: {result.stderr[-1000:]}"
            )
        if snapshot["control_plane_chain"] is not None:
            _assert_control_plane_chain_file(
                workdir / CONTROL_PLANE_CHAIN_PATH,
                expected_sha256=snapshot["control_plane_chain_sha256"],
                expected_size_bytes=snapshot["control_plane_chain_size_bytes"],
            )
        evaluation_payload = _load_evaluation_manifest(
            workdir / "evaluation-manifest.json",
            job_id=public_id,
            stage_key=snapshot["stage_key"],
            attempt=snapshot["attempt"],
            candidate_sha256=materialized.sha256,
            release_manifest_sha256=snapshot["release_manifest_sha256"],
            policy_sha256=snapshot["policy_sha256"],
            required_gates=snapshot["required_gates"],
        )
        db = self.session_factory()
        try:
            if snapshot["control_plane_chain"] is not None:
                current_job, _release, current_stage, _candidate, _execution = (
                    _evaluation_context(db, public_id, candidate_id)
                )
                current_chain = _build_control_plane_chain_snapshot(
                    db,
                    job=current_job,
                    stage=current_stage,
                )
                if _canonical_sha256(current_chain) != _canonical_sha256(
                    snapshot["control_plane_chain"]
                ):
                    raise EvaluationRuntimeError(
                        "control-plane promotion chain drifted during evaluation"
                    )
            _job, _stage, evaluation = record_evaluation(
                db,
                public_id,
                candidate_id=candidate_id,
                idempotency_key=idempotency_key,
                evaluator_identity=self.evaluator_identity,
                evaluator_version=snapshot["evaluator_version"],
                policy_sha256=snapshot["policy_sha256"],
                decision=evaluation_payload["decision"],
                gate_results=evaluation_payload["gate_results"],
                findings=evaluation_payload["findings"],
                operation_attempt_id=operation_attempt_id,
                owner_token=owner_token,
            )
            db.commit()
            return {
                "ok": True,
                "job_id": public_id,
                "candidate_id": str(candidate_id),
                "evaluation_id": str(evaluation.id),
                "decision": evaluation.decision,
                "spec_passed": evaluation.spec_passed,
                "policy_sha256": evaluation.policy_sha256,
                "status": _stage.machine_status,
                "operation_attempt_id": str(operation_attempt_id),
                "workdir": str(workdir),
            }
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _cancelled(self, public_id: str) -> bool:
        db = self.session_factory()
        try:
            return (
                db.query(WorkflowV3Job.machine_status)
                .filter(WorkflowV3Job.public_id == public_id)
                .scalar()
                == "cancelled"
            )
        finally:
            db.close()

    def _heartbeat(
        self,
        public_id: str,
        candidate_id: int,
        *,
        operation_attempt_id: int,
        owner_token: str,
    ) -> None:
        db: Session = self.session_factory()
        try:
            if not touch_operation_heartbeat(
                db,
                public_id,
                operation_attempt_id=operation_attempt_id,
                operation="evaluation",
                target_id=candidate_id,
                owner_identity=self.evaluator_identity,
                owner_token=owner_token,
            ):
                raise EvaluationRuntimeError("evaluation operation lease was lost")
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _fail_operation(
        self,
        public_id: str,
        candidate_id: int,
        *,
        operation_attempt_id: int,
        owner_token: str,
        error_code: str,
        error_message: str,
    ) -> None:
        db: Session = self.session_factory()
        try:
            finish_operation_attempt(
                db,
                public_id,
                operation_attempt_id=operation_attempt_id,
                operation="evaluation",
                target_id=candidate_id,
                owner_identity=self.evaluator_identity,
                owner_token=owner_token,
                status="failed",
                error_code=error_code,
                error_message=error_message[:2000],
                retryable=True,
            )
            db.commit()
        except WorkflowV3TransitionError:
            db.rollback()
        finally:
            db.close()


class WorkflowV3PromotionController:
    """Promote only a release-bound, independently passed candidate."""

    def __init__(
        self,
        *,
        session_factory,
        release_resolver: ReleaseResolver,
        artifact_store: ReadonlyArtifactStore,
        promoter_identity: str,
        qualification_mode: bool = False,
        runtime_guard: RuntimeBindingGuardProtocol | None = None,
    ):
        if not promoter_identity:
            raise ValueError("promoter_identity is required")
        self.session_factory = session_factory
        self.release_resolver = release_resolver
        self.artifact_store = artifact_store
        self.promoter_identity = promoter_identity
        if qualification_mode:
            require_qualification_environment()
        self.qualification_mode = qualification_mode
        self.runtime_guard = runtime_guard

    def promote(
        self,
        public_id: str,
        evaluation_id: int,
        *,
        operation_attempt_id: int | None = None,
        owner_token: str = "",
        lease_seconds: int = 300,
        max_attempts: int = 3,
    ) -> dict:
        if operation_attempt_id is None:
            db: Session = self.session_factory()
            try:
                if self.runtime_guard is not None:
                    job = (
                        db.query(WorkflowV3Job)
                        .filter(WorkflowV3Job.public_id == public_id)
                        .one()
                    )
                    release = (
                        db.query(WorkflowV3SkillRelease)
                        .filter(
                            WorkflowV3SkillRelease.id
                            == job.skill_release_id
                        )
                        .one()
                    )
                    self.runtime_guard.assert_bound(
                        self.release_resolver.resolve(release),
                        job=job,
                        release=release,
                        qualification=self.qualification_mode,
                    )
                recover_stale_operation_attempts(db, operation="promotion")
                claimed = claim_promotion_item(
                    db,
                    public_id=public_id,
                    evaluation_id=evaluation_id,
                    owner_identity=self.promoter_identity,
                    lease_seconds=lease_seconds,
                    max_attempts=max_attempts,
                )
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()
            operation_attempt_id = claimed.operation_attempt_id
            owner_token = claimed.owner_token
        if operation_attempt_id is None or not owner_token:
            raise EvaluationRuntimeError("promotion operation claim is required")
        try:
            return self._promote_claimed(
                public_id,
                evaluation_id,
                operation_attempt_id=operation_attempt_id,
                owner_token=owner_token,
            )
        except Exception as exc:
            self._fail_operation(
                public_id,
                evaluation_id,
                operation_attempt_id=operation_attempt_id,
                owner_token=owner_token,
                error_code=getattr(exc, "code", type(exc).__name__),
                error_message=str(exc),
            )
            raise

    def _promote_claimed(
        self,
        public_id: str,
        evaluation_id: int,
        *,
        operation_attempt_id: int,
        owner_token: str,
    ) -> dict:
        db: Session = self.session_factory()
        try:
            evaluation = (
                db.query(WorkflowV3Evaluation)
                .filter(WorkflowV3Evaluation.id == evaluation_id)
                .one()
            )
            job = db.query(WorkflowV3Job).filter(WorkflowV3Job.id == evaluation.workflow_job_id).one()
            candidate = (
                db.query(WorkflowV3Candidate)
                .filter(WorkflowV3Candidate.id == evaluation.candidate_id)
                .one()
            )
            execution = (
                db.query(WorkflowV3Execution)
                .filter(WorkflowV3Execution.id == candidate.execution_id)
                .one()
            )
            release = (
                db.query(WorkflowV3SkillRelease)
                .filter(WorkflowV3SkillRelease.id == job.skill_release_id)
                .one()
            )
            if self.promoter_identity in {
                execution.producer_identity,
                evaluation.evaluator_identity,
            }:
                raise WorkflowV3TransitionError(
                    "promotion identity must differ from producer and evaluator"
                )
            assert_operation_attempt(
                db,
                public_id,
                operation_attempt_id=operation_attempt_id,
                operation="promotion",
                target_id=evaluation_id,
                owner_identity=self.promoter_identity,
                owner_token=owner_token,
            )
            self._touch_operation(
                db,
                public_id,
                evaluation_id,
                operation_attempt_id=operation_attempt_id,
                owner_token=owner_token,
            )
            if self.runtime_guard is not None:
                self.runtime_guard.assert_bound(
                    self.release_resolver.resolve(release),
                    job=job,
                    release=release,
                    qualification=self.qualification_mode,
                )
            else:
                verify_bound_release(
                    self.release_resolver.resolve(release),
                    job=job,
                    release=release,
                    qualification=self.qualification_mode,
                )
            reference = ArtifactRef(
                bucket=candidate.bucket,
                object_name=candidate.object_name,
                sha256=candidate.sha256,
                size_bytes=candidate.size_bytes,
            )
            actual = self.artifact_store.stat(reference)
            _assert_artifact_identity(reference, actual)
            job, stage, promotion = promote_candidate(
                db,
                public_id,
                evaluation_id=evaluation.id,
                idempotency_key=_idempotency_key(
                    "promotion",
                    public_id,
                    str(evaluation.id),
                    candidate.sha256,
                ),
                promoted_by=self.promoter_identity,
                operation_attempt_id=operation_attempt_id,
                owner_token=owner_token,
            )
            db.commit()
            return {
                "ok": True,
                "job_id": public_id,
                "stage": stage.stage_key,
                "evaluation_id": str(evaluation.id),
                "promotion_id": str(promotion.id),
                "artifact_sha256": promotion.artifact_sha256,
                "job_status": job.machine_status,
                "next_stage": job.current_stage_key,
                "operation_attempt_id": str(operation_attempt_id),
            }
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _touch_operation(
        self,
        db: Session,
        public_id: str,
        evaluation_id: int,
        *,
        operation_attempt_id: int,
        owner_token: str,
    ) -> None:
        if not touch_operation_heartbeat(
            db,
            public_id,
            operation_attempt_id=operation_attempt_id,
            operation="promotion",
            target_id=evaluation_id,
            owner_identity=self.promoter_identity,
            owner_token=owner_token,
        ):
            raise EvaluationRuntimeError("promotion operation lease was lost")

    def _fail_operation(
        self,
        public_id: str,
        evaluation_id: int,
        *,
        operation_attempt_id: int,
        owner_token: str,
        error_code: str,
        error_message: str,
    ) -> None:
        db: Session = self.session_factory()
        try:
            finish_operation_attempt(
                db,
                public_id,
                operation_attempt_id=operation_attempt_id,
                operation="promotion",
                target_id=evaluation_id,
                owner_identity=self.promoter_identity,
                owner_token=owner_token,
                status="failed",
                error_code=error_code,
                error_message=error_message[:2000],
                retryable=True,
            )
            db.commit()
        except WorkflowV3TransitionError:
            db.rollback()
        finally:
            db.close()


def evaluation_policy_sha256(
    release: BoundRelease,
    *,
    entrypoint_id: str,
    definition,
) -> str:
    executable = definition["argv"][0]
    executable_sha256 = _sha256_file(release.verification.root / executable)
    payload = {
        "entrypoint_id": entrypoint_id,
        "definition": definition,
        "executable_sha256": executable_sha256,
        "release_tree_sha256": release.verification.tree_sha256,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _build_control_plane_chain_snapshot(
    db: Session,
    *,
    job: WorkflowV3Job,
    stage: WorkflowV3StageRun,
) -> dict:
    """Build Stage 12 evidence exclusively from durable control-plane rows."""

    contracts = contracts_for_version(job.workflow_version)
    final_index = next(
        (
            index
            for index, contract in enumerate(contracts)
            if contract.key == READY_FOR_USER_ACCEPTANCE_STAGE
        ),
        None,
    )
    if final_index is None or final_index != len(contracts) - 1:
        raise EvaluationRuntimeError(
            "ready_for_user_acceptance must be the final registered stage"
        )
    final_contract = contracts[final_index]
    if (
        stage.workflow_job_id != job.id
        or stage.stage_key != final_contract.key
        or stage.stage_version != final_contract.stage_version
        or job.current_stage_key != final_contract.key
        or stage.generation != job.current_generation
        or stage.machine_status != "awaiting_evaluation"
    ):
        raise EvaluationRuntimeError(
            "Stage 12 is not the active immutable evaluation boundary"
        )
    if not _is_sha256(job.skill_release_sha256) or not _is_sha256(
        job.source_popo_sha256
    ):
        raise EvaluationRuntimeError(
            "job release or frozen Popo identity is not a valid SHA-256"
        )

    promotions: list[dict] = []
    previous_promotion: WorkflowV3Promotion | None = None
    previous_candidate: WorkflowV3Candidate | None = None
    for contract in contracts[:final_index]:
        attempts = (
            db.query(WorkflowV3StageRun)
            .filter(
                WorkflowV3StageRun.workflow_job_id == job.id,
                WorkflowV3StageRun.stage_key == contract.key,
            )
            .order_by(
                WorkflowV3StageRun.generation.desc(),
                WorkflowV3StageRun.attempt.desc(),
            )
            .all()
        )
        promoted_attempts = [row for row in attempts if row.promotion_id is not None]
        if not promoted_attempts:
            raise EvaluationRuntimeError(
                f"control-plane chain requires one promotion for {contract.key}"
            )
        promoted_stage = promoted_attempts[0]
        if any(
            row.generation == promoted_stage.generation
            for row in promoted_attempts[1:]
        ):
            raise EvaluationRuntimeError(
                f"control-plane chain has duplicate promotions in one generation for {contract.key}"
            )
        if (
            promoted_stage.stage_version != contract.stage_version
            or promoted_stage.machine_status != "succeeded"
            or promoted_stage.spec_status != "passed"
            or promoted_stage.generation > job.current_generation
            or promoted_stage.promoted_candidate_id is None
            or not _is_sha256(promoted_stage.promoted_artifact_sha256)
        ):
            raise EvaluationRuntimeError(
                f"promoted stage state drifted for {contract.key}"
            )

        promotion_rows = (
            db.query(WorkflowV3Promotion)
            .filter(WorkflowV3Promotion.stage_run_id == promoted_stage.id)
            .all()
        )
        if (
            len(promotion_rows) != 1
            or promotion_rows[0].id != promoted_stage.promotion_id
        ):
            raise EvaluationRuntimeError(
                f"promotion record drifted for {contract.key}"
            )
        promotion = promotion_rows[0]
        candidate = db.get(WorkflowV3Candidate, promotion.candidate_id)
        evaluation = db.get(WorkflowV3Evaluation, promotion.evaluation_id)
        if candidate is None or evaluation is None:
            raise EvaluationRuntimeError(
                f"candidate or evaluation record is missing for {contract.key}"
            )
        if (
            candidate.workflow_job_id != job.id
            or candidate.stage_run_id != promoted_stage.id
            or candidate.id != promoted_stage.promoted_candidate_id
            or candidate.status != "promoted"
            or candidate.immutable is not True
            or candidate.sha256 != promoted_stage.promoted_artifact_sha256
            or promotion.workflow_job_id != job.id
            or promotion.stage_run_id != promoted_stage.id
            or promotion.candidate_id != candidate.id
            or promotion.artifact_sha256 != candidate.sha256
            or evaluation.workflow_job_id != job.id
            or evaluation.stage_run_id != promoted_stage.id
            or evaluation.candidate_id != candidate.id
            or evaluation.id != promotion.evaluation_id
            or evaluation.decision != "passed"
            or evaluation.spec_passed is not True
            or candidate.generation != promoted_stage.generation
            or evaluation.generation != promoted_stage.generation
            or candidate.review_resolution_sha256
            != promoted_stage.review_resolution_sha256
            or evaluation.review_resolution_sha256
            != promoted_stage.review_resolution_sha256
        ):
            raise EvaluationRuntimeError(
                f"candidate/evaluation/promotion lineage drifted for {contract.key}"
            )
        expected_object_identity = hashlib.sha256(
            f"{candidate.bucket}\n{candidate.object_name}\n{candidate.sha256}".encode(
                "utf-8"
            )
        ).hexdigest()
        if (
            not _is_sha256(candidate.sha256)
            or candidate.object_identity_hash != expected_object_identity
            or candidate.size_bytes < 0
            or not candidate.artifact_kind
            or not candidate.bucket
            or not candidate.object_name
            or not _is_sha256(evaluation.policy_sha256)
            or not evaluation.evaluator_identity
            or not evaluation.evaluator_version
            or not promotion.promoted_by
        ):
            raise EvaluationRuntimeError(
                f"immutable control-plane identity drifted for {contract.key}"
            )

        gate_results = _control_plane_json(
            evaluation.gate_results_json,
            label=f"{contract.key} gate results",
        )
        findings = _control_plane_json(
            evaluation.findings_json,
            label=f"{contract.key} findings",
        )
        if (
            not isinstance(gate_results, dict)
            or set(gate_results) != set(contract.acceptance_gates)
            or any(value is not True for value in gate_results.values())
            or not isinstance(findings, list)
            or any(not isinstance(row, dict) for row in findings)
        ):
            raise EvaluationRuntimeError(
                f"passed evaluation evidence drifted for {contract.key}"
            )

        if previous_promotion is None:
            expected_input_kind = "frozen_source"
            expected_input_promotion_id = None
            expected_input_sha256 = job.source_popo_sha256
        else:
            expected_input_kind = "promoted_artifact"
            expected_input_promotion_id = previous_promotion.id
            expected_input_sha256 = previous_candidate.sha256
        if (
            promoted_stage.input_kind != expected_input_kind
            or promoted_stage.input_promotion_id != expected_input_promotion_id
            or promoted_stage.input_artifact_sha256 != expected_input_sha256
        ):
            raise EvaluationRuntimeError(
                f"upstream promotion link drifted for {contract.key}"
            )

        artifact_version = _with_record_sha256(
            {
                "candidate_id": str(candidate.id),
                "kind": candidate.artifact_kind,
                "bucket": candidate.bucket,
                "object": candidate.object_name,
                "object_identity_sha256": candidate.object_identity_hash,
                "artifact_sha256": candidate.sha256,
                "size_bytes": candidate.size_bytes,
                "immutable": True,
                "status": "promoted",
            }
        )
        evaluation_record = _with_record_sha256(
            {
                "evaluation_id": str(evaluation.id),
                "candidate_id": str(candidate.id),
                "decision": "passed",
                "spec_passed": True,
                "policy_sha256": evaluation.policy_sha256,
                "evaluator_identity": evaluation.evaluator_identity,
                "evaluator_version": evaluation.evaluator_version,
                "gate_results": gate_results,
                "findings": findings,
            }
        )
        promotion_record = _with_record_sha256(
            {
                "promotion_id": str(promotion.id),
                "candidate_id": str(candidate.id),
                "evaluation_id": str(evaluation.id),
                "artifact_sha256": promotion.artifact_sha256,
                "promoted_by": promotion.promoted_by,
            }
        )
        chain_row = _with_record_sha256(
            {
                "order": contract.order,
                "stage_key": contract.key,
                "stage_version": promoted_stage.stage_version,
                "stage_run_id": str(promoted_stage.id),
                "stage_attempt": promoted_stage.attempt,
                "stage_machine_status": promoted_stage.machine_status,
                "stage_spec_status": promoted_stage.spec_status,
                "input": {
                    "kind": promoted_stage.input_kind,
                    "promotion_id": (
                        str(promoted_stage.input_promotion_id)
                        if promoted_stage.input_promotion_id is not None
                        else None
                    ),
                    "artifact_sha256": promoted_stage.input_artifact_sha256,
                },
                "artifact_version": artifact_version,
                "evaluation": evaluation_record,
                "promotion": promotion_record,
            }
        )
        promotions.append(chain_row)
        previous_promotion = promotion
        previous_candidate = candidate

    if previous_promotion is None or previous_candidate is None:
        raise EvaluationRuntimeError("control-plane promotion chain is empty")
    if (
        stage.input_kind != "promoted_artifact"
        or stage.input_promotion_id != previous_promotion.id
        or stage.input_artifact_sha256 != previous_candidate.sha256
    ):
        raise EvaluationRuntimeError(
            "Stage 12 input is not bound to the final prior promotion"
        )
    return {
        "schema_version": CONTROL_PLANE_CHAIN_PROTOCOL,
        "job_id": job.public_id,
        "workflow_version": job.workflow_version,
        "stage_key": stage.stage_key,
        "stage_version": stage.stage_version,
        "stage_run_id": str(stage.id),
        "stage_attempt": stage.attempt,
        "release_manifest_sha256": job.skill_release_sha256,
        "source_popo_manifest_sha256": job.source_popo_sha256,
        "promotions": promotions,
    }


def _control_plane_json(value: str, *, label: str):
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise EvaluationRuntimeError(f"{label} is not valid JSON") from exc


def _with_record_sha256(payload: dict) -> dict:
    return {**payload, "record_sha256": _canonical_sha256(payload)}


def _canonical_sha256(payload) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _is_sha256(value) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _assert_control_plane_chain_file(
    path: Path,
    *,
    expected_sha256: str,
    expected_size_bytes: int,
) -> None:
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_size != expected_size_bytes
        or _sha256_file(path) != expected_sha256
    ):
        raise ArtifactIntegrityError(
            "control-plane promotion chain changed during evaluation"
        )


def _evaluation_context(db: Session, public_id: str, candidate_id: int):
    job = db.query(WorkflowV3Job).filter(WorkflowV3Job.public_id == public_id).one()
    candidate = (
        db.query(WorkflowV3Candidate)
        .filter(WorkflowV3Candidate.id == candidate_id)
        .one()
    )
    if candidate.workflow_job_id != job.id or candidate.status != "candidate":
        raise EvaluationRuntimeError("candidate is not awaiting evaluation for this job")
    stage = (
        db.query(WorkflowV3StageRun)
        .filter(WorkflowV3StageRun.id == candidate.stage_run_id)
        .one()
    )
    if stage.stage_key != job.current_stage_key or stage.machine_status != "awaiting_evaluation":
        raise EvaluationRuntimeError("candidate stage is not the active evaluation stage")
    execution = (
        db.query(WorkflowV3Execution)
        .filter(WorkflowV3Execution.id == candidate.execution_id)
        .one()
    )
    release = (
        db.query(WorkflowV3SkillRelease)
        .filter(WorkflowV3SkillRelease.id == job.skill_release_id)
        .one()
    )
    return job, release, stage, candidate, execution


def _load_evaluation_manifest(
    path: Path,
    *,
    job_id: str,
    stage_key: str,
    attempt: int,
    candidate_sha256: str,
    release_manifest_sha256: str,
    policy_sha256: str,
    required_gates: list[str],
) -> dict:
    payload = _read_json_object(path, "evaluation manifest")
    required = {
        "schema_version",
        "job_id",
        "stage_key",
        "attempt",
        "candidate_sha256",
        "release_manifest_sha256",
        "policy_sha256",
        "decision",
        "gate_results",
        "findings",
    }
    if set(payload) != required:
        raise EntrypointProtocolError("evaluation manifest has missing or unknown fields")
    bindings = {
        "schema_version": EVALUATION_PROTOCOL,
        "job_id": job_id,
        "stage_key": stage_key,
        "attempt": attempt,
        "candidate_sha256": candidate_sha256,
        "release_manifest_sha256": release_manifest_sha256,
        "policy_sha256": policy_sha256,
    }
    if any(payload.get(key) != value for key, value in bindings.items()):
        raise EntrypointProtocolError("evaluation manifest is not bound to this candidate")
    if payload["decision"] not in {"passed", "needs_review", "failed"}:
        raise EntrypointProtocolError(
            "evaluation decision must be passed, needs_review, or failed"
        )
    gate_results = payload["gate_results"]
    if not isinstance(gate_results, dict) or set(gate_results) != set(required_gates):
        raise EntrypointProtocolError("evaluation gates do not exactly match the stage contract")
    if any(type(value) is not bool for value in gate_results.values()):
        raise EntrypointProtocolError("evaluation gate results must be boolean")
    if payload["decision"] == "passed" and not all(gate_results.values()):
        raise EntrypointProtocolError("a passed evaluation contains a failed gate")
    if payload["decision"] in {"needs_review", "failed"} and all(
        gate_results.values()
    ):
        raise EntrypointProtocolError(
            f"a {payload['decision']} evaluation contains no failed gate"
        )
    findings = payload["findings"]
    if not isinstance(findings, list) or any(not isinstance(row, dict) for row in findings):
        raise EntrypointProtocolError("evaluation findings must be an array of objects")
    if payload["decision"] == "needs_review" and not findings:
        raise EntrypointProtocolError(
            "needs_review evaluation requires evidence-bound findings"
        )
    return payload
