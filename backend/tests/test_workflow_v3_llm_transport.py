from __future__ import annotations

import json

import httpx
import pytest

from app.workflow_v3.llm_gateway import (
    LlmGatewayError,
    ReleaseBoundLlmCall,
    execute_bounded_call,
    sha256_json,
    sha256_text,
)
from app.workflow_v3.llm_transport import transport_from_runtime_config


class _Response:
    def __init__(self, status_code: int, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class _Client:
    def __init__(self, response, captured, *, timeout_error=False, **kwargs):
        self.response = response
        self.captured = captured
        self.timeout_error = timeout_error
        self.captured["timeout"] = kwargs["timeout"]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def post(self, url, *, headers, json):
        self.captured.update({"url": url, "headers": headers, "payload": json})
        if self.timeout_error:
            raise httpx.ReadTimeout("slow")
        return self.response


def _runtime():
    return {
        "models": {
            "llm": {
                "enabled": True,
                "provider": "deepseek",
                "default_model": "deepseek-v4-flash",
                "reasoning_model": "deepseek-v4-pro",
                "deepseek": {
                    "base_url": "https://api.deepseek.example/v1",
                    "api_key": "runtime-only-secret",
                },
            }
        }
    }


def _policy():
    return {
        "mode": "release-scoped-schema-bounded-json",
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "endpoint_origin_sha256": sha256_text(
            "https://api.deepseek.example"
        ),
        "request_parameters": {
            "temperature": 0,
            "max_output_tokens": 1000,
            "thinking": {"type": "disabled"},
        },
    }


def _call():
    prompt = "Choose one supplied option and return JSON."
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["decisions"],
        "properties": {
            "decisions": {
                "type": "array",
                "minItems": 1,
                "maxItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["task_id", "selected_option_id"],
                    "properties": {
                        "task_id": {"type": "string"},
                        "selected_option_id": {"type": "string"},
                    },
                },
            }
        },
    }
    evidence = {"tasks": [{"task_id": "t1", "options": ["a", "b"]}]}
    return ReleaseBoundLlmCall(
        call_id="call-transport-1",
        release_id="worker-v3-rc.1",
        release_sha256="1" * 64,
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
        provider="deepseek",
        model="deepseek-v4-flash",
        request_parameters={
            "temperature": 0,
            "max_output_tokens": 1000,
            "thinking": {"type": "disabled"},
        },
        allowed_choices={"t1": ("a", "b")},
        timeout_seconds=30,
    )


def _success():
    return _Response(
        200,
        {
            "id": "response-1",
            "model": "deepseek-v4-flash",
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "decisions": [
                                    {"task_id": "t1", "selected_option_id": "a"}
                                ]
                            }
                        )
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 42,
                "completion_tokens": 7,
                "total_tokens": 49,
            },
        },
    )


def _candidate_call():
    call = _call()
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["decisions"],
        "properties": {
            "decisions": {
                "type": "array",
                "minItems": 1,
                "maxItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "candidate_index",
                        "option_index",
                    ],
                    "properties": {
                        "candidate_index": {"type": "integer", "minimum": 0},
                        "option_index": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 99,
                        },
                    },
                },
            }
        },
    }
    return ReleaseBoundLlmCall(
        **{
            **call.__dict__,
            "schema_sha256": sha256_json(schema),
            "output_schema": schema,
            "allowed_choices": {
                "candidate:226": (
                    "0",
                    "1",
                )
            },
        }
    )


def _transport(response=None, *, timeout_error=False):
    captured = {}

    def factory(**kwargs):
        return _Client(
            response or _success(),
            captured,
            timeout_error=timeout_error,
            **kwargs,
        )

    return (
        transport_from_runtime_config(
            release_model_policy=_policy(),
            runtime_config=_runtime(),
            client_factory=factory,
        ),
        captured,
    )


def test_runtime_transport_executes_one_schema_bounded_call_without_persisting_secret():
    transport, captured = _transport()

    result = execute_bounded_call(_call(), transport)

    assert result.parsed_result["decisions"][0]["selected_option_id"] == "a"
    assert result.audit["response_id"] == "response-1"
    assert result.audit["usage"] == {
        "input_tokens": 42,
        "output_tokens": 7,
        "total_tokens": 49,
    }
    assert captured["url"] == "https://api.deepseek.example/v1/chat/completions"
    assert captured["payload"]["temperature"] == 0
    assert captured["payload"]["max_tokens"] == 1000
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert captured["payload"]["thinking"] == {"type": "disabled"}
    provider_task = json.loads(captured["payload"]["messages"][1]["content"])
    assert provider_task == {
        "input": _call().input_evidence,
        "output_schema": _call().output_schema,
    }
    assert "runtime-only-secret" not in json.dumps(
        {"audit": result.audit, "payload": captured["payload"]}
    )
    assert captured["headers"]["Authorization"] == "Bearer runtime-only-secret"


def test_candidate_total_option_policy_rejects_unknown_index():
    transport, _captured = _transport(
        _Response(
            200,
            {
                **_success().json(),
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "decisions": [
                                        {
                                            "candidate_index": 226,
                                            "option_index": 2,
                                        }
                                    ]
                                }
                            )
                        }
                    }
                ],
            },
        )
    )
    call = _candidate_call()

    with pytest.raises(LlmGatewayError) as exc:
        execute_bounded_call(call, transport)

    assert exc.value.code == "decision_policy_violation"
    assert exc.value.audit["status"] == "failed"
    assert exc.value.audit["allowed_choices_sha256"] == sha256_json(
        call.allowed_choices
    )


