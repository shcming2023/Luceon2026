from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.workflow_v3.contracts import (
    STAGE_CONTRACTS,
    WORKFLOW_VERSION,
)
from app.workflow_v3.database import bootstrap_workflow_v3_database
from app.workflow_v3.evaluator import (
    WorkflowV3Evaluator,
    WorkflowV3PromotionController,
)
from app.workflow_v3.executor import (
    CommandTransport,
    DirectoryArtifactStore,
    DirectoryReleaseResolver,
    SubprocessTransport,
    WorkflowV3Executor,
)
from app.workflow_v3.llm_gateway import (
    LlmGatewayError,
    LlmTransportResponse,
    canonical_json_bytes,
    sha256_json,
)
from app.workflow_v3.models import (
    WorkflowV3Candidate,
    WorkflowV3Evaluation,
    WorkflowV3Execution,
    WorkflowV3Job,
    WorkflowV3ModelCall,
    WorkflowV3Promotion,
    WorkflowV3ReviewResolution,
    WorkflowV3StageRun,
)
from app.workflow_v3.review_resolution import (
    evaluation_fingerprint,
    finding_fingerprint,
)
from app.workflow_v3.release import (
    MANIFEST_NAME,
    QualificationArchiveVerification,
    ReleaseValidationError,
    ReleaseVerification,
    materialize_qualification_release_archive,
    require_qualification_environment,
    verify_qualification_release_archive,
    verify_release_directory,
)
from app.workflow_v3.service import (
    create_workflow_job,
    register_skill_release,
    runtime_identity_for_manifest,
)
from app.workflow_v3.state_machine import apply_review_resolution


QUALIFICATION_REPORT_PROTOCOL = "luceon.worker-v3-qualification-report/v1"
QUALIFICATION_FIXTURE_PROTOCOL = "luceon.worker-v3-llm-fixtures/v1"
SPEC05_WARNING_REVIEW_PROTOCOL = "spec05-warning-review/1.0"
QUALIFICATION_STOP_STAGES = (
    "deterministic_elegantbook",
    "ready_for_user_acceptance",
)
_SOURCE_ROLES = (
    "source_pdf",
    "mineru_manifest",
    "mineru_frozen_marker",
    "mineru_archive",
    "frozen_source",
    "popo_frozen_marker",
    "popo_archive",
)
_SOURCE_BINDINGS = {
    "source_pdf": "source_pdf",
    "mineru_manifest": "mineru_manifest",
    "mineru_frozen_marker": "mineru_frozen_marker",
    "frozen_source": "popo_manifest",
    "popo_frozen_marker": "popo_frozen_marker",
}
_SHA256_CHARS = frozenset("0123456789abcdef")
_QUALIFICATION_JOB_NAMESPACE = uuid.UUID(
    "435bd0a5-70ee-57c6-90c4-01e4f3f071f4"
)


class QualificationError(RuntimeError):
    code = "qualification_failed"


@dataclass(frozen=True)
class QualificationConfig:
    release_root: Path | None
    source_package_root: Path
    source_evidence_json: Path
    run_root: Path
    stop_after: str = "deterministic_elegantbook"
    fixture_responses_json: Path | None = None
    release_archive: Path | None = None
    release_archive_sha256: str = ""
    spec05_warning_review_json: Path | None = None


@dataclass(frozen=True)
class QualificationResult:
    report_path: Path
    report_sha256_path: Path
    payload_sha256: str
    report_sha256: str
    job_id: str
    stop_after: str
    passed: bool


class FixtureReplayTransport:
    """Replay exact, hash-bound LLM responses without any network access."""

    def __init__(self, fixture_path: Path) -> None:
        self.path = _readonly_file(fixture_path, "fixture response bundle")
        value = _read_json(self.path, "fixture response bundle")
        rows = value.get("responses")
        if (
            value.get("schema_version") != QUALIFICATION_FIXTURE_PROTOCOL
            or not isinstance(rows, list)
        ):
            raise QualificationError("fixture response bundle is malformed")
        self._responses: dict[str, dict[str, Any]] = {}
        for index, raw in enumerate(rows):
            if not isinstance(raw, dict) or set(raw) != {
                "request_sha256",
                "provider",
                "model",
                "response_id",
                "parsed_result",
                "raw_response",
                "usage",
            }:
                raise QualificationError(
                    f"fixture response {index} fields are not exact"
                )
            request_sha = _sha256(
                raw.get("request_sha256"),
                f"fixture response {index} request_sha256",
            )
            if request_sha in self._responses:
                raise QualificationError(
                    "fixture response request hashes must be unique"
                )
            if (
                not isinstance(raw.get("provider"), str)
                or not raw["provider"]
                or not isinstance(raw.get("model"), str)
                or not raw["model"]
                or not isinstance(raw.get("response_id"), str)
                or not raw["response_id"]
                or not isinstance(raw.get("parsed_result"), dict)
                or not isinstance(raw.get("raw_response"), dict)
                or not _valid_usage(raw.get("usage"))
            ):
                raise QualificationError(
                    f"fixture response {index} is incomplete"
                )
            self._responses[request_sha] = dict(raw)
        self.observed_request_sha256: list[str] = []

    def __call__(
        self,
        request: Mapping[str, Any],
        timeout_seconds: float,
    ) -> LlmTransportResponse:
        del timeout_seconds
        request_sha = sha256_json(request)
        row = self._responses.get(request_sha)
        if row is None:
            raise LlmGatewayError(
                "qualification_fixture_missing",
                "qualification has no exact response for the release-bound request",
            )
        if (
            row["provider"] != request.get("provider")
            or row["model"] != request.get("model")
        ):
            raise LlmGatewayError(
                "provider_binding_mismatch",
                "qualification fixture provider/model differs from the request",
            )
        if request_sha in self.observed_request_sha256:
            raise LlmGatewayError(
                "qualification_fixture_reused",
                "one qualification fixture response can be consumed only once",
            )
        self.observed_request_sha256.append(request_sha)
        return LlmTransportResponse(
            status_code=200,
            provider=row["provider"],
            model=row["model"],
            response_id=row["response_id"],
            content=canonical_json_bytes(row["parsed_result"]).decode("utf-8"),
            usage=dict(row["usage"]),
            raw_response=dict(row["raw_response"]),
        )

    def report(self) -> dict[str, Any]:
        observed = list(self.observed_request_sha256)
        return {
            "fixture_sha256": _sha256_file(self.path),
            "declared_responses": len(self._responses),
            "observed_calls": len(observed),
            "observed_request_sha256": observed,
            "unused_request_sha256": sorted(
                set(self._responses) - set(observed)
            ),
            "network_used": False,
        }


