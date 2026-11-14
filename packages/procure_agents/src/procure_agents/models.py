"""Domain models for intent classification, raw extraction, and parsed procurement intents."""

import uuid
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ClassificationType(StrEnum):
    """5-way intent classification."""

    NEW_PURCHASE = "new_purchase"
    RENEWAL = "renewal"
    VENDOR_QUESTION = "vendor_question"
    STATUS_CHECK = "status_check"
    OTHER = "other"


class BillingPeriod(StrEnum):
    """Normalized billing cadence."""

    ONE_TIME = "one_time"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"
    UNKNOWN = "unknown"


class ExtractedIntentFields(BaseModel):
    """Raw structured output schema returned by the LLM extraction tool."""

    model_config = ConfigDict(extra="ignore")

    vendor_name: str | None = Field(
        default=None,
        description="Primary vendor or tool name mentioned (e.g. 'Datadog', 'Figma')",
    )
    replaces_vendor: str | None = Field(
        default=None,
        description="Existing vendor being replaced, if mentioned (e.g. 'Splunk')",
    )
    candidate_vendors: list[str] = Field(
        default_factory=list,
        description="All vendor names mentioned in the message",
    )
    raw_amount_str: str | None = Field(
        default=None,
        description="Raw monetary amount as written (e.g. '48k', '60,000', 'between 40k and 50k')",
    )
    raw_currency_str: str | None = Field(
        default=None,
        description="Raw currency symbol or word (e.g. '$', 'USD', 'EUR', '£', '¥', 'AUD')",
    )
    raw_billing_period_str: str | None = Field(
        default=None,
        description="Raw billing frequency as written (e.g. 'per year', 'monthly', 'annually')",
    )
    justification: str | None = Field(
        default=None,
        description="Business justification or use case for the purchase",
    )
    department: str | None = Field(
        default=None,
        description="Requesting department or team (e.g. 'Engineering', 'DevOps', 'Design')",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Categorical tags (e.g. ['security', 'saas', 'hardware'])",
    )
    evidence: dict[str, str] = Field(
        default_factory=dict,
        description="Exact verbatim quote snippet from the user text supporting each field. "
        "Keys must match field names. If a field was not explicitly stated, omit quote.",
    )


class IntentClassification(BaseModel):
    """Output schema for the 5-way intake classifier."""

    model_config = ConfigDict(extra="ignore")

    classification: ClassificationType
    confidence: float = Field(ge=0.0, le=1.0)
    request_ref: str | None = Field(
        default=None,
        description="Reference ticket ID if mentioned in status checks (e.g. 'REQ-2048')",
    )
    reasoning: str = Field(description="One-sentence rationale for the classification")


class SpendParseResult(BaseModel):
    """Deterministic spend parsing output."""

    amount: Decimal | None = None
    currency: str | None = None  # ISO 4217 3-letter code
    billing_period: BillingPeriod = BillingPeriod.UNKNOWN
    annualized_spend: Decimal | None = None
    is_range: bool = False
    needs_review: bool = False


class ProcurementIntent(BaseModel):
    """Complete internal domain representation combining trusted context and parsed fields."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    tenant_id: str
    session_id: uuid.UUID
    requester_id: str
    source_event_id: str
    intent_type: ClassificationType
    status: str = "awaiting_clarification"  # awaiting_clarification, complete, needs_human_review

    # Vendor & Scope
    vendor_name: str | None = None
    replaces_vendor: str | None = None
    candidate_vendors: list[str] = Field(default_factory=list)

    # Spend & Cadence
    estimated_spend: Decimal | None = None
    annualized_spend: Decimal | None = None
    currency: str | None = None
    billing_period: BillingPeriod = BillingPeriod.UNKNOWN

    # Justification & Dept
    justification: str | None = None
    department: str | None = None
    tags: list[str] = Field(default_factory=list)

    # Validation & Evidence
    evidence: dict[str, str] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    clarifying_question: str | None = None
    clarification_count: int = 0
    needs_review: bool = False
    prompt_version: str = "v1"
    model: str = ""
    raw_text: str = ""
