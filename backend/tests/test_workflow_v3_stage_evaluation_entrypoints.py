from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path
from typing import Any

import pytest

from app.workflow_v3.stage_entrypoint import BUNDLE_PROTOCOL, StageEntrypointError
from app.workflow_v3.stage_evaluation_entrypoint import (
    EVALUATION_PROTOCOL,
    EVALUATION_REQUEST_PROTOCOL,
    EvaluationInput,
    StageEvaluation,
    _validate_needs_review_findings,
    run_stage_evaluation_entrypoint,
)
from app.workflow_v3.evaluator import _load_evaluation_manifest
from app.workflow_v3.stage_evaluators import STAGE_GATES
from app.workflow_v3 import stage_evaluators


def _json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _candidate(root: Path, *, stage: str, files: dict[str, bytes]) -> Path:
    inventory = [
        {
            "path": name,
            "role": "evidence",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size_bytes": len(payload),
        }
        for name, payload in sorted(files.items())
    ]
    manifest = (
        json.dumps(
            {
                "schema_version": BUNDLE_PROTOCOL,
                "job_id": "job-1",
                "stage_key": stage,
                "stage_version": "test",
                "attempt": 1,
                "artifact_kind": "test-candidate",
                "input_sha256": "1" * 64,
                "predecessor_promotion_sha256": None,
                "release_manifest_sha256": "RELEASE_SHA",
                "files": inventory,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()
    archive = root / "candidate" / "artifact"
    archive.parent.mkdir()
    with tarfile.open(archive, "w:gz") as output:
        for name, payload in sorted(
            {**files, "candidate-content-manifest.json": manifest}.items()
        ):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            output.addfile(info, io.BytesIO(payload))
    return archive


def _request(root: Path, *, stage: str, release_sha: str, candidate: Path) -> Path:
    path = root / "request.json"
    _json(
        path,
        {
            "schema_version": EVALUATION_REQUEST_PROTOCOL,
            "mode": "evaluate",
            "job_id": "job-1",
            "stage_key": stage,
            "stage_version": "test",
            "attempt": 1,
            "candidate": {
                "id": "1",
                "path": candidate.relative_to(root).as_posix(),
                "sha256": _sha(candidate),
                "size_bytes": candidate.stat().st_size,
            },
            "release_manifest_sha256": release_sha,
            "policy_sha256": "2" * 64,
            "required_gates": list(STAGE_GATES[stage]),
            "output_manifest": "evaluation-manifest.json",
        },
    )
    return path


def test_evaluation_entrypoint_emits_only_bound_readonly_decision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = tmp_path / "release"
    release.mkdir()
    _json(release / "release-manifest.json", {"release_id": "test"})
    release_sha = _sha(release / "release-manifest.json")
    candidate = _candidate(
        tmp_path,
        stage="intake_snapshot",
        files={"evidence.json": b"{}\n"},
    )
    with tarfile.open(candidate, "r:gz") as archive:
        members = {
            item.name: archive.extractfile(item).read()
            for item in archive.getmembers()
            if item.isfile()
        }
    manifest = json.loads(members["candidate-content-manifest.json"])
    manifest["release_manifest_sha256"] = release_sha
    members["candidate-content-manifest.json"] = (
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    candidate.unlink()
    with tarfile.open(candidate, "w:gz") as output:
        for name, payload in sorted(members.items()):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            output.addfile(info, io.BytesIO(payload))
    request = _request(
        tmp_path,
        stage="intake_snapshot",
        release_sha=release_sha,
        candidate=candidate,
    )
    monkeypatch.chdir(tmp_path)

    def evaluator(request, candidate, release_root):
        assert candidate.bundle_root.stat().st_mode & 0o222 == 0
        return StageEvaluation(
            gate_results={gate: True for gate in request.required_gates},
        )

    assert (
        run_stage_evaluation_entrypoint(
            stage_key="intake_snapshot",
            request_path=request.relative_to(tmp_path),
            result_path="result.json",
            evaluator=evaluator,
            release_root=release,
        )
        == 0
    )
    result = json.loads((tmp_path / "evaluation-manifest.json").read_text())
    assert result["schema_version"] == EVALUATION_PROTOCOL
    assert result["decision"] == "passed"
    assert "candidate_artifacts" not in result
    assert "promotion" not in result
    assert "human_accepted" not in result


def test_evaluation_candidate_tampering_fails_before_evaluator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = tmp_path / "release"
    release.mkdir()
    _json(release / "release-manifest.json", {"release_id": "test"})
    release_sha = _sha(release / "release-manifest.json")
    candidate = _candidate(
        tmp_path,
        stage="intake_snapshot",
        files={"evidence.json": b"{}\n"},
    )
    request = _request(
        tmp_path,
        stage="intake_snapshot",
        release_sha=release_sha,
        candidate=candidate,
    )
    candidate.write_bytes(candidate.read_bytes() + b"tamper")
    monkeypatch.chdir(tmp_path)
    called = False

    def evaluator(*args):
        nonlocal called
        called = True
        raise AssertionError

    assert (
        run_stage_evaluation_entrypoint(
            stage_key="intake_snapshot",
            request_path=request.relative_to(tmp_path),
            result_path="result.json",
            evaluator=evaluator,
            release_root=release,
        )
        == 2
    )
    assert called is False


def test_real_evaluator_emits_evidence_bound_needs_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = tmp_path / "release"
    release.mkdir()
    _json(release / "release-manifest.json", {"release_id": "test"})
    release_sha = _sha(release / "release-manifest.json")
    evidence = b'{"source_page":1,"ambiguity":"answer-key-boundary"}\n'
    candidate = _candidate(
        tmp_path,
        stage="intake_snapshot",
        files={"evidence/source-page-1.json": evidence},
    )
    with tarfile.open(candidate, "r:gz") as archive:
        members = {
            item.name: archive.extractfile(item).read()
            for item in archive.getmembers()
            if item.isfile()
        }
    manifest = json.loads(members["candidate-content-manifest.json"])
    manifest["release_manifest_sha256"] = release_sha
    members["candidate-content-manifest.json"] = (
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    candidate.unlink()
    with tarfile.open(candidate, "w:gz") as output:
        for name, payload in sorted(members.items()):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            output.addfile(info, io.BytesIO(payload))
    request_path = _request(
        tmp_path,
        stage="intake_snapshot",
        release_sha=release_sha,
        candidate=candidate,
    )
    monkeypatch.chdir(tmp_path)

    def evaluator(request, candidate_input, release_root):
        gates = {gate: True for gate in request.required_gates}
        gates[next(iter(gates))] = False
        return StageEvaluation(
            gate_results=gates,
            disposition="needs_review",
            findings=(
                {
                    "code": "source_scope_ambiguous",
                    "blocking": True,
                    "responsible_stage": "intake_snapshot",
                    "recovery_stage": "intake_snapshot",
                    "evidence_refs": [
                        {
                            "path": "evidence/source-page-1.json",
                            "sha256": hashlib.sha256(evidence).hexdigest(),
                        }
                    ],
                    "handoff": {
                        "summary": "The body/answer-key boundary is ambiguous.",
                        "required_action": "Confirm whether source page 1 is body.",
                        "resume_stage": "intake_snapshot",
                    },
                },
            ),
        )

    assert (
        run_stage_evaluation_entrypoint(
            stage_key="intake_snapshot",
            request_path=request_path.relative_to(tmp_path),
            result_path="result.json",
            evaluator=evaluator,
            release_root=release,
        )
        == 0
    )
    output = _load_evaluation_manifest(
        tmp_path / "evaluation-manifest.json",
        job_id="job-1",
        stage_key="intake_snapshot",
        attempt=1,
        candidate_sha256=_sha(candidate),
        release_manifest_sha256=release_sha,
        policy_sha256="2" * 64,
        required_gates=list(STAGE_GATES["intake_snapshot"]),
    )
    assert output["decision"] == "needs_review"
    assert output["findings"][0]["handoff"]["resume_stage"] == "intake_snapshot"


@pytest.mark.parametrize("fault", ("hash", "handoff"))
def test_real_needs_review_rejects_unbound_evidence_or_handoff(
    tmp_path: Path,
    fault: str,
) -> None:
    bundle = tmp_path / "bundle"
    evidence = bundle / "evidence/source-page-1.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_text('{"source_page":1}\n', encoding="utf-8")
    finding = {
        "code": "source_scope_ambiguous",
        "blocking": True,
        "responsible_stage": "intake_snapshot",
        "recovery_stage": "intake_snapshot",
        "evidence_refs": [
            {
                "path": "evidence/source-page-1.json",
                "sha256": _sha(evidence),
            }
        ],
        "handoff": {
            "summary": "Source scope is ambiguous.",
            "required_action": "Confirm the page scope.",
            "resume_stage": "intake_snapshot",
        },
    }
    if fault == "hash":
        finding["evidence_refs"][0]["sha256"] = "f" * 64
    else:
        finding["handoff"]["resume_stage"] = "source_scope_and_order"
    with pytest.raises(StageEntrypointError, match="needs_review"):
        _validate_needs_review_findings(
            EvaluationInput(bundle_root=bundle, content_manifest={}),
            [finding],
        )


def test_all_stages_have_distinct_physical_producer_and_evaluator_wrappers() -> None:
    repo = Path(__file__).resolve().parents[2]
    producers = repo / "release/worker-v3/stage-entrypoints"
    evaluators = repo / "release/worker-v3/stage-evaluators"
    assert {path.stem for path in producers.glob("*.py")} == set(STAGE_GATES)
    assert {path.stem for path in evaluators.glob("*.py")} == set(STAGE_GATES)
    for stage in STAGE_GATES:
        producer = (producers / f"{stage}.py").read_text(encoding="utf-8")
        evaluator = (evaluators / f"{stage}.py").read_text(encoding="utf-8")
        assert 'WORKER_V3_ENTRYPOINT_ROLE = "producer"' in producer
        assert 'WORKER_V3_ENTRYPOINT_ROLE = "evaluator"' in evaluator
        assert f'WORKER_V3_STAGE = "{stage}"' in producer
        assert f'WORKER_V3_STAGE = "{stage}"' in evaluator
        assert producers / f"{stage}.py" != evaluators / f"{stage}.py"


def test_large_overflow_gate_counts_real_xelatex_hbox_and_vbox_syntax() -> None:
    log = "\n".join(
        (
            r"Overfull \hbox (12.0pt too wide) in paragraph at lines 1--2",
            r"Overfull \vbox (11.25pt too high) has occurred while \output is active",
            r"Overfull \hbox (10.0pt too wide) in paragraph at lines 3--4",
            r"Overfull \vbox (0.5pt too high) has occurred while \output is active",
        )
    )
    assert stage_evaluators._large_overflow_count(log) == 2  # noqa: SLF001
