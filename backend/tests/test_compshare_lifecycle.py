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
    cap_derived_scheduler_stop,
    ensure_running,
    exact_instance,
    open_managed_wrapper_transport,
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
    def __init__(self, states, *, start_error="", stop_error="", hourly_price=None, billing_row=None, scheduler_error=""):
        self.states = iter(states)
        self.last_state = ""
        self.start_error = start_error
        self.stop_error = stop_error
        self.start_calls = 0
        self.stop_calls = 0
        self.describe_calls = 0
        self.hourly_price = hourly_price
        self.billing_row = dict(billing_row or {})
        self.scheduler_error = scheduler_error
        self.scheduler_calls = []
        self.mutation_ledger = []
        self.scheduler_stop_time = 0
        self.guard_verification_pending = False

    def describe(self):
        self.describe_calls += 1
        if self.guard_verification_pending:
            self.guard_verification_pending = False
        else:
            try:
                self.last_state = next(self.states)
            except StopIteration:
                pass
        row = {"UHostId": "uhost-1", "State": self.last_state}
        if self.hourly_price is not None:
            row.update({"ChargeType":"Hour", "Price":self.hourly_price})
        row.update(self.billing_row)
        if self.scheduler_stop_time:
            row["SchedulerStopTime"] = self.scheduler_stop_time
        return {"UHostSet": [row]}

    def start(self):
        self.start_calls += 1
        self.mutation_ledger.append("StartCompShareInstance")
        if self.start_error:
            raise CompShareLifecycleError(self.start_error, self.start_error)
        return {"RetCode": 0}

    def stop(self):
        self.stop_calls += 1
        if self.stop_error:
            raise CompShareLifecycleError(self.stop_error, self.stop_error)
        return {"RetCode": 0}

    def update_stop_scheduler(self, stop_time):
        self.scheduler_calls.append(stop_time)
        self.mutation_ledger.append("UpdateCompShareStopScheduler")
        if self.scheduler_error:
            raise CompShareLifecycleError(self.scheduler_error, self.scheduler_error)
        self.scheduler_stop_time = int(stop_time)
        self.guard_verification_pending = True
        return {
            "Action": "UpdateCompShareStopSchedulerResponse",
            "RetCode": 0,
            "UHostId": "uhost-1",
        }


class OfficialSchedulerClient:
    """Official 2026-08-04 Describe/scheduler response shapes."""

    def __init__(
        self,
        states,
        *,
        scheduler_stop_time=0,
        scheduler_uhost_id="uhost-1",
        guard_visibility_lag_observations=0,
    ):
        self.states = iter(states)
        self.last_state = ""
        self.scheduler_stop_time = int(scheduler_stop_time)
        self.scheduler_uhost_id = scheduler_uhost_id
        self.guard_visibility_lag_observations = int(guard_visibility_lag_observations)
        self._guard_visibility_remaining = 0
        self._previous_scheduler_stop_time = self.scheduler_stop_time
        self.scheduler_calls = []
        self.start_calls = 0
        self.call_ledger = []

    def describe(self):
        self.call_ledger.append("DescribeCompShareInstance")
        try:
            self.last_state = next(self.states)
        except StopIteration:
            pass
        row = {
            "UHostId": "uhost-1",
            "State": self.last_state,
            "ChargeType": "Postpay",
            "InstancePrice": 3.13,
        }
        visible_scheduler_stop_time = self.scheduler_stop_time
        if self._guard_visibility_remaining:
            visible_scheduler_stop_time = self._previous_scheduler_stop_time
            self._guard_visibility_remaining -= 1
        if visible_scheduler_stop_time:
            row["SchedulerStopTime"] = visible_scheduler_stop_time
        return {"UHostSet": [row]}

    def update_stop_scheduler(self, stop_time):
        self.call_ledger.append("UpdateCompShareStopScheduler")
        self.scheduler_calls.append(int(stop_time))
        self._previous_scheduler_stop_time = self.scheduler_stop_time
        self.scheduler_stop_time = int(stop_time)
        self._guard_visibility_remaining = self.guard_visibility_lag_observations
        return {
            "Action": "UpdateCompShareStopSchedulerResponse",
            "RetCode": 0,
            "UHostId": self.scheduler_uhost_id,
        }

    def start(self):
        self.call_ledger.append("StartCompShareInstance")
        self.start_calls += 1
        return {"RetCode": 0, "UHostId": "uhost-1"}

    def stop(self):
        return {"RetCode": 0, "UHostId": "uhost-1"}


