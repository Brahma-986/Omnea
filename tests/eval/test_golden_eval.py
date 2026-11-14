"""Golden dataset evaluation runner evaluating live LLM parsing accuracy across fields."""

import json
import os
from pathlib import Path
from typing import Any

import pytest

from procure_agents.intent.classifier import IntentClassifier
from procure_agents.intent.extractor import IntakeExtractor
from procure_agents.llm.client import ResilientLLMClient
from procure_core.log_config import get_logger

logger = get_logger(__name__)


def load_golden_dataset() -> list[dict[str, Any]]:
    """Load test cases from golden_intents.jsonl."""
    dataset_path = Path(__file__).parent / "golden_intents.jsonl"
    cases: list[dict[str, Any]] = []
    with open(dataset_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


@pytest.mark.llm
@pytest.mark.asyncio
async def test_golden_eval_suite() -> None:
    """Run full evaluation suite over real LLM output and report per-field accuracy.

    Excluded from default CI; executed explicitly with:
        pytest -m llm tests/eval/test_golden_eval.py
    """
    api_key_present = any(
        os.environ.get(k)
        for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "MISTRAL_API_KEY")
    )
    if not api_key_present:
        pytest.skip("No live LLM API key found in environment. Skipping live evaluation.")

    cases = load_golden_dataset()
    assert len(cases) >= 20, "Golden evaluation dataset must contain at least 20 cases"

    llm_client = ResilientLLMClient()
    classifier = IntentClassifier(llm_client)
    extractor = IntakeExtractor(llm_client)

    total = len(cases)
    classification_hits = 0
    vendor_hits = 0
    spend_hits = 0
    currency_hits = 0

    print(f"\n================ Running Golden LLM Evaluation ({total} cases) ================")

    for case in cases:
        _case_id = case["id"]
        text = case["text"]
        expected_cls = case["expected_classification"]
        expected_vendor = case["expected_vendor"]
        expected_amount = case["expected_amount"]
        expected_currency = case["expected_currency"]

        # Step 1: Classification
        cls_result = await classifier.classify(text)
        if cls_result.classification.value == expected_cls:
            classification_hits += 1

        # Step 2: Extraction if purchase/renewal
        if expected_cls in ("new_purchase", "renewal"):
            fields, spend, _, _ = await extractor.extract(text)

            # Vendor match
            if expected_vendor is None and fields.vendor_name is None:
                vendor_hits += 1
            elif (
                expected_vendor
                and fields.vendor_name
                and expected_vendor.lower() in fields.vendor_name.lower()
            ):
                vendor_hits += 1

            # Amount match
            if expected_amount is None and spend.amount is None:
                spend_hits += 1
            elif (
                expected_amount is not None
                and spend.amount is not None
                and float(spend.amount) == float(expected_amount)
            ):
                spend_hits += 1

            # Currency match
            if expected_currency == spend.currency:
                currency_hits += 1
        else:
            # Non-purchase cases count towards non-purchase accuracy
            vendor_hits += 1
            spend_hits += 1
            currency_hits += 1

    cls_acc = (classification_hits / total) * 100
    vendor_acc = (vendor_hits / total) * 100
    spend_acc = (spend_hits / total) * 100
    currency_acc = (currency_hits / total) * 100

    print(
        f"\n[Golden Evaluation Report]\n"
        f"Total Test Cases:       {total}\n"
        f"Classification Acc:    {cls_acc:.1f}% ({classification_hits}/{total})\n"
        f"Vendor Name Acc:        {vendor_acc:.1f}% ({vendor_hits}/{total})\n"
        f"Spend Amount Acc:       {spend_acc:.1f}% ({spend_hits}/{total})\n"
        f"Currency Acc:           {currency_acc:.1f}% ({currency_hits}/{total})\n"
    )

    # Acceptance threshold
    assert cls_acc >= 75.0, f"Classification accuracy {cls_acc:.1f}% below 75% threshold"
    assert vendor_acc >= 75.0, f"Vendor accuracy {vendor_acc:.1f}% below 75% threshold"
