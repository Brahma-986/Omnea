"""Unit test to verify catalog benchmark dataset integrity and disjoint family splits."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


def validate_splits(
    catalog: list[dict[str, Any]],
    cases: list[dict[str, Any]],
) -> None:
    """Validate strict disjointness and case counts between Dev and Held-Out."""
    dev_cases = [c for c in cases if c["split"] == "dev"]
    held_cases = [c for c in cases if c["split"] == "held_out"]

    dev_query_families = {c["vendor_family"] for c in dev_cases}
    held_query_families = {c["vendor_family"] for c in held_cases}

    # 1. Query family disjointness
    query_overlap = dev_query_families & held_query_families
    assert not query_overlap, f"Query family leakage detected between splits: {query_overlap}"

    # 2. Catalog family disjointness
    dev_cat_families = {c["family"] for c in catalog if c["split"] == "dev"}
    held_cat_families = {c["family"] for c in catalog if c["split"] == "held_out"}
    cat_overlap = dev_cat_families & held_cat_families
    assert not cat_overlap, f"Catalog family leakage detected between splits: {cat_overlap}"

    # 3. Cross disjointness (Dev queries vs Held-Out catalog and vice versa)
    cross_dev_held = dev_query_families & held_cat_families
    assert not cross_dev_held, f"Dev query family present in Held-Out catalog: {cross_dev_held}"
    cross_held_dev = held_query_families & dev_cat_families
    assert not cross_held_dev, f"Held-Out query family present in Dev catalog: {cross_held_dev}"


def test_catalog_benchmark_split_integrity() -> None:
    """Test frozen catalog snapshot and benchmark cases pass all split integrity checks."""
    benchmark_file = Path("tests/eval/catalog_benchmark.jsonl")
    catalog_file = Path("tests/eval/catalog_snapshot.jsonl")

    assert benchmark_file.exists(), "Benchmark file must exist"
    assert catalog_file.exists(), "Catalog snapshot must exist"

    with open(catalog_file, encoding="utf-8") as f:
        catalog = [json.loads(line) for line in f]

    with open(benchmark_file, encoding="utf-8") as f:
        cases = [json.loads(line) for line in f]

    assert len(catalog) >= 240
    assert len(cases) == 250

    dev_cases = [c for c in cases if c["split"] == "dev"]
    held_cases = [c for c in cases if c["split"] == "held_out"]

    assert len(dev_cases) == 125
    assert len(held_cases) == 125

    # Run full split validation
    validate_splits(catalog, cases)

    held_auto_links = [
        c
        for c in held_cases
        if any(
            outcome[0] in ("MATCH", "SUBSIDIARY_OF") and outcome[1] is not None
            for outcome in c["allowed_outcomes"]
        )
        and not any(
            outcome[0] in ("NEW_ENTITY", "PROVISIONAL_REVIEW") for outcome in c["allowed_outcomes"]
        )
    ]
    held_non_auto = [
        c
        for c in held_cases
        if any(
            outcome[0] in ("NEW_ENTITY", "PROVISIONAL_REVIEW") for outcome in c["allowed_outcomes"]
        )
    ]

    assert len(held_auto_links) == 45, (
        f"Expected 45 held-out auto links, got {len(held_auto_links)}"
    )
    assert len(held_non_auto) == 80, (
        f"Expected 80 held-out non-auto links, got {len(held_non_auto)}"
    )

    # Check adversarial negatives and expected review targets
    mined_high_jw = [c for c in held_cases if c.get("max_jw", 0.0) >= 0.85]
    review_expected = [c for c in held_cases if c.get("expected_best_target") is not None]

    assert len(mined_high_jw) >= 30, (
        f"Expected >=30 mined negatives with JW>=0.85, got {len(mined_high_jw)}"
    )
    assert len(review_expected) >= 30, (
        f"Expected >=30 review cases with expected_best_target, got {len(review_expected)}"
    )


def test_split_integrity_fails_on_injected_leakage() -> None:
    """Negative test: prove validate_splits raises AssertionError on leaked family."""
    benchmark_file = Path("tests/eval/catalog_benchmark.jsonl")
    catalog_file = Path("tests/eval/catalog_snapshot.jsonl")

    with open(catalog_file, encoding="utf-8") as f:
        catalog = [json.loads(line) for line in f]

    with open(benchmark_file, encoding="utf-8") as f:
        cases = [json.loads(line) for line in f]

    # Injected leakage in query families:
    leaked_cases = list(cases)
    # Clone a Dev case and inject into held_out with the same family
    dev_sample = [c for c in leaked_cases if c["split"] == "dev"][0]
    leaked_cases.append(
        {
            "query_id": "case_leaked_999",
            "split": "held_out",
            "query_name": f"{dev_sample['query_name']} Leaked Query",
            "vendor_family": dev_sample["vendor_family"],  # LEAK!
            "allowed_outcomes": [["NEW_ENTITY", None]],
        }
    )

    with pytest.raises(AssertionError, match="Query family leakage detected"):
        validate_splits(catalog, leaked_cases)

    # Injected leakage in catalog families:
    leaked_catalog = list(catalog)
    dev_cat_sample = [c for c in leaked_catalog if c["split"] == "dev"][0]
    leaked_catalog.append(
        {
            "id": "leaked-cat-uuid",
            "name": f"{dev_cat_sample['name']} Subsidiary Leaked",
            "family": dev_cat_sample["family"],  # LEAK!
            "split": "held_out",
            "aliases": [],
            "parent_id": None,
            "status": "active",
        }
    )

    with pytest.raises(AssertionError, match="Catalog family leakage detected"):
        validate_splits(leaked_catalog, cases)