def test_cost_guard_uses_whole_hour_budget_and_reserves_safe_stop_window():
    guard = cap_derived_scheduler_stop(
        {"ChargeType":"Hour", "Price":3.3}, budget_micro_cny=20_000_000, now_epoch=1_000
    )
    assert guard["authorized_units"] == 6
    assert guard["scheduler_stop_time"] == 1_000 + 6 * 3600 - 1800
    assert guard["authorized_units"] * guard["unit_price_micro_cny"] <= 20_000_000


def test_official_postpay_shape_normalizes_to_versioned_hourly_contract():
    guard = cap_derived_scheduler_stop(
        {"ChargeType": "Postpay", "InstancePrice": 3.13},
        budget_micro_cny=20_000_000,
        now_epoch=1_000,
    )
    assert guard["authorized_units"] == 6
    assert guard["unit_price_micro_cny"] == 3_130_000
    assert guard["billing_normalization_schema"] == "luceon.compshare-billing-normalization/v3"
    assert guard["billing_normalization_method"] == "compshare_postpay_instance_price_v1"


def test_official_postpay_instance_price_shape_normalizes_to_hourly_contract():
    guard = cap_derived_scheduler_stop(
        {"ChargeType": "Postpay", "InstancePrice": 3.13},
        budget_micro_cny=20_000_000,
        now_epoch=1_000,
    )
    assert guard["authorized_units"] == 6
    assert guard["unit_price_micro_cny"] == 3_130_000
    assert guard["billing_normalization_method"] == "compshare_postpay_instance_price_v1"


@pytest.mark.parametrize("value", [True, False, 0, -1, "3.13"])
def test_official_postpay_instance_price_rejects_non_exact_positive_number(value):
    with pytest.raises(CompShareLifecycleError) as failure:
        cap_derived_scheduler_stop(
            {"ChargeType": "Postpay", "InstancePrice": value},
            budget_micro_cny=20_000_000,
        )
    assert failure.value.code == "cloud_billing_unqualified"


def test_postpay_legacy_price_without_raw_evidence_is_not_qualified():
    with pytest.raises(CompShareLifecycleError) as failure:
        cap_derived_scheduler_stop(
            {"ChargeType": "Postpay", "Price": 3.13},
            budget_micro_cny=20_000_000,
        )
    assert failure.value.code == "cloud_billing_unqualified"


@pytest.mark.parametrize("legacy_field", [
    {"Price": 3.13}, {"Price": 4.00}, {"DiscountPrice": 3.13},
])
def test_postpay_instance_price_rejects_conflicting_legacy_price_fields(legacy_field):
    with pytest.raises(CompShareLifecycleError) as failure:
        cap_derived_scheduler_stop(
            {"ChargeType": "Postpay", "InstancePrice": 3.13, **legacy_field},
            budget_micro_cny=20_000_000,
        )
    assert failure.value.code == "cloud_billing_unqualified"


@pytest.mark.parametrize("row", [
    {"ChargeType": "Postpay"},
    {"ChargeType": "Postpay", "Price": 3.13, "PriceUnit": "CNY/day"},
    {"ChargeType": "Mystery", "Price": 3.13},
])
def test_unknown_or_ambiguous_billing_still_fails_closed(row):
    with pytest.raises(CompShareLifecycleError) as failure:
        cap_derived_scheduler_stop(row, budget_micro_cny=20_000_000)
    assert failure.value.code == "cloud_billing_unqualified"


