from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.workflow_v3.contracts import STAGE_CONTRACTS, WORKFLOW_VERSION
from app.workflow_v3.evaluator import (
    CONTROL_PLANE_CHAIN_PATH,
    EVALUATION_REQUEST_PROTOCOL,
    EvaluationRuntimeError,
    _build_control_plane_chain_snapshot,
)
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
from app.workflow_v3.stage_entrypoint import StageEntrypointError
from app.workflow_v3.stage_evaluation_entrypoint import StageEvaluationRequest


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )


def _chain_fixture():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    WorkflowV3Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    release = WorkflowV3SkillRelease(
        release_version="stage12-chain-fixture",
        manifest_sha256="1" * 64,
        package_bucket="releases",
        package_object="worker-v3/stage12.tar.gz",
        package_sha256="2" * 64,
        workflow_version=WORKFLOW_VERSION,
        template_sha256="3" * 64,
        runtime_identity_sha256="4" * 64,
        manifest_json="{}",
        status="registered",
        registered_by="release-controller",
    )
    db.add(release)
    db.flush()
    job = WorkflowV3Job(
        public_id="stage12-chain-job",
        idempotency_key="stage12-chain-job-key",
        user_id="fixture-user",
        material_pk=4242,
        material_id="pdf-stage12-chain",
        source_popo_bucket="popo",
        source_popo_object="frozen/manifest.json",
        source_popo_sha256="5" * 64,
        workflow_version=WORKFLOW_VERSION,
        skill_release_id=release.id,
        skill_release_version=release.release_version,
        skill_release_sha256=release.manifest_sha256,
        template_sha256=release.template_sha256,
        machine_status="running",
        spec_status="in_progress",
        current_stage_key="ready_for_user_acceptance",
    )
    db.add(job)
    db.flush()

    previous_promotion = None
    previous_sha256 = job.source_popo_sha256
    for index, contract in enumerate(STAGE_CONTRACTS[:-1]):
        stage = WorkflowV3StageRun(
            workflow_job_id=job.id,
            stage_key=contract.key,
            stage_version=contract.stage_version,
            attempt=1,
            machine_status="succeeded",
            spec_status="passed",
            owner=contract.owner,
            input_kind=(
                "frozen_source"
                if previous_promotion is None
                else "promoted_artifact"
            ),
            input_promotion_id=(
                previous_promotion.id if previous_promotion is not None else None
            ),
            input_artifact_sha256=previous_sha256,
        )
        db.add(stage)
        db.flush()
        execution = WorkflowV3Execution(
            workflow_job_id=job.id,
            stage_run_id=stage.id,
            producer_identity=f"producer-{index}",
            idempotency_key=f"execution-{index}",
            machine_status="succeeded",
            skill_release_sha256=job.skill_release_sha256,
            runtime_identity_sha256=release.runtime_identity_sha256,
        )
        db.add(execution)
        db.flush()
        artifact_sha256 = _sha(f"artifact-{index}")
        bucket = "worker-v3-candidates"
        object_name = f"{job.public_id}/{contract.key}/candidate.tar.gz"
        candidate = WorkflowV3Candidate(
            workflow_job_id=job.id,
            stage_run_id=stage.id,
            execution_id=execution.id,
            idempotency_key=f"candidate-{index}",
            artifact_kind=f"{contract.key}-artifact",
            bucket=bucket,
            object_name=object_name,
            object_identity_hash=hashlib.sha256(
                f"{bucket}\n{object_name}\n{artifact_sha256}".encode("utf-8")
            ).hexdigest(),
            sha256=artifact_sha256,
            size_bytes=100 + index,
            immutable=True,
            status="promoted",
        )
        db.add(candidate)
        db.flush()
        evaluation = WorkflowV3Evaluation(
            workflow_job_id=job.id,
            stage_run_id=stage.id,
            candidate_id=candidate.id,
            idempotency_key=f"evaluation-{index}",
            evaluator_identity=f"evaluator-{index}",
            evaluator_version="fixture-evaluator-v1",
            policy_sha256=_sha(f"policy-{index}"),
            decision="passed",
            spec_passed=True,
            gate_results_json=json.dumps(
                {gate: True for gate in contract.acceptance_gates},
                sort_keys=True,
            ),
            findings_json="[]",
        )
        db.add(evaluation)
        db.flush()
        promotion = WorkflowV3Promotion(
            workflow_job_id=job.id,
            stage_run_id=stage.id,
            candidate_id=candidate.id,
            evaluation_id=evaluation.id,
            idempotency_key=f"promotion-{index}",
            artifact_sha256=candidate.sha256,
            promoted_by="promotion-controller",
        )
        db.add(promotion)
        db.flush()
        stage.promoted_candidate_id = candidate.id
        stage.promotion_id = promotion.id
        stage.promoted_artifact_sha256 = candidate.sha256
        previous_promotion = promotion
        previous_sha256 = candidate.sha256

    final_contract = STAGE_CONTRACTS[-1]
    current = WorkflowV3StageRun(
        workflow_job_id=job.id,
        stage_key=final_contract.key,
        stage_version=final_contract.stage_version,
        attempt=1,
        machine_status="awaiting_evaluation",
        spec_status="not_evaluated",
        owner=final_contract.owner,
        input_kind="promoted_artifact",
        input_promotion_id=previous_promotion.id,
        input_artifact_sha256=previous_sha256,
    )
    db.add(current)
    db.commit()
    return db, job, current


