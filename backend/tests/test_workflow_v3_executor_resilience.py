from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.workflow_v3.executor import (
    ModelCallHeartbeatFailed,
    ReleaseBindingError,
    _enforce_model_request_budget,
    _enforce_model_result_budget,
    _execute_model_call_with_heartbeat,
    _ordinary_model_budget,
    normalize_candidate_prefix,
)
from app.workflow_v3.llm_gateway import (
    LlmCallResult,
    LlmGatewayError,
    ReleaseBoundLlmCall,
    sha256_json,
    sha256_text,
)
from app.workflow_v3.visual_review import (
    VisualReviewError,
    _VisualResourceBudget,
    _visual_limits,
)


def _call(*, timeout_seconds: float = 1.0) -> ReleaseBoundLlmCall:
    prompt = "Return one release-bound decision."
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
    evidence = {"task": "one"}
    return ReleaseBoundLlmCall(
        call_id="call-resilience-1",
        release_id="worker-v3-test",
        release_sha256="a" * 64,
        stage_key="outline_reconstruction",
        prompt_id="outline-review",
        prompt_version="v1",
        prompt_sha256=sha256_text(prompt),
        prompt_text=prompt,
        schema_id="outline-review",
        schema_version="v1",
        schema_sha256=sha256_json(schema),
        output_schema=schema,
        input_sha256=sha256_json(evidence),
        input_evidence=evidence,
        provider="provider",
        model="model",
        request_parameters={
            "temperature": 0,
            "max_output_tokens": 100,
        },
        timeout_seconds=timeout_seconds,
    )


def _transport(_request, _timeout):
    time.sleep(0.04)
    response = {"decision": "accept"}
    return {
        "status_code": 200,
        "provider": "provider",
        "model": "model",
        "response_id": "response-1",
        "content": json.dumps(response),
        "usage": {"input_tokens": 5, "output_tokens": 2},
        "raw_response": {"id": "response-1", "content": response},
    }


def test_model_call_pulses_execution_heartbeat_while_provider_is_blocking():
    pulses: list[float] = []

    result = _execute_model_call_with_heartbeat(
        _call(),
        _transport,
        heartbeat=lambda: pulses.append(time.monotonic()),
        heartbeat_seconds=0.01,
    )

    assert result.parsed_result == {"decision": "accept"}
    assert len(pulses) >= 3


def test_model_call_fails_closed_when_periodic_heartbeat_is_rejected():
    pulses = 0

    def heartbeat() -> None:
        nonlocal pulses
        pulses += 1
        if pulses >= 2:
            raise RuntimeError("lease lost")

    with pytest.raises(
        ModelCallHeartbeatFailed,
        match="heartbeat was rejected",
    ):
        _execute_model_call_with_heartbeat(
            _call(),
            _transport,
            heartbeat=heartbeat,
            heartbeat_seconds=0.01,
        )


def test_ordinary_model_budget_is_release_bound_and_usage_checked():
    policy = {
        "timeout_seconds": 30,
        "max_stage_calls": 1,
        "max_stage_input_tokens": 1000,
        "max_stage_output_tokens": 100,
        "max_stage_request_bytes": 10_000,
        "max_output_json_bytes_per_token": 16,
        "max_stage_seconds": 60,
    }
    request = {"temperature": 0, "max_output_tokens": 100}
    budget = _ordinary_model_budget(policy, request)
    assert budget["max_stage_calls"] == 1

    with pytest.raises(ReleaseBindingError, match="max_stage_calls"):
        _ordinary_model_budget(
            {key: value for key, value in policy.items() if key != "max_stage_calls"},
            request,
        )

    with pytest.raises(LlmGatewayError) as raised:
        _enforce_model_result_budget(
            LlmCallResult(
                parsed_result={"decision": "accept"},
                audit={
                    "usage": {"input_tokens": 5, "output_tokens": 101},
                    "latency_ms": 1,
                },
            ),
            budget,
        )
    assert raised.value.code == "model_output_budget_exceeded"


def test_ordinary_model_request_budget_fails_before_provider_transmission():
    policy = {
        "timeout_seconds": 30,
        "max_stage_calls": 1,
        "max_stage_input_tokens": 1000,
        "max_stage_output_tokens": 100,
        "max_stage_request_bytes": 10_000,
        "max_output_json_bytes_per_token": 16,
        "max_stage_seconds": 60,
    }
    budget = _ordinary_model_budget(
        policy,
        {"temperature": 0, "max_output_tokens": 100},
    )
    call = _call()
    oversized_capacity = {"task": "one", "capacity": {"minimum_response_bytes": 1601}}
    call = replace(
        call,
        input_evidence=oversized_capacity,
        input_sha256=sha256_json(oversized_capacity),
    )
    with pytest.raises(LlmGatewayError) as raised:
        _enforce_model_request_budget(call, budget)
    assert raised.value.code == "model_minimum_output_budget_exceeded"
    assert raised.value.audit["provider_call_started"] is False

    byte_limited = dict(budget)
    byte_limited["max_stage_request_bytes"] = 1
    with pytest.raises(LlmGatewayError) as raised:
        _enforce_model_request_budget(_call(), byte_limited)
    assert raised.value.code == "model_request_budget_exceeded"
    assert raised.value.audit["provider_call_started"] is False


def test_visual_limits_require_explicit_resource_and_call_budgets():
    policy = {
        "batch_size": 2,
        "max_output_tokens": 1000,
        "timeout_seconds": 30,
        "max_stage_calls": 10,
        "max_stage_input_tokens": 100_000,
        "max_stage_output_tokens": 10_000,
        "max_stage_seconds": 300,
        "max_source_pages": 500,
        "max_candidate_pages": 600,
        "min_free_bytes": 1_000_000,
        "max_render_bundle_bytes": 50_000_000,
    }
    limits = _visual_limits(policy)
    assert limits["max_source_pages"] == 500
    assert limits["max_render_bundle_bytes"] == 50_000_000

    with pytest.raises(VisualReviewError, match="max_candidate_pages"):
        _visual_limits(
            {
                key: value
                for key, value in policy.items()
                if key != "max_candidate_pages"
            }
        )


def test_visual_resource_budget_rejects_low_disk_and_oversized_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root = tmp_path / "render"
    budget = _VisualResourceBudget(
        root=root,
        min_free_bytes=10,
        max_render_bundle_bytes=3,
    )
    monkeypatch.setattr(
        "app.workflow_v3.visual_review.shutil.disk_usage",
        lambda _path: SimpleNamespace(free=9),
    )
    with pytest.raises(VisualReviewError, match="disk reserve"):
        budget.preflight()

    monkeypatch.setattr(
        "app.workflow_v3.visual_review.shutil.disk_usage",
        lambda _path: SimpleNamespace(free=1_000_000),
    )
    root.mkdir()
    payload = root / "page.jpg"
    payload.write_bytes(b"four")
    with pytest.raises(VisualReviewError, match="byte budget"):
        budget.add_file(payload)


def test_candidate_prefix_is_normalized_and_rejects_unsafe_values():
    assert normalize_candidate_prefix("v3/candidates") == "v3/candidates"
    assert normalize_candidate_prefix("tenant-a/candidates/") == "tenant-a/candidates"

    for unsafe in (
        "",
        "/v3/candidates",
        "../formal",
        "v3/../formal",
        "v3//candidates",
        "v3\\candidates",
        "v3/candidates//",
        "v3/\x00/candidates",
    ):
        with pytest.raises(ValueError, match="unsafe|string"):
            normalize_candidate_prefix(unsafe)
