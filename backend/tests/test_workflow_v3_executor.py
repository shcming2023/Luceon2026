from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.workflow_v3.contracts import STAGE_CONTRACTS, WORKFLOW_VERSION
from app.workflow_v3.evaluator import WorkflowV3Evaluator, WorkflowV3PromotionController
from app.workflow_v3.executor import (
    _bounded_model_call_id,
    _control_plane_promotion_class,
    DirectoryArtifactStore,
    DirectoryReleaseResolver,
    SubprocessTransport,
    WorkflowV3Executor,
)
from app.workflow_v3.llm_gateway import LlmGatewayError


def test_native_spec03_04_promotions_preserve_formal_native_lineage() -> None:
    assert _control_plane_promotion_class("source_scope_and_order") == "standard"
    for stage_key in (
        "canonical_block_ledger",
        "outline_reconstruction",
        "semantic_annotation",
        "template_construct_binding",
        "frozen_render_plan",
    ):
        assert _control_plane_promotion_class(stage_key) == "formal_native"


def test_subprocess_transport_passes_only_bound_producer_work_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = tmp_path / "inspect_env.py"
    script.write_text(
        "import json, os\n"
        "print(json.dumps({\n"
        "  'producer_root': os.getenv('WORKFLOW_V3_PRODUCER_WORK_ROOT'),\n"
        "  'secret': os.getenv('UNRELATED_SECRET'),\n"
        "}, sort_keys=True))\n",
        encoding="utf-8",
    )
    producer_root = tmp_path / "producer"
    monkeypatch.setenv("WORKFLOW_V3_PRODUCER_WORK_ROOT", str(producer_root))
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-cross")

    result = SubprocessTransport(poll_seconds=0.01).run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        timeout_seconds=10,
        heartbeat=lambda: None,
        cancelled=lambda: False,
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "producer_root": str(producer_root),
        "secret": None,
    }
from app.workflow_v3.models import (
    WorkflowV3Base,
    WorkflowV3Candidate,
    WorkflowV3Evaluation,
    WorkflowV3Execution,
    WorkflowV3Job,
    WorkflowV3Promotion,
    WorkflowV3SkillRelease,
    WorkflowV3StageRun,
)
from app.workflow_v3.queue import (
    cancel,
    next_evaluation_item,
    next_producer_item,
    next_promotion_item,
    recover_stale,
)
from app.workflow_v3.release import (
    REQUIRED_DIRECTORIES,
    build_release_archive,
    install_release_archive,
)
from app.workflow_v3.service import (
    create_workflow_job,
    register_skill_release,
    runtime_identity_for_manifest,
)
from app.workflow_v3.state_machine import (
    WorkflowV3TransitionError,
    claim_current_stage,
    promote_candidate,
    retry_failed_stage,
    submit_candidate,
)


def test_bounded_model_call_id_is_stable_for_the_same_job_scope() -> None:
    values = {
        "job_scope_id": "stable-job-idempotency-key",
        "stage_key": "source_scope_and_order",
        "attempt": 1,
        "prompt_sha256": "a" * 64,
        "input_sha256": "b" * 64,
    }

    first = _bounded_model_call_id(**values)
    assert first == _bounded_model_call_id(**values)
    assert first != _bounded_model_call_id(
        **{**values, "job_scope_id": "different-job-scope"}
    )