def run_qualification(
    config: QualificationConfig,
    *,
    command_transport: CommandTransport | None = None,
) -> QualificationResult:
    """Run an incomplete release only inside a fresh isolated control plane."""

    try:
        require_qualification_environment()
        prepared = _preflight(config)
    except ReleaseValidationError as exc:
        raise QualificationError(str(exc)) from exc
    run_root = prepared["run_root"]
    run_root.mkdir(mode=0o700)
    database_path = run_root / "qualification.sqlite3"
    artifact_root = run_root / "artifacts"
    work_root = run_root / "work"
    report_path = run_root / "qualification-report.json"
    report_sha_path = run_root / "qualification-report.json.sha256"
    work_root.mkdir(mode=0o700)

    verification: ReleaseVerification | None = prepared["verification"]
    release_manifest: dict[str, Any] = prepared["release_manifest"]
    source_evidence: dict[str, Any] = prepared["source_evidence"]
    source_snapshot: dict[str, Any] = prepared["source_snapshot"]
    replay: FixtureReplayTransport | None = prepared["fixture_replay"]
    model_transport = replay
    manifest_policy = release_manifest.get("model_policy")
    if (
        model_transport is None
        and manifest_policy != {"mode": "none"}
    ):
        raise QualificationError(
            "qualification forbids external model fallback; "
            "provide an exact fixture response bundle"
        )

    report_payload: dict[str, Any] = {
        "qualification": {
            "environment": "qualification",
            "database_backend": "sqlite",
            "artifact_backend": "directory",
            "ordinary_api_enabled": False,
            "ordinary_worker_enabled": False,
            "external_model_calls_allowed": False,
            "run_root": str(run_root),
            "database_path": str(database_path),
            "artifact_root": str(artifact_root),
            "work_root": str(work_root),
        },
        "release": {
            "source_kind": prepared["release_source_kind"],
            "source_path": str(prepared["release_source_path"]),
            "archive_sha256": prepared["release_archive_sha256"],
            "materialized_root": str(prepared["materialized_release_root"]),
            "release_id": prepared["release_id"],
            "version": release_manifest["version"],
            "status": release_manifest["status"],
            "manifest_sha256": prepared["manifest_sha256"],
            "tree_sha256": prepared["tree_sha256"],
            "materialized_tree_sha256": "",
            "template_sha256": release_manifest["template"][
                "tree_sha256"
            ],
            "runtime_identity_sha256": runtime_identity_for_manifest(
                release_manifest
            ),
        },
        "source": source_snapshot,
        "stop_after": config.stop_after,
        "stages": [],
        "review_resolutions": [],
        "model_calls": [],
        "fixture_transport": None,
        "outcome": {
            "passed": False,
            "stop_condition_reached": False,
            "release_promoted": False,
            "production_state_written": False,
        },
    }
    job_id = ""
    engine = None
    factory = None
    error: Exception | None = None
    producer_work_root_env = os.environ.get("WORKFLOW_V3_PRODUCER_WORK_ROOT")
    os.environ["WORKFLOW_V3_PRODUCER_WORK_ROOT"] = str(work_root / "producer")
    try:
        archive_verification: QualificationArchiveVerification | None = (
            prepared["archive_verification"]
        )
        if verification is None:
            if archive_verification is None:
                raise QualificationError(
                    "qualification release source was not prepared"
                )
            verification = materialize_qualification_release_archive(
                archive_verification,
                prepared["materialized_release_root"],
                run_root=run_root,
            )
        report_payload["release"]["materialized_tree_sha256"] = (
            verification.tree_sha256
        )
        engine = create_engine(
            f"sqlite+pysqlite:///{database_path}",
            connect_args={"check_same_thread": False},
        )
        bootstrap = bootstrap_workflow_v3_database(engine)
        if bootstrap.get("ready") is not True:
            raise QualificationError(
                f"isolated qualification database bootstrap failed: {bootstrap}"
            )
        factory = sessionmaker(
            bind=engine,
            expire_on_commit=False,
            autoflush=False,
            autocommit=False,
        )
        store = DirectoryArtifactStore(artifact_root)
        _seed_frozen_source_package(
            store,
            package_root=prepared["source_package_root"],
            source_evidence=source_evidence,
        )
        manifest_sha = prepared["manifest_sha256"]
        runtime_sha = runtime_identity_for_manifest(verification.manifest)
        db: Session = factory()
        try:
            release, _ = register_skill_release(
                db,
                release_version=release_manifest["version"],
                manifest_sha256=manifest_sha,
                package_bucket="qualification-release",
                package_object=prepared["package_object"],
                package_sha256=prepared["package_sha256"],
                workflow_version=WORKFLOW_VERSION,
                template_sha256=release_manifest["template"][
                    "tree_sha256"
                ],
                runtime_identity_sha256=runtime_sha,
                manifest=release_manifest,
                registered_by="qualification-cli",
                qualification=True,
            )
            frozen_source = _artifact_by_role(
                source_evidence,
                "frozen_source",
            )
            job, _ = create_workflow_job(
                db,
                user_id="qualification-cli",
                material_pk=1,
                material_id=str(source_evidence["material_id"]),
                source_popo_bucket=str(frozen_source["bucket"]),
                source_popo_object=str(frozen_source["object"]),
                source_popo_sha256=str(frozen_source["sha256"]),
                skill_release_version=release.release_version,
                skill_release_sha256=release.manifest_sha256,
                template_sha256=release.template_sha256,
                workflow_version=WORKFLOW_VERSION,
                payload={
                    "source_evidence": source_evidence,
                    "submission_path": "qualification_cli",
                    "qualification": {
                        "enabled": True,
                        "source_evidence_sha256": source_snapshot[
                            "source_evidence_sha256"
                        ],
                        "source_input_set_sha256": source_evidence[
                            "input_set_sha256"
                        ],
                    },
                },
                qualification=True,
            )
            job.public_id = _qualification_job_public_id(
                manifest_sha256=manifest_sha,
                source_input_set_sha256=str(
                    source_evidence["input_set_sha256"]
                ),
            )
            db.commit()
            job_id = job.public_id
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

        resolver = DirectoryReleaseResolver(verification.root)
        transport = command_transport or SubprocessTransport(
            poll_seconds=0.05,
            heartbeat_seconds=1.0,
        )
        executor = WorkflowV3Executor(
            session_factory=factory,
            release_resolver=resolver,
            artifact_store=store,
            work_root=work_root / "producer",
            producer_identity="qualification-producer",
            candidate_bucket="qualification-candidates",
            candidate_prefix="qualification/candidates",
            transport=transport,
            model_transport=model_transport,
            qualification_mode=True,
        )
        evaluator = WorkflowV3Evaluator(
            session_factory=factory,
            release_resolver=resolver,
            artifact_store=store,
            work_root=work_root / "evaluator",
            evaluator_identity="qualification-evaluator",
            transport=transport,
            qualification_mode=True,
        )
        promoter = WorkflowV3PromotionController(
            session_factory=factory,
            release_resolver=resolver,
            artifact_store=store,
            promoter_identity="qualification-promoter",
            qualification_mode=True,
        )
        warning_review = prepared["spec05_warning_review"]
        warning_review_used = False
        terminal_needs_review_stage = ""
        target_index = _stage_index(config.stop_after)
        for contract in STAGE_CONTRACTS[: target_index + 1]:
            produced = executor.run_one_stage(job_id)
            if produced.get("ok") is not True or not produced.get(
                "candidate_id"
            ):
                raise QualificationError(
                    f"{contract.key} producer failed: {produced}"
                )
            evaluated = evaluator.evaluate(
                job_id,
                int(produced["candidate_id"]),
            )
            if (
                contract.key == "deterministic_elegantbook"
                and evaluated.get("ok") is True
                and evaluated.get("decision") == "needs_review"
                and warning_review is not None
                and not warning_review_used
            ):
                resolution = _apply_qualification_warning_review(
                    factory,
                    store,
                    job_id=job_id,
                    evaluation_id=int(evaluated["evaluation_id"]),
                    warning_review=warning_review,
                    run_root=run_root,
                )
                report_payload["review_resolutions"].append(resolution)
                warning_review_used = True
                produced = executor.run_one_stage(job_id)
                if produced.get("ok") is not True or not produced.get(
                    "candidate_id"
                ):
                    raise QualificationError(
                        f"{contract.key} recovery producer failed: {produced}"
                    )
                evaluated = evaluator.evaluate(
                    job_id,
                    int(produced["candidate_id"]),
                )
            if (
                evaluated.get("ok") is True
                and evaluated.get("decision") == "needs_review"
                and evaluated.get("spec_passed") is False
                and not (
                    contract.key == "deterministic_elegantbook"
                    and warning_review_used
                )
            ):
                report_payload["stages"].append(
                    _stage_report(factory, job_id, contract.key)
                )
                terminal_needs_review_stage = contract.key
                break
            if (
                evaluated.get("ok") is not True
                or evaluated.get("decision") != "passed"
                or evaluated.get("spec_passed") is not True
            ):
                raise QualificationError(
                    f"{contract.key} evaluator did not pass: {evaluated}"
                )
            promoted = promoter.promote(
                job_id,
                int(evaluated["evaluation_id"]),
            )
            if promoted.get("ok") is not True:
                raise QualificationError(
                    f"{contract.key} promotion failed: {promoted}"
                )
            report_payload["stages"].append(
                _stage_report(factory, job_id, contract.key)
            )

        if warning_review is not None and not warning_review_used:
            raise QualificationError(
                "qualification Spec 05 warning review was not consumed"
            )

        _assert_source_unchanged(
            prepared["source_package_root"],
            source_snapshot["artifacts"],
        )
        final_release = verify_release_directory(
            verification.root,
            allow_incomplete=True,
        )
        if (
            final_release.tree_sha256 != verification.tree_sha256
            or _sha256_file(final_release.root / MANIFEST_NAME)
            != prepared["manifest_sha256"]
        ):
            raise QualificationError(
                "incomplete release changed during qualification"
            )
        report_payload["model_calls"] = _model_call_report(
            factory,
            job_id,
        )
        _assert_model_call_costs(report_payload["model_calls"])
        if replay is not None:
            fixture_report = replay.report()
            report_payload["fixture_transport"] = fixture_report
            if fixture_report["unused_request_sha256"]:
                raise QualificationError(
                    "qualification fixture bundle contains unused responses"
                )
        job_report = _job_report(factory, job_id)
        report_payload["outcome"].update(
            {
                "passed": True,
                "stop_condition_reached": True,
                "qualification_disposition": (
                    "evidence_closed_needs_review"
                    if terminal_needs_review_stage
                    else "passed"
                ),
                "actual_stop_stage": (
                    terminal_needs_review_stage or config.stop_after
                ),
                "machine_succeeded": (
                    job_report["machine_status"] == "succeeded"
                ),
                "job": job_report,
            }
        )
    except Exception as exc:
        error = exc
        if factory is not None and job_id:
            report_payload["model_calls"] = _model_call_report(
                factory,
                job_id,
            )
            report_payload["outcome"]["job"] = _job_report(
                factory,
                job_id,
            )
        if replay is not None:
            report_payload["fixture_transport"] = replay.report()
        report_payload["outcome"]["error"] = {
            "code": getattr(exc, "code", type(exc).__name__),
            "message": str(exc)[:4000],
        }
    finally:
        if engine is not None:
            engine.dispose()
        if producer_work_root_env is None:
            os.environ.pop("WORKFLOW_V3_PRODUCER_WORK_ROOT", None)
        else:
            os.environ["WORKFLOW_V3_PRODUCER_WORK_ROOT"] = producer_work_root_env

    envelope = {
        "schema_version": QUALIFICATION_REPORT_PROTOCOL,
        "payload": report_payload,
        "payload_sha256": sha256_json(report_payload),
    }
    _write_json(report_path, envelope)
    report_sha = _sha256_file(report_path)
    report_sha_path.write_text(f"{report_sha}  {report_path.name}\n")
    report_path.chmod(0o444)
    report_sha_path.chmod(0o444)
    result = QualificationResult(
        report_path=report_path,
        report_sha256_path=report_sha_path,
        payload_sha256=envelope["payload_sha256"],
        report_sha256=report_sha,
        job_id=job_id,
        stop_after=config.stop_after,
        passed=error is None,
    )
    if error is not None:
        raise QualificationError(
            f"{error}; report={report_path}; sha256={report_sha}"
        ) from error
    return result


