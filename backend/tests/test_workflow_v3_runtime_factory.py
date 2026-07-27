from __future__ import annotations

from pathlib import Path
import hashlib
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.workflow_v3.minio_artifacts import (
    MinioCandidateArtifactStore,
    MinioFormalArtifactStore,
    MinioReadonlyArtifactStore,
)
from app.workflow_v3.executor import (
    ArtifactRef,
    BoundRelease,
    PreparedInputArtifact,
    _StageRequestBuilder,
)
from app.workflow_v3.llm_gateway import sha256_json
from app.workflow_v3.models import WorkflowV3Base, WorkflowV3WorkerHeartbeat
from app.workflow_v3.minio_role_policy import (
    MINIO_ROLES,
    credential_fingerprint,
)
from app.workflow_v3.runtime_factory import (
    _ConditionalMinioClient,
    WorkerHeartbeatLoop,
    RuntimeAttestation,
    RuntimeBindingGuard,
    WorkflowV3RuntimeBindingError,
    _identity_bytes,
    WorkflowV3RuntimeConfigurationError,
    _validate_minio_credential_boundary,
    artifact_backend_mode,
    producer_artifact_store,
    projector_artifact_stores,
    readonly_artifact_store,
)


def test_directory_backend_is_explicit_and_test_only(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("WORKFLOW_V3_ARTIFACT_BACKEND", raising=False)
    with pytest.raises(
        WorkflowV3RuntimeConfigurationError,
        match="must be explicitly set",
    ):
        artifact_backend_mode()

    monkeypatch.setenv("WORKFLOW_V3_ARTIFACT_BACKEND", "directory")
    monkeypatch.setenv("WORKFLOW_V3_ARTIFACT_ROOT", str(tmp_path / "objects"))
    monkeypatch.setenv("LUCEON_ENVIRONMENT", "production")
    monkeypatch.setenv("WORKFLOW_V3_ALLOW_DIRECTORY_ARTIFACTS", "true")
    with pytest.raises(
        WorkflowV3RuntimeConfigurationError,
        match="development/test",
    ):
        artifact_backend_mode()

    monkeypatch.setenv("LUCEON_ENVIRONMENT", "test")
    assert artifact_backend_mode() == "directory"


def test_minio_roles_receive_only_their_required_capabilities(monkeypatch):
    client = object()
    monkeypatch.setenv("WORKFLOW_V3_ARTIFACT_BACKEND", "minio")
    monkeypatch.setenv("WORKFLOW_V3_CANDIDATE_BUCKET", "worker-v3-candidates")
    monkeypatch.setenv("WORKFLOW_V3_CANDIDATE_PREFIX", "candidates")
    monkeypatch.setenv("WORKFLOW_V3_FORMAL_BUCKET", "eduassets-elegantbook")
    monkeypatch.setenv("WORKFLOW_V3_FORMAL_PREFIX", "elegantbook/v3")
    monkeypatch.setattr(
        "app.workflow_v3.runtime_factory._minio_client",
        lambda _role: client,
    )

    producer = producer_artifact_store()
    evaluator = readonly_artifact_store("evaluator")
    promoter = readonly_artifact_store("promoter")
    projector = projector_artifact_stores()

    assert isinstance(producer, MinioCandidateArtifactStore)
    assert hasattr(producer, "put_candidate")
    assert not hasattr(producer, "put_formal")
    assert isinstance(evaluator, MinioReadonlyArtifactStore)
    assert isinstance(promoter, MinioReadonlyArtifactStore)
    assert not hasattr(evaluator, "put_candidate")
    assert not hasattr(evaluator, "put_formal")
    assert isinstance(projector.candidate_reader, MinioReadonlyArtifactStore)
    assert isinstance(projector.formal_writer, MinioFormalArtifactStore)
    assert not hasattr(projector.candidate_reader, "put_candidate")
    assert not hasattr(projector.formal_writer, "put_candidate")
    assert projector.formal_bucket == "eduassets-elegantbook"
    assert projector.formal_prefix == "elegantbook/v3"


def test_producer_candidate_prefix_default_and_unsafe_override(monkeypatch):
    monkeypatch.setenv("WORKFLOW_V3_ARTIFACT_BACKEND", "minio")
    monkeypatch.setenv("WORKFLOW_V3_CANDIDATE_BUCKET", "worker-v3-candidates")
    monkeypatch.delenv("WORKFLOW_V3_CANDIDATE_PREFIX", raising=False)
    monkeypatch.setattr(
        "app.workflow_v3.runtime_factory._minio_client",
        lambda _role: object(),
    )

    producer = producer_artifact_store()
    assert producer._candidate_scopes == {
        "worker-v3-candidates": ("v3/candidates/",)
    }

    monkeypatch.setenv("WORKFLOW_V3_CANDIDATE_PREFIX", "../formal")
    with pytest.raises(
        WorkflowV3RuntimeConfigurationError,
        match="CANDIDATE_PREFIX is unsafe",
    ):
        producer_artifact_store()


def test_production_minio_credentials_are_distinct_and_do_not_reuse_global(
    monkeypatch,
):
    credentials = {
        role: (f"worker-v3-{role}", f"secret-{role}")
        for role in MINIO_ROLES
    }
    matrix = ",".join(
        f"{role}:{credential_fingerprint(*credentials[role])}"
        for role in MINIO_ROLES
    )
    monkeypatch.setenv("LUCEON_ENVIRONMENT", "rc")
    monkeypatch.setenv("WORKFLOW_V3_MINIO_CREDENTIAL_FINGERPRINTS", matrix)
    monkeypatch.delenv("MINIO_ACCESS_KEY", raising=False)
    monkeypatch.delenv("MINIO_SECRET_KEY", raising=False)

    for role, (access_key, secret_key) in credentials.items():
        _validate_minio_credential_boundary(
            role=role,
            access_key=access_key,
            secret_key=secret_key,
        )

    monkeypatch.setenv("MINIO_ACCESS_KEY", credentials["producer"][0])
    with pytest.raises(
        WorkflowV3RuntimeConfigurationError,
        match="reuse a global credential",
    ):
        _validate_minio_credential_boundary(
            role="producer",
            access_key=credentials["producer"][0],
            secret_key=credentials["producer"][1],
        )

    monkeypatch.delenv("MINIO_ACCESS_KEY")
    monkeypatch.setenv(
        "WORKFLOW_V3_MINIO_CREDENTIAL_FINGERPRINTS",
        matrix.replace(
            credential_fingerprint(*credentials["promoter"]),
            credential_fingerprint(*credentials["evaluator"]),
        ),
    )
    with pytest.raises(
        WorkflowV3RuntimeConfigurationError,
        match="four distinct",
    ):
        _validate_minio_credential_boundary(
            role="promoter",
            access_key=credentials["promoter"][0],
            secret_key=credentials["promoter"][1],
        )


def test_production_minio_credentials_do_not_fall_back_when_role_config_missing(
    monkeypatch,
):
    monkeypatch.setenv("LUCEON_ENVIRONMENT", "production")
    monkeypatch.setenv("WORKFLOW_V3_ARTIFACT_BACKEND", "minio")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "global-admin")
    monkeypatch.setenv("MINIO_SECRET_KEY", "global-secret")
    monkeypatch.delenv(
        "WORKFLOW_V3_PRODUCER_MINIO_ACCESS_KEY",
        raising=False,
    )
    monkeypatch.setenv(
        "WORKFLOW_V3_PRODUCER_MINIO_ENDPOINT",
        "minio:9000",
    )
    monkeypatch.setenv(
        "WORKFLOW_V3_PRODUCER_MINIO_SECRET_KEY",
        "role-secret",
    )

    with pytest.raises(
        WorkflowV3RuntimeConfigurationError,
        match="PRODUCER_MINIO_ACCESS_KEY is required",
    ):
        producer_artifact_store()


