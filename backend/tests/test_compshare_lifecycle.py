from __future__ import annotations

import itertools
import json
import os
import subprocess
import sys
import urllib.parse
from types import SimpleNamespace

import pytest

from app.services.compshare_lifecycle import (
    OPERATION_LOCK_ERROR,
    CompShareConfig,
    CompShareLifecycleError,
    LifecycleLease,
    SafeStopContext,
    UCloudCompShareClient,
    ensure_running,
    exact_instance,
    stop_when_safe,
    ssh_readiness_probe,
)


def config(**overrides):
    values = {
        "endpoint": "https://api.compshare.invalid",
        "public_key": "public",
        "private_key": "private",
        "region": "cn-test",
        "zone": "cn-test-01",
        "project_id": "project",
        "uhost_id": "uhost-1",
        "poll_seconds": 0.001,
        "operation_timeout_seconds": 30,
    }
    values.update(overrides)
    return CompShareConfig(**values)


class FakeClient:
    def __init__(self, states, *, start_error="", stop_error=""):
        self.states = iter(states)
        self.last_state = ""
        self.start_error = start_error
        self.stop_error = stop_error
        self.start_calls = 0
        self.stop_calls = 0
        self.describe_calls = 0

    def describe(self):
        self.describe_calls += 1
        try:
            self.last_state = next(self.states)
        except StopIteration:
            pass
        return {"UHostSet": [{"UHostId": "uhost-1", "State": self.last_state}]}

    def start(self):
        self.start_calls += 1
        if self.start_error:
            raise CompShareLifecycleError(self.start_error, self.start_error)
        return {"RetCode": 0}

    def stop(self):
        self.stop_calls += 1
        if self.stop_error:
            raise CompShareLifecycleError(self.stop_error, self.stop_error)
        return {"RetCode": 0}

    def update_stop_scheduler(self, stop_time):
        return {"RetCode": 0, "SchedulerStopTime": stop_time}


def clock():
    values = itertools.count()
    return lambda: float(next(values))


def ready():
    return {"ready": True, "ssh": {"batch_mode": True}, "wrapper": {"status": "ok"}}


def test_stopped_starts_once_waits_for_running_and_owns_lifecycle():
    client = FakeClient(["Stopped", "Starting", "Starting", "Running"])
    lease = ensure_running(client, config(), ready, sleep=lambda _: None, monotonic=clock())
    assert client.start_calls == 1
    assert lease.prior_state == "Stopped"
    assert lease.lifecycle_owned is True
    assert lease.started_by_pipeline is True
    assert lease.current_state == "Running"


def test_start_accepts_initializing_transition_state():
    client = FakeClient(["Stopped", "Initializing", "Running"])
    lease = ensure_running(client, config(), ready, sleep=lambda _: None, monotonic=clock())
    assert lease.current_state == "Running"
    assert any(row["state"] == "Initializing" for row in lease.timeline)


def test_existing_initializing_state_waits_for_running_without_claiming_ownership():
    client = FakeClient(["Initializing", "Running"])
    lease = ensure_running(client, config(), ready, sleep=lambda _: None, monotonic=clock())
    assert client.start_calls == 0
    assert lease.current_state == "Running"
    assert lease.lifecycle_owned is False


def test_running_is_idempotent_and_never_claims_lifecycle_ownership():
    client = FakeClient(["Running"])
    lease = ensure_running(client, config(), ready, sleep=lambda _: None, monotonic=clock())
    assert client.start_calls == 0
    assert lease.prior_state == "Running"
    assert lease.lifecycle_owned is False


def test_start_operation_lock_is_polled_without_repeated_start():
    client = FakeClient(
        ["Stopped", "Starting", "Running"],
        start_error=OPERATION_LOCK_ERROR,
    )
    lease = ensure_running(client, config(), ready, sleep=lambda _: None, monotonic=clock())
    assert client.start_calls == 1
    assert lease.current_state == "Running"
    assert lease.lifecycle_owned is False
    assert lease.started_by_pipeline is False
    assert any(row["action"] == "start_operation_locked" for row in lease.timeline)


