from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any, Iterable, Mapping


PRICING_SCHEMA_VERSION = "luceon.worker-v3-pricing-snapshot/v1"
COST_SCHEMA_VERSION = "luceon.worker-v3-model-cost/v1"
AGGREGATE_SCHEMA_VERSION = "luceon.worker-v3-model-cost-aggregate/v1"
MICRO_UNIT_EXPONENT = 6
TOKEN_RATE_DENOMINATOR = 1_000_000
ROUNDING_RULE = "ceil_each_component_to_micro_unit"
_HEX = frozenset("0123456789abcdef")


class PricingError(ValueError):
    """The release pricing contract or attributable provider usage is invalid."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


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
        raise PricingError(
            "pricing_snapshot_not_canonical",
            "pricing snapshot must be canonical JSON",
        ) from exc


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def validate_release_pricing(
    model_policy: Mapping[str, Any],
    *,
    require_for_network_models: bool = True,
) -> tuple[dict[str, Any], str]:
    """Validate and return the immutable snapshot and its release-bound hash."""

    if not isinstance(model_policy, Mapping):
        raise PricingError("pricing_policy_missing", "model policy must be an object")
    network_models = _network_model_bindings(model_policy)
    snapshot = model_policy.get("pricing_snapshot")
    digest = model_policy.get("pricing_snapshot_sha256")
    if snapshot is None and digest is None and not network_models:
        return {}, ""
    if snapshot is None or digest is None:
        raise PricingError(
            "pricing_snapshot_missing",
            "network model policy must bind a pricing snapshot and SHA-256",
        )
    if not isinstance(snapshot, Mapping):
        raise PricingError(
            "pricing_snapshot_invalid",
            "pricing_snapshot must be an object",
        )
    actual_digest = sha256_json(snapshot)
    _require_sha256(digest, "pricing_snapshot_sha256")
    if digest != actual_digest:
        raise PricingError(
            "pricing_snapshot_hash_mismatch",
            "pricing snapshot SHA-256 differs from its canonical content",
        )
    validated = _validate_snapshot(snapshot)
    if require_for_network_models:
        available = {
            (row["provider"], row["model"])
            for row in validated["models"]
        }
        missing = sorted(network_models - available)
        if missing:
            rendered = ", ".join(f"{provider}/{model}" for provider, model in missing)
            raise PricingError(
                "pricing_model_missing",
                f"release pricing snapshot does not price: {rendered}",
            )
    return validated, actual_digest


def price_model_usage(
    *,
    model_policy: Mapping[str, Any],
    provider: str,
    model: str,
    usage: Mapping[str, Any],
) -> dict[str, Any]:
    """Price one call from actual provider usage using integer micro-units."""

    snapshot, snapshot_sha = validate_release_pricing(model_policy)
    if not snapshot_sha:
        raise PricingError(
            "pricing_snapshot_missing",
            "model call has no release-bound pricing snapshot",
        )
    rows = [
        row
        for row in snapshot["models"]
        if row["provider"] == provider and row["model"] == model
    ]
    if len(rows) != 1:
        raise PricingError(
            "pricing_model_unknown",
            f"unpriced provider/model: {provider}/{model}",
        )
    if not isinstance(usage, Mapping):
        raise PricingError(
            "pricing_usage_missing",
            "provider token usage must be an object",
        )
    input_tokens = _usage_token(usage, "input_tokens")
    output_tokens = _usage_token(usage, "output_tokens")
    if input_tokens is None or output_tokens is None:
        raise PricingError(
            "pricing_usage_missing",
            "provider usage must contain actual input_tokens and output_tokens",
        )
    row = rows[0]
    tier = _select_tier(row["tiers"], input_tokens)
    hit = _usage_token(usage, "cache_hit_input_tokens")
    miss = _usage_token(usage, "cache_miss_input_tokens")
    if hit is None and miss is None:
        hit = 0
        miss = input_tokens
        cache_attribution = "conservative_all_input_at_cache_miss_rate"
    elif hit is None or miss is None or hit + miss != input_tokens:
        raise PricingError(
            "pricing_cache_usage_invalid",
            "provider cache-hit/miss usage must sum to input_tokens",
        )
    else:
        cache_attribution = "actual_provider_cache_breakdown"

    components: list[dict[str, Any]] = []
    cache_policy = row["cache_pricing_policy"]
    if cache_policy == "provider_breakdown_else_all_miss":
        _append_component(
            components,
            kind="input_cache_hit",
            tokens=hit,
            rate=tier["input_cache_hit_micro_per_million"],
        )
        _append_component(
            components,
            kind="input_cache_miss",
            tokens=miss,
            rate=tier["input_cache_miss_micro_per_million"],
        )
    elif cache_policy == "all_input_at_standard_rate":
        _append_component(
            components,
            kind="input_standard",
            tokens=input_tokens,
            rate=tier["input_cache_miss_micro_per_million"],
        )
        cache_attribution = "conservative_all_input_at_standard_rate"
    else:  # pragma: no cover - validation already rejects this.
        raise PricingError("pricing_snapshot_invalid", "unknown cache pricing policy")
    _append_component(
        components,
        kind="output",
        tokens=output_tokens,
        rate=tier["output_micro_per_million"],
    )
    total = sum(component["amount_micro_units"] for component in components)
    return {
        "schema_version": COST_SCHEMA_VERSION,
        "pricing_snapshot_sha256": snapshot_sha,
        "currency": snapshot["currency"],
        "micro_unit_exponent": snapshot["micro_unit_exponent"],
        "rounding": snapshot["rounding"],
        "provider": provider,
        "model": model,
        "service_region": row["service_region"],
        "billing_mode": row["billing_mode"],
        "inference_mode": row["inference_mode"],
        "tier_id": tier["id"],
        "tier_input_tokens_upper_bound": tier["input_tokens_max_inclusive"],
        "cache_attribution": cache_attribution,
        "usage": {
            "input_tokens": input_tokens,
            "cache_hit_input_tokens": hit,
            "cache_miss_input_tokens": miss,
            "output_tokens": output_tokens,
        },
        "components": components,
        "total_micro_units": total,
    }


def aggregate_model_costs(
    rows: Iterable[Any],
    *,
    stage_key_by_id: Mapping[int, str] | None = None,
) -> dict[str, Any]:
    """Aggregate persisted call costs without converting between currencies."""

    stage_key_by_id = stage_key_by_id or {}
    totals: dict[str, int] = defaultdict(int)
    groups: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    status_counts: dict[str, int] = defaultdict(int)
    call_count = 0
    for row in rows:
        call_count += 1
        status = str(getattr(row, "cost_status", "") or "legacy_unaccounted")
        status_counts[status] += 1
        currency = str(getattr(row, "cost_currency", "") or "")
        amount = getattr(row, "cost_micro_units", None)
        stage_key = stage_key_by_id.get(
            int(getattr(row, "stage_run_id")),
            "",
        )
        provider = str(getattr(row, "provider", "") or "")
        model = str(getattr(row, "model", "") or "")
        group_key = (stage_key, provider, model, currency)
        group = groups.setdefault(
            group_key,
            {
                "stage_key": stage_key,
                "provider": provider,
                "model": model,
                "currency": currency,
                "micro_unit_exponent": MICRO_UNIT_EXPONENT if currency else None,
                "call_count": 0,
                "charged_call_count": 0,
                "micro_units": 0,
            },
        )
        group["call_count"] += 1
        if isinstance(amount, int) and not isinstance(amount, bool) and amount >= 0:
            group["charged_call_count"] += 1
            group["micro_units"] += amount
            totals[currency] += amount
    return {
        "schema_version": AGGREGATE_SCHEMA_VERSION,
        "call_count": call_count,
        "status_counts": dict(sorted(status_counts.items())),
        "totals_by_currency": [
            {
                "currency": currency,
                "micro_unit_exponent": MICRO_UNIT_EXPONENT,
                "micro_units": amount,
            }
            for currency, amount in sorted(totals.items())
            if currency
        ],
        "by_stage_model": [
            groups[key]
            for key in sorted(groups)
        ],
    }


def _network_model_bindings(model_policy: Mapping[str, Any]) -> set[tuple[str, str]]:
    rows: set[tuple[str, str]] = set()
    if (
        model_policy.get("network_calls_allowed") is True
        or model_policy.get("mode")
        in {
            "release-scoped-schema-bounded-json",
            "release-scoped-schema-bounded-vision",
        }
    ):
        provider = str(model_policy.get("provider") or "")
        model = str(model_policy.get("model") or "")
        if provider and model:
            rows.add((provider, model))
        ordinary_models = model_policy.get("ordinary_models")
        if provider and isinstance(ordinary_models, list):
            rows.update(
                (provider, value)
                for value in ordinary_models
                if isinstance(value, str) and value
            )
    visual = model_policy.get("visual_review")
    if isinstance(visual, Mapping) and visual.get("mode") == (
        "release-scoped-schema-bounded-vision"
    ):
        provider = str(visual.get("provider") or "")
        model = str(visual.get("model") or "")
        if provider and model:
            rows.add((provider, model))
    return rows


def _validate_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "schema_version",
        "snapshot_id",
        "retrieved_at",
        "currency",
        "micro_unit_exponent",
        "token_rate_denominator",
        "rounding",
        "sources",
        "models",
    }
    if set(snapshot) != expected:
        raise PricingError(
            "pricing_snapshot_invalid",
            "pricing snapshot fields are not exact",
        )
    if snapshot["schema_version"] != PRICING_SCHEMA_VERSION:
        raise PricingError(
            "pricing_snapshot_invalid",
            "unsupported pricing snapshot schema version",
        )
    if not isinstance(snapshot["snapshot_id"], str) or not snapshot["snapshot_id"]:
        raise PricingError("pricing_snapshot_invalid", "snapshot_id is required")
    if (
        not isinstance(snapshot["retrieved_at"], str)
        or len(snapshot["retrieved_at"]) != 10
    ):
        raise PricingError(
            "pricing_snapshot_invalid",
            "retrieved_at must be an ISO date",
        )
    currency = snapshot["currency"]
    if (
        not isinstance(currency, str)
        or len(currency) != 3
        or currency.upper() != currency
    ):
        raise PricingError(
            "pricing_snapshot_invalid",
            "currency must be an uppercase ISO-4217 code",
        )
    if snapshot["micro_unit_exponent"] != MICRO_UNIT_EXPONENT:
        raise PricingError(
            "pricing_snapshot_invalid",
            "pricing snapshot must use currency micro-units",
        )
    if snapshot["token_rate_denominator"] != TOKEN_RATE_DENOMINATOR:
        raise PricingError(
            "pricing_snapshot_invalid",
            "pricing rates must be per one million tokens",
        )
    if snapshot["rounding"] != ROUNDING_RULE:
        raise PricingError(
            "pricing_snapshot_invalid",
            "unsupported pricing rounding rule",
        )
    sources = snapshot["sources"]
    if not isinstance(sources, list) or not sources:
        raise PricingError("pricing_snapshot_invalid", "pricing sources are required")
    for source in sources:
        if not isinstance(source, Mapping) or set(source) != {
            "provider",
            "url",
            "retrieved_at",
        }:
            raise PricingError(
                "pricing_snapshot_invalid",
                "pricing source fields are not exact",
            )
        if not all(isinstance(source[key], str) and source[key] for key in source):
            raise PricingError(
                "pricing_snapshot_invalid",
                "pricing source values are required",
            )
        if not str(source["url"]).startswith("https://"):
            raise PricingError(
                "pricing_snapshot_invalid",
                "pricing source URL must use HTTPS",
            )
    models = snapshot["models"]
    if not isinstance(models, list) or not models:
        raise PricingError("pricing_snapshot_invalid", "priced models are required")
    identities: set[tuple[str, str]] = set()
    validated_models: list[dict[str, Any]] = []
    for model in models:
        validated = _validate_model(model)
        identity = (validated["provider"], validated["model"])
        if identity in identities:
            raise PricingError(
                "pricing_snapshot_invalid",
                "priced provider/model identities must be unique",
            )
        identities.add(identity)
        validated_models.append(validated)
    return {**dict(snapshot), "models": validated_models}


def _validate_model(model: Any) -> dict[str, Any]:
    expected = {
        "provider",
        "model",
        "service_region",
        "billing_mode",
        "inference_mode",
        "promotional_rates_excluded",
        "cache_pricing_policy",
        "tiers",
    }
    if not isinstance(model, Mapping) or set(model) != expected:
        raise PricingError(
            "pricing_snapshot_invalid",
            "priced model fields are not exact",
        )
    for field in (
        "provider",
        "model",
        "service_region",
        "billing_mode",
        "inference_mode",
    ):
        if not isinstance(model[field], str) or not model[field]:
            raise PricingError(
                "pricing_snapshot_invalid",
                f"priced model {field} is required",
            )
    if model["promotional_rates_excluded"] is not True:
        raise PricingError(
            "pricing_snapshot_invalid",
            "release pricing must exclude promotional rates",
        )
    cache_policy = model["cache_pricing_policy"]
    if cache_policy not in {
        "provider_breakdown_else_all_miss",
        "all_input_at_standard_rate",
    }:
        raise PricingError(
            "pricing_snapshot_invalid",
            "unknown cache pricing policy",
        )
    tiers = model["tiers"]
    if not isinstance(tiers, list) or not tiers:
        raise PricingError("pricing_snapshot_invalid", "model pricing tiers are required")
    previous = 0
    validated_tiers: list[dict[str, Any]] = []
    for tier in tiers:
        validated = _validate_tier(tier)
        if validated["input_tokens_min_exclusive"] != previous:
            raise PricingError(
                "pricing_snapshot_invalid",
                "pricing tiers must be contiguous from zero",
            )
        previous = validated["input_tokens_max_inclusive"]
        validated_tiers.append(validated)
    return {**dict(model), "tiers": validated_tiers}


def _validate_tier(tier: Any) -> dict[str, Any]:
    expected = {
        "id",
        "input_tokens_min_exclusive",
        "input_tokens_max_inclusive",
        "input_cache_hit_micro_per_million",
        "input_cache_miss_micro_per_million",
        "output_micro_per_million",
    }
    if not isinstance(tier, Mapping) or set(tier) != expected:
        raise PricingError(
            "pricing_snapshot_invalid",
            "pricing tier fields are not exact",
        )
    if not isinstance(tier["id"], str) or not tier["id"]:
        raise PricingError("pricing_snapshot_invalid", "pricing tier id is required")
    for field in expected - {"id"}:
        value = tier[field]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise PricingError(
                "pricing_snapshot_invalid",
                f"pricing tier {field} must be a nonnegative integer",
            )
    if tier["input_tokens_max_inclusive"] <= tier["input_tokens_min_exclusive"]:
        raise PricingError(
            "pricing_snapshot_invalid",
            "pricing tier token bounds are invalid",
        )
    return dict(tier)


def _select_tier(tiers: list[dict[str, Any]], input_tokens: int) -> dict[str, Any]:
    for tier in tiers:
        if (
            tier["input_tokens_min_exclusive"]
            < input_tokens
            <= tier["input_tokens_max_inclusive"]
        ) or (
            input_tokens == 0
            and tier["input_tokens_min_exclusive"] == 0
        ):
            return tier
    raise PricingError(
        "pricing_tier_missing",
        f"input token count {input_tokens} is outside the release pricing tiers",
    )


def _usage_token(usage: Mapping[str, Any], field: str) -> int | None:
    value = usage.get(field)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise PricingError(
            "pricing_usage_invalid",
            f"{field} must be a nonnegative integer",
        )
    return value


def _append_component(
    components: list[dict[str, Any]],
    *,
    kind: str,
    tokens: int,
    rate: int,
) -> None:
    numerator = tokens * rate
    amount = (
        (numerator + TOKEN_RATE_DENOMINATOR - 1) // TOKEN_RATE_DENOMINATOR
        if numerator
        else 0
    )
    components.append(
        {
            "kind": kind,
            "tokens": tokens,
            "rate_micro_units_per_million_tokens": rate,
            "amount_micro_units": amount,
        }
    )


def _require_sha256(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _HEX for character in value)
    ):
        raise PricingError("pricing_snapshot_invalid", f"{field} must be a SHA-256")
    return value