def _request_payload(
    root: Path,
    *,
    candidate: Path,
    chain: dict | None,
) -> dict:
    final = STAGE_CONTRACTS[-1]
    payload = {
        "schema_version": EVALUATION_REQUEST_PROTOCOL,
        "mode": "evaluate",
        "job_id": "stage12-chain-job",
        "stage_key": final.key,
        "stage_version": final.stage_version,
        "attempt": 1,
        "candidate": {
            "id": "99",
            "path": candidate.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
            "size_bytes": candidate.stat().st_size,
        },
        "release_manifest_sha256": "1" * 64,
        "policy_sha256": "6" * 64,
        "required_gates": list(final.acceptance_gates),
        "output_manifest": "evaluation-manifest.json",
    }
    if chain is not None:
        payload["control_plane_chain"] = chain
    return payload


def _write_snapshot(root: Path, snapshot: dict) -> dict:
    path = root / CONTROL_PLANE_CHAIN_PATH
    _json(path, snapshot)
    path.chmod(0o444)
    return {
        "path": CONTROL_PLANE_CHAIN_PATH,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size_bytes": path.stat().st_size,
    }


def test_stage12_request_exposes_detached_db_derived_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, job, current = _chain_fixture()
    snapshot = _build_control_plane_chain_snapshot(db, job=job, stage=current)
    candidate = tmp_path / "candidate" / "artifact"
    candidate.parent.mkdir()
    candidate.write_bytes(b'{"promotion_chain":"forged-candidate-value"}\n')
    descriptor = _write_snapshot(tmp_path, snapshot)
    _json(
        tmp_path / "request.json",
        _request_payload(tmp_path, candidate=candidate, chain=descriptor),
    )
    monkeypatch.chdir(tmp_path)

    request = StageEvaluationRequest.load(
        "request.json",
        expected_stage="ready_for_user_acceptance",
    )

    assert request.control_plane_chain is not None
    assert len(request.control_plane_chain.payload["promotions"]) == 11
    mutable_copy = request.control_plane_chain.payload
    mutable_copy["promotions"].clear()
    assert len(request.control_plane_chain.payload["promotions"]) == 11
    assert "forged-candidate-value" not in json.dumps(
        request.control_plane_chain.payload
    )
    db.close()


def test_stage12_control_plane_chain_db_drift_fails_closed() -> None:
    db, job, current = _chain_fixture()
    final_prior = (
        db.query(WorkflowV3Promotion)
        .order_by(WorkflowV3Promotion.id.desc())
        .first()
    )
    final_prior.artifact_sha256 = "f" * 64
    db.flush()

    with pytest.raises(EvaluationRuntimeError, match="lineage drifted"):
        _build_control_plane_chain_snapshot(db, job=job, stage=current)
    db.close()


@pytest.mark.parametrize("failure", ["missing_descriptor", "missing_file", "tamper"])
def test_stage12_control_plane_chain_missing_or_tampered_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    db, job, current = _chain_fixture()
    snapshot = _build_control_plane_chain_snapshot(db, job=job, stage=current)
    candidate = tmp_path / "candidate" / "artifact"
    candidate.parent.mkdir()
    candidate.write_bytes(b"candidate")
    descriptor = _write_snapshot(tmp_path, snapshot)
    if failure == "missing_descriptor":
        descriptor = None
    elif failure == "missing_file":
        (tmp_path / CONTROL_PLANE_CHAIN_PATH).unlink()
    else:
        path = tmp_path / CONTROL_PLANE_CHAIN_PATH
        payload = bytearray(path.read_bytes())
        payload[0] = ord("[")
        path.chmod(0o600)
        path.write_bytes(payload)
        path.chmod(0o444)
    _json(
        tmp_path / "request.json",
        _request_payload(tmp_path, candidate=candidate, chain=descriptor),
    )
    monkeypatch.chdir(tmp_path)

    with pytest.raises(StageEntrypointError):
        StageEvaluationRequest.load(
            "request.json",
            expected_stage="ready_for_user_acceptance",
        )
    db.close()


def test_non_stage12_request_prohibits_control_plane_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = tmp_path / "candidate" / "artifact"
    candidate.parent.mkdir()
    candidate.write_bytes(b"candidate")
    final = STAGE_CONTRACTS[-1]
    request = _request_payload(
        tmp_path,
        candidate=candidate,
        chain={
            "path": CONTROL_PLANE_CHAIN_PATH,
            "sha256": "7" * 64,
            "size_bytes": 1,
        },
    )
    request["stage_key"] = STAGE_CONTRACTS[0].key
    request["stage_version"] = STAGE_CONTRACTS[0].stage_version
    request["required_gates"] = list(STAGE_CONTRACTS[0].acceptance_gates)
    assert final.key != request["stage_key"]
    _json(tmp_path / "request.json", request)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(
        StageEntrypointError,
        match="missing or unknown fields",
    ):
        StageEvaluationRequest.load(
            "request.json",
            expected_stage=STAGE_CONTRACTS[0].key,
        )
