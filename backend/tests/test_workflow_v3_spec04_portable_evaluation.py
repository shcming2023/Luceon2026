from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.workflow_v3 import stage_evaluators
from app.workflow_v3.stage_entrypoint import StageEntrypointError
from app.workflow_v3.stage_evaluation_entrypoint import (
    EvaluationCandidate,
    EvaluationInput,
    StageEvaluationRequest,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _request(
    tmp_path: Path,
    *,
    stage_key: str = "outline_reconstruction",
) -> StageEvaluationRequest:
    return StageEvaluationRequest(
        job_id="job-portable-1",
        stage_key=stage_key,
        stage_version="1.0.0",
        attempt=1,
        candidate=EvaluationCandidate(
            candidate_id="candidate-1",
            path="candidate.tar.gz",
            sha256="a" * 64,
            size_bytes=1,
        ),
        release_manifest_sha256="b" * 64,
        policy_sha256="c" * 64,
        required_gates=stage_evaluators.STAGE_GATES[stage_key],
        output_manifest="evaluation-manifest.json",
        workdir=tmp_path / "evaluation-work",
    )


def _candidate(
    tmp_path: Path,
    *,
    producer_root: Path,
    target_override: Path | None = None,
    stage_key: str = "outline_reconstruction",
    filename: str = "spec04a-outline-review-bundle.json",
    resource_role: str = "book_configuration",
) -> tuple[EvaluationInput, Path]:
    root = tmp_path / "candidate"
    portable = root / "reviews" / filename
    portable.parent.mkdir(parents=True)
    portable.write_text('{"review":"frozen"}\n', encoding="utf-8")
    target = target_override or (
        producer_root
        / "job-portable-1"
        / stage_key
        / "attempt-1"
        / "projected-reviews"
        / portable.name
    )
    manifest = root / "precommit/execution_capability_manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "resources": [
                    {
                        "role": resource_role,
                        "path": str(target),
                        "sha256": _sha(portable),
                        "size_bytes": portable.stat().st_size,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return EvaluationInput(bundle_root=root, content_manifest={}), target


def test_frozen_render_plan_hydrates_its_render_policy_role(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    producer_root = tmp_path / "isolated-producer-work"
    monkeypatch.setenv("WORKFLOW_V3_PRODUCER_WORK_ROOT", str(producer_root))
    candidate, target = _candidate(
        tmp_path,
        producer_root=producer_root,
        stage_key="frozen_render_plan",
        filename="spec04d-render-policy.json",
        resource_role="render_policy",
    )

    hydrated, created = stage_evaluators._hydrate_portable_execution_configuration(
        _request(tmp_path, stage_key="frozen_render_plan"),
        candidate,
    )

    assert hydrated == target
    assert created is True
    assert hydrated.read_bytes() == (
        candidate.bundle_root / "reviews/spec04d-render-policy.json"
    ).read_bytes()


def test_portable_spec04_configuration_is_hydrated_and_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    producer_root = tmp_path / "isolated-producer-work"
    monkeypatch.setenv("WORKFLOW_V3_PRODUCER_WORK_ROOT", str(producer_root))
    candidate, target = _candidate(tmp_path, producer_root=producer_root)

    hydrated, created = stage_evaluators._hydrate_portable_execution_configuration(
        _request(tmp_path),
        candidate,
    )

    assert hydrated == target
    assert created is True
    assert hydrated.read_bytes() == (
        candidate.bundle_root / "reviews/spec04a-outline-review-bundle.json"
    ).read_bytes()
    stage_evaluators._remove_hydrated_execution_configuration(
        hydrated,
        created=created,
    )
    assert not hydrated.exists()
    assert list(producer_root.iterdir()) == []


def test_portable_spec04_configuration_rejects_out_of_scope_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    producer_root = tmp_path / "isolated-producer-work"
    monkeypatch.setenv("WORKFLOW_V3_PRODUCER_WORK_ROOT", str(producer_root))
    candidate, _ = _candidate(
        tmp_path,
        producer_root=producer_root,
        target_override=tmp_path / "outside" / "review.json",
    )

    with pytest.raises(
        StageEntrypointError,
        match="outside the bound producer attempt",
    ):
        stage_evaluators._hydrate_portable_execution_configuration(
            _request(tmp_path),
            candidate,
        )


def test_existing_producer_configuration_is_not_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    producer_root = tmp_path / "shared-producer-work"
    monkeypatch.setenv("WORKFLOW_V3_PRODUCER_WORK_ROOT", str(producer_root))
    candidate, target = _candidate(tmp_path, producer_root=producer_root)
    target.parent.mkdir(parents=True)
    target.write_bytes(
        (candidate.bundle_root / "reviews/spec04a-outline-review-bundle.json").read_bytes()
    )

    hydrated, created = stage_evaluators._hydrate_portable_execution_configuration(
        _request(tmp_path),
        candidate,
    )
    stage_evaluators._remove_hydrated_execution_configuration(
        hydrated,
        created=created,
    )

    assert created is False
    assert target.is_file()


def test_portable_spec04_configuration_rejects_hash_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    producer_root = tmp_path / "isolated-producer-work"
    monkeypatch.setenv("WORKFLOW_V3_PRODUCER_WORK_ROOT", str(producer_root))
    candidate, _ = _candidate(tmp_path, producer_root=producer_root)
    portable = candidate.bundle_root / "reviews/spec04a-outline-review-bundle.json"
    portable.write_text('{"review":"tampered"}\n', encoding="utf-8")

    with pytest.raises(
        StageEntrypointError,
        match="differs from the capability binding",
    ):
        stage_evaluators._hydrate_portable_execution_configuration(
            _request(tmp_path),
            candidate,
        )
