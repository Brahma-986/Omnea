"""Policy evaluation and validation engine for procurement approvals."""

from __future__ import annotations

from decimal import Decimal

from procure_agents.approval.models import (
    ApprovalContextSnapshot,
    ApprovalPolicyRules,
    BudgetSnapshot,
    ProcurementIntentSummary,
)


class ApprovalPolicyEngine:
    """Deterministic policy rules evaluator executing before / alongside LLM explainers."""

    def __init__(self, rules: ApprovalPolicyRules) -> None:
        self.rules = rules

    def evaluate_vp_threshold(
        self, intent: ProcurementIntentSummary
    ) -> tuple[bool, Decimal | None]:
        """Check if request spend exceeds the per-currency VP threshold.

        Returns (is_exceeded, threshold_value).
        """
        curr = intent.currency.upper()
        threshold = self.rules.vp_spend_threshold.get(curr)
        if threshold is None:
            # Undefined currency defaults to human escalation
            return True, None
        return intent.estimated_spend > threshold, threshold

    def evaluate_agent_cap(self, intent: ProcurementIntentSummary) -> bool:
        """Check if request spend exceeds autonomous agent ceiling."""
        return intent.estimated_spend > self.rules.agent_autonomous_cap

    def evaluate_high_risk_tags(self, intent: ProcurementIntentSummary) -> list[str]:
        """Return list of high-risk tags present on intent."""
        return [t for t in intent.tags if t in self.rules.high_risk_tags]

    def evaluate_fast_track(
        self,
        _intent: ProcurementIntentSummary,
        context: ApprovalContextSnapshot,
        consecutive_fast_tracks: int = 0,
    ) -> tuple[bool, str]:
        """Evaluate whether request qualifies for expedited fast-track approval."""
        if not self.rules.allow_fast_track:
            return False, "Policy does not permit fast-track renewals"

        if consecutive_fast_tracks >= self.rules.fast_track_consecutive_cap:
            return (
                False,
                f"Consecutive fast-track renewal cap ({self.rules.fast_track_consecutive_cap}) reached; full review required",
            )

        if not context.active_contracts:
            return False, "No active contract exists for fast-track renewal"

        # Valid fast track
        return True, "Eligible for fast-track renewal"

    def evaluate_budget_compliance(
        self,
        intent: ProcurementIntentSummary,
        budget: BudgetSnapshot | None,
    ) -> tuple[str, list[str], bool]:
        """Evaluate department budget headroom.

        Returns: (verdict, reasons, is_hard_reject)
        """
        if budget is None:
            return (
                "needs_human",
                [f"No budget record found for department '{intent.department}'"],
                False,
            )

        if budget.currency.upper() != intent.currency.upper():
            return (
                "needs_human",
                [
                    f"Budget currency mismatch: request is in {intent.currency}, but budget is in {budget.currency}"
                ],
                False,
            )

        remaining = budget.remaining
        if intent.estimated_spend > remaining:
            overage = intent.estimated_spend - remaining
            msg = (
                f"Spend {intent.estimated_spend} {intent.currency} exceeds remaining budget "
                f"{remaining} {budget.currency} by {overage}"
            )
            if self.rules.over_budget_action == "auto_reject":
                return (
                    "reject",
                    [f"Hard reject: {msg} (over_budget_action=auto_reject)"],
                    True,
                )
            return (
                "needs_human",
                [f"Budget overage: {msg} (escalated for human approval)"],
                False,
            )

        return (
            "approve",
            [
                f"Budget headroom verified: {remaining} {budget.currency} remaining after committed spend"
            ],
            False,
        )
