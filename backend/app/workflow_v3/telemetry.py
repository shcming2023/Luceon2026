from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from sqlalchemy.orm import Session

from app.workflow_v3.llm_gateway import (
    LlmCallResult,
    LlmGatewayError,
    ReleaseBoundLlmCall,
    validate_call_binding,
)
from app.workflow_v3.models import (
    WorkflowV3Job,
    WorkflowV3ModelCall,
    WorkflowV3SkillRelease,
    WorkflowV3StageRun,
)
from app.workflow_v3.pricing import (
    PricingError,
    price_model_usage,
    validate_release_pricing,
)
from app.workflow_v3.state_machine import WorkflowV3TransitionError


_MODEL_TERMINAL = frozenset({"succeeded", "failed", "cancelled"})
_ORDINARY_MODEL_STAGES = frozenset(
    {
        "source_scope_and_order",
        "canonical_block_ledger",
        "outline_reconstruction",
        "semantic_annotation",
        "template_construct_binding",
        "frozen_render_plan",
    }
)
_INDEPENDENT_VISUAL_MODEL_STAGES = frozenset(
    {"independent_full_page_review"}
)


def start_model_call(
    db: Session,
    public_id: str,
    *,
    call: ReleaseBoundLlmCall,
) -> WorkflowV3ModelCall:
    """Persist only immutable bindings and non-secret parameters before I/O."""

    validate_call_binding(call)
    job, stage = _active_job_stage(db, public_id, call.stage_key)
    ordinary_allowed = (
        stage.stage_key in _ORDINARY_MODEL_STAGES
        and stage.owner in {"bounded_llm_and_code", "deterministic_code"}
    )
    independent_visual_allowed = (
        stage.stage_key in _INDEPENDENT_VISUAL_MODEL_STAGES
        and stage.owner == "independent_evaluator"
    )
    if not (ordinary_allowed or independent_visual_allowed):
        raise WorkflowV3TransitionError(
            "this stage does not permit a release-bound model producer"
        )
    if call.release_sha256 != job.skill_release_sha256:
        raise WorkflowV3TransitionError("model call release does not match the job")
    _release, _model_policy, pricing_sha = _release_pricing_for_call(
        db,
        job,
        provider=call.provider,
        model=call.model,
    )
    existing = (
        db.query(WorkflowV3ModelCall)
        .filter(WorkflowV3ModelCall.call_id == call.call_id)
        .first()
    )
    if existing:
        expected = {
            "workflow_job_id": job.id,
            "stage_run_id": stage.id,
            "provider": call.provider,
            "model": call.model,
            "prompt_sha256": call.prompt_sha256,
            "schema_sha256": call.schema_sha256,
            "input_sha256": call.input_sha256,
            "release_sha256": call.release_sha256,
            "attempt": call.attempt_number,
            "parameters_json": WorkflowV3ModelCall.dump(dict(call.request_parameters)),
        }
        if any(getattr(existing, key) != value for key, value in expected.items()):
            raise WorkflowV3TransitionError("model call ID conflicts with another binding")
        if existing.machine_status in _MODEL_TERMINAL:
            raise WorkflowV3TransitionError(
                "model call already has a terminal outcome; replay is forbidden"
            )
        raise WorkflowV3TransitionError(
            "model call is already running; replay is forbidden"
        )
    if ordinary_allowed:
        other = (
            db.query(WorkflowV3ModelCall.call_id)
            .filter(
                WorkflowV3ModelCall.stage_run_id == stage.id,
                WorkflowV3ModelCall.attempt == call.attempt_number,
            )
            .first()
        )
        if other is not None:
            raise WorkflowV3TransitionError(
                "ordinary stage model-call budget is already consumed"
            )
    row = WorkflowV3ModelCall(
        workflow_job_id=job.id,
        stage_run_id=stage.id,
        call_id=call.call_id,
        attempt=call.attempt_number,
        provider=call.provider,
        model=call.model,
        prompt_id=call.prompt_id,
        prompt_version=call.prompt_version,
        prompt_sha256=call.prompt_sha256,
        schema_id=call.schema_id,
        schema_version=call.schema_version,
        schema_sha256=call.schema_sha256,
        input_sha256=call.input_sha256,
        release_sha256=call.release_sha256,
        pricing_snapshot_sha256=pricing_sha,
        machine_status="running",
        parameters_json=WorkflowV3ModelCall.dump(dict(call.request_parameters)),
    )
    db.add(row)
    db.flush()
    return row


