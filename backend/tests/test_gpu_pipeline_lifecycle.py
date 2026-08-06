from __future__ import annotations

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.material import PipelineRun, PipelineRunItem
from app.services.compshare_lifecycle import LifecycleLease
from app.services.gpu_pipeline_lifecycle import (
    _safe_stop_context,
    reconcile_stale_lifecycle_leases,
    release_gpu_after_pipeline,
    remote_activity_snapshot,
)


def make_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def real_health_shape(*, queued=0, mineru_as_string=True):
    mineru = {"status": "healthy", "queued_tasks": queued, "processing_tasks": 0}
    return {
        "status": "ok",
        "queued_jobs": 0,
        "queued_batches": 0,
        "queued_mineru_batches": 0,
        "queued_popo_batches": 0,
        "mineru_health": json.dumps(mineru) if mineru_as_string else mineru,
    }


def complete_inventory(rows=None, *, total=None, next_cursor="", has_more=False, key="items"):
    rows = list(rows or [])
    return {key: rows, "total": len(rows) if total is None else total, "next_cursor": next_cursor, "has_more": has_more}


def complete_fetch(*, health=None, inventories=None):
    health = health or real_health_shape()
    inventories = inventories or {}

    def fetch(url, *, bearer_key=""):
        if url.endswith("/api/v1/health"):
            return 200, health
        for name, path in (("jobs", "/api/v1/jobs"), ("mineru_batches", "/api/v1/mineru/batches"), ("popo_batches", "/api/v1/popo/batches")):
            if path in url:
                return inventories.get(name, (200, complete_inventory()))
        raise AssertionError(url)

    return fetch


def verified_idle_snapshot():
    return {
        "verified": True,
        "idle_verified": True,
        "all_required_denominators_verified": True,
        "active_jobs": 0,
        "active_total": 0,
    }


def test_common_stop_authority_rejects_legacy_idle_summary_without_complete_denominators():
    context = _safe_stop_context(
        queue_empty=True,
        remote={"verified": True, "idle_verified": True, "active_jobs": 0},
        all_results_frozen_local=True,
        grace_elapsed=True,
    )
    assert context.remote_idle_verified is False
    assert context.remote_active_jobs == 1


def test_remote_activity_missing_bearer_never_authorizes_idle(monkeypatch):
    monkeypatch.setattr("app.services.gpu_pipeline_lifecycle._fetch_json", complete_fetch())
    result = remote_activity_snapshot("http://wrapper.test")
    assert result["verified"] is False
    assert result["idle_verified"] is False
    assert result["all_required_denominators_verified"] is False
    assert result["reason"] == "protected_inventory_bearer_missing"


def test_remote_activity_accepts_complete_authenticated_empty_inventories(monkeypatch):
    monkeypatch.setattr("app.services.gpu_pipeline_lifecycle._fetch_json", complete_fetch())
    result = remote_activity_snapshot("http://wrapper.test", bearer_key="present")
    assert result["verified"] is True
    assert result["idle_verified"] is True
    assert result["all_required_denominators_verified"] is True
    assert result["active_jobs"] == 0
    assert result["wrapper_counts"] == {
        "queued_jobs": 0,
        "queued_batches": 0,
        "queued_mineru_batches": 0,
        "queued_popo_batches": 0,
    }


def test_remote_activity_accepts_mineru_health_object(monkeypatch):
    monkeypatch.setattr("app.services.gpu_pipeline_lifecycle._fetch_json", complete_fetch(health=real_health_shape(mineru_as_string=False)))
    result = remote_activity_snapshot("http://wrapper.test", bearer_key="present")
    assert result["idle_verified"] is True


def test_remote_activity_blocks_missing_required_denominator(monkeypatch):
    payload = real_health_shape()
    payload.pop("queued_popo_batches")
    monkeypatch.setattr("app.services.gpu_pipeline_lifecycle._fetch_json", lambda *_args, **_kwargs: (200, payload))
    result = remote_activity_snapshot("http://wrapper.test", bearer_key="present")
    assert result["verified"] is False
    assert result["idle_verified"] is False


def test_remote_activity_blocks_any_wrapper_or_mineru_activity(monkeypatch):
    payload = real_health_shape(queued=1)
    payload["queued_jobs"] = 2
    monkeypatch.setattr("app.services.gpu_pipeline_lifecycle._fetch_json", complete_fetch(health=payload))
    result = remote_activity_snapshot("http://wrapper.test", bearer_key="present")
    assert result["verified"] is True
    assert result["idle_verified"] is False
    assert result["active_jobs"] == 3
    assert result["health_active_total"] == 3
    assert result["protected_active_total"] == 0


