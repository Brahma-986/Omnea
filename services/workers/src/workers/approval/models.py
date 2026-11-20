"""Pydantic models and schemas for Temporal Approval Workflows and Activities."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from procure_agents.approval.models import (
    ApprovalContextSnapshot,
    ApprovalPolicyRules,
    BudgetSnapshot,
    ProcurementIntentSummary,
    ReviewerVerdict,
)


class ApprovalWorkflowInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    request_id: str
    requester_id: str
    round: int = 1
    intent: ProcurementIntentSummary
    context: ApprovalContextSnapshot
    policy: ApprovalPolicyRules
    budget: BudgetSnapshot | None = None
    fault_injection: dict[str, Any] = Field(default_factory=dict)
    t1_seconds: int = 86400
    t2_seconds: int = 172800
    t3_seconds: int = 259200
    vendor_has_unresolved_tasks: bool = False


class HumanDecisionSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str
    step_name: str
    action: Literal["approve", "reject", "request_info"]
    comment: str = ""
    slack_ts: str | None = None
    idempotency_key: str = ""


class WorkflowExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal[
        "approved",
        "rejected",
        "expired",
        "cancelled",
        "failed",
        "awaiting_entity_review",
        "in_review",
    ]
    final_verdict: Literal["approve", "reject", "needs_human"]
    verdicts: dict[str, ReviewerVerdict] = Field(default_factory=dict)
    reservation_action: Literal["committed", "released", "none"] = "none"
    reasons: list[str] = Field(default_factory=list)