def finish_model_call(
    db: Session,
    call_id: str,
    *,
    result: LlmCallResult | None = None,
    error: LlmGatewayError | None = None,
    cancelled: bool = False,
    estimated_cost_microusd: int | None = None,
) -> WorkflowV3ModelCall:
    choices = sum(value is not None for value in (result, error)) + int(cancelled)
    if choices != 1:
        raise WorkflowV3TransitionError("exactly one model terminal outcome is required")
    row = (
        db.query(WorkflowV3ModelCall)
        .filter(WorkflowV3ModelCall.call_id == call_id)
        .with_for_update()
        .one()
    )
    if row.machine_status in _MODEL_TERMINAL:
        expected = (
            "succeeded"
            if result is not None
            else "cancelled"
            if cancelled
            else "failed"
        )
        if row.machine_status != expected:
            raise WorkflowV3TransitionError("model call already has another terminal outcome")
        return row
    audit = dict(result.audit if result is not None else error.audit if error else {})
    if audit and any(
        str(audit.get(key) or "") != expected
        for key, expected in (
            ("release_sha256", row.release_sha256),
            ("prompt_sha256", row.prompt_sha256),
            ("schema_sha256", row.schema_sha256),
            ("input_sha256", row.input_sha256),
        )
    ):
        raise WorkflowV3TransitionError("model result evidence does not match its stored binding")
    row.request_sha256 = str(audit.get("request_sha256") or "")
    row.response_id = str(audit.get("response_id") or "")
    row.raw_response_sha256 = str(audit.get("raw_response_sha256") or "")
    row.output_sha256 = str(audit.get("parsed_result_sha256") or "")
    row.usage_json = WorkflowV3ModelCall.dump(audit.get("usage") or {})
    row.latency_ms = _optional_nonnegative_int(audit.get("latency_ms"))
    row.estimated_cost_microusd = _optional_nonnegative_int(estimated_cost_microusd)
    job = db.get(WorkflowV3Job, row.workflow_job_id)
    release = (
        db.get(WorkflowV3SkillRelease, job.skill_release_id)
        if job is not None
        else None
    )
    if release is None:
        raise WorkflowV3TransitionError(
            "model call release disappeared before cost accounting"
        )
    manifest = release.load(release.manifest_json, {})
    model_policy = manifest.get("model_policy")
    try:
        _snapshot, pricing_sha = validate_release_pricing(model_policy)
    except PricingError as exc:
        raise WorkflowV3TransitionError(
            f"model cost accounting failed closed: {exc.code}: {exc}"
        ) from exc
    if pricing_sha != row.pricing_snapshot_sha256:
        raise WorkflowV3TransitionError(
            "model pricing snapshot drifted after call admission"
        )
    usage = audit.get("usage")
    if isinstance(usage, Mapping) and usage:
        try:
            cost = price_model_usage(
                model_policy=model_policy,
                provider=row.provider,
                model=row.model,
                usage=usage,
            )
        except PricingError as exc:
            raise WorkflowV3TransitionError(
                f"model cost accounting failed closed: {exc.code}: {exc}"
            ) from exc
        row.cost_status = "charged" if result is not None else "charged_failed_call"
        row.cost_currency = cost["currency"]
        row.cost_micro_units = cost["total_micro_units"]
        row.cost_breakdown_json = WorkflowV3ModelCall.dump(cost)
    elif result is not None:
        raise WorkflowV3TransitionError(
            "successful model call has no attributable usage for pricing"
        )
    else:
        row.cost_status = (
            "cancelled_no_attributable_usage"
            if cancelled
            else "failed_no_attributable_usage"
        )
        row.cost_currency = str(_snapshot.get("currency") or "")
        row.cost_micro_units = None
        row.cost_breakdown_json = WorkflowV3ModelCall.dump(
            {
                "schema_version": "luceon.worker-v3-model-cost/v1",
                "pricing_snapshot_sha256": pricing_sha,
                "currency": row.cost_currency,
                "status": row.cost_status,
                "reason": "provider usage absent on terminal non-success",
            }
        )
    row.retryable = bool(error.retryable) if error else False
    row.error_code = "cancelled" if cancelled else str(error.code if error else "")
    row.machine_status = "cancelled" if cancelled else "failed" if error else "succeeded"
    row.finished_at = datetime.utcnow()
    db.flush()
    return row


def _release_pricing_for_call(
    db: Session,
    job: WorkflowV3Job,
    *,
    provider: str,
    model: str,
) -> tuple[WorkflowV3SkillRelease, Mapping[str, Any], str]:
    release = db.get(WorkflowV3SkillRelease, job.skill_release_id)
    if (
        release is None
        or release.manifest_sha256 != job.skill_release_sha256
        or release.release_version != job.skill_release_version
    ):
        raise WorkflowV3TransitionError(
            "model call has no matching immutable skill release"
        )
    manifest = release.load(release.manifest_json, {})
    model_policy = manifest.get("model_policy")
    try:
        snapshot, pricing_sha = validate_release_pricing(model_policy)
    except PricingError as exc:
        raise LlmGatewayError(
            exc.code,
            f"release-bound model pricing is invalid: {exc}",
        ) from exc
    priced = {
        (str(row.get("provider") or ""), str(row.get("model") or ""))
        for row in snapshot.get("models", [])
        if isinstance(row, Mapping)
    }
    if (provider, model) not in priced:
        raise LlmGatewayError(
            "pricing_model_unknown",
            f"release pricing does not cover {provider}/{model}",
        )
    return release, model_policy, pricing_sha


def _active_job_stage(
    db: Session,
    public_id: str,
    stage_key: str,
) -> tuple[WorkflowV3Job, WorkflowV3StageRun]:
    job = db.query(WorkflowV3Job).filter(WorkflowV3Job.public_id == public_id).one()
    if job.current_stage_key != stage_key or job.machine_status in {"cancelled", "succeeded"}:
        raise WorkflowV3TransitionError("telemetry is not attached to the active job stage")
    stage = (
        db.query(WorkflowV3StageRun)
        .filter(
            WorkflowV3StageRun.workflow_job_id == job.id,
            WorkflowV3StageRun.stage_key == stage_key,
        )
        .order_by(WorkflowV3StageRun.attempt.desc())
        .first()
    )
    if not stage or stage.machine_status in {"cancelled", "succeeded"}:
        raise WorkflowV3TransitionError("telemetry stage is not active")
    return job, stage


def _optional_nonnegative_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise WorkflowV3TransitionError("telemetry numeric value is invalid") from exc
    if parsed < 0:
        raise WorkflowV3TransitionError("telemetry numeric value cannot be negative")
    return parsed