def test_conditional_minio_client_sets_if_none_match_and_rejects_source_drift():
    calls = []

    class Client:
        def _put_object(self, bucket, object_name, payload, headers):
            calls.append((bucket, object_name, payload, headers))
            return object()

    client = _ConditionalMinioClient(Client())
    client.put_object_if_absent(
        "worker-v3-candidates",
        "v3/candidates/probe",
        __import__("io").BytesIO(b"candidate"),
        len(b"candidate"),
        metadata={"luceon-sha256": "a" * 64},
    )

    assert calls[0][3]["If-None-Match"] == "*"
    assert calls[0][3]["X-Amz-Meta-luceon-sha256"] == "a" * 64
    with pytest.raises(IOError, match="source length changed"):
        client.put_object_if_absent(
            "worker-v3-candidates",
            "v3/candidates/probe-2",
            __import__("io").BytesIO(b"short"),
            20,
        )


def test_worker_heartbeat_loop_uses_independent_sessions_and_keeps_role():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    WorkflowV3Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    heartbeat = WorkerHeartbeatLoop(
        session_factory=session_factory,
        worker_id="worker-v3-producer-test",
        role="producer",
        interval_seconds=30,
    )

    heartbeat.start()
    heartbeat.update(
        status="busy",
        runtime_identity_sha256="a" * 64,
        current_job_id="job-1",
        current_stage_key="canonical_block_ledger",
        metrics={"attempt": 2},
    )
    heartbeat.pulse()
    heartbeat.stop()

    db = session_factory()
    try:
        row = (
            db.query(WorkflowV3WorkerHeartbeat)
            .filter(
                WorkflowV3WorkerHeartbeat.worker_id
                == "worker-v3-producer-test"
            )
            .one()
        )
        assert row.role == "producer"
        assert row.status == "stopped"
        assert row.heartbeat_at is not None
    finally:
        db.close()


