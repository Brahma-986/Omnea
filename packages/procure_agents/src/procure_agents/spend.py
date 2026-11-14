"""Deterministic spend, currency, and billing period parsing and annualization."""

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from procure_agents.models import BillingPeriod, SpendParseResult

# Standard ISO 4217 minor unit precision lookup
CURRENCY_MINOR_UNITS: dict[str, int] = {
    "JPY": 0,
    "KRW": 0,
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "AUD": 2,
    "CAD": 2,
    "CHF": 2,
    "SGD": 2,
    "INR": 2,
}

SYMBOL_TO_ISO: dict[str, str] = {
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
    "₹": "INR",
}


def normalize_currency(raw_currency: str | None, context_text: str = "") -> str | None:
    """Normalize currency symbols, names, and explicit ISO codes."""
    if not raw_currency and not context_text:
        return None

    raw = (raw_currency or "").strip().upper()
    ctx = context_text.upper()

    # Check for disambiguated dollar/yen explicitly in text or symbol
    if "AUD" in raw or "AUD" in ctx:
        return "AUD"
    if "CAD" in raw or "CAD" in ctx:
        return "CAD"
    if "SGD" in raw or "SGD" in ctx:
        return "SGD"
    if "CNY" in raw or "RMB" in raw:
        return "CNY"

    # Direct symbol match
    for sym, iso in SYMBOL_TO_ISO.items():
        if sym in raw or sym in (raw_currency or ""):
            return iso

    # 3-letter ISO match
    match = re.search(r"\b([A-Z]{3})\b", raw)
    if match and match.group(1) in CURRENCY_MINOR_UNITS:
        return match.group(1)

    # Word matches
    if "DOLLAR" in raw:
        return "USD"
    if "EURO" in raw:
        return "EUR"
    if "POUND" in raw:
        return "GBP"
    if "YEN" in raw:
        return "JPY"

    return None


def parse_numeric_amount(amount_str: str) -> tuple[Decimal | None, bool]:
    """Parse raw amount string handling 'k', 'M', commas, decimals, and ranges.

    Returns (parsed_decimal, is_range).
    """
    cleaned = amount_str.strip().lower()

    # Detect ranges: "between $40k and $50k", "40k - 50k", "40000 to 50000"
    range_match = re.search(
        r"(?:between\s+)?[^\d]*(\d+[\d.,]*[km]?)\s*(?:and|-|to)\s*[^\d]*(\d+[\d.,]*[km]?)",
        cleaned,
    )
    if range_match:
        val1, _ = parse_numeric_amount(range_match.group(1))
        val2, _ = parse_numeric_amount(range_match.group(2))
        if val1 is not None and val2 is not None:
            # Range detected: pick the lower bound or midpoint, and flag is_range
            return val1, True

    # Multiplier suffix (k = 1,000, m = 1,000,000)
    multiplier = Decimal("1")
    if cleaned.endswith("k"):
        multiplier = Decimal("1000")
        cleaned = cleaned[:-1].strip()
    elif cleaned.endswith("m"):
        multiplier = Decimal("1000000")
        cleaned = cleaned[:-1].strip()

    # Strip currency symbols and whitespace
    cleaned = re.sub(r"[^\d.,]", "", cleaned)
    if not cleaned:
        return None, False

    # Handle European decimal format: e.g., "45.000,50" -> "45000.50"
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        # Check if comma is decimal separator (e.g., "12,50") or thousands ("12,000")
        parts = cleaned.split(",")
        if len(parts) == 2 and len(parts[1]) != 3:
            cleaned = f"{parts[0]}.{parts[1]}"
        else:
            cleaned = cleaned.replace(",", "")

    try:
        val = Decimal(cleaned) * multiplier
        return val, False
    except (InvalidOperation, ValueError):
        return None, False


def parse_billing_period(period_str: str | None) -> BillingPeriod:
    """Normalize billing period string into standard enum."""
    if not period_str:
        return BillingPeriod.UNKNOWN

    raw = period_str.strip().lower()

    if any(k in raw for k in ("annual", "year", "yr", "/yr", "annually", "per year", "yearly")):
        return BillingPeriod.ANNUAL
    if any(k in raw for k in ("month", "mo", "/mo", "monthly", "per month")):
        return BillingPeriod.MONTHLY
    if any(k in raw for k in ("quarter", "quarterly", "qtr", "/qtr", "per quarter")):
        return BillingPeriod.QUARTERLY
    if any(k in raw for k in ("one-time", "one time", "once", "single", "flat")):
        return BillingPeriod.ONE_TIME

    return BillingPeriod.UNKNOWN


def parse_and_annualize_spend(
    raw_amount_str: str | None,
    raw_currency_str: str | None,
    raw_billing_period_str: str | None,
    context_text: str = "",
) -> SpendParseResult:
    """Deterministically parse amount, normalize currency and period, and compute annualized spend.

    No LLM arithmetic or arbitrary conversions are performed.
    """
    if not raw_amount_str:
        return SpendParseResult(
            amount=None,
            currency=normalize_currency(raw_currency_str, context_text),
            billing_period=parse_billing_period(raw_billing_period_str),
            annualized_spend=None,
            is_range=False,
            needs_review=False,
        )

    parsed_amount, is_range = parse_numeric_amount(raw_amount_str)
    currency = normalize_currency(raw_currency_str, context_text)
    period = parse_billing_period(raw_billing_period_str)

    # Format amount with currency precision (e.g., JPY = 0, USD = 2)
    if parsed_amount is not None and currency in CURRENCY_MINOR_UNITS:
        precision = CURRENCY_MINOR_UNITS[currency]
        if precision == 0:
            parsed_amount = parsed_amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        else:
            parsed_amount = parsed_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # Compute annualized spend deterministically
    annualized: Decimal | None = None
    if parsed_amount is not None:
        if period == BillingPeriod.ANNUAL:
            annualized = parsed_amount
        elif period == BillingPeriod.MONTHLY:
            annualized = parsed_amount * Decimal("12")
        elif period == BillingPeriod.QUARTERLY:
            annualized = parsed_amount * Decimal("4")
        elif period == BillingPeriod.ONE_TIME:
            annualized = parsed_amount  # One-time spend reflected in year 1

    return SpendParseResult(
        amount=parsed_amount,
        currency=currency,
        billing_period=period,
        annualized_spend=annualized,
        is_range=is_range,
        needs_review=is_range,
    )
