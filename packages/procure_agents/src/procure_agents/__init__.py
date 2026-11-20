"""Procure Agents pure domain package."""

from typing import Any

from procure_agents.models import (
    BillingPeriod,
    ClassificationType,
    ExtractedIntentFields,
    IntentClassification,
    ProcurementIntent,
    SpendParseResult,
)
from procure_agents.spend import parse_and_annualize_spend

__all__ = [
    "ClassificationType",
    "BillingPeriod",
    "ExtractedIntentFields",
    "IntentClassification",
    "SpendParseResult",
    "ProcurementIntent",
    "LLMClientProtocol",
    "LLMTextResponse",
    "ResilientLLMClient",
    "FakeLLMClient",
    "IntentClassifier",
    "IntakeExtractor",
    "generate_clarifying_question",
    "format_merged_text",
    "parse_and_annualize_spend",
    "TprmDomainAssessmentAgent",
    "CitationVerifier",
]


def __getattr__(name: str) -> Any:
    if name in (
        "IntakeExtractor",
        "IntentClassifier",
        "format_merged_text",
        "generate_clarifying_question",
    ):
        import procure_agents.intent as intent

        return getattr(intent, name)
    if name in ("FakeLLMClient", "LLMClientProtocol", "LLMTextResponse", "ResilientLLMClient"):
        import procure_agents.llm as llm

        return getattr(llm, name)
    if name in ("TprmDomainAssessmentAgent", "CitationVerifier"):
        import procure_agents.tprm as tprm

        return getattr(tprm, name)
    raise AttributeError(f"module 'procure_agents' has no attribute '{name}'")