def test_runtime_guard_requires_exact_release_attestation_and_image(
    monkeypatch,
    tmp_path: Path,
):
    image_digest = f"sha256:{'b' * 64}"
    identity = {
        "runtime_id": "worker-v3-runtime-test",
        "image_digest": image_digest,
        "control_plane": {
            "actual_tree_sha256": "c" * 64,
            "matches": True,
        },
        "validation": {"status": "passed", "errors": []},
    }
    identity_bytes = _identity_bytes(identity)
    identity_sha256 = hashlib.sha256(identity_bytes).hexdigest()
    release_root = tmp_path / "release"
    identity_path = release_root / "runtime" / "ordinary-runtime-identity.json"
    identity_path.parent.mkdir(parents=True)
    identity_path.write_bytes(identity_bytes)
    bound = BoundRelease(
        verification=SimpleNamespace(
            root=release_root,
            manifest={
                "runtime": {
                    "system_tools": {
                        "identity": "runtime/ordinary-runtime-identity.json"
                    },
                    "container_image_digest": image_digest,
                }
            },
        ),
        manifest_sha256="a" * 64,
        runtime_identity_sha256=identity_sha256,
    )
    captured = {}

    def verify(_root, **kwargs):
        captured.update(kwargs)
        return bound

    monkeypatch.setattr(
        "app.workflow_v3.runtime_factory.verify_bound_release",
        verify,
    )
    guard = RuntimeBindingGuard(
        RuntimeAttestation(
            identity=identity,
            runtime_identity_sha256=identity_sha256,
            image_digest=image_digest,
            control_plane_tree_sha256="c" * 64,
        )
    )
    job = SimpleNamespace()
    release = SimpleNamespace()

    assert guard.assert_bound(release_root, job=job, release=release) is bound
    assert captured["actual_runtime_identity_sha256"] == identity_sha256

    identity_path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(
        WorkflowV3RuntimeBindingError,
        match="differs from the release identity",
    ):
        guard.assert_bound(release_root, job=job, release=release)