def test_candidate_total_option_policy_accepts_frozen_index():
    transport, _captured = _transport(
        _Response(
            200,
            {
                **_success().json(),
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "decisions": [
                                        {
                                            "candidate_index": 226,
                                            "option_index": 1,
                                        }
                                    ]
                                }
                            )
                        }
                    }
                ],
            },
        )
    )
    call = _candidate_call()

    result = execute_bounded_call(call, transport)

    assert result.parsed_result["decisions"][0]["option_index"] == 1


@pytest.mark.parametrize(
    "mutation,code",
    [
        (lambda runtime, policy: runtime["models"]["llm"].update(provider="other"), "provider_binding_mismatch"),
        (lambda runtime, policy: policy.update(model="unconfigured-model"), "provider_binding_mismatch"),
        (lambda runtime, policy: runtime["models"]["llm"]["deepseek"].update(api_key=""), "provider_auth_unavailable"),
        (lambda runtime, policy: runtime["models"]["llm"].update(enabled=False), "model_runtime_disabled"),
        (
            lambda runtime, policy: runtime["models"]["llm"]["deepseek"].update(
                base_url="http://api.deepseek.example/v1"
            ),
            "model_runtime_incomplete",
        ),
        (
            lambda runtime, policy: runtime["models"]["llm"]["deepseek"].update(
                base_url="https://other.deepseek.example/v1"
            ),
            "endpoint_binding_mismatch",
        ),
        (
            lambda runtime, policy: runtime["models"]["llm"]["deepseek"].update(
                base_url="https://api.deepseek.example/v1?credential=leak"
            ),
            "model_runtime_incomplete",
        ),
        (
            lambda runtime, policy: policy.pop("endpoint_origin_sha256"),
            "model_policy_invalid",
        ),
    ],
)
def test_runtime_release_provider_binding_fails_closed(mutation, code):
    runtime = _runtime()
    policy = _policy()
    mutation(runtime, policy)

    with pytest.raises(LlmGatewayError) as exc:
        transport_from_runtime_config(
            release_model_policy=policy,
            runtime_config=runtime,
        )

    assert exc.value.code == code


def test_runtime_endpoint_origin_is_normalized_before_release_hash_check():
    runtime = _runtime()
    runtime["models"]["llm"]["deepseek"]["base_url"] = (
        "https://API.DEEPSEEK.EXAMPLE:443/v1"
    )

    transport = transport_from_runtime_config(
        release_model_policy=_policy(),
        runtime_config=runtime,
    )

    assert transport is not None


def test_transport_timeout_is_retryable_but_not_retried_inside_gateway():
    transport, _captured = _transport(timeout_error=True)

    with pytest.raises(LlmGatewayError) as exc:
        execute_bounded_call(_call(), transport)

    assert exc.value.code == "timeout"
    assert exc.value.retryable is True


def test_transport_rejects_release_parameters_outside_bounded_allowlist_before_io():
    transport, captured = _transport()
    call = _call()
    call = ReleaseBoundLlmCall(
        **{
            **call.__dict__,
            "request_parameters": {
                "temperature": 0,
                "max_output_tokens": 1000,
                "thinking": {"type": "disabled"},
                "tools": [{"type": "function"}],
            },
        }
    )

    with pytest.raises(LlmGatewayError) as exc:
        execute_bounded_call(call, transport)

    assert exc.value.code == "unbounded_parameters"
    assert captured == {}


def test_transport_rejects_missing_non_thinking_binding_before_io():
    transport, captured = _transport()
    call = _call()
    call = ReleaseBoundLlmCall(
        **{
            **call.__dict__,
            "request_parameters": {
                "temperature": 0,
                "max_output_tokens": 1000,
            },
        }
    )

    with pytest.raises(LlmGatewayError) as exc:
        execute_bounded_call(call, transport)

    assert exc.value.code == "unbounded_parameters"
    assert captured == {}


def test_provider_length_finish_reason_is_reported_as_output_truncated():
    transport, _captured = _transport(
        _Response(
            200,
            {
                **_success().json(),
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": '{"decisions": ['},
                    }
                ],
            },
        )
    )

    with pytest.raises(LlmGatewayError) as exc:
        execute_bounded_call(_call(), transport)

    assert exc.value.code == "output_truncated"
    assert exc.value.retryable is False
    assert exc.value.audit["finish_reason"] == "length"
    assert exc.value.audit["usage"] == {
        "input_tokens": 42,
        "output_tokens": 7,
        "total_tokens": 49,
    }


def test_provider_model_fallback_is_detected_by_release_bound_gateway():
    transport, _captured = _transport(
        _Response(
            200,
            {
                **_success().json(),
                "model": "provider-fallback-model",
            },
        )
    )

    with pytest.raises(LlmGatewayError) as exc:
        execute_bounded_call(_call(), transport)

    assert exc.value.code == "provider_binding_mismatch"


def test_non_json_provider_envelope_fails_closed():
    transport, _captured = _transport(
        _Response(200, json.JSONDecodeError("bad", "x", 0))
    )

    with pytest.raises(LlmGatewayError) as exc:
        execute_bounded_call(_call(), transport)

    assert exc.value.code == "invalid_transport_response"
