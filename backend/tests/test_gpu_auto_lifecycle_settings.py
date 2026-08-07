from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.models.base import Base
from app.models.material import PipelineRun
from app.models.material import PipelineRunItem
from app.services import gpu_runtime_settings
from app.services.compshare_credentials import (
    CompShareCredentialError,
    load_project_credentials,
    project_secret_status,
    write_project_credentials,
)
from app.services.compshare_lifecycle import ManagedWrapperTransport
from app.services.gpu_pipeline_lifecycle import acquire_gpu_for_pipeline, release_gpu_after_pipeline
from app.services.gpu_runtime_settings import (
    GIB,
    GpuRuntimeSettingError,
    load_snapshot,
    required_gpu_disk_bytes,
    save_setting,
)
from app.services.material_inventory import apply_pipeline_resource_gate, run_cloud_deferred_pipeline_preflight
from main import app


def session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def private_secret_path(tmp_path: Path) -> Path:
    directory = tmp_path / "project-secrets"
    directory.mkdir(mode=0o700)
    return directory / "compshare.json"


def configure_identity(db, secret: Path):
    gpu_runtime_settings.PROJECT_SECRET_PATH = secret
    write_project_credentials(secret, "dummy-public", "dummy-private")
    save_setting(db, {
        "region":"cn-test", "zone":"cn-test-01", "project_id":"project-test", "uhost_id":"uhost-test",
        "ssh_host":"127.0.0.1", "automatic_enabled":True,
    }, user_id="1")
    db.commit()
    return load_snapshot(db)


class LifecycleClient:
    def __init__(self, states):
        self.states = iter(states)
        self.state = ""
        self.start_calls = 0
        self.stop_calls = 0
        self.scheduler_calls = 0
        self.scheduler_stop_time = 0
        self.guard_verification_pending = False

    def describe(self):
        if self.guard_verification_pending:
            self.guard_verification_pending = False
        else:
            try:
                self.state = next(self.states)
            except StopIteration:
                pass
        row = {"UHostId":"uhost-test","State":self.state,"ChargeType":"Postpay","InstancePrice":3.3}
        if self.scheduler_stop_time:
            row["SchedulerStopTime"] = self.scheduler_stop_time
        return {"UHostSet":[row]}

    def start(self):
        self.start_calls += 1
        return {"RetCode":0}

    def stop(self):
        self.stop_calls += 1
        return {"RetCode":0}

    def update_stop_scheduler(self, _stop_time):
        self.scheduler_calls += 1
        self.scheduler_stop_time = int(_stop_time)
        self.guard_verification_pending = True
        return {"Action":"UpdateCompShareStopSchedulerResponse", "RetCode":0, "UHostId":"uhost-test"}


class _AliveTransportProcess:
    def poll(self): return None
    def terminate(self): pass
    def wait(self, timeout=None): return 0
    def kill(self): pass


def fake_transport(_config, *, lease_id, prior=None):
    port = int((prior or {}).get("local_port") or 39123)
    return ManagedWrapperTransport(
        lease_id=lease_id, endpoint=f"http://127.0.0.1:{port}", local_port=port,
        remote_port=18080, ssh_host="fake-gpu", process=_AliveTransportProcess(),
    )


def test_project_secret_file_permissions_schema_rotation_and_no_value_status(tmp_path: Path, monkeypatch):
    path = private_secret_path(tmp_path)
    receipt = write_project_credentials(path, "public-one", "private-one")
    assert receipt == {"status":"present", "present":True, "source":"project_secret_file", "version":1, "updated_at":receipt["updated_at"]}
    assert "public-one" not in json.dumps(receipt) and "private-one" not in json.dumps(receipt)
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert oct(path.parent.stat().st_mode & 0o777) == "0o700"
    assert load_project_credentials(path).private_key == "private-one"

    original = path.read_bytes()
    monkeypatch.setattr(os, "replace", lambda *_: (_ for _ in ()).throw(OSError("injected")))
    with pytest.raises(OSError):
        write_project_credentials(path, "public-two", "private-two")
    assert path.read_bytes() == original
    assert load_project_credentials(path).private_key == "private-one"

    path.chmod(0o644)
    assert project_secret_status(path)["status"] == "invalid"


