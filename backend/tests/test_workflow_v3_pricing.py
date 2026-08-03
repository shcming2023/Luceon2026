from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.workflow_v3.contracts import WORKFLOW_VERSION
from app.workflow_v3.llm_gateway import (
    LlmGatewayError,
    ReleaseBoundLlmCall,
    execute_bounded_call,
    sha256_json,
    sha256_text,
)
from app.workflow_v3.models import (
    WorkflowV3Base,
    WorkflowV3SkillRelease,
    WorkflowV3StageRun,
)
from app.workflow_v3.pricing import (
    PricingError,
    aggregate_model_costs,
    price_model_usage,
    sha256_json as pricing_sha256_json,
    validate_release_pricing,
)
from app.workflow_v3.service import create_workflow_job, workflow_job_detail
from app.workflow_v3.telemetry import finish_model_call, start_model_call


def _snapshot() -> dict:
    return {
        "schema_version": "luceon.worker-v3-pricing-snapshot/v1",
        "snapshot_id": "fixture-20260726",
        "retrieved_at": "2026-07-26",
        "currency": "CNY",
        "micro_unit_exponent": 6,
        "token_rate_denominator": 1_000_000,
        "rounding": "ceil_each_component_to_micro_unit",
        "sources": [
            {
                "provider": "deepseek",
                "url": "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
                "retrieved_at": "2026-07-26",
            },
            {
                "provider": "dashscope",
                "url": "https://help.aliyun.com/zh/model-studio/model-pricing",
                "retrieved_at": "2026-07-26",
            },
        ],
        "models": [
            {
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "service_region": "official-api-global",
                "billing_mode": "realtime",
                "inference_mode": "thinking-or-nonthinking-same-rate",
                "promotional_rates_excluded": True,
                "cache_pricing_policy": "provider_breakdown_else_all_miss",
                "tiers": [
                    {
                        "id": "input-0-to-1000000",
                        "input_tokens_min_exclusive": 0,
                        "input_tokens_max_inclusive": 1_000_000,
                        "input_cache_hit_micro_per_million": 20_000,
                        "input_cache_miss_micro_per_million": 1_000_000,
                        "output_micro_per_million": 2_000_000,
                    }
                ],
            },
            {
                "provider": "dashscope",
                "model": "qwen3.7-plus-2026-05-26",
                "service_region": "cn-beijing",
                "billing_mode": "realtime",
                "inference_mode": "non-thinking",
                "promotional_rates_excluded": True,
                "cache_pricing_policy": "all_input_at_standard_rate",
                "tiers": [
                    {
                        "id": "input-0-to-262144",
                        "input_tokens_min_exclusive": 0,
                        "input_tokens_max_inclusive": 262_144,
                        "input_cache_hit_micro_per_million": 2_000_000,
                        "input_cache_miss_micro_per_million": 2_000_000,
                        "output_micro_per_million": 8_000_000,
                    },
                    {
                        "id": "input-262144-to-1048576",
                        "input_tokens_min_exclusive": 262_144,
                        "input_tokens_max_inclusive": 1_048_576,
                        "input_cache_hit_micro_per_million": 6_000_000,
                        "input_cache_miss_micro_per_million": 6_000_000,
                        "output_micro_per_million": 24_000_000,
                    },
                ],
            },
        ],
    }


def _policy(
    *,
    provider: str = "deepseek",
    model: str = "deepseek-v4-flash",
) -> dict:
    snapshot = _snapshot()
    return {
        "mode": "release-scoped-schema-bounded-json",
        "network_calls_allowed": True,
        "provider": provider,
        "model": model,
        "pricing_snapshot": snapshot,
        "pricing_snapshot_sha256": pricing_sha256_json(snapshot),
    }


