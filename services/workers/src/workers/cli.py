"""CLI interface for offline intake parsing and testing."""

import asyncio
import json
import os
import sys

from procure_agents.intent.classifier import IntentClassifier
from procure_agents.intent.extractor import IntakeExtractor
from procure_agents.llm.client import FakeLLMClient, LLMClientProtocol, ResilientLLMClient
from procure_agents.models import (
    ClassificationType,
    ExtractedIntentFields,
    IntentClassification,
)


def get_llm_client() -> LLMClientProtocol:
    """Resolve live or demo LLM client based on environment configuration."""
    api_key_present = any(
        os.environ.get(k)
        for k in ("LLM_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY")
    )
    if api_key_present:
        return ResilientLLMClient()

    print("[Notice]: No live LLM API key found in environment. Running in offline mode.")
    fake = FakeLLMClient()
    # Canonical spec case
    fake.set_canned(
        "Datadog",
        IntentClassification(
            classification=ClassificationType.NEW_PURCHASE,
            confidence=0.98,
            reasoning="Purchase request for Datadog",
        ),
    )
    fake.default_response = ExtractedIntentFields(
        vendor_name="Datadog",
        raw_amount_str="48k",
        raw_currency_str="USD",
        raw_billing_period_str="per year",
        evidence={
            "vendor_name": "Datadog",
            "raw_amount_str": "48k",
            "raw_currency_str": "USD",
            "raw_billing_period_str": "per year",
        },
    )
    return fake


async def run_parse(text: str) -> None:
    """Parse text using classifier and extractor, outputting formatted JSON."""
    llm = get_llm_client()
    classifier = IntentClassifier(llm)
    extractor = IntakeExtractor(llm)

    print(f"\n[Input Message]:\n{text}\n")

    classification = await classifier.classify(text)
    print(
        f"[Classification]: {classification.classification.value} "
        f"(confidence: {classification.confidence:.2f})"
    )
    print(f"[Reasoning]: {classification.reasoning}")

    if classification.classification.value in ("new_purchase", "renewal"):
        fields, spend, missing, evidence = await extractor.extract(text)
        output = {
            "vendor_name": fields.vendor_name,
            "replaces_vendor": fields.replaces_vendor,
            "candidate_vendors": fields.candidate_vendors,
            "estimated_spend": str(spend.amount) if spend.amount is not None else None,
            "annualized_spend": str(spend.annualized_spend)
            if spend.annualized_spend is not None
            else None,
            "currency": spend.currency,
            "billing_period": spend.billing_period.value,
            "justification": fields.justification,
            "department": fields.department,
            "missing_fields": missing,
            "evidence": evidence,
        }
        print("\n[Extracted Entities]:")
        print(json.dumps(output, indent=2))
    elif classification.request_ref:
        print(f"\n[Status Reference]: {classification.request_ref}")


def main() -> None:
    if len(sys.argv) < 3 or sys.argv[1] != "parse":
        print('Usage: python -m workers.cli parse "<text>"')
        sys.exit(1)

    text_to_parse = sys.argv[2]
    asyncio.run(run_parse(text_to_parse))


if __name__ == "__main__":
    main()