def test_production_scripts_use_runtime_capability_factories():
    root = Path(__file__).resolve().parents[1]
    evaluator = (root / "scripts/workflow_v3_evaluator.py").read_text(
        encoding="utf-8"
    )
    projector = (root / "scripts/workflow_v3_projector.py").read_text(
        encoding="utf-8"
    )
    worker = (root / "scripts/workflow_v3_worker.py").read_text(
        encoding="utf-8"
    )

    assert 'readonly_artifact_store(' in evaluator
    assert '"evaluator" if role == "evaluate" else "promoter"' in evaluator
    assert "DirectoryArtifactStore" not in evaluator
    assert "projector_artifact_stores()" in projector
    assert "producer_artifact_store()" in worker
    for source in (evaluator, projector, worker):
        assert "WorkerHeartbeatLoop(" in source
        assert "load_runtime_binding_guard()" in source
        assert "runtime_identity_sha256=" in source


def test_pretty_printed_release_schema_uses_raw_file_hash_then_canonical_call_hash(
    monkeypatch,
    tmp_path: Path,
):
    prompt_id = "worker-v3.spec04a-outline-review"
    prompt = "Return the release-bound decision as JSON."
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["decision"],
        "properties": {
            "decision": {
                "type": "string",
                "enum": ["accept"],
            }
        },
    }
    release_root = tmp_path / "release"
    prompt_path = release_root / "prompts/review.txt"
    schema_path = release_root / "schemas/review.json"
    prompt_path.parent.mkdir(parents=True)
    schema_path.parent.mkdir(parents=True)
    prompt_path.write_text(prompt, encoding="utf-8")
    schema_path.write_text(
        json.dumps(schema, ensure_ascii=False, indent=4) + "\n",
        encoding="utf-8",
    )
    raw_schema_sha256 = hashlib.sha256(schema_path.read_bytes()).hexdigest()
    canonical_schema_sha256 = sha256_json(schema)
    assert raw_schema_sha256 != canonical_schema_sha256
    manifest = {
        "prompts": [
            {
                "id": prompt_id,
                "version": "v1",
                "path": "prompts/review.txt",
                "sha256": hashlib.sha256(prompt_path.read_bytes()).hexdigest(),
                "output_schema": "schemas/review.json",
            }
        ],
        "schemas": [
            {
                "id": "review",
                "version": "v1",
                "path": "schemas/review.json",
                "sha256": raw_schema_sha256,
            }
        ],
        "model_policy": {
            "provider": "provider",
            "model": "model",
            "request_parameters": {
                "temperature": 0,
                "max_output_tokens": 1000,
            },
            "timeout_seconds": 30,
            "max_stage_calls": 1,
            "max_stage_input_tokens": 10_000,
            "max_stage_output_tokens": 1_000,
            "max_stage_request_bytes": 100_000,
            "max_output_json_bytes_per_token": 16,
            "max_stage_seconds": 60,
        },
    }
    captured: dict = {}

    def transport(request, _timeout):
        captured.update(request)
        return {
            "status_code": 200,
            "provider": "provider",
            "model": "model",
            "response_id": "response-1",
            "content": '{"decision":"accept"}',
            "usage": {"input_tokens": 5, "output_tokens": 2},
            "raw_response": {"id": "response-1"},
        }

    class _Db:
        def commit(self):
            return None

        def rollback(self):
            return None

    builder = _StageRequestBuilder(
        db=_Db(),
        session_factory=lambda: None,
        artifact_store=object(),
            job=SimpleNamespace(
                public_id="job-1",
                idempotency_key="stable-job-scope-1",
                material_id="pdf-1",
            ),
        stage=SimpleNamespace(
            stage_key="outline_reconstruction",
            attempt=1,
        ),
        release=SimpleNamespace(),
        release_root=release_root,
        bound=SimpleNamespace(
            manifest_sha256="a" * 64,
            verification=SimpleNamespace(
                release_id="worker-v3-test",
                manifest=manifest,
            ),
        ),
        workdir=tmp_path / "work",
        heartbeat=lambda: None,
    )
    builder._review_input = lambda _prompt_id, _primary: {"task": "one"}
    monkeypatch.setattr(
        "app.workflow_v3.executor.transport_from_runtime_config",
        lambda **_kwargs: transport,
    )
    monkeypatch.setattr(
        "app.workflow_v3.executor.start_model_call",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.workflow_v3.executor.finish_model_call",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.runtime_settings.load_runtime_config",
        lambda **_kwargs: {},
    )
    primary = PreparedInputArtifact(
        role="promoted_predecessor",
        kind="candidate",
        ref=ArtifactRef("bucket", "object", "b" * 64, 1),
        path="inputs/promoted_predecessor/artifact",
    )

    audit = builder._add_bounded_review(
        role="outline_review_bundle",
        prompt_id=prompt_id,
        primary=primary,
    )

    assert captured["binding"]["schema_sha256"] == canonical_schema_sha256
    assert audit["schema_sha256"] == canonical_schema_sha256
    assert captured["output_schema"] == schema