def test_start_timeout_fails_closed():
    client = FakeClient(["Stopped", *(["Starting"] * 20)])
    with pytest.raises(CompShareLifecycleError, match="Timed out") as exc:
        ensure_running(
            client,
            config(operation_timeout_seconds=2),
            ready,
            sleep=lambda _: None,
            monotonic=clock(),
        )
    assert exc.value.code == "cloud_operation_timeout"


def test_start_operation_lock_times_out_without_claiming_or_resubmitting():
    client = FakeClient(["Stopped", *( ["Starting"] * 20)], start_error=OPERATION_LOCK_ERROR)
    checkpoints = []
    with pytest.raises(CompShareLifecycleError) as exc:
        ensure_running(
            client,
            config(operation_timeout_seconds=2),
            ready,
            sleep=lambda _: None,
            monotonic=clock(),
            checkpoint=lambda lease: checkpoints.append(lease.to_dict()),
        )
    assert exc.value.code == "cloud_operation_timeout"
    assert client.start_calls == 1
    assert all(row["lifecycle_owned"] is False for row in checkpoints)
    assert all(row["started_by_pipeline"] is False for row in checkpoints)


def test_start_accepts_bounded_stopped_observation_lag_without_resubmitting():
    client = FakeClient(["Stopped", "Stopped", "Stopped", "Starting", "Running"])
    lease = ensure_running(client, config(state_lag_max_observations=3), ready, sleep=lambda _: None, monotonic=clock())
    assert lease.current_state == "Running"
    assert client.start_calls == 1


def test_start_reverting_after_transition_fails_closed():
    client = FakeClient(["Stopped", "Starting", "Stopped"])
    with pytest.raises(CompShareLifecycleError) as exc:
        ensure_running(client, config(), ready, sleep=lambda _: None, monotonic=clock())
    assert exc.value.code == "cloud_start_reverted"
    assert client.start_calls == 1


def test_running_without_ssh_service_readiness_is_not_submittable():
    client = FakeClient(["Running"])
    with pytest.raises(CompShareLifecycleError) as exc:
        ensure_running(client, config(), lambda: {"ready": False, "reason": "wrapper_down"})
    assert exc.value.code == "gpu_readiness_failed"


def test_start_and_readiness_failure_are_checkpointed_with_ownership():
    client = FakeClient(["Stopped", "Stopped", "Starting", "Running"])
    checkpoints = []
    with pytest.raises(CompShareLifecycleError) as exc:
        ensure_running(
            client,
            config(),
            lambda: {"ready": False, "reason": "wrapper_down"},
            sleep=lambda _: None,
            monotonic=clock(),
            checkpoint=lambda lease: checkpoints.append(lease.to_dict()),
        )
    assert exc.value.code == "gpu_readiness_failed"
    phases = [row["phase"] for row in checkpoints]
    assert phases[0] == "described"
    assert "start_accepted" in phases
    assert phases[-1] == "readiness_failed"
    assert checkpoints[-1]["started_by_pipeline"] is True
    assert checkpoints[-1]["lifecycle_owned"] is True


def test_exact_describe_rejects_wrong_or_duplicate_instance():
    with pytest.raises(CompShareLifecycleError) as missing:
        exact_instance({"UHostSet": [{"UHostId": "other", "State": "Running"}]}, "uhost-1")
    assert missing.value.code == "cloud_instance_identity_mismatch"
    with pytest.raises(CompShareLifecycleError):
        exact_instance(
            {"UHostSet": [{"UHostId": "uhost-1", "State": "Running"}, {"UHostId": "uhost-1", "State": "Stopped"}]},
            "uhost-1",
        )


def owned_lease():
    return LifecycleLease(
        lease_id="lease",
        uhost_id="uhost-1",
        prior_state="Stopped",
        current_state="Running",
        lifecycle_owned=True,
        started_by_pipeline=True,
        acquired_at="2026-08-06T00:00:00Z",
    )


def safe_context(**overrides):
    values = {
        "queue_empty": True,
        "remote_active_jobs": 0,
        "remote_idle_verified": True,
        "all_results_frozen_local": True,
        "grace_elapsed": True,
    }
    values.update(overrides)
    return SafeStopContext(**values)