def test_project_secret_rejects_symlink_and_wrong_schema(tmp_path: Path):
    path = private_secret_path(tmp_path)
    write_project_credentials(path, "public", "private")
    link = path.parent / "link.json"; link.symlink_to(path)
    with pytest.raises(CompShareCredentialError): load_project_credentials(link)
    payload = json.loads(path.read_text()); payload["schema"] = "old"; path.write_text(json.dumps(payload)); path.chmod(0o600)
    with pytest.raises(CompShareCredentialError) as invalid: load_project_credentials(path)
    assert invalid.value.code == "credential_schema_invalid"


def test_settings_default_manual_missing_secret_and_concurrent_update(tmp_path: Path):
    db = session_factory()(); gpu_runtime_settings.PROJECT_SECRET_PATH = private_secret_path(tmp_path)
    initial = load_snapshot(db)
    assert initial.automatic_enabled is False and initial.effective_automatic is False
    assert initial.min_free_disk_bytes == 12 * GIB
    with pytest.raises(GpuRuntimeSettingError) as missing:
        save_setting(db, {"automatic_enabled":True}, user_id="1")
    assert missing.value.code == "credential_missing"
    write_project_credentials(gpu_runtime_settings.PROJECT_SECRET_PATH, "public", "private")
    row = save_setting(db, {"expected_version":0, "automatic_enabled":True, "region":"r", "zone":"z", "project_id":"p", "uhost_id":"u", "ssh_host":"h"}, user_id="1")
    db.commit(); assert row.version == 1 and load_snapshot(db).effective_automatic is True
    with pytest.raises(GpuRuntimeSettingError) as conflict: save_setting(db, {"expected_version":0,"auto_stop":False}, user_id="1")
    assert conflict.value.code == "settings_version_conflict"


@pytest.mark.parametrize(
    ("missing_field", "value"),
    [("region", ""), ("zone", ""), ("project_id", ""), ("uhost_id", ""), ("ssh_host", "")],
)
def test_automatic_enable_rejects_incomplete_identity_without_version_increment(tmp_path: Path, missing_field: str, value: str):
    db = session_factory()()
    gpu_runtime_settings.PROJECT_SECRET_PATH = private_secret_path(tmp_path)
    write_project_credentials(gpu_runtime_settings.PROJECT_SECRET_PATH, "public", "private")
    payload = {
        "automatic_enabled": True,
        "region": "r", "zone": "z", "project_id": "p", "uhost_id": "u", "ssh_host": "h", "ssh_port": 23,
    }
    payload[missing_field] = value
    with pytest.raises(GpuRuntimeSettingError) as failure:
        save_setting(db, payload, user_id="1")
    assert failure.value.code == "automatic_identity_incomplete"
    assert load_snapshot(db).version == 0
    assert load_snapshot(db).automatic_enabled is False
    assert missing_field in load_snapshot(db).automation_blockers


def test_automatic_enable_rejects_kill_switch_and_forces_auto_stop(monkeypatch, tmp_path: Path):
    db = session_factory()()
    secret = private_secret_path(tmp_path)
    gpu_runtime_settings.PROJECT_SECRET_PATH = secret
    write_project_credentials(secret, "public", "private")
    complete = {"automatic_enabled": True, "auto_stop": False, "region": "r", "zone": "z", "project_id": "p", "uhost_id": "u", "ssh_host": "h", "ssh_port": 23}
    monkeypatch.setenv("COMPSHARE_LIFECYCLE_KILL_SWITCH", "true")
    with pytest.raises(GpuRuntimeSettingError) as failure:
        save_setting(db, complete, user_id="1")
    assert failure.value.code == "automatic_kill_switch_active"
    assert load_snapshot(db).version == 0
    monkeypatch.setenv("COMPSHARE_LIFECYCLE_KILL_SWITCH", "false")
    save_setting(db, complete, user_id="1")
    db.commit()
    snapshot = load_snapshot(db)
    assert snapshot.automatic_enabled is True
    assert snapshot.effective_automatic is True
    assert snapshot.auto_stop is True
    assert snapshot.automation_blockers == ()


