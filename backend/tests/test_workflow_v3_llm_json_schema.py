import copy
import json
from pathlib import Path

import pytest

from app.workflow_v3.llm_gateway import (
    JSON_SCHEMA_ALLOWED_KEYWORDS,
    LlmGatewayError,
    ReleaseBoundLlmCall,
    execute_bounded_call,
    sha256_json,
    sha256_text,
    validate_json_schema,
    validate_json_schema_definition,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_ROOT = REPO_ROOT / "release" / "worker-v3"
SPEC04D_SCHEMA_PATH = (
    RELEASE_ROOT
    / "vendor-skills"
    / "luceon-popo-to-refined-elegantbook"
    / "schemas"
    / "spec04d-render-policy.schema.json"
)
SPEC04D_COMPACT_SCHEMA_PATH = (
    RELEASE_ROOT
    / "schemas"
    / "spec04d-render-compact-review-v1.schema.json"
)


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _valid_delivery_preflight():
    return {
        "estimated_generated_body_bytes_upper_bound": 250_000,
        "estimated_editable_text_bytes_upper_bound": 200_000,
        "largest_atomic_tex_line_bytes_upper_bound": 4_000,
        "evidence_refs": ["capacity-evidence-1"],
    }


def _valid_spec04d_output():
    return {
        "schema_version": "spec04d-render-policy/1.1",
        "policy_id": "render-policy-test",
        "ownership_layer": "profile",
        "review": {
            "status": "closed",
            "decision_refs": ["decision-review-1"],
            "basis": "Release-bound evidence was reviewed.",
        },
        "structure_level_constructs": {},
        "toc_representation": {
            "ownership_layer": "profile",
            "semantic_level_to_entry_type": {"1": "chapter"},
            "overflow_strategy": "localized_depth_override",
            "decision_refs": ["decision-toc-1"],
        },
        "local_heading_construct": {},
        "plain_body_construct": {},
        "safe_textual_fragile_types": [],
        "fragile_types_requiring_media_representation": [],
        "media_constructs": {},
        "source_image_layout": {},
        "unsupported_representation_types": [],
        "prohibitions": [],
        "volume_partition": {
            "mode": "two_volume",
            "decision_refs": ["decision-volume-1"],
            "delivery_capacity_preflight": _valid_delivery_preflight(),
            "volumes": [
                {"delivery_capacity_preflight": _valid_delivery_preflight()},
                {"delivery_capacity_preflight": _valid_delivery_preflight()},
            ],
        },
    }


def _formal_prompt_schema_paths():
    recipe = _json(RELEASE_ROOT / "recipe.current-audit.json")
    prompts = recipe["prompts"]
    paths = []
    for prompt in prompts:
        relative = Path(prompt["output_schema"])
        assembled_path = RELEASE_ROOT / relative
        if assembled_path.is_file():
            paths.append(assembled_path)
            continue
        candidates = sorted(
            path
            for path in (RELEASE_ROOT / "vendor-skills").rglob(relative.name)
            if path.is_file()
        )
        assert len(candidates) == 1, (
            f"formal prompt {prompt['id']} schema {relative} must resolve to "
            "exactly one release source"
        )
        paths.append(candidates[0])
    assert len(paths) == len(prompts)
    return paths


def _schema_keywords(schema):
    found = set()

    def visit(node):
        assert not isinstance(node, bool), "formal prompt schemas cannot use boolean schemas"
        assert isinstance(node, dict)
        found.update(node)
        for child in node.get("properties", {}).values():
            visit(child)
        if "items" in node:
            visit(node["items"])
        for child in node.get("$defs", {}).values():
            visit(child)

    visit(schema)
    return found


def test_every_formal_prompt_output_schema_uses_the_supported_fail_closed_subset():
    paths = _formal_prompt_schema_paths()

    for path in paths:
        schema = _json(path)
        assert _schema_keywords(schema) <= JSON_SCHEMA_ALLOWED_KEYWORDS
        validate_json_schema_definition(schema)


def test_spec06_prompt_and_schema_forbid_nonblocking_findings():
    prompt = (
        RELEASE_ROOT
        / "prompts/spec06-full-page-source-fidelity-review-v1.txt"
    ).read_text(encoding="utf-8")
    schema = _json(
        RELEASE_ROOT
        / "schemas/spec06-full-page-source-fidelity-review-v1.schema.json"
    )

    assert "Every emitted finding is a machine-gate finding" in prompt
    assert "MUST set `blocking` to" in prompt
    assert "Never emit advisory or non-blocking findings" in prompt
    assert "Use status \"passed\" only when `findings` is empty" in prompt
    finding = schema["properties"]["pages"]["items"]["properties"][
        "findings"
    ]["items"]
    assert finding["properties"]["blocking"] == {
        "type": "boolean",
        "const": True,
    }


def test_spec04d_schema_accepts_valid_output_with_local_defs_refs():
    validate_json_schema(_valid_spec04d_output(), _json(SPEC04D_SCHEMA_PATH))


def test_spec04d_compact_schema_accepts_total_closed_review():
    validate_json_schema(
        {
            "schema_version": "luceon.worker-v3-spec04d-compact-review/v1",
            "task_id": "spec04d-compact-test",
            "review_status": "closed",
            "decisions": [
                {
                    "task_id": "structure-source-role:0000",
                    "selected_option_id": "option-0001",
                }
            ],
            "open_reviews": [],
        },
        _json(SPEC04D_COMPACT_SCHEMA_PATH),
    )


def test_spec04d_schema_rejects_empty_semantic_level_mapping():
    output = _valid_spec04d_output()
    output["toc_representation"]["semantic_level_to_entry_type"] = {}

    with pytest.raises(LlmGatewayError, match="too few properties") as exc:
        validate_json_schema(output, _json(SPEC04D_SCHEMA_PATH))

    assert exc.value.code == "schema_mismatch"


@pytest.mark.parametrize(
    "invalid_preflight",
    [
        {
            "estimated_generated_body_bytes_upper_bound": 250_000,
            "estimated_editable_text_bytes_upper_bound": 200_000,
            "largest_atomic_tex_line_bytes_upper_bound": 4_000,
        },
        {
            "estimated_generated_body_bytes_upper_bound": -1,
            "estimated_editable_text_bytes_upper_bound": 200_000,
            "largest_atomic_tex_line_bytes_upper_bound": 4_000,
            "evidence_refs": ["capacity-evidence-1"],
        },
    ],
)
def test_spec04d_schema_rejects_invalid_delivery_capacity_preflight(invalid_preflight):
    output = _valid_spec04d_output()
    output["volume_partition"]["delivery_capacity_preflight"] = invalid_preflight

    with pytest.raises(LlmGatewayError) as exc:
        validate_json_schema(output, _json(SPEC04D_SCHEMA_PATH))

    assert exc.value.code == "schema_mismatch"


def test_schema_preflight_rejects_unknown_local_ref_before_instance_acceptance():
    schema = copy.deepcopy(_json(SPEC04D_SCHEMA_PATH))
    schema["properties"]["volume_partition"]["properties"][
        "delivery_capacity_preflight"
    ]["$ref"] = "#/$defs/notPresent"

    with pytest.raises(LlmGatewayError, match="nonexistent local schema") as exc:
        validate_json_schema(_valid_spec04d_output(), schema)

    assert exc.value.code == "unsupported_schema"


def test_release_bound_call_rejects_unsafe_schema_before_transport():
    schema = {
        "type": "object",
        "properties": {"value": {"$ref": "https://example.test/external.json"}},
    }
    prompt = "Return one release-bound JSON object."
    evidence = {"source": "frozen"}
    call = ReleaseBoundLlmCall(
        call_id="schema-preflight-call",
        release_id="worker-v3-test",
        release_sha256="1" * 64,
        stage_key="frozen_render_plan",
        prompt_id="schema-preflight",
        prompt_version="v1",
        prompt_sha256=sha256_text(prompt),
        prompt_text=prompt,
        schema_id="unsafe-test-schema",
        schema_version="v1",
        schema_sha256=sha256_json(schema),
        output_schema=schema,
        input_sha256=sha256_json(evidence),
        input_evidence=evidence,
        provider="test-provider",
        model="test-model",
        request_parameters={"temperature": 0},
    )
    transport_calls = []

    with pytest.raises(LlmGatewayError) as exc:
        execute_bounded_call(call, lambda *_args: transport_calls.append(True))

    assert exc.value.code == "unsupported_schema"
    assert transport_calls == []


@pytest.mark.parametrize(
    "schema",
    [
        True,
        {"type": "object", "futureKeyword": True},
        {
            "type": "object",
            "properties": {"value": {"$ref": "https://example.test/schema.json"}},
        },
        {
            "type": "object",
            "$defs": {"value": {"type": "string"}},
            "properties": {"value": {"$ref": "#/$defs/../value"}},
        },
        {
            "type": "object",
            "$defs": {"node": {"$ref": "#/$defs/node"}},
            "properties": {"node": {"$ref": "#/$defs/node"}},
        },
        {"type": "object", "properties": {"value": False}},
    ],
)
def test_schema_preflight_rejects_unapproved_or_unsafe_capabilities(schema):
    with pytest.raises(LlmGatewayError) as exc:
        validate_json_schema_definition(schema)

    assert exc.value.code == "unsupported_schema"


def test_max_properties_is_enforced_without_opening_new_schema_capabilities():
    schema = {
        "type": "object",
        "minProperties": 1,
        "maxProperties": 1,
        "additionalProperties": True,
    }
    validate_json_schema({"one": 1}, schema)

    with pytest.raises(LlmGatewayError, match="too many properties") as exc:
        validate_json_schema({"one": 1, "two": 2}, schema)

    assert exc.value.code == "schema_mismatch"
