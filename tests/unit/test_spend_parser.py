"""Unit tests for deterministic spend, currency, and billing period parsing."""

from decimal import Decimal

import pytest

from procure_agents.models import BillingPeriod
from procure_agents.spend import (
    normalize_currency,
    parse_and_annualize_spend,
    parse_billing_period,
    parse_numeric_amount,
)


@pytest.mark.unit
def test_parse_numeric_amount_basic_and_suffixes() -> None:
    """Test integer, decimal, k, and M parsing."""
    val, is_range = parse_numeric_amount("500")
    assert val == Decimal("500")
    assert not is_range

    val, is_range = parse_numeric_amount("48k")
    assert val == Decimal("48000")
    assert not is_range

    val, is_range = parse_numeric_amount("12.5k")
    assert val == Decimal("12500")
    assert not is_range

    val, is_range = parse_numeric_amount("1.2M")
    assert val == Decimal("1200000")
    assert not is_range

    val, is_range = parse_numeric_amount("2m")
    assert val == Decimal("2000000")
    assert not is_range


@pytest.mark.unit
def test_parse_numeric_amount_delimiters_and_european() -> None:
    """Test thousands separators and European decimal commas."""
    val, is_range = parse_numeric_amount("120,000")
    assert val == Decimal("120000")
    assert not is_range

    # European format: 45.000,50 -> 45000.50
    val, is_range = parse_numeric_amount("45.000,50")
    assert val == Decimal("45000.50")
    assert not is_range

    # Comma decimal: 12,50 -> 12.50
    val, is_range = parse_numeric_amount("12,50")
    assert val == Decimal("12.50")
    assert not is_range


@pytest.mark.unit
def test_parse_numeric_amount_ranges() -> None:
    """Test range detection."""
    val, is_range = parse_numeric_amount("between 40k and 50k")
    assert val == Decimal("40000")
    assert is_range

    val, is_range = parse_numeric_amount("40k - 50k")
    assert val == Decimal("40000")
    assert is_range

    val, is_range = parse_numeric_amount("40000 to 50000")
    assert val == Decimal("40000")
    assert is_range


@pytest.mark.unit
def test_parse_numeric_amount_invalid() -> None:
    """Test invalid or non-numeric inputs."""
    val, is_range = parse_numeric_amount("")
    assert val is None
    assert not is_range

    val, is_range = parse_numeric_amount("unknown budget")
    assert val is None
    assert not is_range


@pytest.mark.unit
def test_normalize_currency_symbols_and_words() -> None:
    """Test symbol and word mapping."""
    assert normalize_currency("$") == "USD"
    assert normalize_currency("€") == "EUR"
    assert normalize_currency("£") == "GBP"
    assert normalize_currency("¥") == "JPY"
    assert normalize_currency("₹") == "INR"

    assert normalize_currency("USD") == "USD"
    assert normalize_currency("EUR") == "EUR"
    assert normalize_currency("dollars") == "USD"
    assert normalize_currency("euros") == "EUR"
    assert normalize_currency("pounds") == "GBP"
    assert normalize_currency("yen") == "JPY"


@pytest.mark.unit
def test_normalize_currency_context_disambiguation() -> None:
    """Test dollar disambiguation via context text."""
    assert normalize_currency("$", context_text="30k AUD per year") == "AUD"
    assert normalize_currency("$", context_text="50k CAD annually") == "CAD"
    assert normalize_currency("$", context_text="20k SGD per year") == "SGD"
    assert normalize_currency(None, context_text="none") is None


@pytest.mark.unit
def test_parse_billing_period() -> None:
    """Test billing period normalization."""
    assert parse_billing_period("per year") == BillingPeriod.ANNUAL
    assert parse_billing_period("annually") == BillingPeriod.ANNUAL
    assert parse_billing_period("/yr") == BillingPeriod.ANNUAL
    assert parse_billing_period("yearly") == BillingPeriod.ANNUAL

    assert parse_billing_period("monthly") == BillingPeriod.MONTHLY
    assert parse_billing_period("per month") == BillingPeriod.MONTHLY
    assert parse_billing_period("/mo") == BillingPeriod.MONTHLY

    assert parse_billing_period("per quarter") == BillingPeriod.QUARTERLY
    assert parse_billing_period("quarterly") == BillingPeriod.QUARTERLY

    assert parse_billing_period("one-time") == BillingPeriod.ONE_TIME
    assert parse_billing_period("flat") == BillingPeriod.ONE_TIME

    assert parse_billing_period(None) == BillingPeriod.UNKNOWN
    assert parse_billing_period("bi-weekly") == BillingPeriod.UNKNOWN


@pytest.mark.unit
def test_parse_and_annualize_spend_datadog() -> None:
    """Test spec Datadog example: 48k USD per year."""
    result = parse_and_annualize_spend(
        raw_amount_str="48k",
        raw_currency_str="USD",
        raw_billing_period_str="per year",
    )
    assert result.amount == Decimal("48000.00")
    assert result.currency == "USD"
    assert result.billing_period == BillingPeriod.ANNUAL
    assert result.annualized_spend == Decimal("48000.00")
    assert not result.is_range
    assert not result.needs_review


@pytest.mark.unit
def test_parse_and_annualize_spend_monthly_and_quarterly() -> None:
    """Test monthly (x12) and quarterly (x4) multipliers."""
    # Monthly: $100/mo -> $1,200/yr
    result_monthly = parse_and_annualize_spend("100", "$", "monthly")
    assert result_monthly.amount == Decimal("100.00")
    assert result_monthly.annualized_spend == Decimal("1200.00")
    assert result_monthly.billing_period == BillingPeriod.MONTHLY

    # Quarterly: $2,500/quarter -> $10,000/yr
    result_quarterly = parse_and_annualize_spend("2500", "USD", "quarterly")
    assert result_quarterly.amount == Decimal("2500.00")
    assert result_quarterly.annualized_spend == Decimal("10000.00")
    assert result_quarterly.billing_period == BillingPeriod.QUARTERLY


@pytest.mark.unit
def test_parse_and_annualize_spend_currency_precision() -> None:
    """Test currency minor units (JPY = 0, USD = 2)."""
    # JPY minor unit precision 0
    result_jpy = parse_and_annualize_spend("500000.75", "JPY", "flat")
    assert result_jpy.amount == Decimal("500001")
    assert result_jpy.currency == "JPY"
    assert result_jpy.annualized_spend == Decimal("500001")

    # USD minor unit precision 2
    result_usd = parse_and_annualize_spend("19.999", "USD", "monthly")
    assert result_usd.amount == Decimal("20.00")
    assert result_usd.annualized_spend == Decimal("240.00")


@pytest.mark.unit
def test_parse_and_annualize_spend_ranges() -> None:
    """Test range detection sets needs_review."""
    result = parse_and_annualize_spend("between $40k and $50k", "$", "annually")
    assert result.amount == Decimal("40000.00")
    assert result.is_range
    assert result.needs_review