def _preflight(config: QualificationConfig) -> dict[str, Any]:
    if config.stop_after not in QUALIFICATION_STOP_STAGES:
        raise QualificationError(
            "stop_after must be deterministic_elegantbook or "
            "ready_for_user_acceptance"
        )
    configured_db = os.getenv("WORKFLOW_V3_DATABASE_URL", "").strip()
    if configured_db:
        raise QualificationError(
            "qualification CLI refuses an inherited WORKFLOW_V3_DATABASE_URL"
        )
    run_root = _fresh_path(config.run_root, "qualification run root")
    if (config.release_root is None) == (config.release_archive is None):
        raise QualificationError(
            "provide exactly one release root or release archive"
        )
    release_root = None
    archive_verification = None
    if config.release_root is not None:
        if config.release_archive_sha256:
            raise QualificationError(
                "release_archive_sha256 is only valid with a release archive"
            )
        release_root = _readonly_directory(
            config.release_root,
            "release root",
        )
        verification: ReleaseVerification | None = (
            verify_release_directory(
                release_root,
                allow_incomplete=True,
            )
        )
        release_manifest = verification.manifest
        manifest_sha256 = _sha256_file(release_root / MANIFEST_NAME)
        release_source_kind = "directory"
        release_source_path = release_root
        release_archive_sha256 = None
        materialized_release_root = release_root
        package_sha256 = verification.tree_sha256
        package_object = (
            f"{verification.release_id}/qualification-directory"
        )
    else:
        archive_path = _readonly_file(
            config.release_archive,
            "release archive",
        )
        expected_archive_sha256 = _sha256(
            config.release_archive_sha256,
            "release archive SHA-256",
        )
        archive_verification = verify_qualification_release_archive(
            archive_path,
            expected_archive_sha256=expected_archive_sha256,
        )
        verification = None
        release_manifest = archive_verification.manifest
        manifest_sha256 = archive_verification.manifest_sha256
        release_source_kind = "archive"
        release_source_path = archive_verification.archive_path
        release_archive_sha256 = archive_verification.archive_sha256
        materialized_release_root = run_root / "release"
        package_sha256 = archive_verification.archive_sha256
        package_object = (
            f"{archive_verification.release_id}/qualification-archive"
        )
    source_package_root = _readonly_directory(
        config.source_package_root,
        "source evidence package",
    )
    source_json = _readonly_file(
        config.source_evidence_json,
        "source_evidence JSON",
    )
    spec05_warning_review_path = None
    spec05_warning_review = None
    if config.spec05_warning_review_json is not None:
        spec05_warning_review_path = _readonly_file(
            config.spec05_warning_review_json,
            "Spec 05 warning review JSON",
        )
        spec05_warning_review = _validate_spec05_warning_review(
            spec05_warning_review_path
        )
    for protected in (
        release_source_path,
        source_package_root,
        source_json,
        *(
            (spec05_warning_review_path,)
            if spec05_warning_review_path is not None
            else ()
        ),
    ):
        if _overlaps(run_root, protected):
            raise QualificationError(
                "qualification run root overlaps immutable input evidence"
            )
    if release_manifest.get("status") != "incomplete":
        raise QualificationError(
            "qualification harness only accepts an incomplete release"
        )
    eligibility = release_manifest.get("eligibility")
    if (
        not isinstance(eligibility, dict)
        or eligibility.get("rc_eligible") is not False
        or eligibility.get("stable_eligible") is not False
    ):
        raise QualificationError(
            "incomplete qualification release cannot claim eligibility"
        )
    source_evidence = _validate_source_evidence(
        source_json,
        source_package_root,
    )
    fixture_path = None
    fixture_replay = None
    if config.fixture_responses_json is not None:
        fixture_path = _readonly_file(
            config.fixture_responses_json,
            "fixture response bundle",
        )
        if _overlaps(run_root, fixture_path):
            raise QualificationError(
                "qualification run root overlaps fixture evidence"
            )
        fixture_replay = FixtureReplayTransport(fixture_path)
    return {
        "run_root": run_root,
        "verification": verification,
        "archive_verification": archive_verification,
        "release_manifest": release_manifest,
        "release_id": (
            verification.release_id
            if verification is not None
            else archive_verification.release_id
        ),
        "tree_sha256": (
            verification.tree_sha256
            if verification is not None
            else archive_verification.tree_sha256
        ),
        "manifest_sha256": manifest_sha256,
        "release_source_kind": release_source_kind,
        "release_source_path": release_source_path,
        "release_archive_sha256": release_archive_sha256,
        "materialized_release_root": materialized_release_root,
        "package_sha256": package_sha256,
        "package_object": package_object,
        "source_package_root": source_package_root,
        "source_evidence": source_evidence,
        "source_snapshot": _source_snapshot(
            source_json,
            source_package_root,
            source_evidence,
        ),
        "fixture_path": fixture_path,
        "fixture_replay": fixture_replay,
        "spec05_warning_review_path": spec05_warning_review_path,
        "spec05_warning_review": spec05_warning_review,
    }


