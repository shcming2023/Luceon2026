from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

import httpx

from app.workflow_v3.llm_gateway import (
    LlmGatewayError,
    LlmTransportResponse,
    canonical_json_bytes,
    sha256_text,
)


_REQUEST_KEYS = {
    "call_id",
    "binding",
    "provider",
    "model",
    "parameters",
    "prompt",
    "input",
    "output_schema",
}
_ALLOWED_PARAMETERS = {
    "max_output_tokens",
    "seed",
    "temperature",
    "top_p",
}


@dataclass(frozen=True)
class OpenAiCompatibleRuntime:
    provider: str
    model: str
    base_url: str
    api_key: str


HttpClientFactory = Callable[..., Any]


class OpenAiCompatibleJsonTransport:
    """Secret-isolated transport for one release-bound bounded JSON call.

    The release selects the provider, model and non-secret inference
    parameters.  The runtime supplies only the endpoint and credential.  The
    credential is never merged into the persisted gateway request or returned
    response evidence.
    """

    def __init__(
        self,
        runtime: OpenAiCompatibleRuntime,
        *,
        client_factory: HttpClientFactory = httpx.Client,
    ) -> None:
        _validate_runtime(runtime)
        self._runtime = runtime
        self._client_factory = client_factory

    def __call__(
        self,
        request: Mapping[str, Any],
        timeout_seconds: float,
    ) -> LlmTransportResponse:
        _validate_request(request, self._runtime)
        parameters = dict(request["parameters"])
        payload_parameters = _provider_parameters(parameters)
        payload = {
            "model": self._runtime.model,
            "messages": [
                {"role": "system", "content": str(request["prompt"])},
                {
                    "role": "user",
                    "content": canonical_json_bytes(
                        {
                            "input": request["input"],
                            "output_schema": request["output_schema"],
                        }
                    ).decode("utf-8"),
                },
            ],
            "response_format": {"type": "json_object"},
            **payload_parameters,
        }
        headers = {
            "Authorization": f"Bearer {self._runtime.api_key}",
            "Content-Type": "application/json",
        }
        try:
            with self._client_factory(timeout=timeout_seconds) as client:
                response = client.post(
                    _chat_completions_url(self._runtime.base_url),
                    headers=headers,
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise TimeoutError("bounded LLM provider timed out") from exc
        except httpx.HTTPError as exc:
            raise LlmGatewayError(
                "transport_error",
                "bounded LLM provider transport failed",
                retryable=True,
            ) from exc

        raw = _response_json(response)
        if response.status_code >= 400:
            return LlmTransportResponse(
                status_code=int(response.status_code),
                provider=self._runtime.provider,
                model=self._runtime.model,
                response_id=str(raw.get("id") or ""),
                content=None,
                usage={},
                raw_response=raw,
            )
        return LlmTransportResponse(
            status_code=int(response.status_code),
            provider=self._runtime.provider,
            model=str(raw.get("model") or ""),
            response_id=str(raw.get("id") or ""),
            content=_choice_content(raw),
            usage=raw.get("usage") if isinstance(raw.get("usage"), Mapping) else {},
            raw_response=raw,
        )


def transport_from_runtime_config(
    *,
    release_model_policy: Mapping[str, Any],
    runtime_config: Mapping[str, Any],
    client_factory: HttpClientFactory = httpx.Client,
) -> OpenAiCompatibleJsonTransport:
    """Resolve a release-bound provider against secret-bearing runtime config."""

    if not isinstance(release_model_policy, Mapping):
        raise LlmGatewayError("model_policy_invalid", "release model policy is missing")
    if release_model_policy.get("mode") != "release-scoped-schema-bounded-json":
        raise LlmGatewayError(
            "model_policy_invalid",
            "release does not permit bounded JSON model calls",
        )
    provider = str(release_model_policy.get("provider") or "")
    model = str(release_model_policy.get("model") or "")
    models = runtime_config.get("models")
    llm = models.get("llm") if isinstance(models, Mapping) else None
    if not isinstance(llm, Mapping) or not bool(llm.get("enabled")):
        raise LlmGatewayError("model_runtime_disabled", "ordinary LLM runtime is disabled")
    if str(llm.get("provider") or "") != provider:
        raise LlmGatewayError(
            "provider_binding_mismatch",
            "runtime provider differs from the immutable release policy",
        )
    configured_models = {
        str(llm.get("default_model") or ""),
        str(llm.get("reasoning_model") or ""),
    }
    if not model or model not in configured_models:
        raise LlmGatewayError(
            "provider_binding_mismatch",
            "release model is not an explicitly configured runtime model",
        )
    provider_config = llm.get(provider)
    if not isinstance(provider_config, Mapping):
        raise LlmGatewayError(
            "model_runtime_incomplete",
            "provider runtime configuration is missing",
        )
    runtime = OpenAiCompatibleRuntime(
        provider=provider,
        model=model,
        base_url=str(provider_config.get("base_url") or ""),
        api_key=str(provider_config.get("api_key") or ""),
    )
    _validate_runtime(runtime)
    expected_origin_sha256 = release_model_policy.get(
        "endpoint_origin_sha256"
    )
    if (
        not isinstance(expected_origin_sha256, str)
        or len(expected_origin_sha256) != 64
        or any(
            character not in "0123456789abcdef"
            for character in expected_origin_sha256
        )
    ):
        raise LlmGatewayError(
            "model_policy_invalid",
            "release model policy endpoint_origin_sha256 is missing or invalid",
        )
    if expected_origin_sha256 != _endpoint_origin_sha256(runtime.base_url):
        raise LlmGatewayError(
            "endpoint_binding_mismatch",
            "ordinary LLM endpoint origin differs from the immutable release policy",
        )
    return OpenAiCompatibleJsonTransport(runtime, client_factory=client_factory)


def _validate_runtime(runtime: OpenAiCompatibleRuntime) -> None:
    if not runtime.provider or not runtime.model:
        raise LlmGatewayError(
            "model_runtime_incomplete",
            "provider and model are required",
        )
    if not runtime.api_key.strip():
        raise LlmGatewayError(
            "provider_auth_unavailable",
            "ordinary LLM provider credential is unavailable",
        )
    parsed = urlparse(runtime.base_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise LlmGatewayError(
            "model_runtime_incomplete",
            "ordinary LLM provider requires a valid HTTPS base URL",
        )
    try:
        parsed.port
    except ValueError as exc:
        raise LlmGatewayError(
            "model_runtime_incomplete",
            "ordinary LLM provider base URL has an invalid port",
        ) from exc
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise LlmGatewayError(
            "model_runtime_incomplete",
            "ordinary LLM provider base URL contains forbidden credential or query data",
        )


def _endpoint_origin_sha256(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise LlmGatewayError(
            "model_runtime_incomplete",
            "ordinary LLM provider requires a valid HTTPS origin",
        )
    host = parsed.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = parsed.port
    authority = host
    if port is not None and port != 443:
        authority += f":{port}"
    return sha256_text(f"https://{authority}")


def _validate_request(
    request: Mapping[str, Any],
    runtime: OpenAiCompatibleRuntime,
) -> None:
    if not isinstance(request, Mapping) or set(request) != _REQUEST_KEYS:
        raise LlmGatewayError(
            "transport_request_invalid",
            "bounded LLM transport request fields are not exact",
        )
    if request.get("provider") != runtime.provider or request.get("model") != runtime.model:
        raise LlmGatewayError(
            "provider_binding_mismatch",
            "transport runtime differs from the release-bound call",
        )
    if not isinstance(request.get("binding"), Mapping):
        raise LlmGatewayError("transport_request_invalid", "call binding is missing")
    if not isinstance(request.get("output_schema"), Mapping):
        raise LlmGatewayError("transport_request_invalid", "output schema is missing")
    if not isinstance(request.get("parameters"), Mapping):
        raise LlmGatewayError("transport_request_invalid", "request parameters are invalid")
    unknown = sorted(set(request["parameters"]) - _ALLOWED_PARAMETERS)
    if unknown:
        raise LlmGatewayError(
            "unbounded_parameters",
            "release model policy contains unsupported parameters: " + ", ".join(unknown),
        )


def _provider_parameters(parameters: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"temperature": 0}
    if float(parameters.get("temperature", 0)) != 0:
        raise LlmGatewayError("unbounded_parameters", "bounded calls require temperature=0")
    if "max_output_tokens" in parameters:
        value = parameters["max_output_tokens"]
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 32_000:
            raise LlmGatewayError(
                "unbounded_parameters",
                "max_output_tokens must be an integer in 1..32000",
            )
        result["max_tokens"] = value
    if "top_p" in parameters:
        value = parameters["top_p"]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 < value <= 1:
            raise LlmGatewayError("unbounded_parameters", "top_p must be in (0, 1]")
        result["top_p"] = value
    if "seed" in parameters:
        value = parameters["seed"]
        if not isinstance(value, int) or isinstance(value, bool):
            raise LlmGatewayError("unbounded_parameters", "seed must be an integer")
        result["seed"] = value
    return result


def _chat_completions_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base if base.endswith("/chat/completions") else f"{base}/chat/completions"


def _response_json(response: Any) -> Mapping[str, Any]:
    try:
        value = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise LlmGatewayError(
            "invalid_transport_response",
            "bounded LLM provider returned a non-JSON envelope",
            retryable=int(getattr(response, "status_code", 0)) >= 500,
        ) from exc
    if not isinstance(value, Mapping):
        raise LlmGatewayError(
            "invalid_transport_response",
            "bounded LLM provider returned a non-object envelope",
        )
    return value


def _choice_content(raw: Mapping[str, Any]) -> Any:
    choices = raw.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        return None
    choice = choices[0]
    message = choice.get("message") if isinstance(choice, Mapping) else None
    return message.get("content") if isinstance(message, Mapping) else None