PRODUCER_SCRIPT = """#!/usr/bin/env python3
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--input", required=True)
args = parser.parse_args()
request = json.loads(Path(args.input).read_text(encoding="utf-8"))
BEHAVIOR = __BEHAVIOR__
if BEHAVIOR == "nonzero":
    print("fixture nonzero", file=sys.stderr)
    raise SystemExit(17)
if BEHAVIOR == "timeout":
    time.sleep(2.5)
if BEHAVIOR == "missing":
    raise SystemExit(0)
if BEHAVIOR == "malformed":
    Path(request["output_manifest"]).write_text("{not-json", encoding="utf-8")
    raise SystemExit(0)
output = Path("output") / "artifact.json"
output.parent.mkdir(parents=True, exist_ok=True)
payload = json.dumps(
    {
        "stage_key": request["stage_key"],
        "attempt": request["attempt"],
        "input_sha256": request["input"]["sha256"],
    },
    sort_keys=True,
).encode("utf-8")
output.write_bytes(payload)
digest = hashlib.sha256(payload).hexdigest()
if BEHAVIOR == "hash_drift":
    digest = "f" * 64
manifest = {
    "schema_version": "luceon.worker-v3-stage-candidate/v1",
    "job_id": request["job_id"],
    "stage_key": request["stage_key"],
    "attempt": request["attempt"],
    "input_sha256": request["input"]["sha256"],
    "release_manifest_sha256": request["release"]["manifest_sha256"],
    "artifact": {
        "kind": request["stage_key"] + "-artifact",
        "path": str(output),
        "sha256": digest,
        "size_bytes": len(payload),
    },
    "metrics": {"fixture": True},
}
Path(request["output_manifest"]).write_text(json.dumps(manifest), encoding="utf-8")
"""


EVALUATOR_SCRIPT = """#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--input", required=True)
args = parser.parse_args()
request = json.loads(Path(args.input).read_text(encoding="utf-8"))
manifest = {
    "schema_version": "luceon.worker-v3-stage-evaluation/v1",
    "job_id": request["job_id"],
    "stage_key": request["stage_key"],
    "attempt": request["attempt"],
    "candidate_sha256": request["candidate"]["sha256"],
    "release_manifest_sha256": request["release_manifest_sha256"],
    "policy_sha256": request["policy_sha256"],
    "decision": "passed",
    "gate_results": {gate: True for gate in request["required_gates"]},
    "findings": [],
}
Path(request["output_manifest"]).write_text(json.dumps(manifest), encoding="utf-8")
"""


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write(root: Path, relative: str, payload: bytes, *, executable: bool = False) -> str:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    target.chmod(0o755 if executable else 0o644)
    return _sha(payload)