def _call() -> ReleaseBoundLlmCall:
    prompt = "Return one bounded decision."
    schema = {
        "type": "object",
        "required": ["decision"],
        "additionalProperties": False,
        "properties": {
            "decision": {
                "type": "string",
                "enum": ["include", "exclude"],
            }
        },
    }
    evidence = {"block": "one"}
    return ReleaseBoundLlmCall(
        call_id="cost-call-1",
        release_id="worker-v3-cost-fixture",
        release_sha256="1" * 64,
        stage_key="source_scope_and_order",
        prompt_id="scope",
        prompt_version="v1",
        prompt_sha256=sha256_text(prompt),
        prompt_text=prompt,
        schema_id="scope",
        schema_version="v1",
        schema_sha256=sha256_json(schema),
        output_schema=schema,
        input_sha256=sha256_json(evidence),
        input_evidence=evidence,
        provider="deepseek",
        model="deepseek-v4-flash",
        request_parameters={"temperature": 0},
    )


def _control_plane(*, with_pricing: bool = True):
    engine = create_engine("sqlite://")
    WorkflowV3Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    manifest = {
        "model_policy": _policy() if with_pricing else {
            "mode": "release-scoped-schema-bounded-json",
            "network_calls_allowed": True,
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
        }
    }
    release = WorkflowV3SkillRelease(
        release_version="3.0.0-rc.cost",
        manifest_sha256="1" * 64,
        package_bucket="releases",
        package_object="worker-v3.tar.gz",
        package_sha256="2" * 64,
        workflow_version=WORKFLOW_VERSION,
        template_sha256="3" * 64,
        runtime_identity_sha256="4" * 64,
        manifest_json=WorkflowV3SkillRelease.dump(manifest),
        status="registered",
        registered_by="test",
    )
    db.add(release)
    db.flush()
    job, _ = create_workflow_job(
        db,
        user_id="u1",
        material_pk=1,
        material_id="pdf-cost",
        source_popo_bucket="popo",
        source_popo_object="pdf-cost/run/manifest.json",
        source_popo_sha256="5" * 64,
        skill_release_version=release.release_version,
        skill_release_sha256=release.manifest_sha256,
        template_sha256=release.template_sha256,
    )
    first, stage = (
        db.query(WorkflowV3StageRun)
        .filter(
            WorkflowV3StageRun.workflow_job_id == job.id,
            WorkflowV3StageRun.stage_key.in_(
                {"intake_snapshot", "source_scope_and_order"}
            ),
        )
        .order_by(WorkflowV3StageRun.id)
        .all()
    )
    first.machine_status = "succeeded"
    stage.machine_status = "queued"
    job.current_stage_key = stage.stage_key
    db.commit()
    return db, job, stage


def test_integer_ceiling_and_actual_cache_breakdown_are_auditable():
    cost = price_model_usage(
        model_policy=_policy(),
        provider="deepseek",
        model="deepseek-v4-flash",
        usage={
            "input_tokens": 2,
            "cache_hit_input_tokens": 1,
            "cache_miss_input_tokens": 1,
            "output_tokens": 1,
        },
    )

    assert cost["cache_attribution"] == "actual_provider_cache_breakdown"
    assert [row["amount_micro_units"] for row in cost["components"]] == [1, 1, 2]
    assert cost["total_micro_units"] == 4


def test_missing_cache_attribution_charges_all_input_at_cache_miss_rate():
    cost = price_model_usage(
        model_policy=_policy(),
        provider="deepseek",
        model="deepseek-v4-flash",
        usage={"input_tokens": 500_000, "output_tokens": 100_000},
    )

    assert cost["cache_attribution"] == (
        "conservative_all_input_at_cache_miss_rate"
    )
    assert cost["usage"]["cache_hit_input_tokens"] == 0
    assert cost["usage"]["cache_miss_input_tokens"] == 500_000
    assert cost["total_micro_units"] == 700_000