def test_versioned_automatic_start_sets_cap_derived_scheduler_once():
    client = FakeClient(["Stopped", "Starting", "Running"], hourly_price=3.3)
    lease = ensure_running(
        client,
        config(settings_sha256="settings", budget_micro_cny=20_000_000),
        ready,
        sleep=lambda _: None,
        monotonic=clock(),
    )
    assert lease.lifecycle_owned is True
    assert client.start_calls == 1
    assert len(client.scheduler_calls) == 1
    assert client.mutation_ledger == ["UpdateCompShareStopScheduler", "StartCompShareInstance"]
    actions = [row["action"] for row in lease.timeline]
    assert actions.index("guard_accepted_before_start") < actions.index("start_accepted")


def test_scheduler_failure_prevents_start_and_is_checkpointed():
    client = FakeClient(
        ["Stopped"], hourly_price=3.3, scheduler_error="cloud_scheduler_rejected"
    )
    checkpoints = []
    with pytest.raises(CompShareLifecycleError) as failure:
        ensure_running(
            client,
            config(settings_sha256="settings"),
            ready,
            checkpoint=lambda lease: checkpoints.append(lease.to_dict()),
        )
    assert failure.value.code == "cloud_scheduler_rejected"
    assert client.start_calls == 0
    assert client.scheduler_calls
    assert all(row["phase"] != "start_accepted" for row in checkpoints)


def test_official_scheduler_response_requires_post_update_describe_before_start():
    client = OfficialSchedulerClient(["Stopped", "Stopped", "Starting", "Running"])
    lease = ensure_running(
        client,
        config(settings_sha256="settings"),
        ready,
        sleep=lambda _: None,
        monotonic=clock(),
        wall_time=lambda: 1_000,
    )
    assert lease.lifecycle_owned is True
    assert client.call_ledger[:4] == [
        "DescribeCompShareInstance",
        "UpdateCompShareStopScheduler",
        "DescribeCompShareInstance",
        "StartCompShareInstance",
    ]
    assert client.start_calls == 1


def test_scheduler_guard_allows_one_eventually_consistent_describe_before_start():
    client = OfficialSchedulerClient(
        ["Stopped", "Stopped", "Stopped", "Starting", "Running"],
        scheduler_stop_time=1_300,
        guard_visibility_lag_observations=1,
    )
    lease = ensure_running(
        client,
        config(settings_sha256="settings", scheduler_guard_verify_max_observations=3),
        ready,
        sleep=lambda _: None,
        monotonic=clock(),
        wall_time=lambda: 1_000,
    )
    assert lease.lifecycle_owned is True
    assert client.start_calls == 1
    assert client.scheduler_calls and len(client.scheduler_calls) == 1
    assert client.call_ledger[:5] == [
        "DescribeCompShareInstance",
        "UpdateCompShareStopScheduler",
        "DescribeCompShareInstance",
        "DescribeCompShareInstance",
        "StartCompShareInstance",
    ]
    assert any(row["action"] == "guard_verification_pending" for row in lease.timeline)


def test_scheduler_guard_persistent_visibility_drift_prevents_start_without_duplicate_update():
    client = OfficialSchedulerClient(
        ["Stopped", "Stopped", "Stopped", "Stopped"],
        scheduler_stop_time=1_300,
        guard_visibility_lag_observations=3,
    )
    with pytest.raises(CompShareLifecycleError) as failure:
        ensure_running(
            client,
            config(settings_sha256="settings", scheduler_guard_verify_max_observations=3),
            ready,
            sleep=lambda _: None,
            wall_time=lambda: 1_000,
        )
    assert failure.value.code == "cloud_scheduler_not_confirmed"
    assert client.start_calls == 0
    assert len(client.scheduler_calls) == 1


def test_scheduler_guard_instance_leaving_stopped_prevents_start():
    client = OfficialSchedulerClient(
        ["Stopped", "Starting"], scheduler_stop_time=1_300, guard_visibility_lag_observations=1
    )
    with pytest.raises(CompShareLifecycleError) as failure:
        ensure_running(
            client,
            config(settings_sha256="settings", scheduler_guard_verify_max_observations=3),
            ready,
            sleep=lambda _: None,
            wall_time=lambda: 1_000,
        )
    assert failure.value.code == "cloud_scheduler_not_confirmed"
    assert client.start_calls == 0


