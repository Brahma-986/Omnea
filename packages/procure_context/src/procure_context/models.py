"""Pydantic domain models for Graph RAG context engine and entity resolution."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field


class StageScores(BaseModel):
    """Detailed similarity scores from each pipeline stage."""

    jw_score: float = Field(default=0.0, description="Jaro-Winkler string similarity [0.0, 1.0]")
    trigram_score: float = Field(default=0.0, description="Character trigram similarity [0.0, 1.0]")
    embedding_score: float | None = Field(
        default=None, description="pgvector cosine similarity [0.0, 1.0] if available"
    )
    composite_score: float = Field(
        default=0.0, description="Combined composite score for ranking review tasks"
    )


class ResolutionResult(BaseModel):
    """Structured output from entity resolution pipeline."""

    outcome: Literal["MATCH", "SUBSIDIARY_OF", "PROVISIONAL_REVIEW", "NEW_ENTITY"]
    matched_vendor_id: uuid.UUID | None = None
    canonical_name: str | None = None
    confidence: float = Field(..., ge=0.0, le=1.0)
    rule_path: str = Field(
        ...,
        description="Path taken: alias, canonical_key, exact_parent_region, "
        "loose_key, fuzzy_review, low_confidence_new",
    )
    scores: StageScores = Field(default_factory=lambda: StageScores())
    review_task_id: uuid.UUID | None = None
    decision_id: uuid.UUID | None = None
    needs_review: bool = False
    explanation: str = ""
    degraded_reason: str | None = None
    candidate_vendor_ids: list[uuid.UUID] = Field(default_factory=list)


class VendorNode(BaseModel):
    """Neo4j Vendor node projection."""

    id: uuid.UUID
    tenant_id: str
    name: str
    canonical_key: str
    normalized_base: str
    region: str | None = None
    status: str = "active"
    projection_version: int = 1


class ContractNode(BaseModel):
    """Neo4j Contract node projection."""

    id: uuid.UUID
    tenant_id: str
    vendor_id: uuid.UUID
    title: str
    status: str
    total_value: Decimal
    currency: str
    start_date: date
    end_date: date | None = None


class ApprovalNode(BaseModel):
    """Neo4j Approval node projection."""

    id: uuid.UUID
    tenant_id: str
    request_id: uuid.UUID
    vendor_id: uuid.UUID | None = None
    approver_id: str
    status: str
    step: int = 1


class DepartmentNode(BaseModel):
    """Neo4j Department node projection."""

    id: str
    tenant_id: str
    name: str


class DocumentNode(BaseModel):
    """Neo4j Document node projection."""

    id: uuid.UUID
    tenant_id: str
    vendor_id: uuid.UUID
    doc_type: str
    status: str
    effective_date: date
    expiration_date: date


class SpendSummary(BaseModel):
    """Aggregated spend grouped strictly by currency."""

    currency: str
    total_spend: Decimal
    transaction_count: int
    first_transaction_date: date | None = None
    last_transaction_date: date | None = None


class ActiveContractSummary(BaseModel):
    """Summary of active contract for context assembly."""

    contract_id: uuid.UUID
    title: str
    total_value: Decimal
    currency: str
    start_date: date
    end_date: date | None = None
    auto_renew: bool = False


class SecurityReviewSummary(BaseModel):
    """Freshness status for SOC2/ISO27001 documents."""

    doc_type: str
    status: str
    effective_date: date | None = None
    expiration_date: date | None = None
    audit_period_end: date | None = None
    freshness_status: str = "unknown"
    is_fresh: bool = False


class EnterpriseContext(BaseModel):
    """Complete assembled context for a procurement intent."""

    vendor_id: uuid.UUID
    vendor_name: str
    canonical_key: str
    status: str
    parent_vendor_id: uuid.UUID | None = None
    parent_vendor_name: str | None = None
    subsidiary_vendor_ids: list[uuid.UUID] = Field(default_factory=list)
    merged_vendor_ids: list[uuid.UUID] = Field(default_factory=list)
    spend_by_currency: list[SpendSummary] = Field(default_factory=list)
    active_contracts: list[ActiveContractSummary] = Field(default_factory=list)
    prior_approvals_count: int = 0
    security_reviews: list[SecurityReviewSummary] = Field(default_factory=list)
    hierarchy_source: str = "postgres_authoritative"
    projection_lag: int = 0
    as_of: datetime = Field(default_factory=lambda: datetime.now(UTC))
    citations: list[dict[str, Any]] = Field(default_factory=list)
