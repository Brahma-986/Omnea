"""SQLAlchemy ORM models for Enterprise Context Engine (vendors, contracts, spend, review tasks)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import UserDefinedType

from procure_core.models.base import Base


class PGVector(UserDefinedType[Any]):
    """Raw vector(dim) SQL type for pgvector without requiring python pgvector package."""

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim

    def get_col_spec(self, **_kw: Any) -> str:
        return f"vector({self.dim})"


class TenantResolutionSettingsModel(Base):
    """Per-tenant resolution policy thresholds."""

    __tablename__ = "tenant_resolution_settings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="v1.0")
    high_priority_threshold: Mapped[Decimal] = mapped_column(
        Numeric(4, 3), nullable=False, default=Decimal("0.880")
    )
    review_threshold: Mapped[Decimal] = mapped_column(
        Numeric(4, 3), nullable=False, default=Decimal("0.650")
    )
    weight_jw: Mapped[Decimal] = mapped_column(
        Numeric(4, 3), nullable=False, default=Decimal("0.500")
    )
    weight_emb: Mapped[Decimal] = mapped_column(
        Numeric(4, 3), nullable=False, default=Decimal("0.500")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "version", name="uq_tenant_settings_version"),
        CheckConstraint(
            "review_threshold < high_priority_threshold", name="chk_review_lt_high_priority"
        ),
        CheckConstraint("weight_jw + weight_emb = 1.000", name="chk_weights_sum_one"),
    )


class VendorModel(Base):
    """Authoritative identity ledger for enterprise vendors."""

    __tablename__ = "vendors"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    canonical_key: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_base: Mapped[str] = mapped_column(String(255), nullable=False)
    region: Mapped[str | None] = mapped_column(String(64), nullable=True)
    legal_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    previous_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    parent_vendor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    merged_into_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "canonical_key", name="uq_vendors_tenant_canonical_key"),
        UniqueConstraint("tenant_id", "id", name="uq_vendors_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "parent_vendor_id"],
            ["vendors.tenant_id", "vendors.id"],
            name="fk_vendors_parent",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "merged_into_id"],
            ["vendors.tenant_id", "vendors.id"],
            name="fk_vendors_merged_into",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('active', 'pending_review', 'merged')",
            name="chk_vendor_status",
        ),
        CheckConstraint(
            "(status = 'merged' AND merged_into_id IS NOT NULL) "
            "OR (status <> 'merged' AND merged_into_id IS NULL)",
            name="chk_vendor_merge_consistency",
        ),
    )

    aliases: Mapped[list[VendorAliasModel]] = relationship(
        "VendorAliasModel", back_populates="vendor"
    )
    contracts: Mapped[list[ContractModel]] = relationship("ContractModel", back_populates="vendor")


class VendorAliasModel(Base):
    """Known aliases and DBA names for vendors."""

    __tablename__ = "vendor_aliases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    vendor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    alias: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="manual")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    vendor: Mapped[VendorModel] = relationship("VendorModel", back_populates="aliases")

    __table_args__ = (
        UniqueConstraint("tenant_id", "normalized_alias", name="uq_vendor_aliases_tenant_alias"),
        ForeignKeyConstraint(
            ["tenant_id", "vendor_id"],
            ["vendors.tenant_id", "vendors.id"],
            name="fk_vendor_aliases_vendor",
            ondelete="RESTRICT",
        ),
    )


class VendorEmbeddingModel(Base):
    """pgvector semantic embedding for vendor names."""

    __tablename__ = "vendor_embeddings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    vendor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    embedding_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="canonical_name"
    )
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    dimension: Mapped[int] = mapped_column(Integer, nullable=False, default=384)
    embedding: Mapped[list[float]] = mapped_column(PGVector(384), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "vendor_id", "embedding_type", "model", name="uq_vendor_embedding_unique"
        ),
        CheckConstraint("dimension = 384", name="chk_embedding_dim_384"),
        ForeignKeyConstraint(
            ["tenant_id", "vendor_id"],
            ["vendors.tenant_id", "vendors.id"],
            name="fk_vendor_embeddings_vendor",
            ondelete="RESTRICT",
        ),
    )


class SpendTransactionModel(Base):
    """Authoritative ledger for spend transactions."""

    __tablename__ = "spend_transactions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    vendor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    category: Mapped[str] = mapped_column(String(128), nullable=False, default="general")
    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "source_system", "source_id", name="uq_spend_tenant_source"),
        ForeignKeyConstraint(
            ["tenant_id", "vendor_id"],
            ["vendors.tenant_id", "vendors.id"],
            name="fk_spend_vendor",
            ondelete="RESTRICT",
        ),
    )


class ContractModel(Base):
    """Vendor contract metadata."""

    __tablename__ = "contracts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    vendor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    total_value: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    auto_renew: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    vendor: Mapped[VendorModel] = relationship("VendorModel", back_populates="contracts")

    @property
    def effective_date(self) -> date:
        return self.start_date

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_contracts_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "vendor_id"],
            ["vendors.tenant_id", "vendors.id"],
            name="fk_contracts_vendor",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "supersedes_id"],
            ["contracts.tenant_id", "contracts.id"],
            name="fk_contracts_supersedes",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('active', 'expired', 'terminated', 'superseded')",
            name="chk_contract_status",
        ),
    )


class VendorSecurityReviewModel(Base):
    """Vendor SOC2, ISO27001, and compliance security reviews."""

    __tablename__ = "vendor_security_reviews"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    vendor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="compliant")
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    expiration_date: Mapped[date] = mapped_column(Date, nullable=False)
    s3_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    @property
    def sha256_hash(self) -> str:
        return self.sha256 or ""

    @property
    def original_filename(self) -> str:
        return str((self.metadata_ or {}).get("filename", "document.pdf"))

    @property
    def pipeline_status(self) -> str:
        return self.status

    @property
    def is_quarantined(self) -> bool:
        return self.status == "quarantined"

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_security_reviews_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "vendor_id"],
            ["vendors.tenant_id", "vendors.id"],
            name="fk_security_reviews_vendor",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('compliant', 'pending_review', 'expired', 'remediation_required', 'quarantined', 'qualified_opinion', 'superseded')",
            name="chk_security_review_status",
        ),
    )


class TprmRiskRegisterModel(Base):
    """Vendor Third-Party Risk Management (TPRM) register."""

    __tablename__ = "tprm_risk_register"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    vendor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    risk_tier: Mapped[str] = mapped_column(String(32), nullable=False, default="medium")
    overall_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending_review"
    )
    findings_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    active_risks: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    next_review_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "vendor_id", name="uq_tprm_risk_register_vendor"),
        ForeignKeyConstraint(
            ["tenant_id", "vendor_id"],
            ["vendors.tenant_id", "vendors.id"],
            name="fk_tprm_risk_register_vendor",
            ondelete="CASCADE",
        ),
    )


class TprmControlFindingModel(Base):
    """Verified control findings extracted from security review documents."""

    __tablename__ = "tprm_control_findings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    vendor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    security_review_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vendor_security_reviews.id", ondelete="CASCADE"),
        nullable=False,
    )
    domain: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="not_found")
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    @property
    def is_verified(self) -> bool:
        return self.verified

    @property
    def confidence_score(self) -> float:
        val = (self.details or {}).get("confidence_score", 1.0)
        try:
            return float(val)
        except Exception:
            return 1.0

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "vendor_id"],
            ["vendors.tenant_id", "vendors.id"],
            name="fk_tprm_findings_vendor",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "status IN ('satisfied', 'qualified', 'remediation_required', 'not_found')",
            name="chk_tprm_finding_status",
        ),
    )


class ResolutionDecisionModel(Base):
    """Audit log of all vendor entity resolution decisions."""

    __tablename__ = "resolution_decisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    settings_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    resolver_version: Mapped[str] = mapped_column(String(32), nullable=False, default="v1.0")
    threshold_set_version: Mapped[str] = mapped_column(String(32), nullable=False, default="v1.0")
    rule_path: Mapped[str] = mapped_column(String(64), nullable=False)
    input_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_form: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_key: Mapped[str] = mapped_column(String(255), nullable=False)
    matched_vendor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    composite_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    degraded_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    needs_rescoring: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    component_scores: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    candidates: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "outcome IN ('match', 'subsidiary_of', 'provisional_review', 'new_entity')",
            name="chk_decision_outcome",
        ),
        CheckConstraint(
            "rule_path IN ("
            "'alias', 'canonical_key', 'exact_parent_region', 'loose_key', "
            "'strong_identifier', 'fuzzy_review', 'embedding_review', 'low_confidence_new')",
            name="chk_decision_rule_path",
        ),
        ForeignKeyConstraint(
            ["settings_id"],
            ["tenant_resolution_settings.id"],
            name="fk_decision_settings",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "matched_vendor_id"],
            ["vendors.tenant_id", "vendors.id"],
            name="fk_decision_vendor",
            ondelete="RESTRICT",
        ),
    )


class IntentVendorResolutionModel(Base):
    """Links procurement intents to vendor resolution decisions."""

    __tablename__ = "intent_vendor_resolutions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    intent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    decision_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "intent_id", "role", "source_name", name="uq_intent_vendor_resolutions"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "intent_id"],
            ["procurement_intents.tenant_id", "procurement_intents.id"],
            name="fk_ivr_intent",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["decision_id"],
            ["resolution_decisions.id"],
            name="fk_ivr_decision",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "vendor_id"],
            ["vendors.tenant_id", "vendors.id"],
            name="fk_ivr_vendor",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "outcome IN ('match', 'subsidiary_of', 'provisional_review', 'new_entity')",
            name="chk_ivr_outcome",
        ),
        CheckConstraint(
            "role IN ('vendor_name', 'replaces_vendor', 'candidate_vendor')",
            name="chk_ivr_role",
        ),
    )


class EntityReviewTaskModel(Base):
    """Human-in-the-loop review tasks for provisional vendor decisions."""

    __tablename__ = "entity_review_tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    task_type: Mapped[str] = mapped_column(String(32), nullable=False, default="duplicate_review")
    provisional_vendor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    candidate_vendor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    component_scores: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    assignee: Mapped[str | None] = mapped_column(String(128), nullable=True)
    slack_message_ts: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "provisional_vendor_id"],
            ["vendors.tenant_id", "vendors.id"],
            name="fk_ert_provisional",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "candidate_vendor_id"],
            ["vendors.tenant_id", "vendors.id"],
            name="fk_ert_candidate",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "task_type IN ('duplicate_review', 'suggested_parent', 'subsidiary_review')",
            name="chk_review_task_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'approved_merge', 'approved_subsidiary', "
            "'approved_new', 'rejected', 'superseded_by_merge')",
            name="chk_review_task_status",
        ),
    )


class Neo4jOutboxModel(Base):
    """Reliable outbox queue for transactional projection to Neo4j."""

    __tablename__ = "neo4j_outbox"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sequence: Mapped[int] = mapped_column(BigInteger, Identity(start=1))
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(128), nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    ordering_key: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_neo4j_outbox_idempotency"),
        Index("idx_outbox_status_ordering", "status", "ordering_key", "sequence"),
        CheckConstraint(
            "status IN ('pending', 'processing', 'processed', 'dead_letter')",
            name="chk_outbox_status",
        ),
    )