def test_scheduler_response_uhost_mismatch_prevents_start():
    client = OfficialSchedulerClient(
        ["Stopped"], scheduler_uhost_id="uhost-other"
    )
    with pytest.raises(CompShareLifecycleError) as failure:
        ensure_running(
            client,
            config(settings_sha256="settings"),
            ready,
            wall_time=lambda: 1_000,
        )
    assert failure.value.code == "cloud_scheduler_identity_mismatch"
    assert client.start_calls == 0


@pytest.mark.parametrize(
    ("cloud_deadline", "expected_updates"),
    [(5_000, 0), (1_200, 1), (4_999, 1), (0, 1)],
)
def test_resume_guard_is_reverified_and_rearmed_when_expired_missing_or_drifted(
    cloud_deadline, expected_updates
):
    persisted_deadline = 5_000
    lease = LifecycleLease(
        lease_id="lease-1",
        uhost_id="uhost-1",
        prior_state="Stopped",
        current_state="Stopped",
        lifecycle_owned=False,
        started_by_pipeline=False,
        acquired_at="2026-08-06T00:00:00Z",
        phase="guard_accepted_before_start",
        cost_guard={
            "scheduler_stop_time": persisted_deadline,
            "billing_normalization_schema": "luceon.compshare-billing-normalization/v3",
        },
    )
    states = ["Stopped", "Starting", "Running"] if expected_updates == 0 else ["Stopped", "Stopped", "Starting", "Running"]
    client = OfficialSchedulerClient(states, scheduler_stop_time=cloud_deadline)
    result = ensure_running(
        client,
        config(settings_sha256="settings"),
        ready,
        resume_lease=lease,
        sleep=lambda _: None,
        monotonic=clock(),
        wall_time=lambda: 1_000,
    )
    assert len(client.scheduler_calls) == expected_updates
    assert client.start_calls == 1
    assert result.current_state == "Running"


def test_uat_endpoint_allowlist_blocks_official_origin_before_transport(monkeypatch):
    monkeypatch.setenv("COMPSHARE_ALLOWED_ENDPOINT_ORIGINS", "fake-services:8443")
    called = {"value": False}

    def forbidden_transport(*_args, **_kwargs):
        called["value"] = True
        raise AssertionError("transport must remain call0")

    monkeypatch.setattr("urllib.request.urlopen", forbidden_transport)
    with pytest.raises(CompShareLifecycleError) as failure:
        UCloudCompShareClient(config(endpoint="https://api.compshare.cn"))
    assert failure.value.code == "cloud_endpoint_not_allowed"
    assert called["value"] is False


def test_resume_after_guard_checkpoint_starts_once_without_rescheduling():
    first_client = FakeClient(["Stopped"], hourly_price=3.3)
    checkpoints = []

    def crash_after_guard(lease):
        checkpoints.append(lease.to_dict())
        if lease.phase == "guard_accepted_before_start":
            raise RuntimeError("simulated process crash")

    with pytest.raises(RuntimeError, match="simulated process crash"):
        ensure_running(
            first_client,
            config(settings_sha256="settings"),
            ready,
            checkpoint=crash_after_guard,
        )
    guarded = LifecycleLease.from_dict(checkpoints[-1])
    assert first_client.scheduler_calls and first_client.start_calls == 0

    resumed_client = FakeClient(["Stopped", "Starting", "Running"], hourly_price=3.3)
    resumed_client.scheduler_stop_time = int(guarded.cost_guard["scheduler_stop_time"])
    lease = ensure_running(
        resumed_client,
        config(settings_sha256="settings"),
        ready,
        resume_lease=guarded,
        sleep=lambda _: None,
        monotonic=clock(),
    )
    assert resumed_client.scheduler_calls == []
    assert resumed_client.start_calls == 1
    assert lease.current_state == "Running"


def test_versioned_automatic_start_rejects_unqualified_billing_before_start():
    client = FakeClient(["Stopped"])
    with pytest.raises(CompShareLifecycleError) as failure:
        ensure_running(client, config(settings_sha256="settings"), ready)
    assert failure.value.code == "cloud_billing_unqualified"
    assert client.start_calls == 0


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


