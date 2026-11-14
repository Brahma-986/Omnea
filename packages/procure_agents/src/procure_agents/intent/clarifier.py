"""Clarification question generator and multi-turn context merging."""


def generate_clarifying_question(
    missing_fields: list[str],
    vendor_name: str | None = None,
) -> str | None:
    """Generate exactly ONE concise question covering all missing mandatory fields."""
    if not missing_fields:
        return None

    vendor_phrase = f" for {vendor_name}" if vendor_name else ""

    missing_set = set(missing_fields)
    has_vendor = "vendor_name" in missing_set
    has_spend = "estimated_spend" in missing_set
    has_currency = "currency" in missing_set

    if has_vendor and has_spend:
        return (
            "Could you please specify which vendor or tool you need, along with the "
            "estimated budget or spend (including currency)?"
        )

    if has_vendor:
        return "Could you please specify the vendor or tool name for this purchase request?"

    if has_spend and has_currency:
        return (
            f"Could you please specify the estimated budget or spend{vendor_phrase}, "
            "and the currency (e.g. USD, EUR, GBP)?"
        )

    if has_spend:
        return f"Could you please specify the estimated budget or spend amount{vendor_phrase}?"

    if has_currency:
        return (
            f"Could you please specify the currency (e.g. USD, EUR, GBP) "
            f"for the spend{vendor_phrase}?"
        )

    # Generic fallback
    joined = ", ".join(f.replace("_", " ") for f in missing_fields)
    return f"Could you please clarify the following missing details{vendor_phrase}: {joined}?"


def format_merged_text(original_text: str, reply_text: str) -> str:
    """Merge original request text with follow-up clarification reply for re-extraction."""
    return f"{original_text}\n\nClarification reply:\n{reply_text.strip()}"