def test_manual_mode_never_inherits_disabled_auto_stop(tmp_path: Path):
    db = session_factory()()
    save_setting(db, {"automatic_enabled": False, "auto_stop": False}, user_id="1")
    db.commit()
    snapshot = load_snapshot(db)
    assert snapshot.automatic_enabled is False
    assert snapshot.effective_automatic is False
    assert snapshot.auto_stop is True


def test_dynamic_disk_model_accepts_small_at_observed_free_and_blocks_large(tmp_path: Path):
    db=session_factory()(); snapshot=load_snapshot(db)
    observed=14_978_945_024
    small=required_gpu_disk_bytes(snapshot,10*1024**2)
    large=required_gpu_disk_bytes(snapshot,2*GIB)
    assert small == 12*GIB and observed >= small
    assert large == 26*GIB and observed < large
    with pytest.raises(GpuRuntimeSettingError): save_setting(db,{"min_free_disk_bytes":7*GIB},user_id="1")


def test_manual_snapshot_never_constructs_cloud_client(tmp_path: Path):
    db=session_factory()(); gpu_runtime_settings.PROJECT_SECRET_PATH=private_secret_path(tmp_path)
    snapshot=load_snapshot(db)
    run=PipelineRun(user_id="1",status="running",mode="apply",idempotency_key="x",request_json=json.dumps({"apply":True,"snapshot":[],"gpu_runtime_snapshot":snapshot.public_dict()}))
    db.add(run); db.commit()
    assert acquire_gpu_for_pipeline(db,run,client_factory=lambda *_: (_ for _ in ()).throw(AssertionError("cloud called"))) is None


def test_automatic_offline_preflight_is_deferred_and_freezes_dynamic_disk_requirement(tmp_path: Path):
    db=session_factory()(); secret=private_secret_path(tmp_path)
    snapshot=configure_identity(db,secret)
    selected=[{"input_sha256":"a"*64,"input_object":"inputs/a.pdf","size_bytes":10*1024**2,"page_count":4}]
    result=run_cloud_deferred_pipeline_preflight(selected,gpu_settings=snapshot)
    assert result["ready"] is True and result["status"]=="CLOUD_LIFECYCLE_DEFERRED"
    gated=apply_pipeline_resource_gate(result,selected,snapshot)
    assert gated["resource_gate"]["status"]=="deferred_until_gpu_ready"
    assert gated["resource_gate"]["required_headroom_bytes"]==12*GIB


def test_manual_gpu_offline_preflight_is_not_rewritten_as_disk_failure(tmp_path: Path):
    db=session_factory()(); gpu_runtime_settings.PROJECT_SECRET_PATH=private_secret_path(tmp_path)
    snapshot=load_snapshot(db)
    result={"ready":False,"status":"GPU_OFFLINE","health":{}}
    selected=[{"input_sha256":"a"*64,"input_object":"inputs/a.pdf","size_bytes":10*1024**2,"page_count":4}]
    gated=apply_pipeline_resource_gate(result,selected,snapshot)
    assert gated["status"] == "GPU_OFFLINE"
    assert gated["resource_gate"]["status"] == "not_evaluated_upstream_blocked"
    assert gated["resource_gate"]["applies"] is False