def test_managed_wrapper_transport_uses_strict_loopback_forward_and_never_direct_url(tmp_path, monkeypatch):
    known_hosts = tmp_path / "known_hosts"; known_hosts.write_text("gpu ssh-ed25519 test\n")
    cfg = config(ssh_host="gpu.example", ssh_port=23, ssh_known_hosts_path=str(known_hosts), ssh_key_path="/keys/gpu")
    monkeypatch.setenv("GPU_WRAPPER_URL", "http://direct-wrapper.example:18080")
    observed = {}

    class Process:
        def poll(self): return None
        def terminate(self): observed["terminated"] = True
        def wait(self, timeout=None): return 0
        def kill(self): observed["killed"] = True

    def fake_popen(command, **kwargs):
        observed["command"] = command; observed["kwargs"] = kwargs
        return Process()

    transport = open_managed_wrapper_transport(
        cfg, lease_id="lease-1", process_factory=fake_popen,
        health_probe=lambda endpoint: {"ready": endpoint.startswith("http://127.0.0.1:")},
    )
    assert transport.endpoint.startswith("http://127.0.0.1:")
    command = observed["command"]
    assert "BatchMode=yes" in command and "StrictHostKeyChecking=yes" in command and "ExitOnForwardFailure=yes" in command
    assert "direct-wrapper.example" not in " ".join(command)
    assert any(value.endswith(":127.0.0.1:18080") for value in command)
    serialized = transport.to_dict()
    assert "private" not in json.dumps(serialized) and serialized["lease_id"] == "lease-1"
    transport.close()
    assert observed["terminated"] is True


