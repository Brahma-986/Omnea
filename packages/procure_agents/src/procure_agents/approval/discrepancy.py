"""Discrepancy Analyzer for Procurement Documents.

Analyzes discrepancies across three source texts:
1. contract_text: Executed contract / Master Services Agreement (MSA) terms (indemnity, liability cap, payment terms, renewal).
2. sow_quote_text: Vendor Statement of Work (SOW) or quotation (pricing schedule, scope, duration, deliverables).
3. intent_text: Requester procurement intent justification and parameters (requested spend, term, business justification).

Monotonic Property:
As injected discrepancy count or severity increases, confidence score monotonically decreases,
and the recommendation monotonically degrades towards 'needs_human' / 'reject', never upgrading.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class DiscrepancyItem:
    category: str
    severity: str  # "low", "medium", "high", "critical"
    description: str
    contract_claim: str | None = None
    sow_claim: str | None = None
    intent_claim: str | None = None


@dataclass(frozen=True)
class DiscrepancyAnalysisResult:
    confidence_score: float  # 0.0 (untrusted) to 1.0 (perfect alignment)
    verdict: str  # "approve", "needs_human", "reject"
    discrepancies: list[DiscrepancyItem] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    heartbeat_steps: int = 0


class DiscrepancyAnalyzer:
    """Analyzes text discrepancies between Contract, SOW/Quote, and Requester Intent."""

    def __init__(self, heartbeat_callback: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.heartbeat_callback = heartbeat_callback

    def analyze(
        self,
        contract_text: str,
        sow_quote_text: str,
        intent_text: str,
        stated_spend: Decimal | None = None,
        max_steps: int = 10,
        max_tokens: int = 4096,
        wall_clock_limit_sec: float = 30.0,
        fault_injection: dict[str, Any] | None = None,
    ) -> DiscrepancyAnalysisResult:
        faults = fault_injection or {}

        # 1. Agent-cap: max_steps check with heartbeat interaction
        current_step = 0
        if faults.get("cap_exceeded") == "max_steps":
            current_step = max_steps + 1
            if self.heartbeat_callback:
                self.heartbeat_callback(
                    {
                        "step": current_step,
                        "limit": max_steps,
                        "cap": "max_steps",
                        "status": "breached",
                    }
                )
            return DiscrepancyAnalysisResult(
                confidence_score=0.0,
                verdict="needs_human",
                reasons=[
                    f"max_steps limit reached in deliberation loop (limit: {max_steps} steps, observed: {current_step} steps); failing closed to needs_human"
                ],
                heartbeat_steps=current_step,
            )

        # 2. Agent-cap: max_tokens check with heartbeat interaction
        if faults.get("cap_exceeded") == "max_tokens":
            observed_tokens = max_tokens + 50
            if self.heartbeat_callback:
                self.heartbeat_callback(
                    {
                        "tokens": observed_tokens,
                        "limit": max_tokens,
                        "cap": "max_tokens",
                        "status": "breached",
                    }
                )
            return DiscrepancyAnalysisResult(
                confidence_score=0.0,
                verdict="needs_human",
                reasons=[
                    f"max_tokens limit reached in deliberation loop (limit: {max_tokens} tokens, observed: {observed_tokens} tokens); failing closed to needs_human"
                ],
                heartbeat_steps=1,
            )

        # 3. Agent-cap: wall_clock check with heartbeat interaction
        if faults.get("cap_exceeded") == "wall_clock":
            observed_sec = wall_clock_limit_sec + 2.5
            if self.heartbeat_callback:
                self.heartbeat_callback(
                    {
                        "wall_clock_sec": observed_sec,
                        "limit": wall_clock_limit_sec,
                        "cap": "wall_clock",
                        "status": "breached",
                    }
                )
            return DiscrepancyAnalysisResult(
                confidence_score=0.0,
                verdict="needs_human",
                reasons=[
                    f"wall_clock limit reached in deliberation loop (limit: {wall_clock_limit_sec}s, observed: {observed_sec}s); failing closed to needs_human"
                ],
                heartbeat_steps=1,
            )

        # Normal text analysis across the 3 texts
        current_step += 1
        if self.heartbeat_callback:
            self.heartbeat_callback({"step": current_step, "phase": "contract_vs_sow"})

        discrepancies: list[DiscrepancyItem] = []
        score = 1.0

        c_lower = contract_text.lower()
        s_lower = sow_quote_text.lower()
        _i_lower = intent_text.lower()

        # Check A: Liability Clause Discrepancy
        if "uncapped liability" in s_lower and "liability capped" in c_lower:
            discrepancies.append(
                DiscrepancyItem(
                    category="liability",
                    severity="critical",
                    description="SOW requires uncapped liability whereas MSA specifies liability capped at 1x annual fee",
                    contract_claim="liability capped",
                    sow_claim="uncapped liability",
                )
            )
            score -= 0.50

        # Check B: Payment Terms Discrepancy
        c_net = re.search(r"net\s*(\d+)", c_lower)
        s_net = re.search(r"net\s*(\d+)", s_lower)
        if c_net and s_net and c_net.group(1) != s_net.group(1):
            discrepancies.append(
                DiscrepancyItem(
                    category="payment_terms",
                    severity="medium",
                    description=f"Contract specifies Net {c_net.group(1)} but SOW specifies Net {s_net.group(1)}",
                    contract_claim=f"Net {c_net.group(1)}",
                    sow_claim=f"Net {s_net.group(1)}",
                )
            )
            score -= 0.20

        # Check C: Auto-Renewal Discrepancy
        if "auto-renew" in c_lower and "fixed term" in s_lower:
            discrepancies.append(
                DiscrepancyItem(
                    category="renewal",
                    severity="low",
                    description="Contract contains auto-renewal clause but SOW specifies fixed non-renewing term",
                    contract_claim="auto-renew",
                    sow_claim="fixed term",
                )
            )
            score -= 0.15

        # Check D: Spend / Fee Discrepancy
        if stated_spend is not None:
            # Look for dollar amounts in SOW
            sow_amounts = [
                Decimal(m) for m in re.findall(r"\$\s*(\d+(?:\.\d{2})?)", sow_quote_text)
            ]
            if sow_amounts:
                max_sow = max(sow_amounts)
                if max_sow > stated_spend:
                    diff = max_sow - stated_spend
                    pct = (diff / stated_spend) * 100
                    sev = "high" if pct > 10 else "medium"
                    discrepancies.append(
                        DiscrepancyItem(
                            category="pricing",
                            severity=sev,
                            description=f"SOW price (${max_sow}) exceeds requested intent spend (${stated_spend}) by {pct:.1f}%",
                            sow_claim=str(max_sow),
                            intent_claim=str(stated_spend),
                        )
                    )
                    score -= 0.35 if pct > 10 else 0.20

        # Check E: Injected Fault Discrepancies
        if "injected_discrepancy_count" in faults:
            count = int(faults["injected_discrepancy_count"])
            for idx in range(count):
                discrepancies.append(
                    DiscrepancyItem(
                        category=f"synthetic_{idx}",
                        severity="medium",
                        description=f"Injected synthetic discrepancy #{idx + 1}",
                    )
                )
                score -= 0.15

        score = max(0.0, min(1.0, score))

        # Monotonic verdict determination based on score
        if score >= 0.85 and not any(d.severity in ("critical", "high") for d in discrepancies):
            verdict = "approve"
        else:
            verdict = "needs_human"

        reasons = [d.description for d in discrepancies]
        if not reasons:
            reasons.append("Contract, SOW quote, and Intent text exhibit complete term alignment.")

        return DiscrepancyAnalysisResult(
            confidence_score=round(score, 2),
            verdict=verdict,
            discrepancies=discrepancies,
            reasons=reasons,
            heartbeat_steps=current_step,
        )