def test_remote_activity_uses_protected_inventories_without_exposing_bearer(monkeypatch):
    calls = []

    def fetch(url, *, bearer_key=""):
        calls.append((url, bool(bearer_key)))
        if url.endswith("/api/v1/health"):
            return 200, real_health_shape()
        if "/api/v1/jobs" in url:
            return 200, complete_inventory([{"id": "job-1", "status": "succeeded"}], key="jobs")
        return 200, complete_inventory([{"id": url.split("/api/v1/", 1)[1].split("?", 1)[0], "status": "failed"}], key="batches")

    monkeypatch.setattr("app.services.gpu_pipeline_lifecycle._fetch_json", fetch)
    result = remote_activity_snapshot("http://wrapper.test", bearer_key="do-not-persist")
    assert result["idle_verified"] is True
    serialized = json.dumps(result)
    assert "do-not-persist" not in serialized
    assert len(calls) == 4


def test_remote_activity_blocks_404_and_auth_failures(monkeypatch):
    for status in (401, 403, 404):
        monkeypatch.setattr(
            "app.services.gpu_pipeline_lifecycle._fetch_json",
            complete_fetch(inventories={"jobs": (status, None)}),
        )
        result = remote_activity_snapshot("http://wrapper.test", bearer_key="present")
        assert result["idle_verified"] is False
        assert result["all_required_denominators_verified"] is False
        assert f"inventory_http_{status}" in result["reason"]


def test_remote_activity_blocks_truncated_total_without_cursor(monkeypatch):
    rows = [{"id": f"job-{index}", "status": "succeeded"} for index in range(100)]
    monkeypatch.setattr(
        "app.services.gpu_pipeline_lifecycle._fetch_json",
        complete_fetch(inventories={"jobs": (200, complete_inventory(rows, total=101, key="jobs"))}),
    )
    result = remote_activity_snapshot("http://wrapper.test", bearer_key="present")
    assert result["idle_verified"] is False
    assert "inventory_pagination_incomplete" in result["reason"]


def test_remote_activity_follows_next_cursor_and_finds_active_row(monkeypatch):
    calls = []

    def fetch(url, *, bearer_key=""):
        calls.append(url)
        if url.endswith("/api/v1/health"):
            return 200, real_health_shape()
        if "/api/v1/jobs" in url:
            if "cursor=page-2" in url:
                return 200, complete_inventory([{"id": "job-2", "status": "running"}], total=2, key="jobs")
            return 200, complete_inventory([{"id": "job-1", "status": "succeeded"}], total=2, next_cursor="page-2", has_more=True, key="jobs")
        return 200, complete_inventory(key="batches")

    monkeypatch.setattr("app.services.gpu_pipeline_lifecycle._fetch_json", fetch)
    result = remote_activity_snapshot("http://wrapper.test", bearer_key="present")
    assert result["verified"] is True
    assert result["idle_verified"] is False
    assert result["active_total"] == 1
    assert any("cursor=page-2" in url for url in calls)


def test_remote_activity_blocks_duplicate_id_and_schema_drift(monkeypatch):
    duplicate_pages = [
        complete_inventory([{"id": "same", "status": "succeeded"}], total=2, next_cursor="again", has_more=True, key="jobs"),
        complete_inventory([{"id": "same", "status": "succeeded"}], total=2, key="jobs"),
    ]
    index = {"value": 0}

    def duplicate_fetch(url, *, bearer_key=""):
        if url.endswith("/api/v1/health"):
            return 200, real_health_shape()
        if "/api/v1/jobs" in url:
            value = duplicate_pages[index["value"]]
            index["value"] += 1
            return 200, value
        return 200, complete_inventory(key="batches")

    monkeypatch.setattr("app.services.gpu_pipeline_lifecycle._fetch_json", duplicate_fetch)
    assert "inventory_duplicate_id" in remote_activity_snapshot("http://wrapper.test", bearer_key="present")["reason"]
    monkeypatch.setattr(
        "app.services.gpu_pipeline_lifecycle._fetch_json",
        complete_fetch(inventories={"jobs": (200, {"jobs": []})}),
    )
    assert "inventory_total_missing" in remote_activity_snapshot("http://wrapper.test", bearer_key="present")["reason"]

    monkeypatch.setattr(
        "app.services.gpu_pipeline_lifecycle._fetch_json",
        complete_fetch(inventories={"jobs": (200, {"jobs": ["not-an-object"], "total": 0})}),
    )
    assert "inventory_row_schema_invalid" in remote_activity_snapshot("http://wrapper.test", bearer_key="present")["reason"]