def test_managed_transport_rejects_every_persisted_binding_drift(tmp_path):
    known_hosts = tmp_path / "known_hosts"; known_hosts.write_text("gpu ssh-ed25519 test\n")
    cfg = config(ssh_host="gpu.example", ssh_known_hosts_path=str(known_hosts), wrapper_remote_port=18080, settings_sha256="settings-a")

    class Process:
        stderr = None
        def poll(self): return None
        def terminate(self): pass
        def wait(self, timeout=None): return 0
        def kill(self): pass

    prior = open_managed_wrapper_transport(
        cfg, lease_id="lease-binding", process_factory=lambda *args, **kwargs: Process(),
        health_probe=lambda _endpoint: {"ready": True}, port_allocator=lambda: 19001,
    ).to_dict()
    for key, value in (("remote_port", 19000), ("ssh_host", "old.example"), ("endpoint", "http://127.0.0.1:19002"), ("settings_sha256", "settings-b")):
        forged = dict(prior); forged[key] = value
        # Re-signing a self-authored payload cannot bypass frozen config facts.
        unsigned = dict(forged); unsigned.pop("transport_sha256")
        forged["transport_sha256"] = __import__("hashlib").sha256(json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        with pytest.raises(CompShareLifecycleError) as exc:
            open_managed_wrapper_transport(cfg, lease_id="lease-binding", prior=forged, process_factory=lambda *args, **kwargs: Process(), health_probe=lambda _endpoint: {"ready": True})
        assert exc.value.code == "wrapper_transport_binding_drift"


def test_managed_transport_waits_for_slow_ssh_health_without_replacing_commitment(tmp_path):
    known_hosts = tmp_path / "known_hosts"; known_hosts.write_text("gpu ssh-ed25519 test\n")
    cfg = config(ssh_host="gpu.example", ssh_known_hosts_path=str(known_hosts), poll_seconds=0.001, operation_timeout_seconds=2)
    calls = {"health": 0, "popen": 0}
    class Process:
        stderr = None
        def poll(self): return None
        def terminate(self): pass
        def wait(self, timeout=None): return 0
        def kill(self): pass
    def probe(_endpoint):
        calls["health"] += 1
        return {"ready": calls["health"] >= 2}
    transport = open_managed_wrapper_transport(
        cfg, lease_id="lease-slow", process_factory=lambda *args, **kwargs: (calls.__setitem__("popen", calls["popen"] + 1) or Process()),
        health_probe=probe,
    )
    assert calls == {"health": 2, "popen": 1}
    transport.close()


def test_owned_orphan_recovery_requires_exact_kernel_and_argv_identity(monkeypatch, tmp_path):
    from app.services import compshare_lifecycle as lifecycle
    known_hosts = tmp_path / "known_hosts"; known_hosts.write_text("gpu ssh-ed25519 test\n")
    cfg = config(ssh_host="gpu.example", ssh_known_hosts_path=str(known_hosts), wrapper_remote_port=18080)
    expected_argv = lifecycle._managed_wrapper_ssh_argv(
        cfg, local_port=39123, remote_port=18080, owner_token="owner-token"
    )
    expected_sha = lifecycle._argv_sha256(expected_argv)
    prior = {
        "process_pid": 4242, "owner_token": "owner-token", "local_port": 39123,
        "remote_port": 18080, "ssh_host": "gpu.example",
        "process_boot_id": "boot-a", "process_start_ticks": 123456,
        "process_command_sha256": expected_sha,
    }
    killed = []
    monkeypatch.setattr(lifecycle, "_read_linux_process_identity", lambda _pid: {
        "boot_id": "boot-a", "start_ticks": 123456, "state": "S",
        "argv_sha256": lifecycle._argv_sha256(["python", "unrelated-service", "--port", "39123"]),
    })
    monkeypatch.setattr(lifecycle.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    with pytest.raises(CompShareLifecycleError) as exc:
        lifecycle._reap_exact_owned_orphan(prior, cfg)
    assert exc.value.code == "wrapper_transport_orphan_identity_unverified"
    assert killed == []

    identities = iter([
        {"boot_id": "boot-a", "start_ticks": 123456, "state": "S", "argv_sha256": expected_sha},
        FileNotFoundError(),
    ])
    def exact_then_exited(_pid):
        value = next(identities)
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(lifecycle, "_read_linux_process_identity", exact_then_exited)
    lifecycle._reap_exact_owned_orphan(prior, cfg)
    assert killed and killed[0][0] == 4242


@pytest.mark.parametrize(
    ("prior_patch", "live_patch"),
    [
        ({}, {"boot_id": "boot-b"}),
        ({}, {"start_ticks": 123457}),
        ({}, {"argv_sha256": "a" * 64}),
        ({"process_command_sha256": "b" * 64}, {}),
    ],
)
def test_owned_orphan_pid_reuse_or_forged_command_never_kills(monkeypatch, tmp_path, prior_patch, live_patch):
    from app.services import compshare_lifecycle as lifecycle
    known_hosts = tmp_path / "known_hosts"; known_hosts.write_text("gpu ssh-ed25519 test\n")
    cfg = config(ssh_host="gpu.example", ssh_known_hosts_path=str(known_hosts), wrapper_remote_port=18080)
    expected = lifecycle._argv_sha256(lifecycle._managed_wrapper_ssh_argv(
        cfg, local_port=39123, remote_port=18080, owner_token="owner-token"
    ))
    prior = {
        "process_pid": 4242, "owner_token": "owner-token", "local_port": 39123,
        "remote_port": 18080, "ssh_host": "gpu.example", "process_boot_id": "boot-a",
        "process_start_ticks": 123456, "process_command_sha256": expected,
        **prior_patch,
    }
    live = {"boot_id": "boot-a", "start_ticks": 123456, "state": "S", "argv_sha256": expected, **live_patch}
    killed = []
    monkeypatch.setattr(lifecycle, "_read_linux_process_identity", lambda _pid: live)
    monkeypatch.setattr(lifecycle.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    with pytest.raises(CompShareLifecycleError) as exc:
        lifecycle._reap_exact_owned_orphan(prior, cfg)
    assert exc.value.code == "wrapper_transport_orphan_identity_unverified"
    assert killed == []


def test_owned_orphan_zombie_is_never_signalled(monkeypatch, tmp_path):
    from app.services import compshare_lifecycle as lifecycle
    known_hosts = tmp_path / "known_hosts"; known_hosts.write_text("gpu ssh-ed25519 test\n")
    cfg = config(ssh_host="gpu.example", ssh_known_hosts_path=str(known_hosts), wrapper_remote_port=18080)
    expected = lifecycle._argv_sha256(lifecycle._managed_wrapper_ssh_argv(
        cfg, local_port=39123, remote_port=18080, owner_token="owner-token"
    ))
    prior = {
        "process_pid": 4242, "owner_token": "owner-token", "local_port": 39123,
        "remote_port": 18080, "ssh_host": "gpu.example", "process_boot_id": "boot-a",
        "process_start_ticks": 123456, "process_command_sha256": expected,
    }
    killed = []
    monkeypatch.setattr(lifecycle, "_read_linux_process_identity", lambda _pid: {
        "boot_id": "boot-a", "start_ticks": 123456, "state": "Z", "argv_sha256": expected,
    })
    monkeypatch.setattr(lifecycle.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    with pytest.raises(CompShareLifecycleError) as exc:
        lifecycle._reap_exact_owned_orphan(prior, cfg)
    assert exc.value.code == "wrapper_transport_orphan_identity_unverified"
    assert killed == []


def test_resigned_transport_with_forged_persisted_command_sha_never_reaps(monkeypatch, tmp_path):
    from app.services import compshare_lifecycle as lifecycle
    known_hosts = tmp_path / "known_hosts"; known_hosts.write_text("gpu ssh-ed25519 test\n")
    cfg = config(
        ssh_host="gpu.example", ssh_known_hosts_path=str(known_hosts),
        wrapper_remote_port=18080, settings_sha256="settings-a",
    )
    expected = lifecycle._argv_sha256(lifecycle._managed_wrapper_ssh_argv(
        cfg, local_port=39123, remote_port=18080, owner_token="owner-token"
    ))
    transport_id = lifecycle._canonical_sha({
        "schema": lifecycle.MANAGED_WRAPPER_TRANSPORT_SCHEMA,
        "lease_id": "lease-forged", "endpoint": "http://127.0.0.1:39123",
        "local_port": 39123, "remote_port": 18080, "ssh_host": "gpu.example",
        "settings_sha256": "settings-a",
    })
    prior = {
        "schema": lifecycle.MANAGED_WRAPPER_TRANSPORT_SCHEMA,
        "transport_id": transport_id, "lease_id": "lease-forged",
        "endpoint": "http://127.0.0.1:39123", "local_port": 39123,
        "remote_port": 18080, "ssh_host": "gpu.example", "settings_sha256": "settings-a",
        "owner_token": "owner-token", "process_pid": 4242,
        "process_boot_id": "boot-a", "process_start_ticks": 123456,
        "process_command_sha256": "f" * 64, "state": "active",
    }
    # The attacker can recompute the self-hash, but not change the command
    # independently reconstructed from frozen config and forwarding identity.
    prior["transport_sha256"] = lifecycle._canonical_sha(prior)
    monkeypatch.setattr(lifecycle, "_read_linux_process_identity", lambda _pid: {
        "boot_id": "boot-a", "start_ticks": 123456, "state": "S", "argv_sha256": expected,
    })
    killed = []
    monkeypatch.setattr(lifecycle.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    with pytest.raises(CompShareLifecycleError) as exc:
        open_managed_wrapper_transport(cfg, lease_id="lease-forged", prior=prior)
    assert exc.value.code == "wrapper_transport_orphan_identity_unverified"
    assert killed == []


def test_legacy_transport_context_is_not_reinterpreted(monkeypatch, tmp_path):
    known_hosts = tmp_path / "known_hosts"; known_hosts.write_text("gpu ssh-ed25519 test\n")
    cfg = config(ssh_host="gpu.example", ssh_known_hosts_path=str(known_hosts))
    with pytest.raises(CompShareLifecycleError) as exc:
        open_managed_wrapper_transport(
            cfg, lease_id="lease-v2", prior={"schema": "luceon.managed-wrapper-transport/v1", "lease_id": "lease-v2"}
        )
    assert exc.value.code == "wrapper_transport_context_legacy"
