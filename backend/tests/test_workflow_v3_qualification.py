from __future__ import annotations

import hashlib
import json
import shutil
import tarfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.workflow_v3.contracts import STAGE_CONTRACTS
from app.workflow_v3.executor import (
    CommandResult,
    ReleaseBindingError,
    verify_bound_release,
)
from app.workflow_v3.llm_gateway import LlmGatewayError, sha256_json
from app.workflow_v3.models import (
    WorkflowV3Job,
    WorkflowV3ReviewResolution,
    WorkflowV3SkillRelease,
)
from app.workflow_v3.release import (
    build_release_archive,
    verify_release_directory,
)
from app.workflow_v3.qualification import (
    QUALIFICATION_FIXTURE_PROTOCOL,
    QUALIFICATION_REPORT_PROTOCOL,
    FixtureReplayTransport,
    QualificationConfig,
    QualificationError,
    run_qualification,
)
from app.workflow_v3.stage_entrypoint import _write_deterministic_tar_gz
from test_workflow_v3_executor import _build_installed_release


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha_file(path: Path) -> str:
    return _sha(path.read_bytes())


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _incomplete_readonly_release(tmp_path: Path) -> Path:
    installed, _built, _manifest, _manifest_sha = (
        _build_installed_release(tmp_path, behavior="valid")
    )
    release_root = tmp_path / "qualification-release"
    shutil.copytree(installed.root, release_root)
    release_root.chmod(0o755)
    manifest_path = release_root / "release-manifest.json"
    manifest_path.chmod(0o644)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["status"] = "incomplete"
    manifest["eligibility"] = {
        "rc_eligible": False,
        "stable_eligible": False,
    }
    _write_json(manifest_path, manifest)
    manifest_path.chmod(0o444)
    release_root.chmod(0o555)
    return release_root