def _build_installed_release(tmp_path: Path, *, behavior: str = "valid"):
    source = tmp_path / f"release-source-{behavior}"
    for directory in REQUIRED_DIRECTORIES:
        (source / directory).mkdir(parents=True, exist_ok=True)
    skill_hash = _write(source, "skills/orchestrator/SKILL.md", b"# V3 fixture skill\n")
    spec_hash = _write(source, "contracts/spec.md", b"# V3 fixture contract\n")
    input_schema_hash = _write(source, "schemas/input.json", b'{"type":"object"}\n')
    output_schema_hash = _write(source, "schemas/output.json", b'{"type":"object"}\n')
    _write(source, "validators/validate.py", b"def validate(value): return value\n")
    _write(source, "prompts/README.md", b"No unbounded prompt.\n")
    _write(source, "references/protocol.md", b"# protocol\n")
    template_hash = _write(source, "templates/elegantbook.zip", b"fixed-template")
    _write(source, "evals/fixture.json", b'{"case":"protocol"}\n')
    _write(source, "runtime/sbom.json", b'{"bomFormat":"CycloneDX"}\n')
    _write(source, "runtime/attestation.json", b'{"verified":true}\n')
    producer = PRODUCER_SCRIPT.replace("__BEHAVIOR__", repr(behavior)).encode()
    _write(source, "scripts/produce.py", producer, executable=True)
    _write(source, "scripts/evaluate.py", EVALUATOR_SCRIPT.encode(), executable=True)
    definitions = {}
    formal = []
    for contract in STAGE_CONTRACTS:
        producer_id = f"formal.{contract.key}.produce"
        evaluator_id = f"formal.{contract.key}.evaluate"
        formal.extend((producer_id, evaluator_id))
        definitions[producer_id] = {
            "classification": "formal",
            "execution_role": "producer",
            "stage": contract.key,
            "argv": ["scripts/produce.py", "--input", "request.json"],
            "input_schema": "schemas/input.json",
            "output_schema": "schemas/output.json",
            "permission_envelope": "candidate-only",
            "timeout_seconds": 1 if behavior == "timeout" else 10,
            "exit_semantics": {"0": "candidate_ready", "other": "failed"},
        }
        definitions[evaluator_id] = {
            "classification": "formal",
            "execution_role": "evaluator",
            "stage": contract.key,
            "argv": ["scripts/evaluate.py", "--input", "request.json"],
            "input_schema": "schemas/input.json",
            "output_schema": "schemas/output.json",
            "permission_envelope": "read-only-evaluator",
            "timeout_seconds": 10,
            "exit_semantics": {"0": "evaluation_ready", "other": "failed"},
        }
    runtime = {
        "python": "CPython fixture",
        "application_dependencies_sha256": "6" * 64,
        "system_tools": {"python": "fixture"},
        "fonts_sha256": "7" * 64,
        "tex_sha256": "8" * 64,
        "poppler_sha256": "9" * 64,
        "container_image_digest": f"sha256:{'b' * 64}",
        "sbom_path": "runtime/sbom.json",
        "attestations": ["runtime/attestation.json"],
    }
    manifest = {
        "schema_version": "luceon.worker-v3-skill-release/v1",
        "release_id": f"worker-v3-runtime-{behavior}",
        "version": f"3.0.0-rc.{abs(hash(behavior)) % 10000}",
        "channel": "rc",
        "status": "rc",
        "created_at": "2026-07-26T00:00:00Z",
        "source": {"git_sha": "a" * 40, "git_tag": "fixture", "dirty": False},
        "eligibility": {"rc_eligible": True, "stable_eligible": False},
        "tree_hash": {
            "algorithm": "sha256-canonical-file-records-v1",
            "sha256": "0" * 64,
        },
        "archive_hash_location": "external-release-registry",
        "files": [],
        "skills": [
            {
                "id": "luceon-popo-to-refined-elegantbook",
                "version": "fixture",
                "path": "skills/orchestrator/SKILL.md",
                "sha256": skill_hash,
            }
        ],
        "specs": [
            {
                "id": "worker-v3-contract",
                "version": "fixture",
                "path": "contracts/spec.md",
                "sha256": spec_hash,
            }
        ],
        "schemas": [
            {
                "id": "stage-input",
                "version": "1",
                "path": "schemas/input.json",
                "sha256": input_schema_hash,
            },
            {
                "id": "stage-output",
                "version": "1",
                "path": "schemas/output.json",
                "sha256": output_schema_hash,
            },
        ],
        "entrypoints": {
            "formal": formal,
            "legacy": [],
            "migration": [],
            "diagnostic": [],
            "prohibited": [],
            "definitions": definitions,
        },
        "dynamic_closure": {
            "modules": ["json", "hashlib"],
            "resources": ["validators/validate.py", "references/protocol.md"],
        },
        "prompts": [],
        "model_policy": {"mode": "none"},
        "template": {
            "id": "approved-elegantbook",
            "version": "2025",
            "archive_path": "templates/elegantbook.zip",
            "archive_sha256": template_hash,
            "tree_sha256": "1" * 64,
            "main_member": "main.tex",
            "main_sha256": "2" * 64,
            "class_member": "elegantbook.cls",
            "class_sha256": "3" * 64,
            "fixed_asset_members": [
                "figure/cover.jpg",
                "figure/logo.jpg",
            ],
            "fixed_assets_sha256": "4" * 64,
            "capabilities_sha256": "5" * 64,
        },
        "runtime": runtime,
        "limits": {
            "delivery_zip_bytes_exclusive_max": 50_000_000,
            "raster_bytes_exclusive_max": 1_000_000,
            "file_count_exclusive_max": 2_000,
            "tex_leaf_bytes_exclusive_max": 900_000,
        },
        "evidence": {"unit": [], "contract": [], "eval": [], "uat": [], "known_gaps": []},
        "compatibility": {"v2_3": "isolated", "rollback": "disable V3 admission"},
    }
    (source / "release-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    archive = tmp_path / f"release-{behavior}.tar.gz"
    built = build_release_archive(source, archive)
    installed = install_release_archive(
        archive,
        tmp_path / f"installed-{behavior}",
        expected_archive_sha256=built["archive_sha256"],
    )
    manifest = installed.manifest
    manifest_sha256 = _sha((installed.root / "release-manifest.json").read_bytes())
    return installed, built, manifest, manifest_sha256


def _environment(tmp_path: Path, *, behavior: str = "valid"):
    installed, built, manifest, manifest_sha256 = _build_installed_release(
        tmp_path, behavior=behavior
    )
    engine = create_engine(
        f"sqlite:///{tmp_path / 'workflow-v3.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    WorkflowV3Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    store = DirectoryArtifactStore(tmp_path / "objects")
    source = tmp_path / "source-popo-manifest.json"
    source.write_text('{"frozen":true}\n', encoding="utf-8")
    source_ref = store.seed(
        source,
        bucket="eduassets-minerupopo",
        object_name="minerupopo/pdf-test/popo-run/manifest.json",
    )
    db = factory()
    try:
        runtime_sha = runtime_identity_for_manifest(manifest)
        release, _ = register_skill_release(
            db,
            release_version=manifest["version"],
            manifest_sha256=manifest_sha256,
            package_bucket="worker-v3-releases",
            package_object=f"{manifest['release_id']}.tar.gz",
            package_sha256=built["archive_sha256"],
            workflow_version=WORKFLOW_VERSION,
            template_sha256=manifest["template"]["tree_sha256"],
            runtime_identity_sha256=runtime_sha,
            manifest=manifest,
            registered_by="fixture-release-controller",
        )
        job, _ = create_workflow_job(
            db,
            user_id="fixture-user",
            material_pk=4242,
            material_id="pdf-worker-v3-runtime",
            source_popo_bucket=source_ref.bucket,
            source_popo_object=source_ref.object_name,
            source_popo_sha256=source_ref.sha256,
            skill_release_version=release.release_version,
            skill_release_sha256=release.manifest_sha256,
            template_sha256=release.template_sha256,
            payload={"shadow": True},
        )
        db.commit()
    finally:
        db.close()
    resolver = DirectoryReleaseResolver(installed.root)
    executor = WorkflowV3Executor(
        session_factory=factory,
        release_resolver=resolver,
        artifact_store=store,
        work_root=tmp_path / "producer-work",
        producer_identity="producer-fixture",
        transport=SubprocessTransport(poll_seconds=0.02, heartbeat_seconds=0.05),
    )
    evaluator = WorkflowV3Evaluator(
        session_factory=factory,
        release_resolver=resolver,
        artifact_store=store,
        work_root=tmp_path / "evaluator-work",
        evaluator_identity="evaluator-fixture",
        transport=SubprocessTransport(poll_seconds=0.02, heartbeat_seconds=0.05),
    )
    promoter = WorkflowV3PromotionController(
        session_factory=factory,
        release_resolver=resolver,
        artifact_store=store,
        promoter_identity="promotion-controller",
    )
    return {
        "factory": factory,
        "store": store,
        "installed": installed,
        "job_id": job.public_id,
        "executor": executor,
        "evaluator": evaluator,
        "promoter": promoter,
        "tmp_path": tmp_path,
    }


def test_producer_evaluator_and_promoter_are_separate_and_sha_chained(tmp_path, monkeypatch):
    env = _environment(tmp_path)
    calls = []
    real_popen = __import__("subprocess").Popen

    def recording_popen(*args, **kwargs):
        calls.append((args, kwargs))
        return real_popen(*args, **kwargs)

    monkeypatch.setattr("app.workflow_v3.executor.subprocess.Popen", recording_popen)
    produced = env["executor"].run_one_stage(env["job_id"])
    assert produced["ok"] is True
    assert produced["status"] == "awaiting_evaluation"
    assert calls and all(call[1]["shell"] is False for call in calls)
    assert all(".codex/skills" not in token for token in calls[0][0][0])
    assert all(
        call[1]["env"]["PYTHONDONTWRITEBYTECODE"] == "1"
        for call in calls
    )
    db = env["factory"]()
    try:
        assert db.query(WorkflowV3Candidate).count() == 1
        assert db.query(WorkflowV3Evaluation).count() == 0
        assert db.query(WorkflowV3Promotion).count() == 0
        assert next_producer_item(db) is None
        item = next_evaluation_item(db)
        assert item.candidate_id == int(produced["candidate_id"])
    finally:
        db.close()

    duplicate = env["executor"].run_one_stage(env["job_id"])
    assert duplicate["ok"] is True
    assert duplicate["idempotent"] is True
    evaluated = env["evaluator"].evaluate(env["job_id"], int(produced["candidate_id"]))
    assert evaluated["ok"] is True
    assert evaluated["status"] == "awaiting_promotion"
    db = env["factory"]()
    try:
        assert db.query(WorkflowV3Promotion).count() == 0
        promotion_item = next_promotion_item(db)
        assert promotion_item.evaluation_id == int(evaluated["evaluation_id"])
    finally:
        db.close()

    promoted = env["promoter"].promote(env["job_id"], int(evaluated["evaluation_id"]))
    assert promoted["ok"] is True
    db = env["factory"]()
    try:
        job = db.query(WorkflowV3Job).filter(WorkflowV3Job.public_id == env["job_id"]).one()
        current = (
            db.query(WorkflowV3StageRun)
            .filter(
                WorkflowV3StageRun.workflow_job_id == job.id,
                WorkflowV3StageRun.stage_key == job.current_stage_key,
            )
            .order_by(WorkflowV3StageRun.attempt.desc())
            .first()
        )
        promotion = db.query(WorkflowV3Promotion).one()
        assert job.current_stage_key == STAGE_CONTRACTS[1].key
        assert current.input_kind == "promoted_artifact"
        assert current.input_promotion_id == promotion.id
        assert current.input_artifact_sha256 == produced["candidate_sha256"]
    finally:
        db.close()


@pytest.mark.parametrize(
    ("behavior", "error_code"),
    [
        ("nonzero", "entrypoint_nonzero_exit"),
        ("timeout", "entrypoint_timeout"),
        ("missing", "entrypoint_protocol_invalid"),
        ("malformed", "entrypoint_protocol_invalid"),
        ("hash_drift", "artifact_integrity_failed"),
    ],
)
def test_external_command_and_manifest_failures_are_closed(
    tmp_path, behavior, error_code
):
    env = _environment(tmp_path, behavior=behavior)
    result = env["executor"].run_one_stage(env["job_id"])
    assert result["ok"] is False
    assert result["error_code"] == error_code
    db = env["factory"]()
    try:
        job = db.query(WorkflowV3Job).filter(WorkflowV3Job.public_id == env["job_id"]).one()
        assert job.machine_status == "failed"
        assert job.error_code == error_code
        assert db.query(WorkflowV3Candidate).count() == 0
        assert db.query(WorkflowV3Promotion).count() == 0
    finally:
        db.close()


def test_retryable_llm_failure_requeues_twice_then_fails_terminally(tmp_path):
    env = _environment(tmp_path)

    for expected_attempt, expected_status in ((1, "retrying"), (2, "retrying"), (3, "failed")):
        db = env["factory"]()
        try:
            job = db.query(WorkflowV3Job).filter(
                WorkflowV3Job.public_id == env["job_id"]
            ).one()
            release = db.query(WorkflowV3SkillRelease).filter(
                WorkflowV3SkillRelease.id == job.skill_release_id
            ).one()
            _, stage, execution = claim_current_stage(
                db,
                job.public_id,
                producer_identity="producer-fixture",
                idempotency_key=f"retryable-provider-{expected_attempt}",
                runtime_identity_sha256=release.runtime_identity_sha256,
            )
            assert stage.attempt == expected_attempt
            execution_id = execution.id
            db.commit()
        finally:
            db.close()

        status = env["executor"]._record_failure(
            env["job_id"],
            execution_id,
            LlmGatewayError(
                "transport_error",
                "temporary provider connection failure",
                retryable=True,
            ),
        )
        assert status == expected_status

    db = env["factory"]()
    try:
        job = db.query(WorkflowV3Job).filter(
            WorkflowV3Job.public_id == env["job_id"]
        ).one()
        attempts = (
            db.query(WorkflowV3StageRun)
            .filter(
                WorkflowV3StageRun.workflow_job_id == job.id,
                WorkflowV3StageRun.stage_key == STAGE_CONTRACTS[0].key,
            )
            .order_by(WorkflowV3StageRun.attempt)
            .all()
        )
        assert [(row.attempt, row.machine_status) for row in attempts] == [
            (1, "failed"),
            (2, "failed"),
            (3, "failed"),
        ]
        assert len({row.input_artifact_sha256 for row in attempts}) == 1
        assert job.machine_status == "failed"
        assert job.error_code == "transport_error"
    finally:
        db.close()


def test_nonretryable_llm_failure_remains_terminal(tmp_path):
    env = _environment(tmp_path)
    db = env["factory"]()
    try:
        job = db.query(WorkflowV3Job).filter(
            WorkflowV3Job.public_id == env["job_id"]
        ).one()
        release = db.query(WorkflowV3SkillRelease).filter(
            WorkflowV3SkillRelease.id == job.skill_release_id
        ).one()
        _, _, execution = claim_current_stage(
            db,
            job.public_id,
            producer_identity="producer-fixture",
            idempotency_key="nonretryable-provider",
            runtime_identity_sha256=release.runtime_identity_sha256,
        )
        execution_id = execution.id
        db.commit()
    finally:
        db.close()

    status = env["executor"]._record_failure(
        env["job_id"],
        execution_id,
        LlmGatewayError(
            "provider_auth_error",
            "provider rejected credentials",
            retryable=False,
        ),
    )

    assert status == "failed"
    db = env["factory"]()
    try:
        assert (
            db.query(WorkflowV3StageRun)
            .filter(WorkflowV3StageRun.stage_key == STAGE_CONTRACTS[0].key)
            .count()
            == 1
        )
        assert db.query(WorkflowV3Job).one().machine_status == "failed"
    finally:
        db.close()


def test_release_hash_drift_and_frozen_input_drift_fail_before_candidate(tmp_path):
    release_env = _environment(tmp_path / "release-drift")
    script = release_env["installed"].root / "scripts/produce.py"
    script.chmod(0o755)
    script.write_text("#!/usr/bin/env python3\nraise SystemExit(0)\n", encoding="utf-8")
    release_result = release_env["executor"].run_one_stage(release_env["job_id"])
    assert release_result["ok"] is False
    assert release_result["error_code"] == "release_binding_invalid"

    input_env = _environment(tmp_path / "input-drift")
    db = input_env["factory"]()
    try:
        job = db.query(WorkflowV3Job).filter(WorkflowV3Job.public_id == input_env["job_id"]).one()
        object_path = (
            input_env["store"].root
            / job.source_popo_bucket
            / job.source_popo_object
        )
    finally:
        db.close()
    object_path.chmod(0o644)
    object_path.write_text('{"frozen":false}\n', encoding="utf-8")
    input_result = input_env["executor"].run_one_stage(input_env["job_id"])
    assert input_result["ok"] is False
    assert input_result["error_code"] == "artifact_integrity_failed"


def test_stale_execution_requeues_same_sha_in_new_attempt_workspace(tmp_path):
    env = _environment(tmp_path)
    db = env["factory"]()
    try:
        job = db.query(WorkflowV3Job).filter(WorkflowV3Job.public_id == env["job_id"]).one()
        release = db.query(WorkflowV3SkillRelease).filter(
            WorkflowV3SkillRelease.id == job.skill_release_id
        ).one()
        _, _, execution = claim_current_stage(
            db,
            job.public_id,
            producer_identity="stale-producer",
            idempotency_key="stale-execution",
            runtime_identity_sha256=release.runtime_identity_sha256,
        )
        execution.heartbeat_at = datetime.utcnow() - timedelta(minutes=5)
        db.commit()
    finally:
        db.close()
    assert recover_stale(env["factory"], stale_after_seconds=60) == [env["job_id"]]
    result = env["executor"].run_one_stage(env["job_id"])
    assert result["ok"] is True
    assert result["attempt"] == 2
    assert "attempt-2" in result["workdir"]
    db = env["factory"]()
    try:
        attempts = (
            db.query(WorkflowV3StageRun)
            .filter(WorkflowV3StageRun.stage_key == STAGE_CONTRACTS[0].key)
            .order_by(WorkflowV3StageRun.attempt)
            .all()
        )
        assert [(row.attempt, row.machine_status) for row in attempts] == [
            (1, "failed"),
            (2, "awaiting_evaluation"),
        ]
        assert attempts[0].input_artifact_sha256 == attempts[1].input_artifact_sha256
    finally:
        db.close()


def test_queued_and_running_cancel_reject_late_candidate_and_cannot_retry(tmp_path):
    queued = _environment(tmp_path / "queued")
    cancelled = cancel(
        queued["factory"],
        queued["job_id"],
        cancelled_by="user-1",
        reason="stop queued run",
    )
    assert cancelled["job_status"] == "cancelled"
    with pytest.raises(WorkflowV3TransitionError, match="already cancelled"):
        db = queued["factory"]()
        try:
            claim_current_stage(
                db,
                queued["job_id"],
                producer_identity="producer",
                idempotency_key="late-claim",
                runtime_identity_sha256="1" * 64,
            )
        finally:
            db.close()

    running = _environment(tmp_path / "running")
    db = running["factory"]()
    try:
        job = db.query(WorkflowV3Job).filter(WorkflowV3Job.public_id == running["job_id"]).one()
        release = db.query(WorkflowV3SkillRelease).filter(
            WorkflowV3SkillRelease.id == job.skill_release_id
        ).one()
        _, stage, execution = claim_current_stage(
            db,
            job.public_id,
            producer_identity="producer-running",
            idempotency_key="running-claim",
            runtime_identity_sha256=release.runtime_identity_sha256,
        )
        db.commit()
        execution_id = execution.id
    finally:
        db.close()
    cancel(
        running["factory"],
        running["job_id"],
        cancelled_by="user-1",
        reason="stop running run",
    )
    db = running["factory"]()
    try:
        execution = db.query(WorkflowV3Execution).filter(
            WorkflowV3Execution.id == execution_id
        ).one()
        assert execution.machine_status == "cancelled"
        with pytest.raises(WorkflowV3TransitionError, match="execution is not active"):
            submit_candidate(
                db,
                running["job_id"],
                execution_id=execution_id,
                idempotency_key="late-candidate",
                artifact_kind="late",
                bucket="candidate",
                object_name="late/artifact",
                sha256="a" * 64,
                size_bytes=1,
            )
        with pytest.raises(
            WorkflowV3TransitionError,
            match="only a failed or needs_review job",
        ):
            retry_failed_stage(db, running["job_id"])
        assert db.query(WorkflowV3Candidate).count() == 0
        assert db.query(WorkflowV3Promotion).count() == 0
    finally:
        db.close()


def test_cancel_after_evaluation_prevents_promotion(tmp_path):
    env = _environment(tmp_path)
    produced = env["executor"].run_one_stage(env["job_id"])
    evaluated = env["evaluator"].evaluate(env["job_id"], int(produced["candidate_id"]))
    cancel(
        env["factory"],
        env["job_id"],
        cancelled_by="user-1",
        reason="stop before promotion",
    )
    with pytest.raises(WorkflowV3TransitionError, match="not promotion-ready"):
        env["promoter"].promote(env["job_id"], int(evaluated["evaluation_id"]))
    db = env["factory"]()
    try:
        assert db.query(WorkflowV3Promotion).count() == 0
        assert db.query(WorkflowV3Job).one().machine_status == "cancelled"
    finally:
        db.close()