def test_remote_activity_blocks_failed_protected_inventory(monkeypatch):
    def fetch(url, *, bearer_key=""):
        if url.endswith("/api/v1/health"):
            return 200, real_health_shape()
        raise TimeoutError("inventory unavailable")

    monkeypatch.setattr("app.services.gpu_pipeline_lifecycle._fetch_json", fetch)
    result = remote_activity_snapshot("http://wrapper.test", bearer_key="hidden")
    assert result["idle_verified"] is False
    assert result["reason"] == "protected_inventory_inventory unavailable"
    assert "hidden" not in json.dumps(result)


class ReaperClient:
    def __init__(self):
        self.states = iter(["Running", "Stopped"])
        self.state = "Running"
        self.stop_calls = 0

    def describe(self):
        try:
            self.state = next(self.states)
        except StopIteration:
            pass
        return {"UHostSet": [{"UHostId": "uhost-1", "State": self.state}]}

    def stop(self):
        self.stop_calls += 1
        return {"RetCode": 0}

    def start(self):
        raise AssertionError("reaper must never start")

    def update_stop_scheduler(self, _stop_time):
        raise AssertionError("scheduler is not a default reaper action")


def _owned_completed_run(db):
    lease = LifecycleLease(
        lease_id="owned",
        uhost_id="uhost-1",
        prior_state="Stopped",
        current_state="Running",
        lifecycle_owned=True,
        started_by_pipeline=True,
        acquired_at="2026-08-01T00:00:00+00:00",
        phase="ready",
    )
    run = PipelineRun(
        user_id="u1",
        status="succeeded",
        mode="apply",
        summary_json=json.dumps({"gpu_lifecycle": {"status": "ready", "lease": lease.to_dict()}}),
    )
    db.add(run)
    db.flush()
    db.add(
        PipelineRunItem(
            run_id=run.id,
            user_id="u1",
            material_pk=1,
            material_id="pdf-1",
            input_bucket="input",
            input_object="book.pdf",
            filename="book.pdf",
            status="succeeded",
            current_stage="done",
            mineru_manifest_bucket="mineru",
            mineru_manifest_object="manifest.json",
            popo_manifest_bucket="popo",
            popo_manifest_object="manifest.json",
        )
    )
    db.commit()
    return run, lease


def test_normal_release_uses_common_fail_closed_stop_authority(monkeypatch):
    monkeypatch.setenv("COMPSHARE_AUTO_STOP", "true")
    monkeypatch.setenv("COMPSHARE_STOP_GRACE_SECONDS", "0")
    db = make_session()
    run, lease = _owned_completed_run(db)
    client = ReaperClient()
    result = release_gpu_after_pipeline(
        db,
        run,
        lease,
        client_factory=lambda _config: client,
        remote_jobs_probe=lambda _url: {"verified": True, "idle_verified": True, "active_jobs": 0},
        sleep=lambda _seconds: None,
    )
    assert result["stopped"] is False
    assert "remote_idle_unverified" in result["blockers"]
    assert client.stop_calls == 0


def test_stale_owned_lease_reaper_stops_only_after_all_gates(monkeypatch):
    for name, value in {
        "COMPSHARE_LIFECYCLE_ENABLED": "true",
        "COMPSHARE_ALLOW_LEGACY_ENV": "true",
        "COMPSHARE_PUBLIC_KEY": "present",
        "COMPSHARE_PRIVATE_KEY": "present",
        "COMPSHARE_REGION": "cn-test",
        "COMPSHARE_ZONE": "cn-test-01",
        "COMPSHARE_PROJECT_ID": "org-test",
        "COMPSHARE_UHOST_ID": "uhost-1",
        "COMPSHARE_STOP_GRACE_SECONDS": "0",
        "GPU_WRAPPER_URL": "http://wrapper.test",
    }.items():
        monkeypatch.setenv(name, value)
    db = make_session()
    lease = LifecycleLease(
        lease_id="owned",
        uhost_id="uhost-1",
        prior_state="Stopped",
        current_state="Running",
        lifecycle_owned=True,
        started_by_pipeline=True,
        acquired_at="2026-08-01T00:00:00+00:00",
        phase="ready",
    )
    run = PipelineRun(
        user_id="u1",
        status="succeeded",
        mode="apply",
        summary_json=json.dumps({"gpu_lifecycle": {"status": "ready", "lease": lease.to_dict()}}),
    )
    db.add(run)
    db.flush()
    db.add(
        PipelineRunItem(
            run_id=run.id,
            user_id="u1",
            material_pk=1,
            material_id="pdf-1",
            input_bucket="input",
            input_object="book.pdf",
            filename="book.pdf",
            status="succeeded",
            current_stage="done",
            mineru_manifest_bucket="mineru",
            mineru_manifest_object="manifest.json",
            popo_manifest_bucket="popo",
            popo_manifest_object="manifest.json",
        )
    )
    db.commit()
    client = ReaperClient()
    result = reconcile_stale_lifecycle_leases(
        db,
        client_factory=lambda _config: client,
        remote_jobs_probe=lambda _url: verified_idle_snapshot(),
    )
    assert result["stopped"] == 1
    assert client.stop_calls == 1
    db.refresh(run)
    assert run.summary()["gpu_shutdown"]["status"] == "stopped"
    assert run.summary()["gpu_lifecycle"]["status"] == "stopped"
    assert run.summary()["gpu_lifecycle"]["lease"]["current_state"] == "Stopped"


