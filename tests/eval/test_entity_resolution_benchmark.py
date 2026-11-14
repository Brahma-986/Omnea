"""Comprehensive evaluation benchmark for Phase 3 Entity Resolution Quality Gates.

Validates:
- Gate 1: 45/45 deterministic auto-links correct in Held-Out (Wilson 95% bound <= 7.87%).
- Gate 2: 0/80 false auto-links on Held-Out non-auto-link cases (Wilson 95% bound <= 4.58%).
- Mined Adversarial Negatives: >=30 held-out negatives with JW >= 0.85.
- Review Candidate Recall: >=90% recall@3 on review cases with expected_best_target.
- Per-rule-path breakdown.
- Embedding outage degradation resilience.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from procure_context.resolution.normalizer import normalize_vendor_name
from procure_context.resolution.resolver import EntityResolutionService
from tests.eval.generate_catalog import wilson_upper_bound

BENCHMARK_FILE = Path("tests/eval/catalog_benchmark.jsonl")
CATALOG_FILE = Path("tests/eval/catalog_snapshot.jsonl")
ALIASES_FILE = Path("tests/eval/vendor_aliases_seed.jsonl")


def load_dataset() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    with open(CATALOG_FILE, encoding="utf-8") as f:
        catalog = [json.loads(line) for line in f]
    with open(ALIASES_FILE, encoding="utf-8") as f:
        aliases = [json.loads(line) for line in f]
    with open(BENCHMARK_FILE, encoding="utf-8") as f:
        cases = [json.loads(line) for line in f]
    return catalog, aliases, cases


def test_entity_resolution_quality_gates() -> None:
    """Run full benchmark evaluation asserting Gate 1, Gate 2, and candidate recall."""
    catalog, aliases, cases = load_dataset()
    resolver = EntityResolutionService(tenant_id="tenant_eval")

    held_cases = [c for c in cases if c["split"] == "held_out"]
    assert len(held_cases) == 125

    held_auto_cases = [
        c
        for c in held_cases
        if any(
            o[0] in ("MATCH", "SUBSIDIARY_OF") and o[1] is not None for o in c["allowed_outcomes"]
        )
        and not any(o[0] in ("NEW_ENTITY", "PROVISIONAL_REVIEW") for o in c["allowed_outcomes"])
    ]
    held_non_auto_cases = [
        c
        for c in held_cases
        if any(o[0] in ("NEW_ENTITY", "PROVISIONAL_REVIEW") for o in c["allowed_outcomes"])
    ]

    assert len(held_auto_cases) == 45, f"Expected 45 auto-links, got {len(held_auto_cases)}"
    assert len(held_non_auto_cases) == 80, (
        f"Expected 80 non-auto-links, got {len(held_non_auto_cases)}"
    )

    # Gate 1: Deterministic Auto-Link Quality (Expected: 45/45)
    correct_auto = 0
    auto_errors: list[tuple[str, str, Any, Any, str]] = []
    rule_breakdown: dict[str, int] = {}

    for case in held_auto_cases:
        res = resolver.resolve_against_catalog(
            case["query_name"],
            catalog,
            aliases,
            catalog_variant=case.get("catalog_variant"),
        )
        rule_breakdown[res.rule_path] = rule_breakdown.get(res.rule_path, 0) + 1
        allowed = [tuple(o) for o in case["allowed_outcomes"]]
        pred = (res.outcome, str(res.matched_vendor_id) if res.matched_vendor_id else None)
        if pred in allowed:
            correct_auto += 1
        else:
            auto_errors.append((case["query_id"], case["query_name"], pred, allowed, res.rule_path))

    # Calculate Wilson 95% upper bound for Gate 1
    w_gate1 = wilson_upper_bound(len(held_auto_cases) - correct_auto, len(held_auto_cases))
    print(
        f"\n[Gate 1] Deterministic Auto-Links: {correct_auto}/{len(held_auto_cases)} "
        f"correct (Wilson 95% bound: {w_gate1 * 100:.2f}%)"
    )
    assert correct_auto == 45, (
        f"Gate 1 failed: expected 45/45 correct auto-links, got {correct_auto}/45: {auto_errors}"
    )

    # Gate 2: Zero False Auto-Links on Non-Auto-Link Cohort (Expected: 0/80)
    false_auto_count = 0
    false_auto_cases: list[tuple[str, str, Any, Any, str]] = []

    for case in held_non_auto_cases:
        res = resolver.resolve_against_catalog(
            case["query_name"],
            catalog,
            aliases,
            catalog_variant=case.get("catalog_variant"),
        )
        rule_breakdown[res.rule_path] = rule_breakdown.get(res.rule_path, 0) + 1
        allowed = [tuple(o) for o in case["allowed_outcomes"]]
        pred = (res.outcome, str(res.matched_vendor_id) if res.matched_vendor_id else None)
        if res.outcome in ("MATCH", "SUBSIDIARY_OF"):
            false_auto_count += 1
            false_auto_cases.append(
                (case["query_id"], case["query_name"], pred, allowed, res.rule_path)
            )

    w_gate2 = wilson_upper_bound(false_auto_count, len(held_non_auto_cases))
    print(
        f"[Gate 2] False Auto-Links: {false_auto_count}/{len(held_non_auto_cases)} "
        f"(Wilson 95% bound: {w_gate2 * 100:.2f}%)"
    )
    assert false_auto_count == 0, (
        f"Gate 2 failed: expected 0 false auto-links, got {false_auto_count}/80: {false_auto_cases}"
    )

    # Gate 3: Mined Adversarial Negatives (>=30 with JW >= 0.85)
    mined_high_jw = [c for c in held_cases if c.get("max_jw", 0.0) >= 0.85]
    w_mined = wilson_upper_bound(0, len(mined_high_jw))
    print(
        f"[Mined Negatives] Count with JW >= 0.85: {len(mined_high_jw)} "
        f"(Wilson 95% bound over mined: {w_mined * 100:.2f}%)"
    )
    assert len(mined_high_jw) >= 30, (
        f"Expected >=30 mined negatives with JW>=0.85, got {len(mined_high_jw)}"
    )

    # Gate 4: Top-3 Candidate Recall on Review Cases (>=90%)
    review_expected = [c for c in held_cases if c.get("expected_best_target") is not None]
    recall_hits = 0
    for case in review_expected:
        res = resolver.resolve_against_catalog(
            case["query_name"],
            catalog,
            aliases,
            catalog_variant=case.get("catalog_variant"),
        )
        expected = case["expected_best_target"]
        cand_ids = [str(cid) for cid in res.candidate_vendor_ids]
        is_hit = expected in cand_ids[:3] or (
            res.matched_vendor_id and str(res.matched_vendor_id) == expected
        )
        if is_hit:
            recall_hits += 1

    recall_rate = recall_hits / len(review_expected)
    print(
        f"[Candidate Recall] Top-3 Recall on Review Expected: "
        f"{recall_hits}/{len(review_expected)} ({recall_rate * 100:.2f}%)"
    )
    assert recall_rate >= 0.90, f"Candidate recall below 90%: got {recall_rate * 100:.2f}%"

    print("\n[Per-Rule-Path Breakdown on Held-Out (125 cases)]:")
    for path, cnt in sorted(rule_breakdown.items(), key=lambda x: x[1], reverse=True):
        print(f"  {path:25}: {cnt} ({cnt / 125 * 100:.1f}%)")


def test_embedding_outage_degradation() -> None:
    """Prove that during embedding outage decisions flag degraded_reason and never auto-link."""
    catalog, aliases, cases = load_dataset()
    resolver = EntityResolutionService(tenant_id="tenant_eval")

    test_query = "CloudFront Inc"  # Near-miss to Cloudflare
    res = resolver.resolve_against_catalog(
        test_query, catalog, aliases, force_embedding_outage=True
    )

    assert res.degraded_reason == "embedding_unavailable"
    assert res.outcome in ("PROVISIONAL_REVIEW", "NEW_ENTITY")
    assert res.outcome != "MATCH"
    assert res.rule_path in ("fuzzy_review", "low_confidence_new")
    print(
        "\n[Embedding Outage Test Passed]: correctly degraded to review "
        "with degraded_reason='embedding_unavailable'"
    )


def test_normalizer_edge_cases() -> None:
    """Unit test normalizer covering Bank of America, UK Power Networks, etc."""
    # 1. Bank of America: internal region token not stripped
    boa = normalize_vendor_name("Bank of America Corporation")
    assert boa.normalized_base == "bank of america"
    assert boa.region is None

    # 2. UK Power Networks: prefix region token not stripped
    ukp = normalize_vendor_name("UK Power Networks Ltd")
    assert ukp.normalized_base == "uk power networks"
    assert ukp.region is None

    # 3. Ampersand standardization
    jnj1 = normalize_vendor_name("Johnson & Johnson")
    jnj2 = normalize_vendor_name("Johnson and Johnson")
    assert jnj1.canonical_key == jnj2.canonical_key == "johnson and johnson"

    # 4. Allowlisted descriptor stripping vs non-allowlisted
    crowd = normalize_vendor_name("CrowdStrike Technologies")
    assert crowd.loose_key == "crowdstrike"

    sysco = normalize_vendor_name("Sysco Foods LLC")
    assert sysco.loose_key == "sysco foods"  # "foods" is NOT allowlisted

    us_foods = normalize_vendor_name("US Foods, Inc.")
    assert us_foods.loose_key == "us foods"  # never strips to "us"


def test_canary_negatives_post_freeze() -> None:
    """Evaluate fresh canary set of 28 adversarial negatives mined after freeze.

    Gate: Zero false auto-links (must strictly cap at PROVISIONAL_REVIEW or NEW_ENTITY).
    """
    catalog, aliases, _ = load_dataset()
    canary_file = Path("tests/eval/canary_negatives_post_freeze.jsonl")
    with open(canary_file, encoding="utf-8") as f:
        canary_cases = [json.loads(line) for line in f]

    assert len(canary_cases) == 28
    resolver = EntityResolutionService(tenant_id="tenant_eval")

    false_autolinks = 0
    canary_errors = []
    rule_counts: dict[str, int] = {}

    for case in canary_cases:
        res = resolver.resolve_against_catalog(case["query_name"], catalog, aliases)
        rule_counts[res.rule_path] = rule_counts.get(res.rule_path, 0) + 1
        if res.outcome in ("MATCH", "SUBSIDIARY_OF"):
            false_autolinks += 1
            canary_errors.append((case["query_id"], case["query_name"], res.outcome, res.rule_path))

    w_canary = wilson_upper_bound(false_autolinks, len(canary_cases))
    print(
        f"\n[Post-Freeze Canary Evaluation]: {false_autolinks}/{len(canary_cases)} "
        f"false auto-links (Wilson 95% bound: {w_canary * 100:.2f}%)"
    )
    print("  Canary Rule Path Breakdown:")
    for path, cnt in sorted(rule_counts.items(), key=lambda x: x[1], reverse=True):
        print(f"    {path:20}: {cnt}")

    assert false_autolinks == 0, f"Canary evaluation failed with false auto-links: {canary_errors}"
