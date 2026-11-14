"""Unit tests for intent classifier and low-confidence fallback routing."""

import pytest

from procure_agents.intent.classifier import IntentClassifier
from procure_agents.llm.client import FakeLLMClient
from procure_agents.models import ClassificationType, IntentClassification


@pytest.mark.unit
@pytest.mark.asyncio
async def test_classify_new_purchase() -> None:
    """Test standard new purchase classification."""
    fake_client = FakeLLMClient(
        default_response=IntentClassification(
            classification=ClassificationType.NEW_PURCHASE,
            confidence=0.95,
            reasoning="User requests buying Datadog.",
        )
    )
    classifier = IntentClassifier(fake_client)
    res = await classifier.classify("48k USD per year for Datadog")

    assert res.classification == ClassificationType.NEW_PURCHASE
    assert res.confidence == 0.95
    assert res.request_ref is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_classify_renewal() -> None:
    """Test contract renewal classification."""
    fake_client = FakeLLMClient(
        default_response=IntentClassification(
            classification=ClassificationType.RENEWAL,
            confidence=0.92,
            reasoning="Renewing existing Figma contract.",
        )
    )
    classifier = IntentClassifier(fake_client)
    res = await classifier.classify("We need to renew our Figma enterprise licenses.")

    assert res.classification == ClassificationType.RENEWAL
    assert res.confidence == 0.92


@pytest.mark.unit
@pytest.mark.asyncio
async def test_classify_status_check_with_reference() -> None:
    """Test status check classification capturing request_ref."""
    fake_client = FakeLLMClient(
        default_response=IntentClassification(
            classification=ClassificationType.STATUS_CHECK,
            confidence=0.98,
            request_ref="REQ-4092",
            reasoning="User asks for status of specific ticket REQ-4092.",
        )
    )
    classifier = IntentClassifier(fake_client)
    res = await classifier.classify("What is the status of request REQ-4092?")

    assert res.classification == ClassificationType.STATUS_CHECK
    assert res.request_ref == "REQ-4092"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_classify_vendor_question() -> None:
    """Test vendor compliance/inquiry classification."""
    fake_client = FakeLLMClient(
        default_response=IntentClassification(
            classification=ClassificationType.VENDOR_QUESTION,
            confidence=0.88,
            reasoning="User asks about vendor SOC2 report.",
        )
    )
    classifier = IntentClassifier(fake_client)
    res = await classifier.classify("Does Datadog support SOC2 Type II?")

    assert res.classification == ClassificationType.VENDOR_QUESTION


@pytest.mark.unit
@pytest.mark.asyncio
async def test_classify_low_confidence_routes_to_other() -> None:
    """Rule 9: Low confidence (< 0.70) routes to 'other'."""
    fake_client = FakeLLMClient(
        default_response=IntentClassification(
            classification=ClassificationType.NEW_PURCHASE,
            confidence=0.62,
            reasoning="Vague message might be a purchase.",
        )
    )
    classifier = IntentClassifier(fake_client)
    res = await classifier.classify("Thinking about maybe getting something for analytics.")

    assert res.classification == ClassificationType.OTHER
    assert res.confidence == 0.62
    assert "Low classification confidence" in res.reasoning
