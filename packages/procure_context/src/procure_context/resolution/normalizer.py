"""Stage 1 Normalization: Legal suffixes, end-of-name region tokens, and canonical keys."""

from __future__ import annotations

import re
from dataclasses import dataclass

# 1. Conservative Legal Suffixes (stripped only at the very end of string)
LEGAL_SUFFIXES: list[str] = [
    "pty ltd",
    "pte ltd",
    "limited liability company",
    "corporation",
    "incorporated",
    "s.p.a.",
    "s.a.",
    "corp.",
    "corp",
    "inc.",
    "inc",
    "llc",
    "llp",
    "ltd.",
    "ltd",
    "gmbh",
    "co.",
    "co",
    "a.g.",
    "ag",
    "n.v.",
    "nv",
    "a.b.",
    "ab",
    "o.y.",
    "oy",
    "s.a.s.",
    "sas",
    "se",
    "co. kg",
    "co kg",
    "kg",
]

# 2. End-of-name Region Tags
REGIONS: list[str] = [
    "emea",
    "apac",
    "latam",
    "nordic",
    "europe",
    "america",
    "germany",
    "deutschland",
    "france",
    "ireland",
    "uk",
    "usa",
    "us",
    "na",
]

# 3. Regional Synonym Map
REGION_SYNONYMS: dict[str, str] = {
    "deutschland": "germany",
    "usa": "us",
    "united kingdom": "uk",
    "great britain": "uk",
}

# 4. Strict Allowlist of Loose Descriptors (technologies/computing/operations only;
# NOT systems/foods/labs)
ALLOWLISTED_DESCRIPTORS: set[str] = {
    "technologies",
    "computing",
    "operations",
}

FORBIDDEN_DESCRIPTORS: set[str] = {
    "systems",
    "foods",
    "labs",
}

# Protected multi-word phrases where internal region tokens must NOT be stripped
PROTECTED_PHRASES: set[str] = {
    "bank of america",
    "power networks uk",
    "uk power networks",
}


@dataclass(frozen=True)
class NormalizedVendorName:
    """Normalized vendor representation with components."""

    raw: str
    normalized_base: str
    region: str | None
    canonical_key: str
    loose_key: str
    legal_suffix: str | None
    digit_tokens: frozenset[str]


def extract_digit_tokens(text: str) -> frozenset[str]:
    """Extract standalone and alphanumeric digit tokens for mismatch blocking."""
    tokens = set(re.findall(r"\b\d+\b", text))
    alphanumeric_digits = set(re.findall(r"(?<=[a-zA-Z])\d+|\d+(?=[a-zA-Z])", text))
    return frozenset(tokens | alphanumeric_digits)


def normalize_vendor_name(raw: str) -> NormalizedVendorName:
    """Execute Stage 1 normalization pipeline.

    1. Casing & punctuation cleanup (standardize '&' to 'and').
    2. Digit spacing normalization (e.g. 'Web 3' -> 'Web3').
    3. Legal suffix stripping (conservative, suffix-only).
    4. End-of-name region extraction (preserves phrases like 'Bank of America').
    5. Regional synonym mapping (e.g. 'Deutschland' -> 'Germany').
    6. Loose key generation with allowlisted descriptors.
    """
    s = raw.strip().lower()

    # Digits for mismatch checking
    digits = extract_digit_tokens(s)

    # Standardize ampersands to 'and'
    s = re.sub(r"\s*&\s*", " and ", s)

    # Normalize digit spacing (e.g., 'web 3' -> 'web3', 'level 4' -> 'level 4')
    # Specifically normalize 'web 3' -> 'web3'
    s = re.sub(r"\bweb\s+(\d+)\b", r"web\1", s)

    # Remove commas, periods, quotes, parens, colons (except meaningful letters/digits)
    s = re.sub(r"[\.,'\"/\\():]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()

    # Step A: Strip legal suffix from the end
    legal_suffix_found: str | None = None
    changed = True
    while changed:
        changed = False
        for suffix in LEGAL_SUFFIXES:
            pattern = rf"\b{re.escape(suffix)}$"
            match = re.search(pattern, s)
            if match:
                remainder = s[: match.start()].strip()
                if remainder:  # Don't strip if nothing remains
                    legal_suffix_found = suffix
                    s = remainder
                    changed = True
                    break

    # Step B: End-of-name region extraction
    # Strip region tokens ONLY if anchored at the end and remainder is non-empty,
    # and string is NOT a protected phrase like 'bank of america'.
    extracted_region: str | None = None

    if s not in PROTECTED_PHRASES:
        for reg in REGIONS:
            # Anchored at word boundary at the END of string
            pattern = rf"\b{re.escape(reg)}$"
            match = re.search(pattern, s)
            if match:
                remainder = s[: match.start()].strip()
                # Make sure remainder is non-empty and not just noise
                if remainder and remainder not in PROTECTED_PHRASES:
                    extracted_region = reg
                    s = remainder
                    # Map synonym
                    extracted_region = REGION_SYNONYMS.get(extracted_region, extracted_region)
                    break

    # If legal suffix was after region (e.g. 'Datadog EMEA LLC'), check legal suffix again
    for suffix in LEGAL_SUFFIXES:
        pattern = rf"\b{re.escape(suffix)}$"
        match = re.search(pattern, s)
        if match:
            remainder = s[: match.start()].strip()
            if remainder:
                s = remainder
                break

    normalized_base = s

    # Canonical key: base + (":" + region if region else "")
    if extracted_region:
        canonical_key = f"{normalized_base}:{extracted_region}"
    else:
        canonical_key = normalized_base

    # Step C: Loose key generation using allowlisted descriptors
    # Strip descriptor ONLY if in ALLOWLISTED_DESCRIPTORS and NOT in FORBIDDEN_DESCRIPTORS
    loose_key = normalized_base
    for desc in ALLOWLISTED_DESCRIPTORS:
        pattern = rf"\b{re.escape(desc)}$"
        match = re.search(pattern, loose_key)
        if match:
            rem = loose_key[: match.start()].strip()
            if rem:
                loose_key = rem
                break

    return NormalizedVendorName(
        raw=raw,
        normalized_base=normalized_base,
        region=extracted_region,
        canonical_key=canonical_key,
        loose_key=loose_key,
        legal_suffix=legal_suffix_found,
        digit_tokens=digits,
    )
