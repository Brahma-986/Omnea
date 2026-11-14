"""Evaluation benchmark stubs to prove quality gates reject naive implementations.

Tests:
1. AlwaysNewStub: predicts NEW_ENTITY -> Fails Gate 1 (0/45 auto-links).
2. ExactAliasOnlyStub: exact/alias only -> Fails candidate recall gate (0/38 recall@3).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

BENCHMARK_FILE = Path("tests/eval/catalog_benchmark.jsonl")
CATALOG_FILE = Path("tests/eval/catalog_snapshot.jsonl")
ALIASES_FILE = Path("tests/eval/vendor_aliases_seed.jsonl")


class AlwaysNewStub:
    """Naive stub that always predicts NEW_ENTITY."""

    def resolve(self, _query_name: str) -> tuple[str, str | None, list[str]]:
        return "NEW_ENTITY", None, []


class ExactAliasOnlyStub:
    """Stub checking only exact and alias matches, returning empty candidates otherwise."""

    def __init__(self, catalog: list[dict[str, Any]], aliases: list[dict[str, Any]]) -> None:
        self.exact_map: dict[str, str] = {c["name"].lower(): c["id"] for c in catalog}
        self.alias_map: dict[str, str] = {a["normalized_alias"]: a["vendor_id"] for a in aliases}

    def resolve(self, query_name: str) -> tuple[str, str | None, list[str]]:
        qn = query_name.strip().lower()
        if qn in self.exact_map:
            vid = self.exact_map[qn]
            return "MATCH", vid, [vid]
        if qn in self.alias_map:
            vid = self.alias_map[qn]
            return "MATCH", vid, [vid]
        return "NEW_ENTITY", None, []


def test_always_new_stub_fails_gate_1() -> None:
    """Prove that an always-NEW_ENTITY resolver fails Gate 1 (0/45 correct auto-links)."""
    with open(BENCHMARK_FILE, encoding="utf-8") as f:
        cases = [json.loads(line) for line in f]

    held_auto_cases = [
        c
        for c in cases
        if c["split"] == "held_out"
        and any(
            o[0] in ("MATCH", "SUBSIDIARY_OF") and o[1] is not None for o in c["allowed_outcomes"]
        )
        and not any(o[0] in ("NEW_ENTITY", "PROVISIONAL_REVIEW") for o in c["allowed_outcomes"])
    ]
    assert len(held_auto_cases) == 45

    stub = AlwaysNewStub()
    correct = 0
    for case in held_auto_cases:
        outcome, target_id, _ = stub.resolve(case["query_name"])
        allowed = [tuple(o) for o in case["allowed_outcomes"]]
        if (outcome, target_id) in allowed:
            correct += 1

    print(f"\n[AlwaysNewStub] Gate 1 Results: {correct}/{len(held_auto_cases)} correct")
    assert correct < 45, "AlwaysNewStub should fail Gate 1"
    with pytest.raises(
        AssertionError,
        match=r"Gate 1 Failed: expected 45/45 correct auto-links, got 0/45",
    ):
        assert correct == 45, f"Gate 1 Failed: expected 45/45 correct auto-links, got {correct}/45"


def test_exact_alias_only_stub_fails_recall_gate() -> None:
    """Prove that an exact+alias-only resolver fails candidate recall on review cases."""
    with open(CATALOG_FILE, encoding="utf-8") as f:
        catalog = [json.loads(line) for line in f]
    with open(ALIASES_FILE, encoding="utf-8") as f:
        aliases = [json.loads(line) for line in f]
    with open(BENCHMARK_FILE, encoding="utf-8") as f:
        cases = [json.loads(line) for line in f]

    review_cases = [
        c for c in cases if c["split"] == "held_out" and c.get("expected_best_target") is not None
    ]
    assert len(review_cases) >= 30

    stub = ExactAliasOnlyStub(catalog, aliases)
    recall_hits = 0
    for case in review_cases:
        _, _, candidates = stub.resolve(case["query_name"])
        expected = case["expected_best_target"]
        if expected in candidates[:3]:
            recall_hits += 1

    recall_rate = recall_hits / len(review_cases)
    print(
        f"\n[ExactAliasOnlyStub] Top-3 Candidate Recall: "
        f"{recall_hits}/{len(review_cases)} ({recall_rate * 100:.2f}%)"
    )
    with pytest.raises(
        AssertionError,
        match=r"Recall Gate Failed: expected recall@3 >= 90%, got 0.00%",
    ):
        assert recall_rate >= 0.90, (
            f"Recall Gate Failed: expected recall@3 >= 90%, got {recall_rate * 100:.2f}%"
        )
