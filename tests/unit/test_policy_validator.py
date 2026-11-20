"""Unit tests for approval policy validator rules, schema constraints, and reviewer definitions."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from procure_agents.approval.models import ApprovalPolicyRules
from procure_agents.approval.policy import ApprovalPolicyEngine


def test_policy_validator_unknown_key_rejected() -> None:
    """Unknown policy rule keys must be strictly forbidden."""
    with pytest.raises(ValidationError) as exc_info:
        ApprovalPolicyRules(
            vp_spend_threshold={"USD": Decimal("50000.00")},
            unknown_arbitrary_key=12345,  # type: ignore
        )
    assert "Extra inputs are not permitted" in str(exc_info.value)


def test_policy_validator_missing_finance_threshold_defaults() -> None:
    """When a currency is missing from vp_spend_threshold, policy engine defaults to human escalation."""
    rules = ApprovalPolicyRules(
        vp_spend_threshold={"USD": Decimal("50000.00")}  # Missing EUR, GBP, JPY
    )
    engine = ApprovalPolicyEngine(rules)

    from procure_agents.approval.models import ProcurementIntentSummary

    intent_jpy = ProcurementIntentSummary(
        request_id="req_jpy",
        requester_id="usr_tokyo",
        department="Engineering",
        vendor_name="JapanCloud",
        estimated_spend=Decimal("1000000"),
        annualized_spend=Decimal("1000000"),
        currency="JPY",  # Not in threshold map
        justification="Cloud infra",
    )
    is_exceeded, threshold = engine.evaluate_vp_threshold(intent_jpy)
    # Undefined currency must safely escalate
    assert is_exceeded is True
    assert threshold is None


def test_policy_validator_empty_reviewers_or_tags() -> None:
    """Validation of high risk tags list and empty reviewer collections."""
    rules = ApprovalPolicyRules(high_risk_tags=[])
    engine = ApprovalPolicyEngine(rules)

    from procure_agents.approval.models import ProcurementIntentSummary

    intent = ProcurementIntentSummary(
        request_id="req_empty_tags",
        requester_id="usr_1",
        department="IT",
        vendor_name="StandardTool",
        estimated_spend=Decimal("500.00"),
        annualized_spend=Decimal("500.00"),
        currency="USD",
        justification="Standard IT purchase",
        tags=["pii_access"],
    )
    # With empty high_risk_tags policy, no tags are flagged
    flagged = engine.evaluate_high_risk_tags(intent)
    assert flagged == []


def test_policy_validator_invalid_over_budget_action() -> None:
    """Invalid over_budget_action enum values must be rejected."""
    with pytest.raises(ValidationError) as exc_info:
        ApprovalPolicyRules(over_budget_action="invalid_action")  # type: ignore
    assert "Input should be 'needs_human' or 'auto_reject'" in str(exc_info.value)


def test_consecutive_fast_track_fourth_renewal_cap() -> None:
    """Fast-track renewal counter keyed by (vendor, department, category) caps at 3; 4th renewal requires full review."""
    from procure_agents.approval.models import (
        ActiveContractSummary,
        ApprovalContextSnapshot,
        ProcurementIntentSummary,
    )

    rules = ApprovalPolicyRules(allow_fast_track=True, fast_track_consecutive_cap=3)
    engine = ApprovalPolicyEngine(rules)

    intent = ProcurementIntentSummary(
        request_id="req_ft_1",
        requester_id="usr_1",
        department="Engineering",
        vendor_name="Acme Corp",
        estimated_spend=Decimal("5000.00"),
        annualized_spend=Decimal("5000.00"),
        currency="USD",
        justification="SaaS subscription renewal",
    )
    context = ApprovalContextSnapshot(
        snapshot_hash="hash123",
        as_of="2026-11-13T00:00:00Z",
        active_contracts=[
            ActiveContractSummary(
                contract_id="c_1",
                title="Acme Master",
                total_value=Decimal("5000.00"),
                currency="USD",
                status="active",
                start_date="2025-01-01",
                end_date="2026-01-01",
                auto_renew=True,
            )
        ],
    )

    # Fast-track counter dictionary tracking (vendor, department, category)
    key = ("vendor_acme", "Engineering", "Software")
    approval_record_counters: dict[tuple[str, str, str], int] = {key: 0}

    # 1st renewal (consecutive count = 0 -> 1): Eligible
    ok, msg = engine.evaluate_fast_track(
        intent, context, consecutive_fast_tracks=approval_record_counters[key]
    )
    assert ok is True
    assert msg == "Eligible for fast-track renewal"
    approval_record_counters[key] += 1

    # 2nd renewal (consecutive count = 1 -> 2): Eligible
    ok, msg = engine.evaluate_fast_track(
        intent, context, consecutive_fast_tracks=approval_record_counters[key]
    )
    assert ok is True
    approval_record_counters[key] += 1

    # 3rd renewal (consecutive count = 2 -> 3): Eligible
    ok, msg = engine.evaluate_fast_track(
        intent, context, consecutive_fast_tracks=approval_record_counters[key]
    )
    assert ok is True
    approval_record_counters[key] += 1

    # 4th renewal (consecutive count = 3 == cap): BLOCKED, requires full review
    assert approval_record_counters[key] == 3
    ok, msg = engine.evaluate_fast_track(
        intent, context, consecutive_fast_tracks=approval_record_counters[key]
    )
    assert ok is False
    assert "Consecutive fast-track renewal cap (3) reached; full review required" in msg
