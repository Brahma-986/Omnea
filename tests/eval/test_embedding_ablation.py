"""Ablation benchmark comparing JW-only vs JW+HashEmbedding vs JW+RealEmbedding (384-dim)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from procure_context.resolution.embeddings import HashEmbeddingProvider, RealEmbeddingProvider
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


@pytest.mark.real_embeddings
def test_embedding_ablation_evaluation() -> None:
    """Run full benchmark ablation comparing:
    1. JW-only (embeddings disabled / outage fallback)
    2. JW + HashEmbeddingProvider (384-dim trigram hash)
    3. JW + RealEmbeddingProvider (384-dim all-MiniLM-L6-v2)
    """
    catalog, aliases, cases = load_dataset()
    held_cases = [c for c in cases if c["split"] == "held_out"]
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
    held_review_expected = [c for c in held_cases if c.get("expected_best_target") is not None]

    configs = [
        (
            "JW-only (Embeddings Outage Fallback)",
            EntityResolutionService("eval", embedding_provider=HashEmbeddingProvider()),
            True,
        ),
        (
            "JW + HashEmbeddingProvider (MD5 Trigram R^384)",
            EntityResolutionService("eval", embedding_provider=HashEmbeddingProvider()),
            False,
        ),
        (
            "JW + RealEmbeddingProvider (all-MiniLM-L6-v2 R^384)",
            EntityResolutionService("eval", embedding_provider=RealEmbeddingProvider()),
            False,
        ),
    ]

    print("\n" + "=" * 80)
    print("PHASE 3 ENTITY RESOLUTION ABLATION STUDY (JW vs HASH vs REAL MINI-LM 384-DIM)")
    print("=" * 80)

    for config_name, resolver, force_outage in configs:
        # Gate 1: Deterministic auto-links (must be 45/45)
        correct_auto = 0
        for case in held_auto_cases:
            res = resolver.resolve_against_catalog(
                case["query_name"],
                catalog,
                aliases,
                catalog_variant=case.get("catalog_variant"),
                force_embedding_outage=force_outage,
            )
            allowed = [tuple(o) for o in case["allowed_outcomes"]]
            pred = (res.outcome, str(res.matched_vendor_id) if res.matched_vendor_id else None)
            if pred in allowed:
                correct_auto += 1

        w_gate1 = wilson_upper_bound(len(held_auto_cases) - correct_auto, len(held_auto_cases))

        # Gate 2: False auto-links on non-auto-link cases (must be 0/80)
        false_auto = 0
        for case in held_non_auto_cases:
            res = resolver.resolve_against_catalog(
                case["query_name"],
                catalog,
                aliases,
                catalog_variant=case.get("catalog_variant"),
                force_embedding_outage=force_outage,
            )
            allowed = [tuple(o) for o in case["allowed_outcomes"]]
            pred = (res.outcome, str(res.matched_vendor_id) if res.matched_vendor_id else None)
            if res.outcome in ("MATCH", "SUBSIDIARY_OF") and pred not in allowed:
                false_auto += 1

        w_gate2 = wilson_upper_bound(false_auto, len(held_non_auto_cases))

        # Review Candidate Recall@3
        recall_hits = 0
        for case in held_review_expected:
            exp_target = case["expected_best_target"]
            res = resolver.resolve_against_catalog(
                case["query_name"],
                catalog,
                aliases,
                catalog_variant=case.get("catalog_variant"),
                force_embedding_outage=force_outage,
            )
            cands = [str(cid) for cid in res.candidate_vendor_ids]
            if exp_target in cands[:3] or (
                res.matched_vendor_id and str(res.matched_vendor_id) == exp_target
            ):
                recall_hits += 1

        recall_at_3 = (
            (recall_hits / len(held_review_expected)) * 100 if held_review_expected else 100.0
        )

        print(f"\nConfiguration: {config_name}")
        print(
            f"  Gate 1 (Deterministic Auto-Links): {correct_auto}/{len(held_auto_cases)} "
            f"correct (Wilson 95% bound: {w_gate1 * 100:.2f}%)"
        )
        print(
            f"  Gate 2 (False Auto-Links on Held-out Negatives): "
            f"{false_auto}/{len(held_non_auto_cases)} false links "
            f"(Wilson 95% bound: {w_gate2 * 100:.2f}%)"
        )
        print(
            f"  Review Candidate Recall@3: {recall_hits}/{len(held_review_expected)} "
            f"({recall_at_3:.2f}%)"
        )

        assert correct_auto == 45, f"{config_name} failed Gate 1"
        assert false_auto == 0, f"{config_name} failed Gate 2"
        assert recall_at_3 >= 90.0, f"{config_name} failed Candidate Recall@3"