def test_admin_api_never_returns_secret_and_describe_has_no_mutation(tmp_path: Path, monkeypatch):
    factory=session_factory(); secret=private_secret_path(tmp_path)
    gpu_runtime_settings.PROJECT_SECRET_PATH=secret
    import app.api.runtime_settings as api_module
    api_module.PROJECT_SECRET_PATH=secret
    def override_db():
        db=factory()
        try: yield db
        finally: db.close()
    app.dependency_overrides[get_db]=override_db
    monkeypatch.setenv("LUCEON_AUTH_DISABLED","false"); monkeypatch.setenv("LUCEON_PIPELINE_ADMIN_EMAILS","ops@example.com")
    client=TestClient(app)
    try:
        client.post("/api/auth/register",json={"email":"reader@example.com","password":"secret123"})
        assert client.get("/api/runtime/gpu/automation").status_code == 403
        client.post("/api/auth/logout")
        client.post("/api/auth/register",json={"email":"ops@example.com","password":"secret123"})
        response=client.put("/api/runtime/gpu/credentials",json={"public_key":"api-public-dummy","private_key":"api-private-dummy"})
        assert response.status_code==200 and "dummy" not in response.text
        settings=client.get("/api/runtime/gpu/automation").json()
        assert "public_key" not in json.dumps(settings) and settings["credential_status"]=="present"

        rejected = client.put("/api/runtime/gpu/automation", json={"expected_version": 0, "automatic_enabled": True})
        assert rejected.status_code == 400
        assert rejected.json()["detail"]["code"] == "automatic_identity_incomplete"
        unchanged = client.get("/api/runtime/gpu/automation").json()
        assert unchanged["version"] == 0 and unchanged["automatic_enabled"] is False
        assert {"region", "zone", "project_id", "uhost_id", "ssh_host"}.issubset(unchanged["automation_blockers"])

        enabled = client.put("/api/runtime/gpu/automation", json={"expected_version":0,"automatic_enabled":True,"auto_stop":False,"region":"cn-test","zone":"cn-test-01","project_id":"project-test","uhost_id":"uhost-test","ssh_host":"127.0.0.1","ssh_port":23})
        assert enabled.status_code == 200
        assert enabled.json()["automatic_enabled"] is True
        assert enabled.json()["effective_automatic"] is True
        assert enabled.json()["auto_stop"] is True
        assert enabled.json()["automation_blockers"] == []
        calls={"describe":0,"start":0,"stop":0,"scheduler":0}
        class DescribeOnly:
            def __init__(self,_config): pass
            def describe(self):
                calls["describe"]+=1
                return {"UHostSet":[{"UHostId":"uhost-test","State":"Stopped","ChargeType":"Hour","Price":3.3}]}
            def start(self): calls["start"]+=1
            def stop(self): calls["stop"]+=1
            def update_stop_scheduler(self,_value): calls["scheduler"]+=1
        monkeypatch.setattr(api_module,"UCloudCompShareClient",DescribeOnly)
        described=client.post("/api/runtime/gpu/describe")
        assert described.status_code==200
        assert described.json()["mutation_performed"] is False
        assert calls=={"describe":1,"start":0,"stop":0,"scheduler":0}
        assert "api-public-dummy" not in described.text and "api-private-dummy" not in described.text
    finally: app.dependency_overrides.clear()


def test_automatic_owned_lifecycle_starts_once_schedules_and_stops_after_freeze(tmp_path: Path, monkeypatch):
    db=session_factory()(); secret=private_secret_path(tmp_path)
    snapshot=configure_identity(db,secret)
    import app.services.gpu_pipeline_lifecycle as lifecycle_module
    monkeypatch.setattr(lifecycle_module,"PROJECT_SECRET_PATH",secret)
    run=PipelineRun(user_id="1",status="running",mode="apply",idempotency_key="run-1",request_json=json.dumps({"apply":True,"snapshot":[{"size_bytes":10*1024**2}],"gpu_runtime_snapshot":snapshot.public_dict()}))
    db.add(run); db.flush()
    db.add(PipelineRunItem(run_id=run.id,user_id="1",material_pk=1,material_id="m1",input_bucket="input",input_object="m1.pdf",filename="m1.pdf",status="succeeded",current_stage="done",mineru_manifest_bucket="mineru",mineru_manifest_object="m.json",popo_manifest_bucket="popo",popo_manifest_object="p.json"))
    db.commit()
    client=LifecycleClient(["Stopped","Running","Running","Stopping","Stopped"])
    lease=acquire_gpu_for_pipeline(db,run,client_factory=lambda _config:client,readiness_probe=lambda config,_url:{"ready":True,"ssh":{"disk_available_bytes":14_978_945_024,"disk_required_bytes":config.minimum_disk_bytes}},transport_factory=fake_transport)
    assert lease is not None and lease.lifecycle_owned is True
    assert client.start_calls==1 and client.scheduler_calls==1
    result=release_gpu_after_pipeline(db,run,lease,client_factory=lambda _config:client,remote_jobs_probe=lambda _url:{"verified":True,"all_required_denominators_verified":True,"idle_verified":True,"active_total":0},sleep=lambda _seconds:None)
    assert result["stopped"] is True and client.stop_calls==1


