"""Structured entity extractor enforcing verbatim quote verification and spend parsing."""

from pathlib import Path

from procure_agents.llm.client import LLMClientProtocol, ResilientLLMClient
from procure_agents.models import ExtractedIntentFields, SpendParseResult
from procure_agents.spend import parse_and_annualize_spend


def load_intent_prompt() -> str:
    """Load the versioned extraction system prompt."""
    prompt_path = Path(__file__).parent.parent / "prompts" / "intent_v1.txt"
    return prompt_path.read_text(encoding="utf-8")


def verify_evidence_quotes(
    fields: ExtractedIntentFields,
    source_text: str,
) -> tuple[ExtractedIntentFields, dict[str, str]]:
    """Verify that every extracted field has a verbatim substring quote in source_text.

    If a field has no supporting quote, null it out (hallucination defense).
    """
    normalized_source = source_text.lower()
    verified_evidence: dict[str, str] = {}
    verified_fields = fields.model_copy()

    # Field-to-attribute mappings to check
    checkable_fields = [
        "vendor_name",
        "replaces_vendor",
        "raw_amount_str",
        "raw_currency_str",
        "raw_billing_period_str",
        "justification",
        "department",
    ]

    for field_name in checkable_fields:
        val = getattr(verified_fields, field_name)
        if val is None:
            continue

        quote = fields.evidence.get(field_name, "").strip()
        if not quote and str(val).strip().lower() in normalized_source:
            quote = str(val).strip()

        # Verify quote exists as a verbatim substring in source_text
        if quote and quote.lower() in normalized_source:
            verified_evidence[field_name] = quote
        else:
            # No valid supporting quote: null out the field (treat as missing)
            setattr(verified_fields, field_name, None)

    return verified_fields, verified_evidence


def compute_missing_fields(
    fields: ExtractedIntentFields,
    spend: SpendParseResult,
) -> list[str]:
    """Deterministically compute missing mandatory fields required for a valid purchase request."""
    missing: list[str] = []

    if not fields.vendor_name:
        missing.append("vendor_name")
    if spend.amount is None:
        missing.append("estimated_spend")
    if not spend.currency:
        missing.append("currency")

    return missing


class IntakeExtractor:
    """Extracts procurement intent fields, validates verbatim evidence, and computes spend."""

    def __init__(self, llm_client: LLMClientProtocol | None = None) -> None:
        self.llm = llm_client or ResilientLLMClient()
        self.system_prompt = load_intent_prompt()

    @property
    def last_response_id(self) -> str | None:
        """Expose the LLM provider response ID from the underlying client."""
        return getattr(self.llm, "last_response_id", None)

    async def extract(
        self,
        text: str,
    ) -> tuple[ExtractedIntentFields, SpendParseResult, list[str], dict[str, str]]:
        """Extract structured fields, verify evidence quotes, and parse spend."""
        raw_fields: ExtractedIntentFields = await self.llm.call_structured(
            system_prompt=self.system_prompt,
            user_content=text,
            response_model=ExtractedIntentFields,
            temperature=0.0,
        )

        # 1. Deterministic evidence quote verification
        verified_fields, verified_evidence = verify_evidence_quotes(raw_fields, text)

        # 2. Deterministic spend and period parsing
        spend = parse_and_annualize_spend(
            raw_amount_str=verified_fields.raw_amount_str,
            raw_currency_str=verified_fields.raw_currency_str,
            raw_billing_period_str=verified_fields.raw_billing_period_str,
            context_text=text,
        )

        # 3. Deterministic missing fields evaluation
        missing = compute_missing_fields(verified_fields, spend)

        return verified_fields, spend, missing, verified_evidence