def test_stale_reaper_uses_common_fail_closed_stop_authority(monkeypatch):
    for name, value in {
        "COMPSHARE_LIFECYCLE_ENABLED": "true",
        "COMPSHARE_ALLOW_LEGACY_ENV": "true",
        "COMPSHARE_PUBLIC_KEY": "present",
        "COMPSHARE_PRIVATE_KEY": "present",
        "COMPSHARE_REGION": "cn-test",
        "COMPSHARE_ZONE": "cn-test-01",
        "COMPSHARE_PROJECT_ID": "org-test",
        "COMPSHARE_UHOST_ID": "uhost-1",
        "COMPSHARE_STOP_GRACE_SECONDS": "0",
        "GPU_WRAPPER_URL": "http://wrapper.test",
    }.items():
        monkeypatch.setenv(name, value)
    db = make_session()
    _owned_completed_run(db)
    client = ReaperClient()
    result = reconcile_stale_lifecycle_leases(
        db,
        client_factory=lambda _config: client,
        remote_jobs_probe=lambda _url: {"verified": True, "idle_verified": True, "active_jobs": 0},
    )
    assert result["retained"] == 1
    assert result["stopped"] == 0
    assert client.stop_calls == 0


def test_reaper_never_stops_originally_running_instance(monkeypatch):
    monkeypatch.setenv("COMPSHARE_LIFECYCLE_ENABLED", "true")
    monkeypatch.setenv("COMPSHARE_ALLOW_LEGACY_ENV", "true")
    for name in ("PUBLIC_KEY", "PRIVATE_KEY", "REGION", "ZONE", "PROJECT_ID", "UHOST_ID"):
        monkeypatch.setenv(f"COMPSHARE_{name}", "uhost-1" if name == "UHOST_ID" else "present")
    db = make_session()
    lease = LifecycleLease(
        lease_id="unowned",
        uhost_id="uhost-1",
        prior_state="Running",
        current_state="Running",
        lifecycle_owned=False,
        started_by_pipeline=False,
        acquired_at="2026-08-01T00:00:00+00:00",
    )
    db.add(
        PipelineRun(
            user_id="u1",
            status="failed",
            mode="apply",
            summary_json=json.dumps({"gpu_lifecycle": {"status": "failed", "lease": lease.to_dict()}}),
        )
    )
    db.commit()
    client = ReaperClient()
    result = reconcile_stale_lifecycle_leases(
        db,
        client_factory=lambda _config: client,
        remote_jobs_probe=lambda _url: verified_idle_snapshot(),
    )
    assert result["examined"] == 0
    assert client.stop_calls == 0


def test_reaper_retains_invalid_lease_timestamp_without_stopping(monkeypatch):
    monkeypatch.setenv("COMPSHARE_LIFECYCLE_ENABLED", "true")
    monkeypatch.setenv("COMPSHARE_ALLOW_LEGACY_ENV", "true")
    for name in ("PUBLIC_KEY", "PRIVATE_KEY", "REGION", "ZONE", "PROJECT_ID", "UHOST_ID"):
        monkeypatch.setenv(f"COMPSHARE_{name}", "uhost-1" if name == "UHOST_ID" else "present")
    monkeypatch.setenv("GPU_WRAPPER_URL", "http://wrapper.test")
    db = make_session()
    lease = LifecycleLease(
        lease_id="bad-time",
        uhost_id="uhost-1",
        prior_state="Stopped",
        current_state="Running",
        lifecycle_owned=True,
        started_by_pipeline=True,
        acquired_at="not-a-timestamp",
    )
    run = PipelineRun(
        user_id="u1",
        status="failed",
        mode="apply",
        summary_json=json.dumps({"gpu_lifecycle": {"status": "failed", "lease": lease.to_dict()}}),
    )
    db.add(run)
    db.commit()
    client = ReaperClient()
    result = reconcile_stale_lifecycle_leases(
        db,
        client_factory=lambda _config: client,
        remote_jobs_probe=lambda _url: verified_idle_snapshot(),
    )
    assert result["invalid"] == 1
    assert result["stopped"] == 0
    assert client.stop_calls == 0
