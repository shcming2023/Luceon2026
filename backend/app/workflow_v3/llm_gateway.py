from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping


HEX_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SECRET_PARAMETER_NAMES = {
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "auth_token",
    "bearer_token",
}


class LlmGatewayError(RuntimeError):
    """A fail-closed gateway error with evidence suitable for persistence."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        audit: Mapping[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.audit = dict(audit or {})
        self.retryable = retryable


@dataclass(frozen=True)
class ReleaseBoundLlmCall:
    call_id: str
    release_id: str
    release_sha256: str
    stage_key: str
    prompt_id: str
    prompt_version: str
    prompt_sha256: str
    prompt_text: str
    schema_id: str
    schema_version: str
    schema_sha256: str
    output_schema: Mapping[str, Any]
    input_sha256: str
    input_evidence: Any
    provider: str
    model: str
    request_parameters: Mapping[str, Any] = field(default_factory=dict)
    allowed_choices: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    timeout_seconds: float = 180.0
    attempt_number: int = 1


@dataclass(frozen=True)
class LlmTransportResponse:
    status_code: int
    provider: str
    model: str
    response_id: str
    content: Any
    usage: Mapping[str, Any]
    raw_response: Any


@dataclass(frozen=True)
class LlmCallResult:
    parsed_result: Any
    audit: Mapping[str, Any]


Transport = Callable[[Mapping[str, Any], float], LlmTransportResponse | Mapping[str, Any]]


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise LlmGatewayError("non_canonical_json", "value is not canonical JSON") from exc


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def validate_call_binding(call: ReleaseBoundLlmCall) -> None:
    required_text = {
        "call_id": call.call_id,
        "release_id": call.release_id,
        "stage_key": call.stage_key,
        "prompt_id": call.prompt_id,
        "prompt_version": call.prompt_version,
        "schema_id": call.schema_id,
        "schema_version": call.schema_version,
        "provider": call.provider,
        "model": call.model,
    }
    missing = sorted(name for name, value in required_text.items() if not str(value).strip())
    if missing:
        raise LlmGatewayError("binding_incomplete", f"missing binding fields: {', '.join(missing)}")

    _require_sha256("release_sha256", call.release_sha256)
    _require_sha256("prompt_sha256", call.prompt_sha256)
    _require_sha256("schema_sha256", call.schema_sha256)
    _require_sha256("input_sha256", call.input_sha256)
    if sha256_text(call.prompt_text) != call.prompt_sha256:
        raise LlmGatewayError("prompt_hash_mismatch", "prompt bytes do not match the release-bound hash")
    if sha256_json(call.output_schema) != call.schema_sha256:
        raise LlmGatewayError("schema_hash_mismatch", "output schema does not match the release-bound hash")
    if sha256_json(call.input_evidence) != call.input_sha256:
        raise LlmGatewayError("input_hash_mismatch", "input evidence does not match the release-bound hash")
    if not isinstance(call.output_schema, Mapping) or call.output_schema.get("type") != "object":
        raise LlmGatewayError("unsupported_schema", "the release-bound output schema must describe one JSON object")
    validate_json_schema_definition(call.output_schema)
    if not 0 < float(call.timeout_seconds) <= 900:
        raise LlmGatewayError("invalid_timeout", "timeout_seconds must be greater than zero and at most 900")
    if call.attempt_number < 1:
        raise LlmGatewayError("invalid_attempt", "attempt_number must be positive")
    if not isinstance(call.request_parameters, Mapping):
        raise LlmGatewayError("invalid_parameters", "request parameters must be an object")
    secret_keys = _secret_parameter_paths(call.request_parameters)
    if secret_keys:
        raise LlmGatewayError(
            "secret_in_persisted_parameters",
            f"secrets cannot be embedded in the persisted request: {', '.join(secret_keys)}",
        )
    if "temperature" in call.request_parameters and float(call.request_parameters["temperature"]) != 0:
        raise LlmGatewayError("unbounded_parameters", "bounded calls require temperature=0")
    _validate_allowed_choices(call.allowed_choices)


def execute_bounded_call(call: ReleaseBoundLlmCall, transport: Transport) -> LlmCallResult:
    """Execute exactly one release-bound call through an injected transport."""

    validate_call_binding(call)
    started = time.monotonic()
    audit = _base_audit(call)
    request = {
        "call_id": call.call_id,
        "binding": {
            "release_id": call.release_id,
            "release_sha256": call.release_sha256,
            "stage_key": call.stage_key,
            "prompt_id": call.prompt_id,
            "prompt_version": call.prompt_version,
            "prompt_sha256": call.prompt_sha256,
            "schema_id": call.schema_id,
            "schema_version": call.schema_version,
            "schema_sha256": call.schema_sha256,
            "input_sha256": call.input_sha256,
        },
        "provider": call.provider,
        "model": call.model,
        "parameters": dict(call.request_parameters),
        "prompt": call.prompt_text,
        "input": call.input_evidence,
        "output_schema": call.output_schema,
    }
    audit["request_sha256"] = sha256_json(request)

    try:
        response = _normalize_transport_response(transport(request, call.timeout_seconds))
    except LlmGatewayError as exc:
        if not exc.audit:
            exc.audit = _failed_audit(audit, exc.code, started)
        raise
    except Exception as exc:
        is_timeout = isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower()
        code = "timeout" if is_timeout else "transport_error"
        raise LlmGatewayError(
            code,
            "LLM transport timed out" if is_timeout else "LLM transport failed",
            audit=_failed_audit(audit, code, started),
            retryable=True,
        ) from exc

    audit["http_status"] = response.status_code
    audit["actual_provider"] = response.provider
    audit["actual_model"] = response.model
    audit["response_id"] = response.response_id
    # This is the canonicalizable provider response body only. Runtime
    # credentials and HTTP headers never enter the transport response.
    audit["raw_response"] = response.raw_response
    audit["raw_response_sha256"] = sha256_json(response.raw_response)
    finish_reason = _provider_finish_reason(response.raw_response)
    if finish_reason:
        audit["finish_reason"] = finish_reason
    if response.status_code >= 400:
        if response.status_code in {401, 403}:
            code, retryable = "provider_auth_error", False
        elif response.status_code == 429:
            code, retryable = "rate_limited", True
        elif response.status_code >= 500:
            code, retryable = "provider_error", True
        else:
            code, retryable = "provider_rejected", False
        raise LlmGatewayError(
            code,
            f"LLM provider returned HTTP {response.status_code}",
            audit=_failed_audit(audit, code, started),
            retryable=retryable,
        )
    if response.provider != call.provider or response.model != call.model:
        raise LlmGatewayError(
            "provider_binding_mismatch",
            "LLM provider or model differs from the release-bound request",
            audit=_failed_audit(audit, "provider_binding_mismatch", started),
        )
    if not response.response_id.strip():
        raise LlmGatewayError(
            "missing_response_id",
            "LLM response is missing a provider response id",
            audit=_failed_audit(audit, "missing_response_id", started),
        )
    normalized_usage = _normalize_usage(response.usage)
    if normalized_usage is None:
        raise LlmGatewayError(
            "missing_usage",
            "LLM response is missing attributable token usage",
            audit=_failed_audit(audit, "missing_usage", started),
        )
    audit["usage"] = normalized_usage
    if finish_reason == "length":
        raise LlmGatewayError(
            "output_truncated",
            "LLM provider exhausted the release-bound output budget",
            audit=_failed_audit(audit, "output_truncated", started),
        )

    parsed = _parse_content(response.content, audit, started)
    try:
        validate_json_schema(parsed, call.output_schema)
        _validate_bounded_decisions(parsed, call.allowed_choices)
    except LlmGatewayError as exc:
        if not exc.audit:
            exc.audit = _failed_audit(audit, exc.code, started)
        raise

    audit.update(
        {
            "status": "succeeded",
            "parsed_result_sha256": sha256_json(parsed),
            "latency_ms": round((time.monotonic() - started) * 1000),
            "error_code": "",
        }
    )
    return LlmCallResult(parsed_result=parsed, audit=audit)


JSON_SCHEMA_ALLOWED_KEYWORDS = frozenset(
    {
        "$schema",
        "$id",
        "$defs",
        "$ref",
        "title",
        "description",
        "type",
        "required",
        "properties",
        "additionalProperties",
        "items",
        "minItems",
        "maxItems",
        "uniqueItems",
        "minProperties",
        "maxProperties",
        "enum",
        "const",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "pattern",
    }
)
JSON_SCHEMA_ALLOWED_TYPES = frozenset(
    {"object", "array", "string", "integer", "number", "boolean", "null"}
)
JSON_SCHEMA_REF_ANNOTATIONS = frozenset({"$ref", "title", "description"})


def validate_json_schema_definition(schema: Mapping[str, Any]) -> None:
    """Fail closed unless ``schema`` uses the release-approved local subset."""

    if not isinstance(schema, Mapping) or isinstance(schema, bool):
        raise LlmGatewayError("unsupported_schema", "the JSON schema must be one object")
    root_schema = schema
    _validate_schema_definition(
        schema,
        root_schema=root_schema,
        path="$",
        ref_stack=(),
        is_root=True,
    )


def validate_json_schema(instance: Any, schema: Mapping[str, Any], path: str = "$") -> None:
    """Validate one instance against the small, preflighted V3 schema subset."""

    validate_json_schema_definition(schema)
    _validate_json_schema_instance(
        instance,
        schema,
        root_schema=schema,
        path=path,
        ref_stack=(),
    )


def _validate_schema_definition(
    schema: Any,
    *,
    root_schema: Mapping[str, Any],
    path: str,
    ref_stack: tuple[str, ...],
    is_root: bool,
) -> None:
    if not isinstance(schema, Mapping) or isinstance(schema, bool):
        raise LlmGatewayError(
            "unsupported_schema",
            f"{path} uses an unapproved boolean or non-object schema",
        )
    unsupported = set(schema) - JSON_SCHEMA_ALLOWED_KEYWORDS
    if unsupported:
        raise LlmGatewayError(
            "unsupported_schema",
            f"schema uses unsupported keywords at {path}: {', '.join(sorted(unsupported))}",
        )
    for annotation in ("$schema", "$id", "title", "description"):
        if annotation in schema and not isinstance(schema[annotation], str):
            raise LlmGatewayError(
                "unsupported_schema",
                f"{path}.{annotation} must be a string",
            )

    if "$ref" in schema:
        if set(schema) - JSON_SCHEMA_REF_ANNOTATIONS:
            raise LlmGatewayError(
                "unsupported_schema",
                f"{path} combines $ref with unapproved sibling keywords",
            )
        reference, target = _resolve_local_schema_ref(
            schema["$ref"],
            root_schema=root_schema,
            path=path,
        )
        if reference in ref_stack:
            raise LlmGatewayError(
                "unsupported_schema",
                f"{path} contains a recursive $ref: {reference}",
            )
        _validate_schema_definition(
            target,
            root_schema=root_schema,
            path=f"{path}.$ref({reference})",
            ref_stack=(*ref_stack, reference),
            is_root=False,
        )
        return

    if "$defs" in schema:
        if not is_root:
            raise LlmGatewayError(
                "unsupported_schema",
                f"{path} declares $defs outside the schema root",
            )
        definitions = schema["$defs"]
        if not isinstance(definitions, Mapping) or isinstance(definitions, bool):
            raise LlmGatewayError("unsupported_schema", "$.$defs must be an object")
        for name, definition in definitions.items():
            if not isinstance(name, str) or not name:
                raise LlmGatewayError(
                    "unsupported_schema",
                    "$.$defs contains an empty or non-string definition name",
                )
            _validate_schema_definition(
                definition,
                root_schema=root_schema,
                path=f"$.$defs[{name!r}]",
                ref_stack=(),
                is_root=False,
            )

    expected = schema.get("type")
    if expected is not None and (
        not isinstance(expected, str) or expected not in JSON_SCHEMA_ALLOWED_TYPES
    ):
        raise LlmGatewayError(
            "unsupported_schema",
            f"{path} has an unsupported type",
        )
    if expected is None and not any(key in schema for key in ("enum", "const", "$defs")):
        raise LlmGatewayError(
            "unsupported_schema",
            f"{path} has an unsupported or missing type",
        )

    if "enum" in schema:
        enum_values = schema["enum"]
        if not isinstance(enum_values, list) or not enum_values:
            raise LlmGatewayError("unsupported_schema", f"{path}.enum must be a non-empty array")
        try:
            if len({canonical_json_bytes(value) for value in enum_values}) != len(enum_values):
                raise LlmGatewayError(
                    "unsupported_schema",
                    f"{path}.enum contains duplicate values",
                )
        except LlmGatewayError:
            raise
    if expected is None and "const" not in schema and "enum" not in schema and "$defs" in schema:
        # A root with only definitions has no instance contract and is therefore
        # not admitted by the Worker gateway.
        raise LlmGatewayError(
            "unsupported_schema",
            f"{path} has definitions but no instance constraint",
        )

    _validate_schema_keyword_placement(schema, expected=expected, path=path)

    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping) or isinstance(properties, bool):
        raise LlmGatewayError("unsupported_schema", f"{path}.properties must be an object")
    for name, child_schema in properties.items():
        if not isinstance(name, str):
            raise LlmGatewayError(
                "unsupported_schema",
                f"{path}.properties contains a non-string name",
            )
        _validate_schema_definition(
            child_schema,
            root_schema=root_schema,
            path=f"{path}.properties[{name!r}]",
            ref_stack=ref_stack,
            is_root=False,
        )

    required = schema.get("required", [])
    if (
        not isinstance(required, list)
        or any(not isinstance(name, str) or not name for name in required)
        or len(set(required)) != len(required)
    ):
        raise LlmGatewayError(
            "unsupported_schema",
            f"{path}.required must contain unique non-empty strings",
        )
    additional = schema.get("additionalProperties", True)
    if not isinstance(additional, bool):
        raise LlmGatewayError(
            "unsupported_schema",
            f"{path}.additionalProperties must be boolean",
        )

    if "items" in schema:
        _validate_schema_definition(
            schema["items"],
            root_schema=root_schema,
            path=f"{path}.items",
            ref_stack=ref_stack,
            is_root=False,
        )
    if "uniqueItems" in schema and not isinstance(schema["uniqueItems"], bool):
        raise LlmGatewayError(
            "unsupported_schema",
            f"{path}.uniqueItems must be boolean",
        )
    if "pattern" in schema:
        if not isinstance(schema["pattern"], str):
            raise LlmGatewayError("unsupported_schema", f"{path}.pattern must be a string")
        try:
            re.compile(schema["pattern"])
        except re.error as exc:
            raise LlmGatewayError(
                "unsupported_schema",
                f"{path}.pattern is not a valid regular expression",
            ) from exc

    for minimum_key, maximum_key in (
        ("minItems", "maxItems"),
        ("minProperties", "maxProperties"),
        ("minLength", "maxLength"),
    ):
        _validate_nonnegative_integer_keyword(schema, minimum_key, path)
        _validate_nonnegative_integer_keyword(schema, maximum_key, path)
        if (
            minimum_key in schema
            and maximum_key in schema
            and schema[minimum_key] > schema[maximum_key]
        ):
            raise LlmGatewayError(
                "unsupported_schema",
                f"{path}.{minimum_key} exceeds {maximum_key}",
            )
    for key in ("minimum", "maximum"):
        if key in schema and (
            not isinstance(schema[key], (int, float)) or isinstance(schema[key], bool)
        ):
            raise LlmGatewayError("unsupported_schema", f"{path}.{key} must be numeric")
    if (
        "minimum" in schema
        and "maximum" in schema
        and schema["minimum"] > schema["maximum"]
    ):
        raise LlmGatewayError(
            "unsupported_schema",
            f"{path}.minimum exceeds maximum",
        )


def _validate_schema_keyword_placement(
    schema: Mapping[str, Any],
    *,
    expected: Any,
    path: str,
) -> None:
    constrained_keywords = {
        "object": {"required", "properties", "additionalProperties", "minProperties", "maxProperties"},
        "array": {"items", "minItems", "maxItems", "uniqueItems"},
        "string": {"minLength", "maxLength", "pattern"},
        "integer": {"minimum", "maximum"},
        "number": {"minimum", "maximum"},
        "boolean": set(),
        "null": set(),
        None: set(),
    }
    all_constrained = set().union(*constrained_keywords.values())
    misplaced = (set(schema) & all_constrained) - constrained_keywords[expected]
    if misplaced:
        raise LlmGatewayError(
            "unsupported_schema",
            f"{path} uses keywords incompatible with type {expected!r}: "
            f"{', '.join(sorted(misplaced))}",
        )


def _validate_nonnegative_integer_keyword(
    schema: Mapping[str, Any],
    key: str,
    path: str,
) -> None:
    if key not in schema:
        return
    value = schema[key]
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise LlmGatewayError(
            "unsupported_schema",
            f"{path}.{key} must be a non-negative integer",
        )


def _resolve_local_schema_ref(
    raw_reference: Any,
    *,
    root_schema: Mapping[str, Any],
    path: str,
) -> tuple[str, Mapping[str, Any]]:
    if not isinstance(raw_reference, str) or not raw_reference.startswith("#/$defs/"):
        raise LlmGatewayError(
            "unsupported_schema",
            f"{path} uses an external or unapproved $ref",
        )
    raw_tokens = raw_reference[2:].split("/")
    if len(raw_tokens) < 2 or raw_tokens[0] != "$defs":
        raise LlmGatewayError(
            "unsupported_schema",
            f"{path} uses an unapproved $ref",
        )
    tokens: list[str] = []
    for raw_token in raw_tokens:
        if re.search(r"~(?:[^01]|$)", raw_token):
            raise LlmGatewayError(
                "unsupported_schema",
                f"{path} uses an invalid JSON Pointer escape in $ref",
            )
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if token in {"", ".", ".."}:
            raise LlmGatewayError(
                "unsupported_schema",
                f"{path} uses an escaping or empty $ref token",
            )
        tokens.append(token)

    target: Any = root_schema
    for token in tokens:
        if not isinstance(target, Mapping) or token not in target:
            raise LlmGatewayError(
                "unsupported_schema",
                f"{path} references a nonexistent local schema: {raw_reference}",
            )
        target = target[token]
    if not isinstance(target, Mapping) or isinstance(target, bool):
        raise LlmGatewayError(
            "unsupported_schema",
            f"{path} resolves $ref to an unapproved boolean or non-object schema",
        )
    return raw_reference, target


def _validate_json_schema_instance(
    instance: Any,
    schema: Mapping[str, Any],
    *,
    root_schema: Mapping[str, Any],
    path: str,
    ref_stack: tuple[str, ...],
) -> None:
    if "$ref" in schema:
        reference, target = _resolve_local_schema_ref(
            schema["$ref"],
            root_schema=root_schema,
            path=path,
        )
        if reference in ref_stack:
            raise LlmGatewayError(
                "unsupported_schema",
                f"{path} contains a recursive $ref: {reference}",
            )
        _validate_json_schema_instance(
            instance,
            target,
            root_schema=root_schema,
            path=path,
            ref_stack=(*ref_stack, reference),
        )
        return

    if "enum" in schema and instance not in schema["enum"]:
        raise LlmGatewayError("schema_mismatch", f"{path} is not an allowed enum value")
    if "const" in schema and instance != schema["const"]:
        raise LlmGatewayError("schema_mismatch", f"{path} does not match the required constant")

    expected = schema.get("type")
    if expected is None:
        return
    if expected == "object":
        if not isinstance(instance, dict):
            raise LlmGatewayError("schema_mismatch", f"{path} must be an object")
        if len(instance) < schema.get("minProperties", 0):
            raise LlmGatewayError("schema_mismatch", f"{path} has too few properties")
        if "maxProperties" in schema and len(instance) > schema["maxProperties"]:
            raise LlmGatewayError("schema_mismatch", f"{path} has too many properties")
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        missing = sorted(str(key) for key in required if key not in instance)
        if missing:
            raise LlmGatewayError("schema_mismatch", f"{path} is missing: {', '.join(missing)}")
        if schema.get("additionalProperties") is False:
            extra = sorted(str(key) for key in instance if key not in properties)
            if extra:
                raise LlmGatewayError("schema_mismatch", f"{path} has undeclared fields: {', '.join(extra)}")
        for key, value in instance.items():
            if key in properties:
                _validate_json_schema_instance(
                    value,
                    properties[key],
                    root_schema=root_schema,
                    path=f"{path}.{key}",
                    ref_stack=ref_stack,
                )
    elif expected == "array":
        if not isinstance(instance, list):
            raise LlmGatewayError("schema_mismatch", f"{path} must be an array")
        if len(instance) < schema.get("minItems", 0):
            raise LlmGatewayError("schema_mismatch", f"{path} has too few items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            raise LlmGatewayError("schema_mismatch", f"{path} has too many items")
        if schema.get("uniqueItems") and len({canonical_json_bytes(row) for row in instance}) != len(instance):
            raise LlmGatewayError("schema_mismatch", f"{path} contains duplicate items")
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, value in enumerate(instance):
                _validate_json_schema_instance(
                    value,
                    item_schema,
                    root_schema=root_schema,
                    path=f"{path}[{index}]",
                    ref_stack=ref_stack,
                )
    elif expected == "string":
        if not isinstance(instance, str):
            raise LlmGatewayError("schema_mismatch", f"{path} must be a string")
        if len(instance) < schema.get("minLength", 0):
            raise LlmGatewayError("schema_mismatch", f"{path} is too short")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            raise LlmGatewayError("schema_mismatch", f"{path} is too long")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            raise LlmGatewayError("schema_mismatch", f"{path} does not match its pattern")
    elif expected == "integer":
        if not isinstance(instance, int) or isinstance(instance, bool):
            raise LlmGatewayError("schema_mismatch", f"{path} must be an integer")
        _validate_number_range(instance, schema, path)
    elif expected == "number":
        if not isinstance(instance, (int, float)) or isinstance(instance, bool):
            raise LlmGatewayError("schema_mismatch", f"{path} must be a number")
        _validate_number_range(instance, schema, path)
    elif expected == "boolean":
        if not isinstance(instance, bool):
            raise LlmGatewayError("schema_mismatch", f"{path} must be a boolean")
    elif expected == "null":
        if instance is not None:
            raise LlmGatewayError("schema_mismatch", f"{path} must be null")


def _normalize_transport_response(value: LlmTransportResponse | Mapping[str, Any]) -> LlmTransportResponse:
    if isinstance(value, LlmTransportResponse):
        return value
    if not isinstance(value, Mapping):
        raise LlmGatewayError("invalid_transport_response", "transport returned a non-object response")
    try:
        status_code = int(value.get("status_code", 0))
    except (TypeError, ValueError) as exc:
        raise LlmGatewayError("invalid_transport_response", "transport status is invalid") from exc
    return LlmTransportResponse(
        status_code=status_code,
        provider=str(value.get("provider") or ""),
        model=str(value.get("model") or ""),
        response_id=str(value.get("response_id") or ""),
        content=value.get("content"),
        usage=value.get("usage") if isinstance(value.get("usage"), Mapping) else {},
        raw_response=value.get("raw_response", dict(value)),
    )


def _parse_content(content: Any, audit: dict[str, Any], started: float) -> Any:
    if isinstance(content, str):
        try:
            return json.loads(content)
        except (TypeError, json.JSONDecodeError) as exc:
            raise LlmGatewayError(
                "malformed_json",
                "LLM content is not one valid JSON value",
                audit=_failed_audit(audit, "malformed_json", started),
            ) from exc
    if isinstance(content, (dict, list)):
        return content
    raise LlmGatewayError(
        "malformed_json",
        "LLM content is not JSON",
        audit=_failed_audit(audit, "malformed_json", started),
    )


def _provider_finish_reason(raw_response: Any) -> str:
    if not isinstance(raw_response, Mapping):
        return ""
    choices = raw_response.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        return ""
    choice = choices[0]
    if not isinstance(choice, Mapping):
        return ""
    value = choice.get("finish_reason")
    return value.strip() if isinstance(value, str) else ""


def _normalize_usage(usage: Mapping[str, Any]) -> dict[str, int] | None:
    input_value = usage.get("input_tokens", usage.get("prompt_tokens"))
    output_value = usage.get("output_tokens", usage.get("completion_tokens"))
    if input_value is None or output_value is None:
        return None
    try:
        input_tokens = int(input_value)
        output_tokens = int(output_value)
    except (TypeError, ValueError):
        return None
    if input_tokens < 0 or output_tokens < 0:
        return None
    try:
        total_tokens = int(
            usage.get("total_tokens", input_tokens + output_tokens)
        )
    except (TypeError, ValueError):
        return None
    if total_tokens != input_tokens + output_tokens:
        return None
    normalized = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }
    cache_hit = _provider_usage_token(
        usage,
        (
            "cache_hit_input_tokens",
            "prompt_cache_hit_tokens",
            "cached_input_tokens",
        ),
    )
    cache_miss = _provider_usage_token(
        usage,
        (
            "cache_miss_input_tokens",
            "prompt_cache_miss_tokens",
        ),
    )
    details = usage.get("input_tokens_details", usage.get("prompt_tokens_details"))
    if cache_hit is None and isinstance(details, Mapping):
        cache_hit = _provider_usage_token(details, ("cached_tokens",))
    if cache_hit is not None and cache_miss is None:
        cache_miss = input_tokens - cache_hit
    if cache_miss is not None and cache_hit is None:
        cache_hit = input_tokens - cache_miss
    if (
        cache_hit is not None
        and cache_miss is not None
        and cache_hit >= 0
        and cache_miss >= 0
        and cache_hit + cache_miss == input_tokens
    ):
        normalized["cache_hit_input_tokens"] = cache_hit
        normalized["cache_miss_input_tokens"] = cache_miss
    return normalized


def _provider_usage_token(
    usage: Mapping[str, Any],
    names: tuple[str, ...],
) -> int | None:
    for name in names:
        if name not in usage:
            continue
        value = usage[name]
        if isinstance(value, bool):
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None
    return None


def _validate_allowed_choices(allowed: Mapping[str, tuple[str, ...]]) -> None:
    if not isinstance(allowed, Mapping):
        raise LlmGatewayError("invalid_choice_policy", "allowed_choices must be an object")
    for task_id, options in allowed.items():
        if not str(task_id).strip() or not isinstance(options, tuple) or not options:
            raise LlmGatewayError("invalid_choice_policy", "every bounded task requires immutable options")
        if any(not str(option).strip() for option in options) or len(set(options)) != len(options):
            raise LlmGatewayError("invalid_choice_policy", f"task {task_id} has invalid options")


def _validate_bounded_decisions(result: Any, allowed: Mapping[str, tuple[str, ...]]) -> None:
    if not allowed:
        return
    decisions = result.get("decisions") if isinstance(result, dict) else None
    if not isinstance(decisions, list):
        raise LlmGatewayError("decision_policy_violation", "bounded result must contain a decisions array")
    if all(str(task_id).startswith("candidate:") for task_id in allowed):
        _validate_candidate_decisions(decisions, allowed)
        return
    rows: dict[str, str] = {}
    for decision in decisions:
        if not isinstance(decision, dict):
            raise LlmGatewayError("decision_policy_violation", "each decision must be an object")
        task_id = str(decision.get("task_id") or "")
        option_id = str(decision.get("selected_option_id") or "")
        if task_id not in allowed or task_id in rows:
            raise LlmGatewayError("decision_policy_violation", "result contains an unknown or duplicate task")
        if option_id not in allowed[task_id]:
            raise LlmGatewayError("decision_policy_violation", f"task {task_id} selected an unknown option")
        rows[task_id] = option_id
    if set(rows) != set(allowed):
        raise LlmGatewayError("decision_policy_violation", "result did not decide every bounded task")


def _validate_candidate_decisions(
    decisions: list[Any],
    allowed: Mapping[str, tuple[str, ...]],
) -> None:
    rows: dict[str, str] = {}
    for decision in decisions:
        if not isinstance(decision, dict):
            raise LlmGatewayError(
                "decision_policy_violation",
                "each candidate decision must be an object",
            )
        candidate_index = decision.get("candidate_index")
        if (
            not isinstance(candidate_index, int)
            or isinstance(candidate_index, bool)
            or candidate_index < 0
        ):
            raise LlmGatewayError(
                "decision_policy_violation",
                "candidate decision index must be a nonnegative integer",
            )
        task_id = f"candidate:{candidate_index}"
        option_id = (
            f"{str(decision.get('disposition') or '')}|"
            f"{str(decision.get('semantic_role') or '')}"
        )
        if task_id not in allowed or task_id in rows:
            raise LlmGatewayError(
                "decision_policy_violation",
                "result contains an unknown or duplicate candidate",
            )
        if option_id not in allowed[task_id]:
            raise LlmGatewayError(
                "decision_policy_violation",
                f"{task_id} selected a disposition or role outside its frozen choices",
            )
        rows[task_id] = option_id
    if set(rows) != set(allowed):
        raise LlmGatewayError(
            "decision_policy_violation",
            "result did not decide every bounded candidate",
        )


def _validate_number_range(value: float, schema: Mapping[str, Any], path: str) -> None:
    if "minimum" in schema and value < float(schema["minimum"]):
        raise LlmGatewayError("schema_mismatch", f"{path} is below its minimum")
    if "maximum" in schema and value > float(schema["maximum"]):
        raise LlmGatewayError("schema_mismatch", f"{path} is above its maximum")


def _require_sha256(name: str, value: str) -> None:
    if not HEX_SHA256_RE.fullmatch(str(value)):
        raise LlmGatewayError("invalid_hash", f"{name} must be a lowercase SHA-256")


def _looks_secret_parameter(name: str) -> bool:
    normalized = name.strip().lower().replace("-", "_")
    return normalized in SECRET_PARAMETER_NAMES or any(
        normalized.endswith(suffix)
        for suffix in ("_api_key", "_password", "_secret", "_access_token", "_refresh_token", "_auth_token")
    )


def _secret_parameter_paths(value: Mapping[str, Any], prefix: str = "") -> list[str]:
    found: list[str] = []
    for key, child in value.items():
        name = str(key)
        path = f"{prefix}.{name}" if prefix else name
        if _looks_secret_parameter(name):
            found.append(path)
        if isinstance(child, Mapping):
            found.extend(_secret_parameter_paths(child, path))
        elif isinstance(child, list):
            for index, item in enumerate(child):
                if isinstance(item, Mapping):
                    found.extend(_secret_parameter_paths(item, f"{path}[{index}]"))
    return sorted(found)


def _base_audit(call: ReleaseBoundLlmCall) -> dict[str, Any]:
    return {
        "call_id": call.call_id,
        "status": "running",
        "provider": call.provider,
        "model": call.model,
        "stage_key": call.stage_key,
        "release_id": call.release_id,
        "release_sha256": call.release_sha256,
        "prompt_id": call.prompt_id,
        "prompt_version": call.prompt_version,
        "prompt_sha256": call.prompt_sha256,
        "schema_id": call.schema_id,
        "schema_version": call.schema_version,
        "schema_sha256": call.schema_sha256,
        "input_sha256": call.input_sha256,
        "request_parameters_sha256": sha256_json(call.request_parameters),
        "allowed_choices_sha256": sha256_json(call.allowed_choices),
        "attempt_number": call.attempt_number,
        "timeout_seconds": call.timeout_seconds,
        "retry_count": 0,
    }


def _failed_audit(audit: Mapping[str, Any], code: str, started: float) -> dict[str, Any]:
    return {
        **audit,
        "status": "failed",
        "error_code": code,
        "latency_ms": round((time.monotonic() - started) * 1000),
    }
