from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.workflow_v3 import spec01_04_stage_adapters as adapters
from app.workflow_v3.stage_entrypoint import (
    BUNDLE_PROTOCOL,
    KernelExecution,
    REQUEST_PROTOCOL,
    RESULT_PROTOCOL,
    ReleaseBinding,
    StageEntrypointError,
    StageInputRoot,
    StageProduction,
    StageRequest,
    prepare_input_root,
    run_stage_entrypoint,
    _verify_release_binding,
)


ZERO_SHA = "0" * 64


def _json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _canonical_sha(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _release(
    root: Path,
    *,
    prompts: list[dict[str, Any]] | None = None,
    schemas: list[dict[str, Any]] | None = None,
    model_policy: dict[str, Any] | None = None,
) -> tuple[Path, dict[str, str]]:
    release = root / "installed-release"
    release.mkdir(parents=True)
    payload = {
        "release_id": "worker-v3-test-release",
        "version": "3.0.0-test",
        "tree_hash": {"sha256": "1" * 64},
        "runtime": {"python": "3.13-test"},
        "model_policy": model_policy or {
            "mode": "release-scoped-schema-bounded-json",
            "provider": "test-provider",
            "model": "test-model",
            "request_parameters": {
                "temperature": 0,
                "max_output_tokens": 4096,
            },
        },
        "files": [],
        "prompts": prompts or [],
        "schemas": schemas or [],
    }
    _json(release / "release-manifest.json", payload)
    return release, {
        "release_id": payload["release_id"],
        "version": payload["version"],
        "manifest_sha256": _sha(release / "release-manifest.json"),
        "tree_sha256": payload["tree_hash"]["sha256"],
        "runtime_identity_sha256": _canonical_sha(payload["runtime"]),
    }


def _artifact(
    root: Path,
    *,
    role: str,
    relative: str,
    payload: bytes,
    kind: str = "evidence",
) -> dict[str, Any]:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return {
        "role": role,
        "kind": kind,
        "sha256": _sha(path),
        "size_bytes": path.stat().st_size,
        "path": relative,
        "read_only": True,
    }


def _request(
    root: Path,
    *,
    stage: str,
    release_binding: dict[str, str],
    artifacts: list[dict[str, Any]],
    parameters: dict[str, Any] | None = None,
    predecessor_stage: str | None = None,
) -> Path:
    primary = artifacts[0]
    predecessor = None
    if predecessor_stage is not None:
        promotion = next(
            row for row in artifacts if row["role"] == "predecessor_promotion_manifest"
        )
        predecessor = {
            "promotion_id": "promotion-1",
            "stage_key": predecessor_stage,
            "artifact_sha256": primary["sha256"],
            "evaluation_sha256": "3" * 64,
            "promotion_manifest_sha256": promotion["sha256"],
        }
    path = root / "request.json"
    _json(
        path,
        {
            "schema_version": REQUEST_PROTOCOL,
            "mode": "produce",
            "job_id": "job-1",
            "stage_key": stage,
            "stage_version": "1.0.0",
            "attempt": 1,
            "input": {
                "kind": primary["kind"],
                "sha256": primary["sha256"],
                "size_bytes": primary["size_bytes"],
                "path": primary["path"],
            },
            "input_artifacts": artifacts,
            "predecessor_promotion": predecessor,
            "release": release_binding,
            "parameters": parameters or {},
            "output_manifest": "candidate-manifest.json",
        },
    )
    return path


def _candidate_bundle(
    root: Path,
    *,
    relative: str,
    release_sha: str,
    stage: str,
    files: dict[str, bytes],
) -> dict[str, Any]:
    inventory = [
        {
            "path": name,
            "role": "evidence",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size_bytes": len(payload),
        }
        for name, payload in sorted(files.items())
    ]
    manifest = json.dumps(
        {
            "schema_version": BUNDLE_PROTOCOL,
            "job_id": "job-1",
            "stage_key": stage,
            "stage_version": "1.0.0",
            "attempt": 1,
            "artifact_kind": "test-candidate",
            "input_sha256": "4" * 64,
            "predecessor_promotion_sha256": "5" * 64,
            "release_manifest_sha256": release_sha,
            "files": inventory,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode() + b"\n"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as archive:
        for name, payload in sorted(
            {**files, "candidate-content-manifest.json": manifest}.items()
        ):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return {
        "role": "promoted_predecessor",
        "kind": "worker-v3-candidate-bundle",
        "sha256": _sha(path),
        "size_bytes": path.stat().st_size,
        "path": relative,
        "read_only": True,
    }


def _failure_code(path: Path) -> str:
    result = json.loads(path.read_text(encoding="utf-8"))
    assert result["schema_version"] == RESULT_PROTOCOL
    assert result["status"] == "failed"
    return result["findings"][0]["code"]


def test_common_protocol_emits_deterministic_candidate_only_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hashes: list[str] = []
    for name in ("a", "b"):
        root = tmp_path / name
        root.mkdir()
        release, binding = _release(root)
        source = _artifact(
            root,
            role="frozen_source",
            relative="inputs/source.bin",
            payload=b"frozen\n",
        )
        request = _request(
            root,
            stage="intake_snapshot",
            release_binding=binding,
            artifacts=[source],
        )

        def producer(request, inputs, output, release_root):
            _json(output / "evidence.json", {"status": "candidate"})
            return StageProduction(
                artifact_kind="worker-v3-test-candidate",
                metrics={"promotion_status": "not_evaluated"},
            )

        monkeypatch.chdir(root)
        assert (
            run_stage_entrypoint(
                stage_key="intake_snapshot",
                request_path=request,
                result_path="result.json",
                producer=producer,
                release_root=release,
                first_stage=True,
            )
            == 0
        )
        result = json.loads((root / "result.json").read_text(encoding="utf-8"))
        assert result["status"] == "candidate_ready"
        assert "decision" not in result
        assert "promotion" not in result
        hashes.append(result["candidate_artifacts"][0]["sha256"])
    assert hashes[0] == hashes[1]


def test_legacy_single_input_request_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release, binding = _release(tmp_path)
    source = _artifact(
        tmp_path,
        role="frozen_source",
        relative="inputs/source.bin",
        payload=b"source",
    )
    request = _request(
        tmp_path,
        stage="intake_snapshot",
        release_binding=binding,
        artifacts=[source],
    )
    payload = json.loads(request.read_text(encoding="utf-8"))
    payload.pop("input_artifacts")
    _json(request, payload)
    monkeypatch.chdir(tmp_path)
    assert (
        run_stage_entrypoint(
            stage_key="intake_snapshot",
            request_path=request,
            result_path="result.json",
            producer=lambda *args: None,
            release_root=release,
            first_stage=True,
        )
        == 2
    )
    assert _failure_code(tmp_path / "result.json") == "request_shape_invalid"


def test_release_runtime_identity_drift_fails_before_producer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release, binding = _release(tmp_path)
    source = _artifact(
        tmp_path,
        role="frozen_source",
        relative="inputs/source.bin",
        payload=b"source",
    )
    binding["runtime_identity_sha256"] = ZERO_SHA
    request = _request(
        tmp_path,
        stage="intake_snapshot",
        release_binding=binding,
        artifacts=[source],
    )
    monkeypatch.chdir(tmp_path)
    assert (
        run_stage_entrypoint(
            stage_key="intake_snapshot",
            request_path=request,
            result_path="result.json",
            producer=lambda *args: pytest.fail("producer must not run"),
            release_root=release,
            first_stage=True,
        )
        == 3
    )
    assert _failure_code(tmp_path / "result.json") == "release_binding_mismatch"


def test_release_runtime_identity_uses_bound_identity_file(
    tmp_path: Path,
) -> None:
    release, binding = _release(tmp_path)
    identity = release / "runtime/ordinary-runtime-identity.json"
    identity.parent.mkdir()
    identity.write_bytes(b'{"schema":"runtime-identity/v1"}\n')
    manifest_path = release / "release-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["runtime"]["system_tools"] = {
        "identity": "runtime/ordinary-runtime-identity.json",
    }
    manifest["files"] = [
        {
            "path": "runtime/ordinary-runtime-identity.json",
            "sha256": _sha(identity),
        },
    ]
    _json(manifest_path, manifest)
    binding["manifest_sha256"] = _sha(manifest_path)
    binding["runtime_identity_sha256"] = _sha(identity)

    _verify_release_binding(release, ReleaseBinding(**binding))


def test_promoted_input_requires_exact_artifact_and_manifest_hashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, binding = _release(tmp_path)
    primary = _candidate_bundle(
        tmp_path,
        relative="inputs/parent.bin",
        release_sha=binding["manifest_sha256"],
        stage="intake_snapshot",
        files={"evidence.json": b"{}\n"},
    )
    promotion = _artifact(
        tmp_path,
        role="predecessor_promotion_manifest",
        relative="inputs/promotion.json",
        payload=b"{}\n",
    )
    request = _request(
        tmp_path,
        stage="source_scope_and_order",
        release_binding=binding,
        artifacts=[primary, promotion],
        predecessor_stage="intake_snapshot",
    )
    payload = json.loads(request.read_text(encoding="utf-8"))
    payload["predecessor_promotion"]["artifact_sha256"] = ZERO_SHA
    _json(request, payload)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(StageEntrypointError) as error:
        StageRequest.load(request, expected_stage="source_scope_and_order")
    assert error.value.code == "predecessor_artifact_mismatch"

    payload["predecessor_promotion"]["artifact_sha256"] = primary["sha256"]
    payload["input_artifacts"] = [primary]
    _json(request, payload)
    with pytest.raises(StageEntrypointError) as error:
        StageRequest.load(request, expected_stage="source_scope_and_order")
    assert error.value.code == "predecessor_promotion_manifest_missing"


def test_request_rejects_command_and_host_path_parameters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, binding = _release(tmp_path)
    source = _artifact(
        tmp_path,
        role="frozen_source",
        relative="inputs/source.bin",
        payload=b"source",
    )
    request = _request(
        tmp_path,
        stage="intake_snapshot",
        release_binding=binding,
        artifacts=[source],
        parameters={"shell": "python", "skill": "/Users/test/.codex/skills/example"},
    )
    monkeypatch.chdir(tmp_path)
    with pytest.raises(StageEntrypointError) as error:
        StageRequest.load(request, expected_stage="intake_snapshot", first_stage=True)
    assert error.value.code == "unsafe_parameter_control"


def test_input_symlink_is_rejected_before_hash_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, binding = _release(tmp_path)
    target = tmp_path / "inputs/source.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"source")
    linked = tmp_path / "inputs/source-link.bin"
    os.symlink(target, linked)
    source = {
        "role": "frozen_source",
        "kind": "source-pdf",
        "sha256": _sha(target),
        "size_bytes": target.stat().st_size,
        "path": "inputs/source-link.bin",
        "read_only": True,
    }
    request = _request(
        tmp_path,
        stage="intake_snapshot",
        release_binding=binding,
        artifacts=[source],
    )
    monkeypatch.chdir(tmp_path)
    with pytest.raises(StageEntrypointError) as error:
        StageRequest.load(request, expected_stage="intake_snapshot", first_stage=True)
    assert error.value.code == "unsafe_input_path"


def test_candidate_bundle_rejects_path_traversal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, binding = _release(tmp_path)
    bundle_path = tmp_path / "inputs" / "parent.tar.gz"
    bundle_path.parent.mkdir(parents=True)
    with tarfile.open(bundle_path, "w:gz") as archive:
        payload = b"escape"
        info = tarfile.TarInfo("../escape")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    primary = {
        "role": "promoted_predecessor",
        "kind": "worker-v3-candidate-bundle",
        "sha256": _sha(bundle_path),
        "size_bytes": bundle_path.stat().st_size,
        "path": "inputs/parent.tar.gz",
        "read_only": True,
    }
    promotion = _artifact(
        tmp_path,
        role="predecessor_promotion_manifest",
        relative="inputs/promotion.json",
        payload=b"{}\n",
    )
    request_path = _request(
        tmp_path,
        stage="source_scope_and_order",
        release_binding=binding,
        artifacts=[primary, promotion],
        predecessor_stage="intake_snapshot",
    )
    monkeypatch.chdir(tmp_path)
    request = StageRequest.load(request_path, expected_stage="source_scope_and_order")
    with pytest.raises(StageEntrypointError) as error:
        prepare_input_root(request)
    assert error.value.code == "unsafe_relative_path"
    assert not (tmp_path / "escape").exists()


def test_candidate_bundle_rejects_wrong_internal_stage_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, binding = _release(tmp_path)
    primary = _candidate_bundle(
        tmp_path,
        relative="inputs/parent.tar.gz",
        release_sha=binding["manifest_sha256"],
        stage="semantic_annotation",
        files={"evidence.json": b"{}\n"},
    )
    promotion = _artifact(
        tmp_path,
        role="predecessor_promotion_manifest",
        relative="inputs/promotion.json",
        payload=b"{}\n",
    )
    request_path = _request(
        tmp_path,
        stage="semantic_annotation",
        release_binding=binding,
        artifacts=[primary, promotion],
        predecessor_stage="outline_reconstruction",
    )
    monkeypatch.chdir(tmp_path)
    request = StageRequest.load(request_path, expected_stage="semantic_annotation")
    with pytest.raises(StageEntrypointError) as error:
        prepare_input_root(request)
    assert error.value.code == "input_bundle_binding_mismatch"


def test_atomic_storage_policy_rejects_frozen_binary_and_thumbnail_explosion(
    tmp_path: Path,
) -> None:
    frozen = tmp_path / "frozen"
    frozen.mkdir()
    (frozen / "source.pdf").write_bytes(b"%PDF-1.4\n")
    with pytest.raises(StageEntrypointError) as frozen_error:
        adapters._verify_atomic_storage_policy(frozen, stage="intake_snapshot")
    assert frozen_error.value.code == "candidate_storage_policy_violation"

    thumbnails = tmp_path / "thumbnails/evidence/risk-page-thumbnails"
    thumbnails.mkdir(parents=True)
    for index in range(13):
        (thumbnails / f"page-{index:06d}.png").write_bytes(b"png")
    with pytest.raises(StageEntrypointError) as thumbnail_error:
        adapters._verify_atomic_storage_policy(
            tmp_path / "thumbnails",
            stage="source_scope_and_order",
        )
    assert thumbnail_error.value.code == "candidate_storage_policy_violation"


@pytest.mark.parametrize(
    ("stage", "predecessor"),
    [
        ("intake_snapshot", None),
        ("source_scope_and_order", "intake_snapshot"),
        ("canonical_block_ledger", "source_scope_and_order"),
    ],
)
def test_atomic_stages_fail_closed_on_incomplete_role_set_without_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    predecessor: str | None,
) -> None:
    root = tmp_path / stage
    root.mkdir()
    release, binding = _release(root)
    primary = _artifact(
        root,
        role="frozen_source",
        relative="inputs/source.bin",
        payload=b"source",
    ) if predecessor is None else _candidate_bundle(
        root,
        relative="inputs/source.tar.gz",
        release_sha=binding["manifest_sha256"],
        stage=predecessor,
        files={"evidence.json": b"{}\n"},
    )
    artifacts = [primary]
    if predecessor is not None:
        artifacts.append(
            _artifact(
                root,
                role="predecessor_promotion_manifest",
                relative="inputs/promotion.json",
                payload=b"{}\n",
            )
        )
    parameters_by_stage = {
        "intake_snapshot": {
            "run_id": "run-1",
            "decision_index_id": "decision-index-1",
            "decision_snapshot_id": "decision-snapshot-1",
            "stage_decision_id": "stage-decision-1",
        },
        "source_scope_and_order": {
            "run_id": "run-1",
            "decision_snapshot_id": "decision-snapshot-1",
            "stage_decision_id": "stage-decision-1",
            "review_binding": {},
        },
        "canonical_block_ledger": {
            "run_id": "run-1",
            "decision_snapshot_id": "decision-snapshot-1",
            "stage_decision_id": "stage-decision-1",
            "ledger_id": "ledger-1",
            "ledger_snapshot_id": "ledger-snapshot-1",
            "ledger_version": 1,
            "review_binding": {},
        },
    }
    request = _request(
        root,
        stage=stage,
        release_binding=binding,
        artifacts=artifacts,
        parameters=parameters_by_stage[stage],
        predecessor_stage=predecessor,
    )
    monkeypatch.chdir(root)
    assert (
        run_stage_entrypoint(
            stage_key=stage,
            request_path=request,
            result_path="result.json",
            producer=adapters.produce_stage,
            release_root=release,
            first_stage=predecessor is None,
        )
        == 2
    )
    assert _failure_code(root / "result.json") == "stage_input_roles_invalid"
    assert not (root / "candidate-manifest.json").exists()


def _stage4_case(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    released_prompt_hash: bool = True,
    host_reference: bool = False,
    qualified_model_policy: bool = True,
    pretty_schema: bool = False,
) -> tuple[int, Path]:
    prompt_path = root / "prompt.txt"
    schema_path = root / "schema.json"
    prompt_path.write_text("bounded prompt\n", encoding="utf-8")
    schema_value = {"type": "object"}
    if pretty_schema:
        schema_path.write_text(
            json.dumps(schema_value, indent=2) + "\n",
            encoding="utf-8",
        )
    else:
        _json(schema_path, schema_value)
    prompt_sha = _sha(prompt_path)
    schema_file_sha = _sha(schema_path)
    schema_sha = _canonical_sha(schema_value)
    release, provisional = _release(
        root,
        prompts=[
            {
                "id": "worker-v3.spec04a-outline-review",
                "version": "2.0.0",
                "path": "prompts/spec04a-outline-review-v2.txt",
                "sha256": prompt_sha,
                "output_schema": "schemas/spec04a-outline-compact-review-v1.schema.json",
            }
        ],
        schemas=[
            {
                "id": "worker-v3.spec04a-outline-compact-review",
                "version": "1.0.0",
                "path": "schemas/spec04a-outline-compact-review-v1.schema.json",
                "sha256": schema_file_sha,
            }
        ],
        model_policy=(
            None
            if qualified_model_policy
            else {
                "mode": "not-yet-qualified",
                "network_calls_allowed": False,
            }
        ),
    )
    release_prompt = release / "prompts/spec04a-outline-review-v2.txt"
    release_prompt.parent.mkdir(parents=True)
    release_prompt.write_bytes(prompt_path.read_bytes())
    release_schema = (
        release / "schemas/spec04a-outline-compact-review-v1.schema.json"
    )
    release_schema.parent.mkdir(parents=True)
    release_schema.write_bytes(schema_path.read_bytes())
    review_task = {
        "schema_version": "luceon.worker-v3-spec04a-review-task/v1",
        "stage_key": "outline_reconstruction",
        "task_id": "outline-review-test",
        "parent_binding": {
            "ledger_snapshot_id": "ledger-1",
            "ledger_payload_hash": ZERO_SHA,
            "source_pdf_sha256": ZERO_SHA,
            "promotion_id": "promotion-volatile-1",
            "promotion_manifest_sha256": "1" * 64,
        },
    }
    review = {
        "schema_version": "luceon.worker-v3-spec04a-compact-review/v1",
        "task_id": "outline-review-test",
        "review_status": "closed",
        "selected_nodes": [
            {
                "candidate_index": 0,
                "level": 0,
                "include_in_toc": True,
            }
        ],
        "open_reviews": [],
    }
    review_sha = _canonical_sha(review)
    review_input_sha = _canonical_sha(
        adapters.outline_model_evidence(review_task)
    )
    assert review_input_sha != _canonical_sha(review_task)
    raw_response = {
        "id": "response-stage4",
        "model": "test-model",
        "content": review,
    }
    audit = {
        "status": "succeeded",
        "stage_key": "outline_reconstruction",
        "release_id": provisional["release_id"],
        "release_sha256": provisional["manifest_sha256"],
        "prompt_id": "worker-v3.spec04a-outline-review",
        "prompt_version": "2.0.0",
        "prompt_sha256": prompt_sha,
        "schema_id": "worker-v3.spec04a-outline-compact-review",
        "schema_version": "1.0.0",
        "schema_sha256": schema_sha,
        "input_sha256": review_input_sha,
        "parsed_result_sha256": review_sha,
        "raw_response": raw_response,
        "raw_response_sha256": _canonical_sha(raw_response),
        "provider": "test-provider",
        "model": "test-model",
        "actual_provider": "test-provider",
        "actual_model": "test-model",
        "request_parameters_sha256": _canonical_sha(
            {
                "temperature": 0,
                "max_output_tokens": 4096,
            }
        ),
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    predecessor = _candidate_bundle(
        root,
        relative="inputs/parent.tar.gz",
        release_sha=provisional["manifest_sha256"],
        stage="canonical_block_ledger",
        files={
            "ledgers/canonical_block_ledger.jsonl": b"{}\n",
            "decisions/canonical_decision_index.json": b"{}\n",
        },
    )
    artifacts = [
        predecessor,
        _artifact(
            root,
            role="predecessor_promotion_manifest",
            relative="inputs/promotion.json",
            payload=b"{}\n",
        ),
        _artifact(
            root,
            role="promotion_registry",
            relative="inputs/registry.json",
            payload=b"{}\n",
        ),
        _artifact(
            root,
            role="source_pdf",
            relative="inputs/source.pdf",
            payload=b"%PDF-1.4\n",
            kind="source-pdf",
        ),
        _artifact(
            root,
            role="outline_review_task",
            relative="inputs/review-task.json",
            payload=(
                json.dumps(
                    review_task,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode(),
            kind="worker-v3-deterministic-review-task",
        ),
        _artifact(
            root,
            role="outline_review_decision",
            relative="inputs/review.json",
            payload=(
                json.dumps(review, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode(),
            kind="bounded-llm-result",
        ),
        _artifact(
            root,
            role="llm_call_audit",
            relative="inputs/llm-audit.json",
            payload=(
                json.dumps(audit, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode(),
            kind="bounded-llm-audit",
        ),
    ]
    request = _request(
        root,
        stage="outline_reconstruction",
        release_binding=provisional,
        artifacts=artifacts,
        predecessor_stage="canonical_block_ledger",
        parameters={
            "parent_lineage_key": "material/spec03",
            "ledger_snapshot_id": "ledger-1",
            "ledger_version": 1,
            "decision_snapshot_id": "decision-1",
            "stage_decision_id": "stage-decision-1",
            "run_id": "run-1",
            "review_binding": {
                "prompt_id": "worker-v3.spec04a-outline-review",
                "prompt_version": "2.0.0",
                "prompt_sha256": prompt_sha if released_prompt_hash else ZERO_SHA,
                "schema_id": "worker-v3.spec04a-outline-compact-review",
                "schema_version": "1.0.0",
                "schema_sha256": schema_sha,
                "input_canonical_sha256": review_input_sha,
                "result_canonical_sha256": review_sha,
                "audit_sha256": artifacts[-1]["sha256"],
            },
        },
    )

    def fake_kernel(*, args, **kwargs):
        if (
            kwargs["kernel_relative"]
            == "scripts/worker-v3/spec01_03_atomic_kernel.py"
        ):
            assert args[0] == "project-outline-review"
            projected = Path(args[args.index("--output") + 1])
            _json(
                projected,
                {
                    "schema_version": "spec04a-outline-review-bundle/1.0",
                    "review_id": "outline-review-test",
                },
            )
            return KernelExecution(
                argv=("fixed-projector",),
                returncode=0,
                stdout="",
                stderr="",
            )
        assert (
            kwargs["kernel_relative"]
            == "skills/luceon-popo-to-refined-elegantbook/scripts/spec04a_structure_contract.py"
        )
        output = Path(args[args.index("--output-dir") + 1])
        assert not output.exists()
        _json(
            output / "manifests/spec04a_structure_stage_manifest.json",
            {
                "schema_version": "spec04a-structure-stage-manifest/1.0",
                "status": "passed",
                "promotion_status": "not_evaluated",
            },
        )
        _json(
            output / "manifests/run_manifest.json",
            {"runtime": "/Users/mutable"} if host_reference else {"runtime": "release-local"},
        )
        _json(output / "structure/source_outline_ledger.json", {})
        _json(output / "structure/final_toc_plan.json", {})
        (output / "ledgers").mkdir(parents=True, exist_ok=True)
        (output / "ledgers/canonical_block_ledger.jsonl").write_text(
            "{}\n", encoding="utf-8"
        )
        _json(output / "decisions/canonical_decision_index.json", {})
        return KernelExecution(argv=("fixed-kernel",), returncode=0, stdout="", stderr="")

    monkeypatch.setattr(adapters, "run_release_python_kernel", fake_kernel)
    monkeypatch.setattr(
        adapters,
        "_materialize_native_lineage_bridge",
        lambda *, request, inputs, bindings: (
            inputs.file("promotion_registry"),
            {role: inputs.file(role) for role in bindings},
        ),
    )
    monkeypatch.setattr(
        adapters,
        "_rebind_projected_review_promotion",
        lambda *args, **kwargs: None,
    )
    monkeypatch.chdir(root)
    result_path = root / "result.json"
    status = run_stage_entrypoint(
        stage_key="outline_reconstruction",
        request_path=request,
        result_path=result_path,
        producer=adapters.produce_stage,
        release_root=release,
    )
    return status, result_path


def test_stage4_adapter_accepts_only_hash_bound_bounded_review_and_fixed_kernel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _stage4_case(tmp_path, monkeypatch)[0] == 0
    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "candidate_ready"
    assert result["metrics"]["promotion_status"] == "not_evaluated"


def test_stage4_adapter_binds_stable_model_projection_not_volatile_audit_envelope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _stage4_case(tmp_path, monkeypatch)[0] == 0


def test_stage4_adapter_separates_release_file_hash_from_canonical_schema_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _stage4_case(tmp_path, monkeypatch, pretty_schema=True)[0] == 0


def test_release_schema_binding_preserves_file_and_canonical_hashes(
    tmp_path: Path,
) -> None:
    schema = {"type": "object"}
    schema_bytes = (json.dumps(schema, indent=2) + "\n").encode()
    release, _ = _release(
        tmp_path,
        schemas=[
            {
                "id": "worker-v3.test-schema",
                "version": "1.0.0",
                "path": "schema.json",
                "sha256": hashlib.sha256(schema_bytes).hexdigest(),
            }
        ],
    )
    schema_path = release / "schema.json"
    schema_path.write_bytes(schema_bytes)

    path, file_sha, canonical_sha = adapters._release_schema_binding(
        release,
        schema_id="worker-v3.test-schema",
        schema_version="1.0.0",
    )

    assert path == "schema.json"
    assert file_sha == _sha(schema_path)
    assert canonical_sha == _canonical_sha(schema)
    assert file_sha != canonical_sha


def test_stage4_adapter_rejects_unreleased_prompt_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status, result = _stage4_case(
        tmp_path,
        monkeypatch,
        released_prompt_hash=False,
    )
    assert status == 2
    assert _failure_code(result) == "review_prompt_release_binding_missing"


def test_stage4_adapter_rejects_mutable_host_reference_in_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status, result = _stage4_case(tmp_path, monkeypatch, host_reference=True)
    assert status == 3
    assert _failure_code(result) == "candidate_host_path_reference"


def test_stage4_adapter_rejects_unqualified_llm_model_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status, result = _stage4_case(
        tmp_path,
        monkeypatch,
        qualified_model_policy=False,
    )
    assert status == 2
    assert _failure_code(result) == "llm_model_policy_unqualified"


def _native_lineage_case(
    root: Path,
    bindings: dict[str, tuple[str, str, str]],
) -> tuple[SimpleNamespace, StageInputRoot, dict[str, bytes]]:
    files_by_role: dict[str, Path] = {}
    extracted_by_role: dict[str, Path] = {}
    entries: list[dict[str, Any]] = []
    active: dict[str, dict[str, Any]] = {}
    original_bytes: dict[str, bytes] = {}
    for index, (manifest_role, (bundle_role, lineage, stage_kind)) in enumerate(
        bindings.items(),
        start=1,
    ):
        bundle = root / "materialized" / bundle_role / "bundle"
        stage_manifest = bundle / "manifests/stage.json"
        ledger = bundle / "ledgers/canonical_block_ledger.jsonl"
        decision = bundle / "decisions/canonical_decision_index.json"
        _json(stage_manifest, {"status": "passed", "stage_kind": stage_kind})
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text(f'{{"lineage":"{lineage}"}}\n', encoding="utf-8")
        _json(decision, {"lineage": lineage})
        original_root = root / "control-plane-original" / bundle_role
        promotion = {
            "schema_version": "stage-promotion-manifest/1.0",
            "promotion_id": f"promotion-{index}",
            "lineage_key": lineage,
            "stage_kind": stage_kind,
            "run_dir": str(original_root.resolve()),
            "stage_manifest": {
                "path": str((original_root / "manifests/stage.json").resolve()),
                "sha256": _sha(stage_manifest),
            },
            "disposition": "promoted",
            "promotion_class": "standard",
            "producer_execution_provenance": "control_plane_release_bound",
            "checks": [{"id": "test", "status": "passed"}],
            "summary": {"spec_passed": True},
            "promoted_artifacts": {
                "decision_index_D": {
                    "path": str(
                        (
                            original_root
                            / "decisions/canonical_decision_index.json"
                        ).resolve()
                    ),
                    "sha256": _sha(decision),
                },
                "ledger_L": {
                    "path": str(
                        (
                            original_root
                            / "ledgers/canonical_block_ledger.jsonl"
                        ).resolve()
                    ),
                    "sha256": _sha(ledger),
                },
            },
            "consumer_rule": "verify exact path and SHA-256",
        }
        promotion_path = root / "materialized" / manifest_role / "artifact"
        _json(promotion_path, promotion)
        promotion_sha = _sha(promotion_path)
        files_by_role[manifest_role] = promotion_path
        extracted_by_role[bundle_role] = bundle
        original_manifest_path = (
            root / "control-plane-inputs" / manifest_role / "artifact"
        ).resolve()
        entry = {
            "promotion_id": promotion["promotion_id"],
            "lineage_key": lineage,
            "disposition": "promoted",
            "promotion_class": "standard",
            "manifest_path": str(original_manifest_path),
            "manifest_sha256": promotion_sha,
            "run_dir": promotion["run_dir"],
            "stage_manifest_sha256": promotion["stage_manifest"]["sha256"],
        }
        entries.append(entry)
        active[lineage] = {
            "promotion_id": promotion["promotion_id"],
            "manifest_path": str(original_manifest_path),
            "manifest_sha256": promotion_sha,
            "promotion_class": "standard",
        }
        original_bytes[manifest_role] = promotion_path.read_bytes()
    registry = {
        "schema_version": "promotion-registry/1.0",
        "registry_id": "registry-test",
        "snapshot_id": "snapshot-test",
        "version": len(entries),
        "generated_at": "2026-07-29T00:00:00Z",
        "parent_registry_ref": None,
        "parent_registry_sha256": None,
        "entries": entries,
        "active_promotions": active,
        "selection_rule": "last promoted entry is active",
        "payload_hash": "",
    }
    registry["payload_hash"] = _canonical_sha(
        {
            key: value
            for key, value in registry.items()
            if key not in {"generated_at", "payload_hash"}
        }
    )
    registry_path = root / "materialized/promotion_registry/artifact"
    _json(registry_path, registry)
    files_by_role["promotion_registry"] = registry_path
    original_bytes["promotion_registry"] = registry_path.read_bytes()
    return (
        SimpleNamespace(workdir=root),
        StageInputRoot(
            root=root / "materialized",
            files_by_role=files_by_role,
            extracted_by_role=extracted_by_role,
        ),
        original_bytes,
    )


def test_native_lineage_bridge_rebinds_paths_and_registry_hash_without_mutation(
    tmp_path: Path,
) -> None:
    bindings = {
        "predecessor_promotion_manifest": (
            "promoted_predecessor",
            "material/spec03",
            "spec03_media_contract",
        )
    }
    request, inputs, originals = _native_lineage_case(tmp_path, bindings)

    registry_path, manifests = adapters._materialize_native_lineage_bridge(
        request=request,
        inputs=inputs,
        bindings=bindings,
    )

    bridged_manifest = json.loads(
        manifests["predecessor_promotion_manifest"].read_text(encoding="utf-8")
    )
    bundle = inputs.extracted("promoted_predecessor").resolve()
    assert Path(bridged_manifest["run_dir"]) == bundle
    assert (
        Path(bridged_manifest["promoted_artifacts"]["ledger_L"]["path"])
        == bundle / "ledgers/canonical_block_ledger.jsonl"
    )
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    assert registry["payload_hash"] == _canonical_sha(
        {
            key: value
            for key, value in registry.items()
            if key not in {"generated_at", "payload_hash"}
        }
    )
    active = registry["active_promotions"]["material/spec03"]
    assert Path(active["manifest_path"]) == manifests[
        "predecessor_promotion_manifest"
    ]
    assert active["manifest_sha256"] == _sha(
        manifests["predecessor_promotion_manifest"]
    )
    for role, original in originals.items():
        assert inputs.file(role).read_bytes() == original


def test_native_lineage_bridge_fails_closed_on_promoted_artifact_drift(
    tmp_path: Path,
) -> None:
    bindings = {
        "predecessor_promotion_manifest": (
            "promoted_predecessor",
            "material/spec03",
            "spec03_media_contract",
        )
    }
    request, inputs, _ = _native_lineage_case(tmp_path, bindings)
    inputs.extracted(
        "promoted_predecessor"
    ).joinpath("ledgers/canonical_block_ledger.jsonl").write_text(
        '{"tampered":true}\n',
        encoding="utf-8",
    )

    with pytest.raises(StageEntrypointError) as error:
        adapters._materialize_native_lineage_bridge(
            request=request,
            inputs=inputs,
            bindings=bindings,
        )

    assert error.value.code == "native_lineage_bridge_invalid"
    assert "hash-drifted" in str(error.value)


def test_native_lineage_bridge_rebinds_all_three_render_plan_parents(
    tmp_path: Path,
) -> None:
    bindings = {
        "predecessor_promotion_manifest": (
            "promoted_predecessor",
            "material/spec04c",
            "spec04c_construct_binding_contract",
        ),
        "structure_promotion_manifest": (
            "structure_candidate",
            "material/spec04a",
            "spec04a_structure_contract",
        ),
        "media_promotion_manifest": (
            "media_candidate",
            "material/spec03",
            "spec03_media_contract",
        ),
    }
    request, inputs, _ = _native_lineage_case(tmp_path, bindings)

    registry_path, manifests = adapters._materialize_native_lineage_bridge(
        request=request,
        inputs=inputs,
        bindings=bindings,
    )

    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    assert set(registry["active_promotions"]) == {
        "material/spec04c",
        "material/spec04a",
        "material/spec03",
    }
    assert len(manifests) == 3
    for role, (bundle_role, lineage, _) in bindings.items():
        promotion = json.loads(manifests[role].read_text(encoding="utf-8"))
        assert Path(promotion["run_dir"]) == inputs.extracted(bundle_role).resolve()
        assert Path(
            registry["active_promotions"][lineage]["manifest_path"]
        ) == manifests[role]


def test_projected_outline_review_rebinds_only_verified_promotion_hash(
    tmp_path: Path,
) -> None:
    original = tmp_path / "original-promotion.json"
    native = tmp_path / "native-promotion.json"
    _json(original, {"promotion_id": "promotion-1", "run_dir": "/original"})
    _json(native, {"promotion_id": "promotion-1", "run_dir": "/isolated"})
    review = tmp_path / "review.json"
    _json(
        review,
        {
            "schema_version": "spec04a-outline-review-bundle/1.0",
            "parent_binding": {
                "promotion_id": "promotion-1",
                "promotion_manifest_sha256": _sha(original),
                "ledger_snapshot_id": "ledger-1",
            },
            "nodes": [{"node_id": "node-1"}],
        },
    )

    adapters._rebind_projected_review_promotion(
        review,
        original_promotion=original,
        native_promotion=native,
    )

    rebound = json.loads(review.read_text(encoding="utf-8"))
    assert rebound["parent_binding"]["promotion_manifest_sha256"] == _sha(native)
    assert rebound["parent_binding"]["ledger_snapshot_id"] == "ledger-1"
    assert rebound["nodes"] == [{"node_id": "node-1"}]


def test_projected_outline_review_rejects_unbound_original_promotion(
    tmp_path: Path,
) -> None:
    original = tmp_path / "original-promotion.json"
    native = tmp_path / "native-promotion.json"
    _json(original, {"promotion_id": "promotion-1"})
    _json(native, {"promotion_id": "promotion-1"})
    review = tmp_path / "review.json"
    _json(
        review,
        {
            "parent_binding": {
                "promotion_id": "promotion-1",
                "promotion_manifest_sha256": ZERO_SHA,
            }
        },
    )

    with pytest.raises(StageEntrypointError) as error:
        adapters._rebind_projected_review_promotion(
            review,
            original_promotion=original,
            native_promotion=native,
        )

    assert error.value.code == "native_review_binding_invalid"


def test_semantic_review_projection_owns_only_deterministic_parent_binding(
    tmp_path: Path,
) -> None:
    source_pdf = tmp_path / "source.pdf"
    source_pdf.write_bytes(b"%PDF-1.4\n")
    source_sha = _sha(source_pdf)
    parent = tmp_path / "parent"
    ledger = parent / "ledgers/canonical_block_ledger.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        json.dumps(
            {
                "ledger_snapshot_id": "ledger-1",
                "current_ledger_hash": "a" * 64,
                "material_identity": {"source_pdf_sha256": source_sha},
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    promotion = tmp_path / "promotion.json"
    _json(
        promotion,
        {
            "promotion_id": "promotion-1",
            "promoted_artifacts": {
                "source_outline_ledger": {"sha256": "b" * 64},
                "final_toc_plan": {"sha256": "c" * 64},
            },
        },
    )
    review = tmp_path / "review.json"
    _json(
        review,
        {
            "schema_version": "spec04b-semantic-review-bundle/1.0",
            "parent_binding": {
                "promotion_id": "model-cannot-own-this-value",
            },
            "teaching_groups": [{"group_id": "group-1"}],
            "standalone_labels": [],
        },
    )
    output = tmp_path / "projected/review.json"

    adapters._project_review_parent_binding(
        review,
        output_path=output,
        parent_root=parent,
        source_pdf=source_pdf,
        native_promotion=promotion,
        promoted_fields={
            "source_outline_ledger_sha256": "source_outline_ledger",
            "final_toc_plan_sha256": "final_toc_plan",
        },
    )

    projected = json.loads(output.read_text(encoding="utf-8"))
    assert projected["teaching_groups"] == [{"group_id": "group-1"}]
    assert projected["parent_binding"] == {
        "ledger_snapshot_id": "ledger-1",
        "ledger_payload_hash": "a" * 64,
        "source_pdf_sha256": source_sha,
        "promotion_id": "promotion-1",
        "promotion_manifest_sha256": _sha(promotion),
        "source_outline_ledger_sha256": "b" * 64,
        "final_toc_plan_sha256": "c" * 64,
    }


def test_candidate_symlink_is_never_bundled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release, binding = _release(tmp_path)
    source = _artifact(
        tmp_path,
        role="frozen_source",
        relative="inputs/source.bin",
        payload=b"source",
    )
    request = _request(
        tmp_path,
        stage="intake_snapshot",
        release_binding=binding,
        artifacts=[source],
    )

    def producer(request, inputs, output, release_root):
        (output / "real.txt").write_text("candidate", encoding="utf-8")
        os.symlink(output / "real.txt", output / "linked.txt")
        return StageProduction(artifact_kind="unsafe-candidate")

    monkeypatch.chdir(tmp_path)
    assert (
        run_stage_entrypoint(
            stage_key="intake_snapshot",
            request_path=request,
            result_path="result.json",
            producer=producer,
            release_root=release,
            first_stage=True,
        )
        == 3
    )
    assert _failure_code(tmp_path / "result.json") == "candidate_symlink_forbidden"


def test_all_four_spec04_producer_adapters_are_explicitly_dispatched(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dispatched: list[str] = []
    mapping = {
        "outline_reconstruction": "_produce_outline",
        "semantic_annotation": "_produce_semantic",
        "template_construct_binding": "_produce_construct",
        "frozen_render_plan": "_produce_render_plan",
    }
    for stage, function_name in mapping.items():
        monkeypatch.setattr(
            adapters,
            function_name,
            lambda request, inputs, output, release, stage=stage: dispatched.append(stage),
        )
    for stage in mapping:
        request = type("Request", (), {"stage_key": stage})()
        adapters.produce_stage(request, object(), tmp_path, tmp_path)
    assert dispatched == list(mapping)