def test_pipeline_resume_reuses_persisted_guard_without_duplicate_scheduler_or_start(tmp_path: Path, monkeypatch):
    db = session_factory()(); secret = private_secret_path(tmp_path)
    snapshot = configure_identity(db, secret)
    import app.services.gpu_pipeline_lifecycle as lifecycle_module
    monkeypatch.setattr(lifecycle_module, "PROJECT_SECRET_PATH", secret)
    run = PipelineRun(user_id="1", status="running", mode="apply", idempotency_key="resume-guard", request_json=json.dumps({"apply": True, "snapshot": [{"size_bytes": 1}], "gpu_runtime_snapshot": snapshot.public_dict()}))
    db.add(run); db.commit()

    first = LifecycleClient(["Stopped"])
    real_commit = db.commit
    def crash_after_guard_commit():
        real_commit()
        lifecycle = run.summary().get("gpu_lifecycle", {})
        if lifecycle.get("lease", {}).get("phase") == "guard_accepted_before_start":
            raise RuntimeError("simulated death between guard and accepted start")
    db.commit = crash_after_guard_commit
    with pytest.raises(RuntimeError, match="simulated death"):
        acquire_gpu_for_pipeline(db, run, client_factory=lambda _config: first, readiness_probe=lambda *_: {"ready": True}, transport_factory=fake_transport)
    db.commit = real_commit
    persisted = run.summary()["gpu_lifecycle"]["lease"]
    assert persisted["phase"] == "guard_accepted_before_start"
    assert first.scheduler_calls == 1 and first.start_calls == 0

    resumed = LifecycleClient(["Stopped", "Running"])
    resumed.scheduler_stop_time = int(persisted["cost_guard"]["scheduler_stop_time"])
    lease = acquire_gpu_for_pipeline(db, run, client_factory=lambda _config: resumed, readiness_probe=lambda *_: {"ready": True}, transport_factory=fake_transport)
    assert resumed.scheduler_calls == 0
    assert resumed.start_calls == 1
    assert lease.phase == "ready"


def test_already_running_instance_is_never_claimed_or_stopped(tmp_path: Path, monkeypatch):
    db=session_factory()(); secret=private_secret_path(tmp_path)
    snapshot=configure_identity(db,secret)
    import app.services.gpu_pipeline_lifecycle as lifecycle_module
    monkeypatch.setattr(lifecycle_module,"PROJECT_SECRET_PATH",secret)
    run=PipelineRun(user_id="1",status="running",mode="apply",idempotency_key="run-2",request_json=json.dumps({"apply":True,"snapshot":[{"size_bytes":1}],"gpu_runtime_snapshot":snapshot.public_dict()}))
    db.add(run); db.commit()
    client=LifecycleClient(["Running"])
    lease=acquire_gpu_for_pipeline(db,run,client_factory=lambda _config:client,readiness_probe=lambda _config,_url:{"ready":True},transport_factory=fake_transport)
    assert lease is not None and lease.lifecycle_owned is False and client.start_calls==0
    result=release_gpu_after_pipeline(db,run,lease,client_factory=lambda _config:client,remote_jobs_probe=lambda _url:{"verified":True,"all_required_denominators_verified":True,"idle_verified":True,"active_total":0},sleep=lambda _seconds:None)
    assert result["stopped"] is False and "lifecycle_not_owned" in result["blockers"] and client.stop_calls==0