def test_safe_stop_polls_cloud_to_stopped():
    client = FakeClient(["Running", "Stopping", "Stopped"])
    result = stop_when_safe(
        client,
        config(),
        owned_lease(),
        safe_context(),
        sleep=lambda _: None,
        monotonic=clock(),
    )
    assert result["stopped"] is True
    assert result["status"] == "stopped"
    assert client.stop_calls == 1
    assert result["lease"]["current_state"] == "Stopped"


@pytest.mark.parametrize(
    ("lease_mutation", "context", "blocker"),
    [
        ({"lifecycle_owned": False}, safe_context(), "lifecycle_not_owned"),
        ({}, safe_context(queue_empty=False), "queue_not_empty"),
        ({}, safe_context(remote_active_jobs=1), "remote_jobs_active"),
        ({}, safe_context(remote_idle_verified=False), "remote_idle_unverified"),
        ({}, safe_context(all_results_frozen_local=False), "results_not_frozen_local"),
        ({}, safe_context(grace_elapsed=False), "grace_period_not_elapsed"),
    ],
)
def test_safe_stop_fencing_blocks_unsafe_shutdown(lease_mutation, context, blocker):
    lease = owned_lease()
    for key, value in lease_mutation.items():
        setattr(lease, key, value)
    client = FakeClient(["Running"])
    result = stop_when_safe(client, config(), lease, context)
    assert result["stopped"] is False
    assert blocker in result["blockers"]
    assert client.stop_calls == 0


def test_instance_originally_running_is_never_auto_stopped():
    client = FakeClient(["Running"])
    lease = ensure_running(client, config(), ready)
    result = stop_when_safe(client, config(), lease, safe_context())
    assert result["status"] == "retained_running"
    assert "lifecycle_not_owned" in result["blockers"]
    assert client.stop_calls == 0


def test_stop_operation_lock_is_polled_without_repeated_stop():
    client = FakeClient(["Running", "Stopping", "Stopped"], stop_error=OPERATION_LOCK_ERROR)
    result = stop_when_safe(
        client,
        config(),
        owned_lease(),
        safe_context(),
        sleep=lambda _: None,
        monotonic=clock(),
    )
    assert result["stopped"] is True
    assert client.stop_calls == 1


def test_stop_accepts_bounded_running_observation_lag_without_resubmitting():
    client = FakeClient(["Running", "Running", "Running", "Stopping", "Stopped"])
    result = stop_when_safe(
        client,
        config(state_lag_max_observations=3),
        owned_lease(),
        safe_context(),
        sleep=lambda _: None,
        monotonic=clock(),
    )
    assert result["stopped"] is True
    assert client.stop_calls == 1


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def test_ucloud_transport_uses_post_form_and_exact_official_fields(monkeypatch):
    captured = []

    def open_request(request, timeout):
        form = urllib.parse.parse_qs(request.data.decode("utf-8"), keep_blank_values=True)
        captured.append((request, form, timeout))
        action = form["Action"][0]
        if action == "DescribeCompShareInstance":
            return FakeResponse({"RetCode": 0, "UHostSet": [{"UHostId": "uhost-1", "State": "Stopped"}]})
        return FakeResponse({"RetCode": 0, "UHostId": "uhost-1"})

    monkeypatch.setattr("app.services.compshare_lifecycle.urllib.request.urlopen", open_request)
    monkeypatch.setattr("app.services.compshare_lifecycle.time.time", lambda: 1000)
    client = UCloudCompShareClient(config())
    client.describe()
    client.start()
    client.stop()
    client.update_stop_scheduler(1300)

    expected_extra = [
        {"UHostIds.0"},
        {"UHostId"},
        {"UHostId"},
        {"UHostId", "SchedulerStopTime"},
    ]
    common = {"Action", "PublicKey", "Region", "Zone", "ProjectId", "Signature"}
    for (request, form, _timeout), extra in zip(captured, expected_extra, strict=True):
        assert request.get_method() == "POST"
        assert urllib.parse.urlsplit(request.full_url).query == ""
        assert request.headers["Content-type"] == "application/x-www-form-urlencoded"
        assert set(form) == common | extra
        unsigned = {key: values[0] for key, values in form.items() if key != "Signature"}
        expected_signature = client._signature(unsigned)
        assert form["Signature"] == [expected_signature]
    assert "WithoutGpuSpec" not in captured[1][1]
    assert captured[3][1]["SchedulerStopTime"] == ["1300"]
    sanitized = json.dumps(client.last_sanitized_request)
    assert "public" not in sanitized
    assert "private" not in sanitized
    assert "Signature" in sanitized  # field name is auditable; value is not.


