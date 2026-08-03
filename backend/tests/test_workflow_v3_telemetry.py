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
    WorkflowV3Job,
    WorkflowV3SkillRelease,
    WorkflowV3StageRun,
)
from app.workflow_v3.pricing import sha256_json as pricing_sha256_json
from app.workflow_v3.service import create_workflow_job, workflow_job_detail
from app.workflow_v3.state_machine import WorkflowV3TransitionError
from app.workflow_v3.telemetry import (
    finish_model_call,
    start_model_call,
)


@pytest.fixture
def control_plane():
    engine = create_engine("sqlite://")
    WorkflowV3Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory()
    pricing_snapshot = {
        "schema_version": "luceon.worker-v3-pricing-snapshot/v1",
        "snapshot_id": "telemetry-fixture",
        "retrieved_at": "2026-07-26",
        "currency": "CNY",
        "micro_unit_exponent": 6,
        "token_rate_denominator": 1_000_000,
        "rounding": "ceil_each_component_to_micro_unit",
        "sources": [
            {
                "provider": "provider",
                "url": "https://example.test/pricing",
                "retrieved_at": "2026-07-26",
            }
        ],
        "models": [
            {
                "provider": "provider",
                "model": "model",
                "service_region": "fixture",
                "billing_mode": "realtime",
                "inference_mode": "bounded",
                "promotional_rates_excluded": True,
                "cache_pricing_policy": "provider_breakdown_else_all_miss",
                "tiers": [
                    {
                        "id": "all",
                        "input_tokens_min_exclusive": 0,
                        "input_tokens_max_inclusive": 1_000_000,
                        "input_cache_hit_micro_per_million": 1,
                        "input_cache_miss_micro_per_million": 1,
                        "output_micro_per_million": 1,
                    }
                ],
            }
        ],
    }
    release = WorkflowV3SkillRelease(
        release_version="3.0.0-rc.1",
        manifest_sha256="1" * 64,
        package_bucket="releases",
        package_object="worker-v3.tar.gz",
        package_sha256="2" * 64,
        workflow_version=WORKFLOW_VERSION,
        template_sha256="3" * 64,
        runtime_identity_sha256="4" * 64,
        manifest_json=WorkflowV3SkillRelease.dump(
            {
                "model_policy": {
                    "mode": "release-scoped-schema-bounded-json",
                    "network_calls_allowed": True,
                    "provider": "provider",
                    "model": "model",
                    "pricing_snapshot": pricing_snapshot,
                    "pricing_snapshot_sha256": pricing_sha256_json(
                        pricing_snapshot
                    ),
                }
            }
        ),
        status="registered",
        registered_by="test",
    )
    db.add(release)
    db.commit()
    job, _ = create_workflow_job(
        db,
        user_id="u1",
        material_pk=1,
        material_id="pdf-test",
        source_popo_bucket="popo",
        source_popo_object="pdf-test/run/manifest.json",
        source_popo_sha256="5" * 64,
        skill_release_version=release.release_version,
        skill_release_sha256=release.manifest_sha256,
        template_sha256=release.template_sha256,
    )
    first = (
        db.query(WorkflowV3StageRun)
        .filter(
            WorkflowV3StageRun.workflow_job_id == job.id,
            WorkflowV3StageRun.stage_key == "intake_snapshot",
        )
        .one()
    )
    second = (
        db.query(WorkflowV3StageRun)
        .filter(
            WorkflowV3StageRun.workflow_job_id == job.id,
            WorkflowV3StageRun.stage_key == "source_scope_and_order",
        )
        .one()
    )
    first.machine_status = "succeeded"
    second.machine_status = "queued"
    job.current_stage_key = second.stage_key
    db.commit()
    try:
        yield db, job, second
    finally:
        db.close()


def _call(stage_key: str = "source_scope_and_order", **overrides):
    prompt = "Choose only one release-bound option."
    schema = {
        "type": "object",
        "required": ["decision"],
        "additionalProperties": False,
        "properties": {"decision": {"type": "string", "enum": ["include", "exclude"]}},
    }
    evidence = {"source_block_id": "block-1"}
    values = {
        "call_id": "call-1",
        "release_id": "worker-v3-rc1",
        "release_sha256": "1" * 64,
        "stage_key": stage_key,
        "prompt_id": "scope-choice",
        "prompt_version": "v1",
        "prompt_sha256": sha256_text(prompt),
        "prompt_text": prompt,
        "schema_id": "scope-choice",
        "schema_version": "v1",
        "schema_sha256": sha256_json(schema),
        "output_schema": schema,
        "input_sha256": sha256_json(evidence),
        "input_evidence": evidence,
        "provider": "provider",
        "model": "model",
        "request_parameters": {"temperature": 0},
    }
    values.update(overrides)
    return ReleaseBoundLlmCall(**values)


