"""Strict citation and hallucination verifier for approval reviewers.

Enforces:
1. Every cited citation_id / evidence_id must exist in the context snapshot citations.
2. Every quote must be an exact literal substring of the source document or contract fact.
3. Monotonic degradation: verification failures degrade verdict towards 'needs_human'.
"""

from __future__ import annotations

from typing import Tuple

from procure_agents.approval.models import (
    ApprovalContextSnapshot,
    ReviewerVerdict,
)


class CitationVerificationError(Exception):
    """Raised when an evidence citation or quote fails literal verification."""


class CitationVerifier:
    """Verifies that LLM and agent claims are strictly grounded in ground-truth context facts."""

    @staticmethod
    def verify_verdict(
        verdict: ReviewerVerdict,
        context: ApprovalContextSnapshot,
    ) -> ReviewerVerdict:
        """Verifies all evidence IDs and quotes cited in a ReviewerVerdict.

        Enforces:
        1. Rule-Derived Evidence: Every evidence ID cited by the deterministic rule engine
           must exist in context snapshot citations. Failure degrades verdict to 'needs_human'.
        2. Explainer Citations (Anti-DoS): If an LLM explainer hallucinates a citation or quote,
           the explanation is marked discarded (audited), but the rule-derived verdict is PRESERVED.
           This prevents adversaries from weaponizing explainer hallucinations to cause Denial of Service.
        """
        valid_citations = {c.citation_id: c for c in context.citations}
        new_reasons = list(verdict.reasons)
        rule_failed = False
        explainer_failed = False

        # 1. Verify existence and quotes of rule-derived evidence_ids
        for eid in verdict.evidence_ids:
            if eid not in valid_citations:
                rule_failed = True
                new_reasons.append(
                    f"Rule citation verification failed: cited evidence ID '{eid}' does not exist in context snapshot"
                )
            else:
                cite = valid_citations[eid]
                if cite.quote and cite.quote not in cite.fact and cite.fact not in cite.quote:
                    rule_failed = True
                    new_reasons.append(
                        f"Rule citation verification failed: quote '{cite.quote[:40]}...' is not a literal substring of fact '{cite.fact[:40]}...'"
                    )

        # 2. Verify explainer-derived citations (Explainer-Citation Anti-DoS Guard)
        explainer_cites = list(verdict.explainer_citations)
        for eid in explainer_cites:
            if eid not in valid_citations:
                explainer_failed = True
                new_reasons.append(
                    f"Explainer citation verification failed: cited evidence ID '{eid}' does not exist in context snapshot; explanation discarded (verdict preserved to prevent DoS)"
                )
            else:
                cite = valid_citations[eid]
                if cite.quote and cite.quote not in cite.fact and cite.fact not in cite.quote:
                    explainer_failed = True
                    new_reasons.append(
                        f"Explainer citation verification failed: quote '{cite.quote[:40]}...' is not a literal substring; explanation discarded (verdict preserved to prevent DoS)"
                    )

        # Monotonic degradation on rule evidence failure
        degraded_verdict = ("needs_human" if verdict.verdict == "approve" else verdict.verdict) if rule_failed else verdict.verdict
        discarded_explanation = verdict.explanation_discarded or explainer_failed
        final_explanation = None if discarded_explanation else verdict.explanation

        return ReviewerVerdict(
            reviewer_type=verdict.reviewer_type,
            verdict=degraded_verdict,
            reasons=new_reasons,
            evidence_ids=verdict.evidence_ids,
            policy_version=verdict.policy_version,
            model=verdict.model,
            prompt_version=verdict.prompt_version,
            is_hard_reject=verdict.is_hard_reject,
            hard_reject_trigger=verdict.hard_reject_trigger,
            suggested_assignee="head_of_procurement" if rule_failed else verdict.suggested_assignee,
            explanation=final_explanation,
            explainer_citations=verdict.explainer_citations,
            explanation_discarded=discarded_explanation,
        )

    @staticmethod
    def verify_quote_substring(quote: str, source_text: str) -> bool:
        """Exact literal substring check without loose fuzzy tolerance."""
        if not quote or not source_text:
            return False
        return quote in source_text