def _frozen_source_package(tmp_path: Path) -> tuple[Path, Path, dict]:
    package_root = tmp_path / "source-package"
    source_json = tmp_path / "source-evidence.json"
    definitions = (
        ("source_pdf", "source-pdf", "source/original.pdf"),
        (
            "mineru_manifest",
            "mineru-manifest",
            "mineru/run-1/manifest.json",
        ),
        (
            "mineru_frozen_marker",
            "mineru-frozen-marker",
            "mineru/run-1/mineru_done_frozen.json",
        ),
        ("mineru_archive", "mineru-archive", "mineru/run-1/archive.tar"),
        (
            "frozen_source",
            "popo-manifest",
            "popo/run-2/manifest.json",
        ),
        (
            "popo_frozen_marker",
            "popo-frozen-marker",
            "popo/run-2/popo_done_frozen.json",
        ),
        ("popo_archive", "popo-archive", "popo/run-2/archive.tar"),
    )
    artifacts = []
    for index, (role, kind, object_name) in enumerate(definitions, start=1):
        bucket = "qualification-frozen"
        payload = (
            json.dumps(
                {"fixture": role, "ordinal": index},
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        path = package_root / bucket / object_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        artifacts.append(
            {
                "role": role,
                "kind": kind,
                "bucket": bucket,
                "object": object_name,
                "sha256": _sha(payload),
                "size_bytes": len(payload),
                "read_only": True,
            }
        )
    by_role = {row["role"]: row for row in artifacts}

    def binding(role: str) -> dict:
        row = by_role[role]
        return {
            "bucket": row["bucket"],
            "object": row["object"],
            "sha256": row["sha256"],
            "size_bytes": row["size_bytes"],
        }

    evidence = {
        "popo_manifest": binding("frozen_source"),
        "popo_frozen_marker": binding("popo_frozen_marker"),
        "material_id": "pdf-qualification-seven",
        "run_id": "popo-qualification-run-2",
        "stage_run_ids": {
            "mineru": "mineru-qualification-run-1",
            "popo": "popo-qualification-run-2",
        },
        "source_pdf": binding("source_pdf"),
        "mineru_manifest": binding("mineru_manifest"),
        "mineru_frozen_marker": binding("mineru_frozen_marker"),
        "artifacts": artifacts,
        "verified_at": "2026-07-26T00:00:00Z",
        "input_set_sha256": sha256_json(artifacts),
        "review_asset": {
            "id": 1,
            "bucket": by_role["frozen_source"]["bucket"],
            "object": by_role["frozen_source"]["object"],
            "sha256": by_role["frozen_source"]["sha256"],
        },
    }
    _write_json(source_json, evidence)
    source_json.chmod(0o444)
    for path in package_root.rglob("*"):
        if path.is_file():
            path.chmod(0o444)
    for path in sorted(
        (item for item in package_root.rglob("*") if item.is_dir()),
        key=lambda item: len(item.parts),
        reverse=True,
    ):
        path.chmod(0o555)
    package_root.chmod(0o555)
    return package_root, source_json, evidence


class QualificationCommandFixture:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def run(
        self,
        argv,
        *,
        cwd: Path,
        timeout_seconds: int,
        heartbeat,
        cancelled,
    ) -> CommandResult:
        del argv, timeout_seconds
        assert cancelled() is False
        heartbeat()
        request = json.loads((cwd / "request.json").read_text(encoding="utf-8"))
        mode = request["mode"]
        self.calls.append((mode, request["stage_key"]))
        if mode == "produce":
            self._produce(cwd, request)
        elif mode == "evaluate":
            self._evaluate(cwd, request)
        else:
            raise AssertionError(f"unexpected qualification mode: {mode}")
        return CommandResult(0, "qualification-fixture", "", 0.001)

    @staticmethod
    def _produce(cwd: Path, request: dict) -> None:
        candidate_root = cwd / "qualification-candidate"
        stage_manifest = candidate_root / "manifests" / "stage.json"
        stage_payload = (
            json.dumps(
                {
                    "schema_version": "qualification-stage-fixture/v1",
                    "job_id": request["job_id"],
                    "stage_key": request["stage_key"],
                    "attempt": request["attempt"],
                    "input_sha256": request["input"]["sha256"],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        stage_manifest.parent.mkdir(parents=True, exist_ok=True)
        stage_manifest.write_bytes(stage_payload)
        content_manifest = {
            "schema_version": "luceon.worker-v3-candidate-bundle/v1",
            "job_id": request["job_id"],
            "stage_key": request["stage_key"],
            "stage_version": request["stage_version"],
            "attempt": request["attempt"],
            "artifact_kind": "worker-v3-candidate-bundle",
            "input_sha256": request["input"]["sha256"],
            "predecessor_promotion_sha256": (
                request["predecessor_promotion"][
                    "promotion_manifest_sha256"
                ]
                if request["predecessor_promotion"]
                else None
            ),
            "release_manifest_sha256": request["release"][
                "manifest_sha256"
            ],
            "files": [
                {
                    "path": "manifests/stage.json",
                    "role": "stage_manifest",
                    "sha256": _sha(stage_payload),
                    "size_bytes": len(stage_payload),
                }
            ],
        }
        _write_json(
            candidate_root / "candidate-content-manifest.json",
            content_manifest,
        )
        bundle_path = cwd / "output" / f"{request['stage_key']}.tar.gz"
        bundle_path.parent.mkdir(parents=True, exist_ok=True)
        _write_deterministic_tar_gz(candidate_root, bundle_path)
        _write_json(
            cwd / request["output_manifest"],
            {
                "schema_version": "luceon.worker-v3-stage-candidate/v1",
                "job_id": request["job_id"],
                "stage_key": request["stage_key"],
                "attempt": request["attempt"],
                "input_sha256": request["input"]["sha256"],
                "release_manifest_sha256": request["release"][
                    "manifest_sha256"
                ],
                "artifact": {
                    "kind": "worker-v3-candidate-bundle",
                    "path": bundle_path.relative_to(cwd).as_posix(),
                    "sha256": _sha_file(bundle_path),
                    "size_bytes": bundle_path.stat().st_size,
                },
                "metrics": {"fixture_transport": True},
            },
        )

    @staticmethod
    def _evaluate(cwd: Path, request: dict) -> None:
        _write_json(
            cwd / request["output_manifest"],
            {
                "schema_version": "luceon.worker-v3-stage-evaluation/v1",
                "job_id": request["job_id"],
                "stage_key": request["stage_key"],
                "attempt": request["attempt"],
                "candidate_sha256": request["candidate"]["sha256"],
                "release_manifest_sha256": request[
                    "release_manifest_sha256"
                ],
                "policy_sha256": request["policy_sha256"],
                "decision": "passed",
                "gate_results": {
                    gate: True for gate in request["required_gates"]
                },
                "findings": [],
            },
        )


class QualificationSpec05ReviewFixture(QualificationCommandFixture):
    warning_fingerprint = "a" * 64

    @classmethod
    def _evaluate(cls, cwd: Path, request: dict) -> None:
        if (
            request["stage_key"] == "deterministic_elegantbook"
            and request["attempt"] == 1
        ):
            _write_json(
                cwd / request["output_manifest"],
                {
                    "schema_version": (
                        "luceon.worker-v3-stage-evaluation/v1"
                    ),
                    "job_id": request["job_id"],
                    "stage_key": request["stage_key"],
                    "attempt": request["attempt"],
                    "candidate_sha256": request["candidate"]["sha256"],
                    "release_manifest_sha256": request[
                        "release_manifest_sha256"
                    ],
                    "policy_sha256": request["policy_sha256"],
                    "decision": "needs_review",
                    "gate_results": {
                        gate: gate == "xelatex_recompile_passed"
                        for gate in request["required_gates"]
                    },
                    "findings": [
                        {
                            "code": "spec05_compile_warning_review_open",
                            "blocking": True,
                            "responsible_stage": (
                                "deterministic_elegantbook"
                            ),
                            "recovery_stage": "deterministic_elegantbook",
                            "warning_fingerprints": [
                                cls.warning_fingerprint
                            ],
                            "evidence_refs": [
                                {
                                    "path": (
                                        "spec05/reports/"
                                        "compile_warnings.json"
                                    ),
                                    "sha256": "b" * 64,
                                }
                            ],
                            "handoff": {
                                "summary": "Rendered warning needs review.",
                                "required_action": (
                                    "Inspect the bound page and close the "
                                    "exact warning fingerprint."
                                ),
                                "resume_stage": (
                                    "deterministic_elegantbook"
                                ),
                            },
                        }
                    ],
                },
            )
            return
        super()._evaluate(cwd, request)


def _qualification_inputs(tmp_path: Path):
    release_root = _incomplete_readonly_release(tmp_path)
    package_root, source_json, evidence = _frozen_source_package(tmp_path)
    return release_root, package_root, source_json, evidence


def _spec05_warning_review(tmp_path: Path, fingerprint: str) -> Path:
    path = tmp_path / f"spec05-warning-review-{fingerprint[:8]}.json"
    _write_json(
        path,
        {
            "schema_version": "spec05-warning-review/1.0",
            "status": "approved",
            "closures": [
                {
                    "fingerprint": fingerprint,
                    "classification": "C2_REVIEW_REQUIRED_CLOSED",
                    "rationale": (
                        "Hash-bound rendered page inspection found no "
                        "clipping or lost content."
                    ),
                    "visual_pages": [1],
                }
            ],
        },
    )
    path.chmod(0o444)
    return path


@pytest.mark.parametrize(
    ("stop_after", "expected_stage_count"),
    [
        ("deterministic_elegantbook", 8),
        ("ready_for_user_acceptance", len(STAGE_CONTRACTS)),
    ],
)
def test_isolated_qualification_runs_normal_three_role_chain_and_hash_report(
    tmp_path,
    monkeypatch,
    stop_after,
    expected_stage_count,
):
    monkeypatch.setenv("LUCEON_ENVIRONMENT", "qualification")
    monkeypatch.delenv("WORKFLOW_V3_DATABASE_URL", raising=False)
    release_root, package_root, source_json, evidence = (
        _qualification_inputs(tmp_path)
    )
    source_hashes = {
        row["role"]: _sha_file(
            package_root / row["bucket"] / row["object"]
        )
        for row in evidence["artifacts"]
    }
    transport = QualificationCommandFixture()
    run_root = tmp_path / f"run-{stop_after}"
    result = run_qualification(
        QualificationConfig(
            release_root=release_root,
            source_package_root=package_root,
            source_evidence_json=source_json,
            run_root=run_root,
            stop_after=stop_after,
        ),
        command_transport=transport,
    )

    assert result.passed is True
    assert len(transport.calls) == expected_stage_count * 2
    assert transport.calls[::2] == [
        ("produce", contract.key)
        for contract in STAGE_CONTRACTS[:expected_stage_count]
    ]
    assert transport.calls[1::2] == [
        ("evaluate", contract.key)
        for contract in STAGE_CONTRACTS[:expected_stage_count]
    ]
    envelope = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert envelope["schema_version"] == QUALIFICATION_REPORT_PROTOCOL
    assert envelope["payload_sha256"] == sha256_json(envelope["payload"])
    assert result.report_sha256 == _sha_file(result.report_path)
    assert result.report_sha256_path.read_text(encoding="utf-8") == (
        f"{result.report_sha256}  {result.report_path.name}\n"
    )
    payload = envelope["payload"]
    assert payload["qualification"] == {
        "environment": "qualification",
        "database_backend": "sqlite",
        "artifact_backend": "directory",
        "ordinary_api_enabled": False,
        "ordinary_worker_enabled": False,
        "external_model_calls_allowed": False,
        "run_root": str(run_root),
        "database_path": str(run_root / "qualification.sqlite3"),
        "artifact_root": str(run_root / "artifacts"),
        "work_root": str(run_root / "work"),
    }
    assert payload["release"]["status"] == "incomplete"
    assert payload["outcome"]["passed"] is True
    assert payload["outcome"]["stop_condition_reached"] is True
    assert payload["outcome"]["release_promoted"] is False
    assert payload["outcome"]["production_state_written"] is False
    assert payload["model_calls"] == []
    assert payload["fixture_transport"] is None
    assert len(payload["stages"]) == expected_stage_count
    previous_sha = None
    for index, row in enumerate(payload["stages"]):
        assert row["stage"]["stage_key"] == STAGE_CONTRACTS[index].key
        assert row["stage"]["machine_status"] == "succeeded"
        assert row["execution"]["status"] == "succeeded"
        assert row["candidate"]["immutable"] is True
        assert row["candidate"]["status"] == "promoted"
        assert row["evaluation"]["decision"] == "passed"
        assert row["evaluation"]["spec_passed"] is True
        assert all(row["evaluation"]["gate_results"].values())
        assert (
            row["promotion"]["artifact_sha256"]
            == row["candidate"]["sha256"]
        )
        if previous_sha is not None:
            assert row["stage"]["input"]["sha256"] == previous_sha
        previous_sha = row["candidate"]["sha256"]
    assert {
        row["role"]: _sha_file(
            package_root / row["bucket"] / row["object"]
        )
        for row in evidence["artifacts"]
    } == source_hashes
    assert all(row["mode"] == "0444" for row in payload["source"]["artifacts"])
    assert result.report_path.stat().st_mode & 0o777 == 0o444
    assert result.report_sha256_path.stat().st_mode & 0o777 == 0o444

    engine = create_engine(
        f"sqlite+pysqlite:///{run_root / 'qualification.sqlite3'}"
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory()
    try:
        release = db.query(WorkflowV3SkillRelease).one()
        job = db.query(WorkflowV3Job).one()
        assert release.status == "qualification"
        assert job.load(job.payload_json, {})["qualification"]["enabled"] is True
        with pytest.raises(
            ReleaseBindingError,
            match="ordinary execution cannot consume qualification state",
        ):
            verify_bound_release(
                release_root,
                job=job,
                release=release,
            )
    finally:
        db.close()
        engine.dispose()


def test_qualification_closes_exact_spec05_warning_and_resumes_only_stage8(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("LUCEON_ENVIRONMENT", "qualification")
    monkeypatch.delenv("WORKFLOW_V3_DATABASE_URL", raising=False)
    release_root, package_root, source_json, _ = _qualification_inputs(
        tmp_path
    )
    transport = QualificationSpec05ReviewFixture()
    warning_review = _spec05_warning_review(
        tmp_path,
        transport.warning_fingerprint,
    )
    run_root = tmp_path / "run-spec05-review"

    result = run_qualification(
        QualificationConfig(
            release_root=release_root,
            source_package_root=package_root,
            source_evidence_json=source_json,
            run_root=run_root,
            stop_after="deterministic_elegantbook",
            spec05_warning_review_json=warning_review,
        ),
        command_transport=transport,
    )

    assert result.passed is True
    assert transport.calls[-4:] == [
        ("produce", "deterministic_elegantbook"),
        ("evaluate", "deterministic_elegantbook"),
        ("produce", "deterministic_elegantbook"),
        ("evaluate", "deterministic_elegantbook"),
    ]
    assert len(transport.calls) == (len(STAGE_CONTRACTS[:8]) * 2) + 2
    payload = json.loads(
        result.report_path.read_text(encoding="utf-8")
    )["payload"]
    assert len(payload["review_resolutions"]) == 1
    resolution = payload["review_resolutions"][0]
    assert resolution["source_generation"] == 1
    assert resolution["recovery_generation"] == 2
    assert resolution["recovery_stage"] == "deterministic_elegantbook"
    assert resolution["warning_fingerprints"] == [
        transport.warning_fingerprint
    ]
    stage = payload["stages"][-1]
    assert stage["stage"]["generation"] == 2
    assert stage["stage"]["attempt"] == 2
    assert stage["evaluation"]["decision"] == "passed"
    assert [row["evaluation_decision"] for row in stage["attempts"]] == [
        "needs_review",
        "passed",
    ]
    assert stage["attempts"][0]["promotion_id"] == ""
    assert stage["attempts"][1]["promotion_id"]

    engine = create_engine(
        f"sqlite+pysqlite:///{run_root / 'qualification.sqlite3'}"
    )
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        persisted = db.query(WorkflowV3ReviewResolution).one()
        assert persisted.manifest_sha256 == resolution["manifest_sha256"]
        assert persisted.authorized_by == "qualification-visual-reviewer"
    finally:
        db.close()
        engine.dispose()


def test_qualification_rejects_unconsumed_or_mismatched_spec05_review(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("LUCEON_ENVIRONMENT", "qualification")
    monkeypatch.delenv("WORKFLOW_V3_DATABASE_URL", raising=False)
    release_root, package_root, source_json, _ = _qualification_inputs(
        tmp_path
    )
    unused = _spec05_warning_review(tmp_path, "c" * 64)
    with pytest.raises(
        QualificationError,
        match="warning review was not consumed",
    ):
        run_qualification(
            QualificationConfig(
                release_root=release_root,
                source_package_root=package_root,
                source_evidence_json=source_json,
                run_root=tmp_path / "run-unused-review",
                spec05_warning_review_json=unused,
            ),
            command_transport=QualificationCommandFixture(),
        )

    mismatched = _spec05_warning_review(tmp_path, "d" * 64)
    with pytest.raises(
        QualificationError,
        match="does not match every open warning fingerprint",
    ):
        run_qualification(
            QualificationConfig(
                release_root=release_root,
                source_package_root=package_root,
                source_evidence_json=source_json,
                run_root=tmp_path / "run-mismatched-review",
                spec05_warning_review_json=mismatched,
            ),
            command_transport=QualificationSpec05ReviewFixture(),
        )


def test_qualification_identity_is_replayable_across_run_roots(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("LUCEON_ENVIRONMENT", "qualification")
    monkeypatch.delenv("WORKFLOW_V3_DATABASE_URL", raising=False)
    release_root, package_root, source_json, _ = _qualification_inputs(
        tmp_path
    )
    results = []
    for suffix in ("first", "second"):
        result = run_qualification(
            QualificationConfig(
                release_root=release_root,
                source_package_root=package_root,
                source_evidence_json=source_json,
                run_root=tmp_path / f"run-{suffix}",
                stop_after="deterministic_elegantbook",
            ),
            command_transport=QualificationCommandFixture(),
        )
        results.append(result)

    assert results[0].job_id == results[1].job_id


def test_qualification_preflight_rejects_wrong_environment_inherited_db_and_writable_input(
    tmp_path,
    monkeypatch,
):
    release_root, package_root, source_json, evidence = (
        _qualification_inputs(tmp_path)
    )

    def config(name: str) -> QualificationConfig:
        return QualificationConfig(
            release_root=release_root,
            source_package_root=package_root,
            source_evidence_json=source_json,
            run_root=tmp_path / name,
        )

    monkeypatch.delenv("LUCEON_ENVIRONMENT", raising=False)
    with pytest.raises(
        QualificationError,
        match="LUCEON_ENVIRONMENT=qualification",
    ):
        run_qualification(config("wrong-environment"))
    assert not (tmp_path / "wrong-environment").exists()

    monkeypatch.setenv("LUCEON_ENVIRONMENT", "qualification")
    monkeypatch.setenv(
        "WORKFLOW_V3_DATABASE_URL",
        "sqlite:////tmp/forbidden-inherited.sqlite3",
    )
    with pytest.raises(
        QualificationError,
        match="refuses an inherited WORKFLOW_V3_DATABASE_URL",
    ):
        run_qualification(config("inherited-db"))
    assert not (tmp_path / "inherited-db").exists()

    monkeypatch.delenv("WORKFLOW_V3_DATABASE_URL", raising=False)
    existing = tmp_path / "existing-run-root"
    existing.mkdir()
    with pytest.raises(
        QualificationError,
        match="must not already exist",
    ):
        run_qualification(config("existing-run-root"))

    first = evidence["artifacts"][0]
    source_path = package_root / first["bucket"] / first["object"]
    source_path.chmod(0o644)
    with pytest.raises(
        QualificationError,
        match="must be read-only|contains writable content",
    ):
        run_qualification(config("writable-source"))
    assert not (tmp_path / "writable-source").exists()


def test_fixture_replay_is_exact_hash_bound_and_has_no_fallback(
    tmp_path,
):
    request = {
        "provider": "fixture-provider",
        "model": "fixture-model",
        "payload": {"stage": "outline_reconstruction"},
    }
    fixture = tmp_path / "fixture-responses.json"
    _write_json(
        fixture,
        {
            "schema_version": QUALIFICATION_FIXTURE_PROTOCOL,
            "responses": [
                {
                    "request_sha256": sha256_json(request),
                    "provider": "fixture-provider",
                    "model": "fixture-model",
                    "response_id": "fixture-response-1",
                    "parsed_result": {"decision": "passed"},
                    "raw_response": {"fixture": True},
                    "usage": {
                        "input_tokens": 3,
                        "output_tokens": 2,
                        "total_tokens": 5,
                    },
                }
            ],
        },
    )
    fixture.chmod(0o444)
    replay = FixtureReplayTransport(fixture)
    response = replay(request, 10.0)
    assert json.loads(response.content) == {"decision": "passed"}
    assert response.raw_response == {"fixture": True}
    assert replay.report()["network_used"] is False
    with pytest.raises(
        LlmGatewayError,
        match="consumed only once",
    ) as reused:
        replay(request, 10.0)
    assert reused.value.code == "qualification_fixture_reused"
    with pytest.raises(
        LlmGatewayError,
        match="no exact response",
    ) as exc_info:
        replay({**request, "payload": {"stage": "semantic_annotation"}}, 10.0)
    assert exc_info.value.code == "qualification_fixture_missing"


def test_incomplete_builder_archive_is_materialized_only_inside_run_root(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("LUCEON_ENVIRONMENT", "qualification")
    monkeypatch.delenv("WORKFLOW_V3_DATABASE_URL", raising=False)
    release_root, package_root, source_json, _evidence = (
        _qualification_inputs(tmp_path)
    )
    expected_release = verify_release_directory(
        release_root,
        allow_incomplete=True,
    )
    archive_path = tmp_path / "incomplete-release.tar.gz"
    built = build_release_archive(release_root, archive_path)
    archive_path.chmod(0o444)
    run_root = tmp_path / "archive-run"
    result = run_qualification(
        QualificationConfig(
            release_root=None,
            release_archive=archive_path,
            release_archive_sha256=built["archive_sha256"],
            source_package_root=package_root,
            source_evidence_json=source_json,
            run_root=run_root,
            stop_after="deterministic_elegantbook",
        ),
        command_transport=QualificationCommandFixture(),
    )

    payload = json.loads(
        result.report_path.read_text(encoding="utf-8")
    )["payload"]
    release = payload["release"]
    assert release["source_kind"] == "archive"
    assert release["source_path"] == str(archive_path)
    assert release["archive_sha256"] == built["archive_sha256"]
    assert release["release_id"] == expected_release.release_id
    assert release["tree_sha256"] == expected_release.tree_sha256
    assert release["materialized_tree_sha256"] == expected_release.tree_sha256
    assert release["materialized_root"] == str(run_root / "release")
    assert (run_root / "release").stat().st_mode & 0o777 == 0o555
    assert (
        (run_root / "release" / "release-manifest.json").stat().st_mode
        & 0o777
        == 0o444
    )
    assert not any(
        path.name.startswith(".release.qualification-")
        for path in run_root.iterdir()
    )


def test_archive_preflight_rejects_external_hash_drift_and_link_without_writing_run_root(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("LUCEON_ENVIRONMENT", "qualification")
    monkeypatch.delenv("WORKFLOW_V3_DATABASE_URL", raising=False)
    release_root, package_root, source_json, _evidence = (
        _qualification_inputs(tmp_path)
    )
    valid_archive = tmp_path / "valid-incomplete.tar.gz"
    built = build_release_archive(release_root, valid_archive)
    valid_archive.chmod(0o444)
    wrong_hash_root = tmp_path / "wrong-hash-run"
    with pytest.raises(
        QualificationError,
        match="archive SHA-256 mismatch",
    ):
        run_qualification(
            QualificationConfig(
                release_root=None,
                release_archive=valid_archive,
                release_archive_sha256="0" * 64,
                source_package_root=package_root,
                source_evidence_json=source_json,
                run_root=wrong_hash_root,
            )
        )
    assert not wrong_hash_root.exists()

    malicious = tmp_path / "link-release.tar.gz"
    with tarfile.open(malicious, "w:gz") as archive:
        link = tarfile.TarInfo("release-manifest.json")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../production/release-manifest.json"
        link.mode = 0o444
        link.mtime = 0
        link.uid = 0
        link.gid = 0
        link.uname = ""
        link.gname = ""
        archive.addfile(link)
    malicious_sha = _sha_file(malicious)
    malicious.chmod(0o444)
    link_root = tmp_path / "link-run"
    with pytest.raises(
        QualificationError,
        match="links are forbidden",
    ):
        run_qualification(
            QualificationConfig(
                release_root=None,
                release_archive=malicious,
                release_archive_sha256=malicious_sha,
                source_package_root=package_root,
                source_evidence_json=source_json,
                run_root=link_root,
            )
        )
    assert not link_root.exists()
    assert built["archive_sha256"] == _sha_file(valid_archive)
