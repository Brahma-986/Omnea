"""Stage 2 Candidate Blocking, Hard Invalidation Guards, and Fuzzy Matching."""

from __future__ import annotations

from typing import Any

from rapidfuzz.distance import JaroWinkler

from procure_context.resolution.normalizer import NormalizedVendorName, extract_digit_tokens


def trigram_similarity(s1: str, s2: str) -> float:
    """Calculate character trigram Jaccard similarity."""
    a, b = s1.lower(), s2.lower()
    t1 = {a[i : i + 3] for i in range(len(a) - 2)}
    t2 = {b[i : i + 3] for i in range(len(b) - 2)}
    if not t1 or not t2:
        return 0.0
    return len(t1 & t2) / len(t1 | t2)


def hard_invalidation_check(
    query: NormalizedVendorName,
    candidate_name: str,
    candidate_canonical_key: str,
    candidate_region: str | None = None,
) -> tuple[bool, str]:
    """Check hard invalidation guards that strictly block any match.

    Returns:
        (is_blocked: bool, reason: str)
    """
    # 1. Short name guard: length <= 3 characters blocks fuzzy matching
    # Note: exact match is evaluated prior to fuzzy matching in the resolver.
    q_len = len(query.normalized_base)
    cand_len = len(candidate_canonical_key.split(":")[0])
    if q_len <= 3 or cand_len <= 3:
        if query.normalized_base != candidate_canonical_key.split(":")[0]:
            return True, f"Short name guard: length <= 3 chars (query={q_len}, cand={cand_len})"

    # 2. Digit mismatch guard: differing digit token sets strictly block match
    cand_digits = extract_digit_tokens(candidate_name)
    if (query.digit_tokens or cand_digits) and query.digit_tokens != cand_digits:
        return (
            True,
            f"Digit mismatch: query digits {set(query.digit_tokens)} "
            f"!= candidate digits {set(cand_digits)}",
        )

    # 3. Differing region tags guard: differing regions strictly block match
    if query.region and candidate_region and query.region != candidate_region:
        return (
            True,
            f"Region mismatch: query region '{query.region}' "
            f"!= candidate region '{candidate_region}'",
        )

    return False, ""


def fuzzy_score_candidate(
    query: NormalizedVendorName,
    candidate_canonical_key: str,
    candidate_name: str,
    candidate_region: str | None = None,
) -> tuple[float, float, bool, str]:
    """Calculate JW similarity, trigram similarity, and apply hard invalidation guards.

    Returns:
        (jw_score: float, trigram_score: float, is_blocked: bool, block_reason: str)
    """
    blocked, reason = hard_invalidation_check(
        query, candidate_name, candidate_canonical_key, candidate_region
    )
    if blocked:
        return 0.0, 0.0, True, reason

    # Compare query base against candidate base
    cand_base = candidate_canonical_key.split(":")[0]
    jw = float(JaroWinkler.similarity(query.normalized_base, cand_base))
    tri = float(trigram_similarity(query.normalized_base, cand_base))

    return jw, tri, False, ""


def block_candidates(
    query: NormalizedVendorName,
    catalog: list[dict[str, Any]],
    min_trigram: float = 0.20,
) -> list[dict[str, Any]]:
    """Stage 2 candidate blocking: prefix OR trigram similarity."""
    q_prefix = (
        query.normalized_base[:3] if len(query.normalized_base) >= 3 else query.normalized_base
    )
    blocked: list[dict[str, Any]] = []

    for item in catalog:
        cand_key = item.get("canonical_key", item["name"].lower())
        cand_base = cand_key.split(":")[0]

        # Prefix match or trigram match
        prefix_match = cand_base.startswith(q_prefix) if len(q_prefix) >= 3 else False
        tri = trigram_similarity(query.normalized_base, cand_base)

        if prefix_match or tri >= min_trigram:
            blocked.append(item)

    return blocked
