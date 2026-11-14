"""Pydantic data models for the Phase 4 Approval Engine with Decimal math and extra='forbid'."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


HARD_REJECT_TRIGGERS: tuple[str, ...] = (
    "hard_reject_sanctions",
    "hard_reject_department_over_cap",
    "hard_reject_vendor_blacklisted",
    "hard_reject_contract_terminated",
    "hard_reject_security_vulnerability",
)


class CitationFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    citation_id: str
    source_table: str
    fact: str
    quote: str | None = None
    as_of: str | datetime


class ApprovalPolicyRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vp_spend_threshold: dict[str, Decimal] = Field(
        default_factory=lambda: {
            "USD": Decimal("50000.00"),
            "EUR": Decimal("45000.00"),
            "GBP": Decimal("40000.00"),
            "CAD": Decimal("65000.00"),
        }
    )
    agent_autonomous_cap: Decimal = Field(default=Decimal("100000.00"))
    allow_fast_track: bool = False
    fast_track_consecutive_cap: int = 3
    high_risk_tags: list[str] = Field(
        default_factory=lambda: ["pii_access", "production_credentials", "regulated_data"]
    )
    over_budget_action: Literal["needs_human", "auto_reject"] = "needs_human"
    hard_reject_triggers: list[str] = Field(
        default_factory=lambda: list(HARD_REJECT_TRIGGERS)
    )


class BudgetSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    department: str
    currency: str
    period: str
    amount: Decimal
    committed: Decimal

    @property
    def remaining(self) -> Decimal:
        return self.amount - self.committed


class ActiveContractSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_id: str
    title: str
    total_value: Decimal
    currency: str
    status: str
    start_date: str
    end_date: str
    auto_renew: bool = False


class SecurityReviewSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_type: str
    status: str  # compliant | remediation_required | not_checked | none
    effective_date: str | None = None
    expiration_date: str | None = None
    audit_period_end: str | None = None
    freshness_status: str = "compliant"
    is_fresh: bool = True
    critical_vulnerabilities_unresolved: int = 0
    remediation_deadline_passed: bool = False


class ApprovalContextSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_hash: str
    as_of: str
    snapshot_age_seconds: int = 0
    hierarchy_source: str = "postgres_authoritative"
    projection_lag: int = 0
    spend_summaries: list[dict[str, Any]] = Field(default_factory=list)
    active_contracts: list[ActiveContractSummary] = Field(default_factory=list)
    security_reviews: list[SecurityReviewSummary] = Field(default_factory=list)
    citations: list[CitationFact] = Field(default_factory=list)


class ProcurementIntentSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    requester_id: str
    department: str
    vendor_name: str
    estimated_spend: Decimal
    annualized_spend: Decimal
    currency: str
    justification: str
    tags: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)


class ReviewerVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer_type: Literal["finance", "security", "legal"]
    verdict: Literal["approve", "reject", "needs_human"]
    reasons: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    policy_version: str = "v1.0"
    model: str = "deterministic_rule_engine"
    prompt_version: str | None = None
    is_hard_reject: bool = False
    hard_reject_trigger: str | None = None
    suggested_assignee: str | None = None
    explanation: str | None = None
    explainer_citations: list[str] = Field(default_factory=list)
    explanation_discarded: bool = False


class AggregatedApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    final_verdict: Literal["approve", "reject", "needs_human"]
    required_reviewers: list[str]
    verdicts: dict[str, ReviewerVerdict]
    reasons: list[str]
    escalation_role: str | None = None
    is_terminal_hard_reject: bool = False
    hard_reject_trigger: str | None = None