def _transport(_request, _timeout):
    response = {"decision": "include"}
    return {
        "status_code": 200,
        "provider": "provider",
        "model": "model",
        "response_id": "response-1",
        "content": json.dumps(response),
        "usage": {"input_tokens": 10, "output_tokens": 2},
        "raw_response": {"id": "response-1", "content": response},
    }


def test_model_call_persists_hashes_usage_and_no_plaintext(control_plane):
    db, job, _stage = control_plane
    call = _call()
    start_model_call(db, job.public_id, call=call)
    result = execute_bounded_call(call, _transport)
    row = finish_model_call(db, call.call_id, result=result, estimated_cost_microusd=123)
    db.commit()

    assert row.machine_status == "succeeded"
    assert row.response_id == "response-1"
    assert row.output_sha256 == sha256_json(result.parsed_result)
    assert row.load(row.usage_json, {})["total_tokens"] == 12
    assert row.estimated_cost_microusd == 123
    assert call.prompt_text not in row.parameters_json
    detail = workflow_job_detail(db, job.public_id)
    assert detail["model_calls"][0]["status"] == "succeeded"
    assert detail["model_calls"][0]["stage_key"] == "source_scope_and_order"


def test_model_call_terminal_replay_is_forbidden(control_plane):
    db, job, _stage = control_plane
    call = _call()
    start_model_call(db, job.public_id, call=call)
    result = execute_bounded_call(call, _transport)
    finish_model_call(db, call.call_id, result=result)
    db.commit()

    with pytest.raises(
        WorkflowV3TransitionError,
        match="terminal outcome; replay is forbidden",
    ):
        start_model_call(db, job.public_id, call=call)


def test_model_call_running_replay_and_second_ordinary_call_are_forbidden(
    control_plane,
):
    db, job, _stage = control_plane
    call = _call()
    start_model_call(db, job.public_id, call=call)
    db.flush()

    with pytest.raises(
        WorkflowV3TransitionError,
        match="already running; replay is forbidden",
    ):
        start_model_call(db, job.public_id, call=call)

    with pytest.raises(
        WorkflowV3TransitionError,
        match="budget is already consumed",
    ):
        start_model_call(
            db,
            job.public_id,
            call=_call(call_id="call-2"),
        )


def test_model_call_fails_closed_on_binding_or_owner_drift(control_plane):
    db, job, _stage = control_plane
    with pytest.raises(LlmGatewayError, match="secrets"):
        start_model_call(
            db,
            job.public_id,
            call=_call(request_parameters={"authorization": "plaintext"}),
        )

    job.current_stage_key = "intake_snapshot"
    intake = (
        db.query(WorkflowV3StageRun)
        .filter(
            WorkflowV3StageRun.workflow_job_id == job.id,
            WorkflowV3StageRun.stage_key == "intake_snapshot",
        )
        .one()
    )
    intake.machine_status = "queued"
    db.flush()
    with pytest.raises(WorkflowV3TransitionError, match="does not permit"):
        start_model_call(db, job.public_id, call=_call(stage_key="intake_snapshot"))


def test_independent_visual_stage_accepts_only_its_release_bound_call(control_plane):
    db, job, _stage = control_plane
    visual = (
        db.query(WorkflowV3StageRun)
        .filter(
            WorkflowV3StageRun.workflow_job_id == job.id,
            WorkflowV3StageRun.stage_key == "independent_full_page_review",
        )
        .one()
    )
    visual.machine_status = "running"
    job.current_stage_key = visual.stage_key
    db.flush()

    row = start_model_call(
        db,
        job.public_id,
        call=_call(
            stage_key=visual.stage_key,
            call_id="visual-call-1",
        ),
    )

    assert row.stage_run_id == visual.id
    assert row.machine_status == "running"
