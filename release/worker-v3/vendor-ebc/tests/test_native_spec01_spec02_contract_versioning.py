from __future__ import annotations

import pytest

from scripts.validate_native_spec01_spec02 import (
    producer_contract,
    validate_rule_id_expression,
)


def test_current_producer_contract_requires_composite_and_page_strategy_gates() -> None:
    assert producer_contract("native-spec01-spec02/1.3.3") == {
        "materialized_rehash": True,
        "composite_relationships": True,
        "page_reading_strategies": True,
    }


def test_content_correction_producer_contract_is_current_and_explicit() -> None:
    assert producer_contract("native-spec01-spec02/1.4.0") == {
        "materialized_rehash": True,
        "composite_relationships": True,
        "page_reading_strategies": True,
        "content_corrections": True,
    }


def test_unknown_future_producer_fails_closed() -> None:
    with pytest.raises(ValueError, match="unsupported source producer contract"):
        producer_contract("native-spec01-spec02/1.5.0")


def test_current_composite_decision_rule_ids_are_normative() -> None:
    assert validate_rule_id_expression("RO-R01/RO-R03/RO-R06/RO-H08") == [
        "RO-R01",
        "RO-R03",
        "RO-R06",
        "RO-H08",
    ]


def test_unknown_rule_id_fails_closed() -> None:
    with pytest.raises(ValueError, match="unknown normative rule ids"):
        validate_rule_id_expression("RO-R01/RO-H12")