def _validate_source_evidence(
    source_json: Path,
    package_root: Path,
) -> dict[str, Any]:
    value = _read_json(source_json, "source_evidence JSON")
    expected_keys = {
        "popo_manifest",
        "popo_frozen_marker",
        "material_id",
        "run_id",
        "stage_run_ids",
        "source_pdf",
        "mineru_manifest",
        "mineru_frozen_marker",
        "artifacts",
        "verified_at",
        "input_set_sha256",
        "review_asset",
    }
    artifacts = value.get("artifacts")
    if (
        set(value) != expected_keys
        or not isinstance(value.get("material_id"), str)
        or not value["material_id"]
        or not isinstance(value.get("run_id"), str)
        or not value["run_id"]
        or not isinstance(value.get("verified_at"), str)
        or not value["verified_at"]
        or not isinstance(artifacts, list)
        or len(artifacts) != len(_SOURCE_ROLES)
        or [row.get("role") for row in artifacts if isinstance(row, dict)]
        != list(_SOURCE_ROLES)
    ):
        raise QualificationError(
            "source_evidence JSON is not the exact seven-artifact contract"
        )
    seen_objects: set[tuple[str, str]] = set()
    for index, row in enumerate(artifacts):
        if not isinstance(row, dict) or set(row) != {
            "role",
            "kind",
            "bucket",
            "object",
            "sha256",
            "size_bytes",
            "read_only",
        }:
            raise QualificationError(
                f"source artifact {index} fields are not exact"
            )
        _sha256(row.get("sha256"), f"source artifact {index} SHA-256")
        if (
            not isinstance(row.get("kind"), str)
            or not row["kind"]
            or not isinstance(row.get("bucket"), str)
            or not row["bucket"]
            or not isinstance(row.get("object"), str)
            or not row["object"]
            or not isinstance(row.get("size_bytes"), int)
            or isinstance(row.get("size_bytes"), bool)
            or row["size_bytes"] < 1
            or row.get("read_only") is not True
        ):
            raise QualificationError(
                f"source artifact {index} is incomplete"
            )
        relative = _artifact_relative(row["bucket"], row["object"])
        identity = (row["bucket"], row["object"])
        if identity in seen_objects:
            raise QualificationError(
                "source artifact object identities must be unique"
            )
        seen_objects.add(identity)
        path = _readonly_file(
            package_root / relative,
            f"source artifact {row['role']}",
        )
        if (
            path.stat().st_size != row["size_bytes"]
            or _sha256_file(path) != row["sha256"]
        ):
            raise QualificationError(
                f"source artifact {row['role']} differs from its frozen bytes"
            )
    actual_files = {
        path.relative_to(package_root).as_posix()
        for path in package_root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    expected_files = {
        _artifact_relative(row["bucket"], row["object"])
        for row in artifacts
    }
    if actual_files != expected_files:
        raise QualificationError(
            "source package must contain exactly the seven declared artifacts"
        )
    if value.get("input_set_sha256") != sha256_json(artifacts):
        raise QualificationError(
            "source_evidence input_set_sha256 differs from its artifacts"
        )
    for role, binding_name in _SOURCE_BINDINGS.items():
        row = _artifact_by_role(value, role)
        expected = {
            "bucket": row["bucket"],
            "object": row["object"],
            "sha256": row["sha256"],
            "size_bytes": row["size_bytes"],
        }
        if value.get(binding_name) != expected:
            raise QualificationError(
                f"source_evidence {binding_name} binding drifted"
            )
    stage_ids = value.get("stage_run_ids")
    if (
        not isinstance(stage_ids, dict)
        or set(stage_ids) != {"mineru", "popo"}
        or not all(
            isinstance(item, str) and item
            for item in stage_ids.values()
        )
        or stage_ids["popo"] != value["run_id"]
    ):
        raise QualificationError("source_evidence stage run IDs are invalid")
    review = value.get("review_asset")
    popo = value["popo_manifest"]
    if (
        not isinstance(review, dict)
        or set(review) != {"id", "bucket", "object", "sha256"}
        or not str(review.get("id") or "").isdigit()
        or int(review["id"]) < 1
        or review.get("bucket") != popo["bucket"]
        or review.get("object") != popo["object"]
        or review.get("sha256") != popo["sha256"]
    ):
        raise QualificationError(
            "source_evidence review asset is not bound to frozen Popo"
        )
    return value


def _validate_spec05_warning_review(path: Path) -> dict[str, Any]:
    value = _read_json(path, "Spec 05 warning review JSON")
    if (
        set(value) != {"schema_version", "status", "closures"}
        or value.get("schema_version") != SPEC05_WARNING_REVIEW_PROTOCOL
        or value.get("status") != "approved"
        or not isinstance(value.get("closures"), list)
        or not value["closures"]
    ):
        raise QualificationError("Spec 05 warning review JSON is malformed")
    fingerprints: list[str] = []
    for index, closure in enumerate(value["closures"]):
        if (
            not isinstance(closure, dict)
            or set(closure)
            != {
                "fingerprint",
                "classification",
                "rationale",
                "visual_pages",
            }
        ):
            raise QualificationError(
                f"Spec 05 warning closure {index} fields are not exact"
            )
        fingerprint = _sha256(
            closure.get("fingerprint"),
            f"Spec 05 warning closure {index} fingerprint",
        )
        rationale = closure.get("rationale")
        pages = closure.get("visual_pages")
        if (
            closure.get("classification")
            not in {"C2_REVIEW_REQUIRED_CLOSED", "C3_INFO_CLOSED"}
            or not isinstance(rationale, str)
            or not rationale.strip()
            or len(rationale) > 4000
            or not isinstance(pages, list)
            or not pages
            or len(set(pages)) != len(pages)
            or any(
                not isinstance(page, int)
                or isinstance(page, bool)
                or page < 1
                for page in pages
            )
        ):
            raise QualificationError(
                f"Spec 05 warning closure {index} is incomplete"
            )
        fingerprints.append(fingerprint)
    if len(set(fingerprints)) != len(fingerprints):
        raise QualificationError(
            "Spec 05 warning review contains duplicate fingerprints"
        )
    return value


def _apply_qualification_warning_review(
    factory,
    store: DirectoryArtifactStore,
    *,
    job_id: str,
    evaluation_id: int,
    warning_review: Mapping[str, Any],
    run_root: Path,
) -> dict[str, Any]:
    authorized_by = "qualification-visual-reviewer"
    db: Session = factory()
    try:
        job = (
            db.query(WorkflowV3Job)
            .filter(WorkflowV3Job.public_id == job_id)
            .one()
        )
        evaluation = db.get(WorkflowV3Evaluation, evaluation_id)
        if (
            evaluation is None
            or evaluation.workflow_job_id != job.id
            or evaluation.decision != "needs_review"
        ):
            raise QualificationError(
                "Spec 05 warning review has no exact needs_review evaluation"
            )
        candidate = db.get(WorkflowV3Candidate, evaluation.candidate_id)
        if candidate is None or candidate.workflow_job_id != job.id:
            raise QualificationError(
                "Spec 05 warning review candidate binding is invalid"
            )
        findings = evaluation.load(evaluation.findings_json, [])
        if (
            len(findings) != 1
            or not isinstance(findings[0], dict)
            or findings[0].get("blocking") is not True
            or findings[0].get("code")
            != "spec05_compile_warning_review_open"
            or findings[0].get("recovery_stage")
            != "deterministic_elegantbook"
        ):
            raise QualificationError(
                "Spec 05 warning review cannot resolve this evaluation"
            )
        expected_warning_fingerprints = findings[0].get(
            "warning_fingerprints"
        )
        supplied_warning_fingerprints = [
            row["fingerprint"] for row in warning_review["closures"]
        ]
        if (
            not isinstance(expected_warning_fingerprints, list)
            or supplied_warning_fingerprints
            != expected_warning_fingerprints
        ):
            raise QualificationError(
                "Spec 05 warning review does not match every open warning "
                "fingerprint in evaluator order"
            )
        blocker_fingerprints = [finding_fingerprint(row) for row in findings]
        manifest = {
            "schema_version": "luceon.worker-v3.review-resolution/v1",
            "job_id": job.public_id,
            "evaluation": {
                "id": str(evaluation.id),
                "sha256": evaluation_fingerprint(evaluation, candidate),
                "candidate_id": str(candidate.id),
                "candidate_sha256": candidate.sha256,
                "finding_fingerprints": blocker_fingerprints,
            },
            "authorization": {
                "authorized_by": authorized_by,
                "decision": "revise",
            },
            "blocker_resolutions": [
                {
                    "finding_fingerprint": fingerprint,
                    "disposition": "resolved_for_revision",
                    "rationale": (
                        "The exact warning fingerprints were closed by "
                        "hash-bound rendered-page inspection."
                    ),
                }
                for fingerprint in blocker_fingerprints
            ],
            "recovery_stage": "deterministic_elegantbook",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "stage_payload": {
                "stage_key": "deterministic_elegantbook",
                "kind": "spec05_warning_review",
                "payload": dict(warning_review),
            },
        }
        manifest_path = (
            run_root
            / "review-resolutions"
            / f"evaluation-{evaluation.id}.json"
        )
        _write_json(manifest_path, manifest)
        manifest_path.chmod(0o444)
        manifest_sha256 = _sha256_file(manifest_path)
        artifact = store.seed(
            manifest_path,
            bucket="qualification-review-resolutions",
            object_name=(
                f"{job.public_id}/evaluation-{evaluation.id}/"
                f"{manifest_sha256}/manifest.json"
            ),
        )
        _job, resolution, recovery_stage, _candidate = (
            apply_review_resolution(
                db,
                job.public_id,
                idempotency_key=(
                    "qualification-spec05-warning-review:"
                    f"{manifest_sha256}"
                ),
                authorized_by=authorized_by,
                manifest_bucket=artifact.bucket,
                manifest_object=artifact.object_name,
                manifest_sha256=artifact.sha256,
                manifest_size_bytes=artifact.size_bytes,
                manifest=manifest,
            )
        )
        db.commit()
        return {
            "id": str(resolution.id),
            "manifest_sha256": resolution.manifest_sha256,
            "manifest_size_bytes": resolution.manifest_size_bytes,
            "authorized_by": resolution.authorized_by,
            "evaluation_id": str(evaluation.id),
            "evaluation_sha256": resolution.evaluation_sha256,
            "source_generation": resolution.source_generation,
            "recovery_generation": resolution.recovery_generation,
            "recovery_stage": resolution.recovery_stage_key,
            "recovery_stage_run_id": str(recovery_stage.id),
            "warning_review_sha256": sha256_json(warning_review),
            "warning_fingerprints": supplied_warning_fingerprints,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _seed_frozen_source_package(
    store: DirectoryArtifactStore,
    *,
    package_root: Path,
    source_evidence: Mapping[str, Any],
) -> None:
    for row in source_evidence["artifacts"]:
        source = package_root / _artifact_relative(
            row["bucket"],
            row["object"],
        )
        seeded = store.seed(
            source,
            bucket=row["bucket"],
            object_name=row["object"],
        )
        if (
            seeded.sha256 != row["sha256"]
            or seeded.size_bytes != row["size_bytes"]
        ):
            raise QualificationError(
                f"directory artifact seed drifted for {row['role']}"
            )


def _stage_report(
    factory,
    job_id: str,
    stage_key: str,
) -> dict[str, Any]:
    db: Session = factory()
    try:
        job = (
            db.query(WorkflowV3Job)
            .filter(WorkflowV3Job.public_id == job_id)
            .one()
        )
        stage_rows = (
            db.query(WorkflowV3StageRun)
            .filter(
                WorkflowV3StageRun.workflow_job_id == job.id,
                WorkflowV3StageRun.stage_key == stage_key,
            )
            .order_by(
                WorkflowV3StageRun.generation.asc(),
                WorkflowV3StageRun.attempt.asc(),
                WorkflowV3StageRun.id.asc(),
            )
            .all()
        )
        if not stage_rows:
            raise QualificationError(
                f"qualification stage report is missing {stage_key}"
            )
        stage = stage_rows[-1]
        execution = (
            db.query(WorkflowV3Execution)
            .filter(WorkflowV3Execution.stage_run_id == stage.id)
            .one()
        )
        candidate = (
            db.query(WorkflowV3Candidate)
            .filter(WorkflowV3Candidate.stage_run_id == stage.id)
            .one()
        )
        evaluation = (
            db.query(WorkflowV3Evaluation)
            .filter(WorkflowV3Evaluation.stage_run_id == stage.id)
            .one()
        )
        promotion = (
            db.query(WorkflowV3Promotion)
            .filter(WorkflowV3Promotion.stage_run_id == stage.id)
            .one_or_none()
        )
        attempts = []
        for row in stage_rows:
            row_candidate = (
                db.query(WorkflowV3Candidate)
                .filter(WorkflowV3Candidate.stage_run_id == row.id)
                .one_or_none()
            )
            row_evaluation = (
                db.query(WorkflowV3Evaluation)
                .filter(WorkflowV3Evaluation.stage_run_id == row.id)
                .one_or_none()
            )
            row_promotion = (
                db.query(WorkflowV3Promotion)
                .filter(WorkflowV3Promotion.stage_run_id == row.id)
                .one_or_none()
            )
            attempts.append(
                {
                    "stage_run_id": str(row.id),
                    "attempt": row.attempt,
                    "generation": row.generation,
                    "machine_status": row.machine_status,
                    "spec_status": row.spec_status,
                    "review_resolution_sha256": (
                        row.review_resolution_sha256
                    ),
                    "candidate_id": (
                        str(row_candidate.id) if row_candidate else ""
                    ),
                    "candidate_sha256": (
                        row_candidate.sha256 if row_candidate else ""
                    ),
                    "evaluation_id": (
                        str(row_evaluation.id) if row_evaluation else ""
                    ),
                    "evaluation_decision": (
                        row_evaluation.decision if row_evaluation else ""
                    ),
                    "promotion_id": (
                        str(row_promotion.id) if row_promotion else ""
                    ),
                }
            )
        return {
            "stage": stage.to_dict(),
            "attempts": attempts,
            "execution": {
                "id": str(execution.id),
                "producer_identity": execution.producer_identity,
                "runtime_identity_sha256": (
                    execution.runtime_identity_sha256
                ),
                "status": execution.machine_status,
            },
            "candidate": {
                "id": str(candidate.id),
                "kind": candidate.artifact_kind,
                "bucket": candidate.bucket,
                "object": candidate.object_name,
                "sha256": candidate.sha256,
                "size_bytes": candidate.size_bytes,
                "immutable": bool(candidate.immutable),
                "status": candidate.status,
            },
            "evaluation": {
                "id": str(evaluation.id),
                "decision": evaluation.decision,
                "spec_passed": bool(evaluation.spec_passed),
                "policy_sha256": evaluation.policy_sha256,
                "gate_results": evaluation.load(
                    evaluation.gate_results_json,
                    {},
                ),
                "findings": evaluation.load(
                    evaluation.findings_json,
                    [],
                ),
            },
            "promotion": (
                {
                    "id": str(promotion.id),
                    "artifact_sha256": promotion.artifact_sha256,
                    "promoted_by": promotion.promoted_by,
                }
                if promotion is not None
                else None
            ),
        }
    finally:
        db.close()


def _model_call_report(factory, job_id: str) -> list[dict[str, Any]]:
    db: Session = factory()
    try:
        job = (
            db.query(WorkflowV3Job)
            .filter(WorkflowV3Job.public_id == job_id)
            .one()
        )
        rows = (
            db.query(WorkflowV3ModelCall)
            .filter(WorkflowV3ModelCall.workflow_job_id == job.id)
            .order_by(WorkflowV3ModelCall.id.asc())
            .all()
        )
        return [row.to_dict() for row in rows]
    finally:
        db.close()


def _assert_model_call_costs(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        if row.get("status") != "succeeded":
            continue
        cost = row.get("cost")
        if (
            not isinstance(cost, Mapping)
            or cost.get("status") != "charged"
            or not cost.get("currency")
            or not isinstance(cost.get("micro_units"), int)
            or isinstance(cost.get("micro_units"), bool)
            or cost["micro_units"] < 0
            or not isinstance(cost.get("breakdown"), Mapping)
            or not cost["breakdown"]
            or not isinstance(row.get("pricing_snapshot_sha256"), str)
            or len(row["pricing_snapshot_sha256"]) != 64
        ):
            raise QualificationError(
                "successful model call lacks release-bound attributable cost"
            )


def _job_report(factory, job_id: str) -> dict[str, Any]:
    db: Session = factory()
    try:
        job = (
            db.query(WorkflowV3Job)
            .filter(WorkflowV3Job.public_id == job_id)
            .one()
        )
        return {
            "id": job.public_id,
            "machine_status": job.machine_status,
            "spec_status": job.spec_status,
            "readiness_status": job.readiness_status,
            "human_acceptance_status": job.human_acceptance_status,
            "current_stage_key": job.current_stage_key,
            "error_code": job.error_code,
        }
    finally:
        db.close()


def _source_snapshot(
    source_json: Path,
    package_root: Path,
    source_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "material_id": source_evidence["material_id"],
        "run_id": source_evidence["run_id"],
        "source_evidence_sha256": _sha256_file(source_json),
        "input_set_sha256": source_evidence["input_set_sha256"],
        "artifacts": [
            {
                "role": row["role"],
                "path": _artifact_relative(
                    row["bucket"],
                    row["object"],
                ),
                "sha256": row["sha256"],
                "size_bytes": row["size_bytes"],
                "mode": (
                    f"{stat.S_IMODE((package_root / _artifact_relative(row['bucket'], row['object'])).stat().st_mode):04o}"
                ),
            }
            for row in source_evidence["artifacts"]
        ],
    }


def _assert_source_unchanged(
    package_root: Path,
    artifacts: list[Mapping[str, Any]],
) -> None:
    for row in artifacts:
        path = _readonly_file(
            package_root / str(row["path"]),
            f"source artifact {row['role']}",
        )
        if (
            _sha256_file(path) != row["sha256"]
            or path.stat().st_size != row["size_bytes"]
        ):
            raise QualificationError(
                f"source artifact {row['role']} changed during qualification"
            )


def _artifact_by_role(
    source_evidence: Mapping[str, Any],
    role: str,
) -> Mapping[str, Any]:
    rows = [
        row
        for row in source_evidence.get("artifacts", [])
        if isinstance(row, Mapping) and row.get("role") == role
    ]
    if len(rows) != 1:
        raise QualificationError(f"source artifact {role!r} is missing")
    return rows[0]


def _stage_index(stage_key: str) -> int:
    for index, contract in enumerate(STAGE_CONTRACTS):
        if contract.key == stage_key:
            return index
    raise QualificationError(f"unknown qualification stop stage: {stage_key}")


def _fresh_path(path: Path, label: str) -> Path:
    raw = Path(path).expanduser()
    parent = raw.parent.resolve()
    resolved = parent / raw.name
    if resolved.exists() or resolved.is_symlink():
        raise QualificationError(f"{label} must not already exist")
    if not parent.is_dir() or parent.is_symlink():
        raise QualificationError(f"{label} parent is unavailable")
    return resolved


def _readonly_file(path: Path, label: str) -> Path:
    value = Path(path).expanduser()
    if value.is_symlink() or not value.is_file():
        raise QualificationError(f"{label} is not a regular file")
    resolved = value.resolve()
    if stat.S_IMODE(resolved.stat().st_mode) & 0o222:
        raise QualificationError(f"{label} must be read-only")
    _assert_no_symlink_components(resolved)
    return resolved


def _readonly_directory(path: Path, label: str) -> Path:
    value = Path(path).expanduser()
    if value.is_symlink() or not value.is_dir():
        raise QualificationError(f"{label} is not a directory")
    resolved = value.resolve()
    if stat.S_IMODE(resolved.stat().st_mode) & 0o222:
        raise QualificationError(f"{label} must be read-only")
    _assert_no_symlink_components(resolved)
    for child in resolved.rglob("*"):
        if child.is_symlink():
            raise QualificationError(f"{label} contains a symlink")
        if stat.S_IMODE(child.stat().st_mode) & 0o222:
            raise QualificationError(f"{label} contains writable content")
    return resolved


def _assert_no_symlink_components(path: Path) -> None:
    cursor = Path(path.anchor)
    for part in path.parts[1:]:
        cursor = cursor / part
        if cursor.is_symlink():
            raise QualificationError(
                f"immutable evidence path contains symlink: {path}"
            )


def _artifact_relative(bucket: str, object_name: str) -> str:
    bucket_path = _safe_relative(bucket, "artifact bucket")
    object_path = _safe_relative(object_name, "artifact object")
    return f"{bucket_path}/{object_path}"


def _safe_relative(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith("/")
        or "\\" in value
        or "\x00" in value
    ):
        raise QualificationError(f"{label} is not a safe relative path")
    parsed = PurePosixPath(value)
    if (
        any(part in {"", ".", ".."} for part in parsed.parts)
        or parsed.as_posix() != value
    ):
        raise QualificationError(f"{label} is not normalized")
    return value


def _overlaps(left: Path, right: Path) -> bool:
    left = left.resolve()
    right = right.resolve()
    return (
        left == right
        or left in right.parents
        or right in left.parents
    )


def _sha256(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _SHA256_CHARS for character in value)
    ):
        raise QualificationError(f"{label} must be lowercase SHA-256")
    return value


def _qualification_job_public_id(
    *,
    manifest_sha256: str,
    source_input_set_sha256: str,
) -> str:
    """Derive the isolated qualification identity from immutable inputs."""

    manifest_sha256 = _sha256(
        manifest_sha256,
        "qualification release manifest SHA-256",
    )
    source_input_set_sha256 = _sha256(
        source_input_set_sha256,
        "qualification source input-set SHA-256",
    )
    identity = "\n".join(
        (
            WORKFLOW_VERSION,
            manifest_sha256,
            source_input_set_sha256,
        )
    )
    return str(uuid.uuid5(_QUALIFICATION_JOB_NAMESPACE, identity))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QualificationError(f"{label} is invalid: {exc}") from exc
    if not isinstance(value, dict):
        raise QualificationError(f"{label} must be one JSON object")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value) + b"\n")


def _valid_usage(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    allowed = {"input_tokens", "output_tokens", "total_tokens"}
    if not set(value).issubset(allowed):
        return False
    required = {"input_tokens", "output_tokens"}
    return required.issubset(value) and all(
        isinstance(item, int)
        and not isinstance(item, bool)
        and item >= 0
        for item in value.values()
    )