def test_stage10_builds_dynamic_visual_inputs_instead_of_release_fixtures(
    monkeypatch,
    tmp_path: Path,
):
    workdir = tmp_path / "work"
    parent = workdir / "control-plane-bundles/promoted_predecessor"
    parent.mkdir(parents=True)
    source_pdf = workdir / "control-plane-evidence/source_pdf"
    source_pdf.parent.mkdir(parents=True)
    source_pdf.write_bytes(b"%PDF-bound-source")
    generated = workdir / "generated-visual-review"
    evidence = generated / "page-review-evidence.json"
    bundle = generated / "page-render-bundle.tar.gz"
    captured: dict = {}

    def build_inputs(**kwargs):
        captured.update(kwargs)
        generated.mkdir(parents=True)
        evidence.write_text('{"schema_version":"evidence"}\n')
        bundle.write_bytes(b"render-bundle")
        return SimpleNamespace(
            evidence_path=evidence,
            render_bundle_path=bundle,
        )

    builder = _StageRequestBuilder(
        db=object(),
        session_factory=lambda: None,
        artifact_store=object(),
            job=SimpleNamespace(
                public_id="job-1",
                idempotency_key="stable-job-scope-1",
            ),
        stage=SimpleNamespace(
            stage_key="independent_full_page_review",
            stage_version="spec06.v1",
            attempt=2,
        ),
        release=SimpleNamespace(),
        release_root=tmp_path / "release",
        bound=SimpleNamespace(
            manifest_sha256="a" * 64,
            verification=SimpleNamespace(release_id="worker-v3-test"),
        ),
        workdir=workdir,
        heartbeat=lambda: None,
    )
    builder.extracted["promoted_predecessor"] = parent
    monkeypatch.setattr(
        builder,
        "_source_evidence",
        lambda: {"artifacts": []},
    )
    monkeypatch.setattr(
        builder,
        "_materialize_control_plane_source",
        lambda _role, _source: source_pdf,
    )
    monkeypatch.setattr(
        "app.workflow_v3.executor.build_full_page_review_inputs",
        build_inputs,
    )
    monkeypatch.setattr(
        "app.services.runtime_settings.load_runtime_config",
        lambda **_kwargs: {"models": {"vision": {"enabled": True}}},
    )
    primary = PreparedInputArtifact(
        role="promoted_predecessor",
        kind="worker-v3-candidate-bundle",
        ref=ArtifactRef("bucket", "object", "b" * 64, 1),
        path="inputs/promoted_predecessor/artifact",
    )
    builder.artifacts.append(primary)

    assert builder._prepare_visual_review_inputs(
        primary,
        {"promotion_manifest_sha256": "c" * 64},
    ) == {}

    assert captured["source_pdf"] == source_pdf
    assert captured["predecessor_root"] == parent
    assert captured["predecessor_sha256"] == "b" * 64
    assert captured["call_runner"] == builder._run_visual_model_call
    assert [item.role for item in builder.artifacts] == [
        "promoted_predecessor",
        "page_review_evidence",
        "page_render_bundle",
    ]
