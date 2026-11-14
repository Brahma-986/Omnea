"""Unit tests for clarification question generation and dialogue merging."""

import pytest

from procure_agents.intent.clarifier import (
    format_merged_text,
    generate_clarifying_question,
)


@pytest.mark.unit
def test_generate_clarifying_question_all_missing() -> None:
    """Test question when both vendor and spend are missing."""
    q = generate_clarifying_question(["vendor_name", "estimated_spend", "currency"])
    assert q is not None
    assert "which vendor or tool" in q
    assert "estimated budget or spend" in q


@pytest.mark.unit
def test_generate_clarifying_question_missing_vendor_only() -> None:
    """Test question when only vendor is missing."""
    q = generate_clarifying_question(["vendor_name"])
    assert q is not None
    assert "vendor or tool name" in q


@pytest.mark.unit
def test_generate_clarifying_question_missing_spend_and_currency_with_vendor() -> None:
    """Test question when spend & currency are missing for a known vendor."""
    q = generate_clarifying_question(["estimated_spend", "currency"], vendor_name="Datadog")
    assert q is not None
    assert "for Datadog" in q
    assert "estimated budget or spend" in q
    assert "currency" in q


@pytest.mark.unit
def test_generate_clarifying_question_missing_currency_only() -> None:
    """Test question when currency is missing for a known vendor."""
    q = generate_clarifying_question(["currency"], vendor_name="Slack")
    assert q is not None
    assert "for Slack" in q
    assert "specify the currency" in q


@pytest.mark.unit
def test_generate_clarifying_question_none_when_complete() -> None:
    """Test no question generated when no fields are missing."""
    assert generate_clarifying_question([]) is None


@pytest.mark.unit
def test_format_merged_text() -> None:
    """Test multi-turn text concatenation for re-extraction."""
    original = "Can we get 10 licenses for Notion?"
    reply = "$80 USD per month"
    merged = format_merged_text(original, reply)

    assert "Can we get 10 licenses for Notion?" in merged
    assert "Clarification reply:" in merged
    assert "$80 USD per month" in merged
