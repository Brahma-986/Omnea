"""Unit tests for Normalizer, Fuzzy Hard Blocks, Decision Policy, and Outages."""

from __future__ import annotations

import uuid

from procure_context.resolution.embeddings import (
    FailingEmbeddingProvider,
)
from procure_context.resolution.fuzzy import (
    hard_invalidation_check,
)
from procure_context.resolution.normalizer import (
    normalize_vendor_name,
)
from procure_context.resolution.resolver import EntityResolutionService


def test_normalizer_idempotency_property() -> None:
    """Property test: norm(norm(x).canonical_key).canonical_key == norm(x).canonical_key."""
    test_cases = [
        "Datadog, Inc.",
        "SAP America LLC",
        "Amazon Web Services EMEA",
        "Snowflake Computing Technologies Inc.",
        "Salesforce.com Operations GmbH",
        "AT&T Communications, Corp.",
        "Stripe Payments UK Limited",
        "Google Cloud Platform Germany",
    ]
    for raw in test_cases:
        norm1 = normalize_vendor_name(raw)
        norm2 = normalize_vendor_name(norm1.canonical_key)
        assert norm2.canonical_key == norm1.canonical_key, (
            f"Idempotency failed for '{raw}': '{norm1.canonical_key}' != '{norm2.canonical_key}'"
        )
        assert norm2.normalized_base == norm1.normalized_base


def test_normalizer_phrase_protection() -> None:
    """Protected phrases (e.g. Bank of America) must not be corrupted or stripped."""
    bofa = normalize_vendor_name("Bank of America Corporation")
    assert bofa.canonical_key == "bank of america"
    assert bofa.normalized_base == "bank of america"
    assert bofa.region is None  # "America" must NOT be treated as a stripped region suffix!

    ukpn = normalize_vendor_name("UK Power Networks Ltd")
    assert ukpn.canonical_key == "uk power networks"
    assert ukpn.normalized_base == "uk power networks"


def test_normalizer_region_and_synonym_mapping() -> None:
    """Regional synonyms (e.g. Deutschland -> germany) and end-anchored regional qualifiers."""
    de = normalize_vendor_name("SAP Deutschland SE")
    assert de.normalized_base == "sap"
    assert de.region == "germany"
    assert de.canonical_key == "sap:germany"

    emea = normalize_vendor_name("Datadog EMEA")
    assert emea.normalized_base == "datadog"
    assert emea.region == "emea"
    assert emea.canonical_key == "datadog:emea"

    # Region not at end or part of standard name should not be stripped if remainder empty
    us = normalize_vendor_name("US Foods Inc")
    assert us.normalized_base == "us foods"
    assert us.canonical_key == "us foods"


def test_fuzzy_hard_blocks_short_names() -> None:
    """Names <= 3 characters never auto-link or match via fuzzy matching."""
    norm_sap = normalize_vendor_name("SAP")
    blocked, reason = hard_invalidation_check(norm_sap, "SAPI Corp", "sapi")
    assert blocked is True
    assert "Short name guard" in reason

    # When exact, not blocked by guard
    blocked_exact, _ = hard_invalidation_check(norm_sap, "SAP SE", "sap")
    assert blocked_exact is False


def test_fuzzy_hard_blocks_differing_digits() -> None:
    """Differing digits (e.g. 'Salesforce 3' vs 'Salesforce 4') strictly block matching."""
    norm_sf3 = normalize_vendor_name("Salesforce 3")
    blocked, reason = hard_invalidation_check(norm_sf3, "Salesforce 4", "salesforce")
    assert blocked is True
    assert "Digit mismatch" in reason

    norm_off365 = normalize_vendor_name("Office 365")
    blocked_same, _ = hard_invalidation_check(norm_off365, "Office 365 Pro", "office 365")
    assert blocked_same is False


def test_fuzzy_hard_blocks_differing_regions() -> None:
    """Differing region tags (e.g. 'Datadog EMEA' vs 'Datadog America') block candidate matching."""
    norm_emea = normalize_vendor_name("Datadog EMEA")
    blocked, reason = hard_invalidation_check(
        norm_emea, "Datadog America", "datadog", candidate_region="america"
    )
    assert blocked is True
    assert "Region mismatch" in reason

    # Same region is not blocked
    blocked_same, _ = hard_invalidation_check(
        norm_emea, "Datadog EMEA SAS", "datadog", candidate_region="emea"
    )
    assert blocked_same is False


def test_decision_policy_banding_and_caps() -> None:
    """Fuzzy and embedding matches must STRICTLY cap at PROVISIONAL_REVIEW and never auto-link."""
    resolver = EntityResolutionService("eval_tenant")
    catalog = [
        {
            "id": str(uuid.uuid4()),
            "name": "Datadog, Inc.",
            "canonical_key": "datadog",
            "family": "datadog",
            "status": "active",
        }
    ]

    # Typo query with JW review fallback (embeddings disabled to test review floor)
    res_fuzzy = resolver.resolve_against_catalog(
        "Datadog Systems", catalog, force_embedding_outage=True
    )
    # Datadog Systems has JW > 0.70 with Datadog
    assert res_fuzzy.outcome in ("PROVISIONAL_REVIEW", "NEW_ENTITY")
    assert res_fuzzy.outcome != "MATCH"
    assert res_fuzzy.outcome != "SUBSIDIARY_OF"

    # Completely novel vendor -> NEW_ENTITY
    res_novel = resolver.resolve_against_catalog("Quantum Widgets Galactic", catalog)
    assert res_novel.outcome == "NEW_ENTITY"
    assert res_novel.rule_path == "low_confidence_new"
    assert res_novel.matched_vendor_id is None


def test_embedding_outage_degradation_behavior() -> None:
    """When embedding provider raises an error, resolution degrades to JW-only without crashing."""
    failing_provider = FailingEmbeddingProvider()
    resolver = EntityResolutionService("eval_tenant", embedding_provider=failing_provider)
    catalog = [
        {
            "id": str(uuid.uuid4()),
            "name": "CrowdStrike, Inc.",
            "canonical_key": "crowdstrike",
            "family": "crowdstrike",
            "status": "active",
        }
    ]

    # Typo with failing embeddings
    res = resolver.resolve_against_catalog(
        "CrowdStrike Systems", catalog, force_embedding_outage=True
    )
    assert res.degraded_reason == "embedding_unavailable"
    assert res.scores.embedding_score is None
    assert res.scores.composite_score == res.scores.jw_score
    # Must still cap at PROVISIONAL_REVIEW, never auto-link
    assert res.outcome in ("PROVISIONAL_REVIEW", "NEW_ENTITY")
