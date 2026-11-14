"""Unit tests for structured intake entity extraction and evidence verification."""

from decimal import Decimal

import pytest

from procure_agents.intent.extractor import (
    IntakeExtractor,
    compute_missing_fields,
    verify_evidence_quotes,
)
from procure_agents.llm.client import FakeLLMClient
from procure_agents.models import BillingPeriod, ExtractedIntentFields, SpendParseResult


@pytest.mark.unit
def test_verify_evidence_quotes_valid_and_hallucinated() -> None:
    """Rule 4: Extracted fields must be supported by verbatim quotes; hallucinations nulled."""
    source_text = "Need 48k USD per year for Datadog to replace Splunk in Engineering"

    fields = ExtractedIntentFields(
        vendor_name="Datadog",
        replaces_vendor="Splunk",
        raw_amount_str="48k",
        raw_currency_str="USD",
        raw_billing_period_str="per year",
        department="Engineering",
        justification="Log monitoring platform",  # Hallucinated! Not in source_text
        evidence={
            "vendor_name": "Datadog",
            "replaces_vendor": "Splunk",
            "raw_amount_str": "48k",
            "raw_currency_str": "USD",
            "raw_billing_period_str": "per year",
            "department": "Engineering",
            "justification": "Log monitoring platform",  # Quote does not appear in source text!
        },
    )

    verified_fields, verified_evidence = verify_evidence_quotes(fields, source_text)

    # Valid quotes kept
    assert verified_fields.vendor_name == "Datadog"
    assert verified_fields.replaces_vendor == "Splunk"
    assert verified_fields.raw_amount_str == "48k"
    assert verified_evidence["vendor_name"] == "Datadog"

    # Unsupported quote nulled out!
    assert verified_fields.justification is None
    assert "justification" not in verified_evidence


@pytest.mark.unit
def test_compute_missing_fields() -> None:
    """Test deterministic missing fields calculation."""
    fields = ExtractedIntentFields(vendor_name="Datadog")
    spend = SpendParseResult(
        amount=Decimal("48000.00"),
        currency="USD",
        billing_period=BillingPeriod.ANNUAL,
    )
    # All present
    assert compute_missing_fields(fields, spend) == []

    # Missing vendor
    fields_no_vendor = ExtractedIntentFields(vendor_name=None)
    assert "vendor_name" in compute_missing_fields(fields_no_vendor, spend)

    # Missing amount
    spend_no_amount = SpendParseResult(amount=None, currency="USD")
    assert "estimated_spend" in compute_missing_fields(fields, spend_no_amount)

    # Missing currency
    spend_no_currency = SpendParseResult(amount=Decimal("100"), currency=None)
    assert "currency" in compute_missing_fields(fields, spend_no_currency)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_extractor_datadog_canonical() -> None:
    """Test canonical acceptance case: 48k USD per year for Datadog."""
    text = "48k USD per year for Datadog"

    mock_raw = ExtractedIntentFields(
        vendor_name="Datadog",
        candidate_vendors=["Datadog"],
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
    fake_client = FakeLLMClient(default_response=mock_raw)
    extractor = IntakeExtractor(fake_client)

    fields, spend, missing, evidence = await extractor.extract(text)

    assert fields.vendor_name == "Datadog"
    assert spend.amount == Decimal("48000.00")
    assert spend.currency == "USD"
    assert spend.billing_period == BillingPeriod.ANNUAL
    assert spend.annualized_spend == Decimal("48000.00")
    assert missing == []
    assert evidence["vendor_name"] == "Datadog"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_extractor_multi_vendor_replacement() -> None:
    """Test candidate vendors and replaces_vendor extraction."""
    text = "We want to replace Splunk with Datadog for $60k/yr"

    mock_raw = ExtractedIntentFields(
        vendor_name="Datadog",
        replaces_vendor="Splunk",
        candidate_vendors=["Splunk", "Datadog"],
        raw_amount_str="60k",
        raw_currency_str="$",
        raw_billing_period_str="/yr",
        evidence={
            "vendor_name": "Datadog",
            "replaces_vendor": "Splunk",
            "raw_amount_str": "60k",
            "raw_currency_str": "$",
            "raw_billing_period_str": "/yr",
        },
    )
    fake_client = FakeLLMClient(default_response=mock_raw)
    extractor = IntakeExtractor(fake_client)

    fields, spend, missing, _ = await extractor.extract(text)

    assert fields.vendor_name == "Datadog"
    assert fields.replaces_vendor == "Splunk"
    assert "Splunk" in fields.candidate_vendors
    assert "Datadog" in fields.candidate_vendors
    assert spend.amount == Decimal("60000.00")
    assert spend.currency == "USD"
    assert missing == []
