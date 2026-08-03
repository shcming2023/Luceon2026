from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.workflow_v3.models import WorkflowV3Base, WorkflowV3SkillRelease
from app.workflow_v3.operations import operational_snapshot, record_worker_heartbeat
from test_workflow_v3_control_plane import make_job


def _db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    WorkflowV3Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def test_execution_readiness_requires_release_all_worker_roles_and_admitted_store(
    monkeypatch,
):
    db = _db()
    make_job(db)
    now = datetime(2026, 7, 26, 12, 0, 0)
    monkeypatch.setenv("WORKFLOW_V3_ARTIFACT_BACKEND", "minio")
    release_runtime = db.query(WorkflowV3SkillRelease).one().runtime_identity_sha256
    for role in ("producer", "evaluator", "promoter", "projector"):
        record_worker_heartbeat(
            db,
            worker_id=f"{role}-1",
            role=role,
            status="idle",
            runtime_identity_sha256=release_runtime,
            now=now,
        )
    snapshot = operational_snapshot(db, stale_after_seconds=60, now=now)
    assert snapshot["execution_enabled"] is True
    assert snapshot["blockers"] == []
    assert snapshot["queues"]["producer"] == 1
    assert snapshot["registered_release_count"] == 1


def test_empty_or_mismatched_runtime_heartbeat_is_not_ready(monkeypatch):
    db = _db()
    make_job(db)
    now = datetime(2026, 7, 26, 12, 0, 0)
    monkeypatch.setenv("WORKFLOW_V3_ARTIFACT_BACKEND", "minio")
    for index, role in enumerate(("producer", "evaluator", "promoter", "projector")):
        record_worker_heartbeat(
            db,
            worker_id=f"{role}-1",
            role=role,
            status="idle",
            runtime_identity_sha256="" if index == 0 else "9" * 64,
            now=now,
        )
    snapshot = operational_snapshot(db, stale_after_seconds=60, now=now)
    assert snapshot["execution_enabled"] is False
    assert "required_worker_runtime_unbound_or_mismatched" in snapshot["blockers"]
    assert all(not row["ready"] for row in snapshot["workers"].values())


def test_readiness_requires_one_runtime_staffed_across_all_roles(monkeypatch):
    db = _db()
    make_job(db)
    db.add(
        WorkflowV3SkillRelease(
            release_version="0.1.0-rc2",
            manifest_sha256="7" * 64,
            package_bucket="worker-v3-releases",
            package_object="skills/rc2/release.tar.gz",
            package_sha256="8" * 64,
            workflow_version="3.0",
            template_sha256="4" * 64,
            runtime_identity_sha256="9" * 64,
            manifest_json="{}",
            status="registered",
            registered_by="test",
        )
    )
    release_runtime = (
        db.query(WorkflowV3SkillRelease)
        .filter(WorkflowV3SkillRelease.release_version == "0.1.0-rc1")
        .one()
        .runtime_identity_sha256
    )
    now = datetime(2026, 7, 26, 12, 0, 0)
    monkeypatch.setenv("WORKFLOW_V3_ARTIFACT_BACKEND", "minio")
    runtimes = (release_runtime, release_runtime, "9" * 64, "9" * 64)
    for role, runtime_identity in zip(
        ("producer", "evaluator", "promoter", "projector"),
        runtimes,
    ):
        record_worker_heartbeat(
            db,
            worker_id=f"{role}-1",
            role=role,
            status="idle",
            runtime_identity_sha256=runtime_identity,
            now=now,
        )

    snapshot = operational_snapshot(db, stale_after_seconds=60, now=now)

    assert snapshot["execution_enabled"] is False
    assert snapshot["ready_runtime_identities"] == []
    assert "required_worker_runtime_unbound_or_mismatched" in snapshot["blockers"]


def test_stale_or_degraded_worker_and_unadmitted_directory_fail_closed(monkeypatch):
    db = _db()
    make_job(db)
    now = datetime(2026, 7, 26, 12, 0, 0)
    monkeypatch.setenv("WORKFLOW_V3_ARTIFACT_BACKEND", "directory")
    monkeypatch.delenv("WORKFLOW_V3_ALLOW_DIRECTORY_ARTIFACTS", raising=False)
    for role in ("producer", "evaluator", "promoter", "projector"):
        record_worker_heartbeat(
            db,
            worker_id=f"{role}-1",
            role=role,
            status="degraded" if role == "evaluator" else "idle",
            now=now - timedelta(minutes=2) if role == "producer" else now,
        )
    snapshot = operational_snapshot(db, stale_after_seconds=60, now=now)
    assert snapshot["execution_enabled"] is False
    assert "required_worker_role_missing_or_stale" in snapshot["blockers"]
    assert "artifact_backend_not_admitted" in snapshot["blockers"]


def test_worker_identity_cannot_change_role():
    db = _db()
    record_worker_heartbeat(
        db,
        worker_id="worker-1",
        role="producer",
        status="idle",
    )
    try:
        record_worker_heartbeat(
            db,
            worker_id="worker-1",
            role="evaluator",
            status="idle",
        )
    except ValueError as exc:
        assert "another role" in str(exc)
    else:
        raise AssertionError("worker identity role drift must fail closed")