def test_dashscope_tier_is_selected_from_actual_request_input_tokens():
    low = price_model_usage(
        model_policy=_policy(
            provider="dashscope",
            model="qwen3.7-plus-2026-05-26",
        ),
        provider="dashscope",
        model="qwen3.7-plus-2026-05-26",
        usage={"input_tokens": 262_144, "output_tokens": 1},
    )
    high = price_model_usage(
        model_policy=_policy(
            provider="dashscope",
            model="qwen3.7-plus-2026-05-26",
        ),
        provider="dashscope",
        model="qwen3.7-plus-2026-05-26",
        usage={"input_tokens": 262_145, "output_tokens": 1},
    )

    assert low["tier_id"] == "input-0-to-262144"
    assert high["tier_id"] == "input-262144-to-1048576"
    assert low["components"][0]["rate_micro_units_per_million_tokens"] == 2_000_000
    assert high["components"][0]["rate_micro_units_per_million_tokens"] == 6_000_000
    assert high["cache_attribution"] == "conservative_all_input_at_standard_rate"


def test_unknown_model_missing_usage_and_snapshot_drift_fail_closed():
    with pytest.raises(PricingError, match="unpriced provider/model"):
        price_model_usage(
            model_policy=_policy(),
            provider="deepseek",
            model="unknown",
            usage={"input_tokens": 1, "output_tokens": 1},
        )
    with pytest.raises(PricingError, match="actual input_tokens"):
        price_model_usage(
            model_policy=_policy(),
            provider="deepseek",
            model="deepseek-v4-flash",
            usage={"output_tokens": 1},
        )
    policy = _policy()
    policy["pricing_snapshot"]["retrieved_at"] = "2026-07-27"
    with pytest.raises(PricingError, match="differs"):
        validate_release_pricing(policy)


def test_successful_call_persists_generic_cost_and_api_aggregates_by_stage_model():
    db, job, stage = _control_plane()
    call = _call()
    start_model_call(db, job.public_id, call=call)

    result = execute_bounded_call(
        call,
        lambda _request, _timeout: {
            "status_code": 200,
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "response_id": "cost-response-1",
            "content": json.dumps({"decision": "include"}),
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 2,
                "prompt_cache_hit_tokens": 4,
                "prompt_cache_miss_tokens": 6,
            },
            "raw_response": {"id": "cost-response-1"},
        },
    )
    row = finish_model_call(db, call.call_id, result=result)
    db.commit()

    assert row.cost_status == "charged"
    assert row.cost_currency == "CNY"
    assert row.cost_micro_units == 11
    breakdown = row.load(row.cost_breakdown_json, {})
    assert breakdown["usage"]["cache_hit_input_tokens"] == 4
    assert breakdown["pricing_snapshot_sha256"] == row.pricing_snapshot_sha256
    detail = workflow_job_detail(db, job.public_id)
    assert detail["model_costs"]["totals_by_currency"] == [
        {
            "currency": "CNY",
            "micro_unit_exponent": 6,
            "micro_units": 11,
        }
    ]
    group = detail["model_costs"]["by_stage_model"][0]
    assert group["stage_key"] == stage.stage_key
    assert group["model"] == "deepseek-v4-flash"
    assert group["micro_units"] == 11
    assert aggregate_model_costs([row])["status_counts"] == {"charged": 1}


def test_failed_transport_without_usage_has_explicit_no_cost_status():
    db, job, _stage = _control_plane()
    call = _call()
    start_model_call(db, job.public_id, call=call)
    row = finish_model_call(
        db,
        call.call_id,
        error=LlmGatewayError(
            "transport_error",
            "network unavailable",
            retryable=True,
        ),
    )
    db.commit()

    assert row.machine_status == "failed"
    assert row.cost_status == "failed_no_attributable_usage"
    assert row.cost_currency == "CNY"
    assert row.cost_micro_units is None


def test_model_call_is_rejected_before_io_when_release_has_no_pricing():
    db, job, _stage = _control_plane(with_pricing=False)
    with pytest.raises(LlmGatewayError, match="pricing snapshot"):
        start_model_call(db, job.public_id, call=_call())