def test_scheduler_rejects_less_than_now_plus_300(monkeypatch):
    monkeypatch.setattr("app.services.compshare_lifecycle.time.time", lambda: 1000)
    client = UCloudCompShareClient(config())
    with pytest.raises(CompShareLifecycleError) as exc:
        client.update_stop_scheduler(1299)
    assert exc.value.code == "scheduler_stop_time_invalid"


def test_cloud_error_never_echoes_credentials_signature_or_password(monkeypatch):
    def open_request(request, timeout):
        assert timeout > 0
        form = urllib.parse.parse_qs(request.data.decode("utf-8"))
        return FakeResponse(
            {
                "RetCode": 1,
                "ErrCode": "Rejected",
                "Message": f"public private {form['Signature'][0]} Password=sensitive",
            }
        )

    monkeypatch.setattr("app.services.compshare_lifecycle.urllib.request.urlopen", open_request)
    client = UCloudCompShareClient(config())
    with pytest.raises(CompShareLifecycleError) as exc:
        client.start()
    message = str(exc.value)
    assert "public" not in message
    assert "private" not in message
    assert "sensitive" not in message


def test_runtime_preflight_reports_presence_without_secret_values(tmp_path):
    runtime_env = os.environ.copy()
    runtime_env.update(
        {
            "COMPSHARE_ALLOW_LEGACY_ENV": "true",
            "COMPSHARE_PUBLIC_KEY": "task32-public-secret",
            "COMPSHARE_PRIVATE_KEY": "task32-private-secret",
            "COMPSHARE_REGION": "cn-test",
            "COMPSHARE_ZONE": "cn-test-01",
            "COMPSHARE_PROJECT_ID": "org-secret-project",
            "COMPSHARE_UHOST_ID": "uhost-1",
        }
    )
    completed = subprocess.run(
        [sys.executable, "scripts/compshare_runtime_preflight.py"],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=runtime_env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["ready"] is True
    assert all(payload["present"].values())
    assert "task32-public-secret" not in completed.stdout
    assert "task32-private-secret" not in completed.stdout
    assert "org-secret-project" not in completed.stdout


def test_ssh_readiness_requires_gpu_disk_and_healthy_wrapper(monkeypatch, tmp_path):
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("gpu ssh-ed25519 test\n")
    cfg = config(
        ssh_host="gpu.example",
        ssh_known_hosts_path=str(known_hosts),
        ssh_key_path="",
        remote_service_root="/srv/mineru",
    )
    monkeypatch.setattr(
        "app.services.compshare_lifecycle.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="RTX 4090, 49140\n/dev/vda1 100000000 1 60000000 1% /srv\n",
            stderr="",
        ),
    )
    monkeypatch.setattr(
        "app.services.compshare_lifecycle.urllib.request.urlopen",
        lambda *_args, **_kwargs: FakeResponse({"status": "healthy", "active_jobs": 0}),
    )
    result = ssh_readiness_probe(cfg, "http://wrapper.test")
    assert result["ready"] is True
    assert result["ssh"]["disk_available_bytes"] == 60_000_000 * 1024
    assert result["ssh"]["disk_required_bytes"] == 50 * 1024**3
    assert result["ssh"]["gpu_inventory"] == ["RTX 4090, 49140"]


def test_ssh_readiness_fails_closed_on_disk_shortage(monkeypatch, tmp_path):
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("gpu ssh-ed25519 test\n")
    cfg = config(ssh_host="gpu.example", ssh_known_hosts_path=str(known_hosts), ssh_key_path="")
    monkeypatch.setenv("GPU_MIN_FREE_DISK_BYTES", str(20 * 1024**3))
    monkeypatch.setattr(
        "app.services.compshare_lifecycle.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="RTX 4090, 49140\n/dev/vda1 100000 1 1000 1% /srv\n",
            stderr="",
        ),
    )
    result = ssh_readiness_probe(cfg, "http://wrapper.test")
    assert result["ready"] is False
    assert result["reason"] == "disk_headroom_insufficient"
