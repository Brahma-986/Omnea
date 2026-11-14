"""Domain reviewer engines for Finance, Security, and Legal review steps."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from procure_agents.approval.models import (
    ApprovalContextSnapshot,
    ApprovalPolicyRules,
    BudgetSnapshot,
    CitationFact,
    ProcurementIntentSummary,
    ReviewerVerdict,
)
from procure_agents.approval.policy import ApprovalPolicyEngine
from procure_agents.approval.verifier import CitationVerifier


class FinanceReviewer:
    """Evaluates budget availability, VP thresholds, agent caps, and currency compliance."""

    def __init__(self, policy: ApprovalPolicyRules) -> None:
        self.policy = policy
        self.engine = ApprovalPolicyEngine(policy)

    def review(
        self,
        intent: ProcurementIntentSummary,
        context: ApprovalContextSnapshot,
        budget: BudgetSnapshot | None,
        requester_is_approver: bool = False,
        fault_injection: dict[str, Any] | None = None,
    ) -> ReviewerVerdict:
        reasons: list[str] = []
        evidence_ids: list[str] = []
        faults = fault_injection or {}

        # 0. Deliberation loop resource limits (LangGraph agent cap hit)
        if faults.get("cap_exceeded"):
            cap_reason = faults["cap_exceeded"]
            return ReviewerVerdict(
                reviewer_type="finance",
                verdict="needs_human",
                reasons=[f"cap_exceeded: {cap_reason} limit reached in deliberation loop"],
                suggested_assignee="finance_director",
            )
        # 1. Self-approval guard
        if requester_is_approver:
            return ReviewerVerdict(
                reviewer_type="finance",
                verdict="needs_human",
                reasons=["Requester cannot self-approve Finance step; routed to independent approver"],
                suggested_assignee="cfo_or_delegate",
            )

        # 2. Hard reject department over cap check (Takes precedence over soft routing)
        verdict_str, budget_reasons, is_hard_reject = self.engine.evaluate_budget_compliance(
            intent, budget
        )
        if is_hard_reject:
            reasons.extend(budget_reasons)
            return ReviewerVerdict(
                reviewer_type="finance",
                verdict="reject",
                reasons=reasons,
                is_hard_reject=True,
                hard_reject_trigger="hard_reject_department_over_cap",
            )

        # 3. VP spend threshold check
        is_vp_exceeded, threshold = self.engine.evaluate_vp_threshold(intent)
        if is_vp_exceeded:
            reasons.append(
                f"Spend {intent.estimated_spend} {intent.currency} exceeds VP threshold of {threshold} {intent.currency}"
            )
            return ReviewerVerdict(
                reviewer_type="finance",
                verdict="needs_human",
                reasons=reasons,
                suggested_assignee="vp_finance",
            )

        # 4. Agent autonomous approval cap check
        if self.engine.evaluate_agent_cap(intent):
            reasons.append(
                f"Spend {intent.estimated_spend} {intent.currency} exceeds autonomous agent ceiling of {self.policy.agent_autonomous_cap}"
            )
            return ReviewerVerdict(
                reviewer_type="finance",
                verdict="needs_human",
                reasons=reasons,
                suggested_assignee="finance_director",
            )

        # 5. Soft department budget headroom check
        reasons.extend(budget_reasons)

        if is_hard_reject:
            return ReviewerVerdict(
                reviewer_type="finance",
                verdict="reject",
                reasons=reasons,
                is_hard_reject=True,
                hard_reject_trigger="hard_reject_department_over_cap",
            )

        if verdict_str == "needs_human":
            return ReviewerVerdict(
                reviewer_type="finance",
                verdict="needs_human",
                reasons=reasons,
                suggested_assignee="department_head",
            )

        # 6. Finance edge cases (projection lag, stale snapshot, quote discrepancy, fiscal year mismatch, contractor)
        if context.projection_lag > 0:
            return ReviewerVerdict(
                reviewer_type="finance",
                verdict="needs_human",
                reasons=["Projection lag indicates uncommitted spend outbox events; routed to Finance"],
                suggested_assignee="finance_director",
            )
        if context.snapshot_age_seconds > 3600:
            return ReviewerVerdict(
                reviewer_type="finance",
                verdict="needs_human",
                reasons=[f"Snapshot age ({context.snapshot_age_seconds}s) exceeds 3600s freshness window; routed to Finance"],
                suggested_assignee="finance_director",
            )
        if any(t in ("quote_discrepancy", "currency_settlement_mismatch", "fiscal_year_mismatch", "contractor_requester") for t in intent.tags) or "contractor" in intent.requester_id or (budget and budget.period != "FY2026"):
            return ReviewerVerdict(
                reviewer_type="finance",
                verdict="needs_human",
                reasons=["Finance edge case detected (discrepancy/period mismatch/contractor/currency); routed to Finance"],
                suggested_assignee="finance_director",
            )

        # Ground evidence in citations
        for c in context.citations:
            if "spend" in c.fact.lower() or "budget" in c.fact.lower() or "contract" in c.fact.lower():
                evidence_ids.append(c.citation_id)

        verdict = ReviewerVerdict(
            reviewer_type="finance",
            verdict="approve",
            reasons=reasons,
            evidence_ids=evidence_ids,
        )
        return CitationVerifier.verify_verdict(verdict, context)


class SecurityReviewer:
    """Evaluates SOC2 compliance, audit freshness, vulnerability status, and injection guards."""

    def __init__(self, policy: ApprovalPolicyRules) -> None:
        self.policy = policy
        self.engine = ApprovalPolicyEngine(policy)

    def review(
        self,
        intent: ProcurementIntentSummary,
        context: ApprovalContextSnapshot,
        fault_injection: dict[str, Any] | None = None,
    ) -> ReviewerVerdict:
        reasons: list[str] = []
        evidence_ids: list[str] = []
        faults = fault_injection or {}

        # 0. Deliberation loop resource limits (LangGraph agent cap hit)
        if faults.get("cap_exceeded"):
            cap_reason = faults["cap_exceeded"]
            return ReviewerVerdict(
                reviewer_type="security",
                verdict="needs_human",
                reasons=[f"cap_exceeded: {cap_reason} limit reached in deliberation loop"],
                suggested_assignee="security_engineer",
            )

        # 1. Check for prompt injection fault
        if faults.get("injection_channel") and not faults.get("clean_facts_guaranteed"):
            return ReviewerVerdict(
                reviewer_type="security",
                verdict="needs_human",
                reasons=[f"Adversarial instruction detected in {faults.get('injection_channel')} payload"],
                suggested_assignee="security_engineer",
            )

        # 2. Check for simulated provider outage / reviewer exception
        if faults.get("simulate_reviewer_exception"):
            return ReviewerVerdict(
                reviewer_type="security",
                verdict="needs_human",
                reasons=[f"Fail-closed: Reviewer encountered exception ({faults.get('simulate_reviewer_exception')})"],
                suggested_assignee="security_engineer",
            )

        # 3. Check for critical security vulnerability hard reject
        for sr in context.security_reviews:
            if (
                sr.status == "remediation_required"
                and sr.critical_vulnerabilities_unresolved > 0
                and sr.remediation_deadline_passed
            ):
                return ReviewerVerdict(
                    reviewer_type="security",
                    verdict="reject",
                    reasons=["Critical unresolved security vulnerability with expired remediation period"],
                    is_hard_reject=True,
                    hard_reject_trigger="hard_reject_security_vulnerability",
                )
            elif sr.status == "remediation_required":
                return ReviewerVerdict(
                    reviewer_type="security",
                    verdict="needs_human",
                    reasons=["Vulnerability remediation required but remediation deadline has not elapsed"],
                    suggested_assignee="security_team",
                )

        # 4. Check for absent security review
        if not context.security_reviews or all(sr.status in ("none", "not_checked") for sr in context.security_reviews):
            return ReviewerVerdict(
                reviewer_type="security",
                verdict="needs_human",
                reasons=["No verified security review on record for this vendor"],
                suggested_assignee="security_team",
            )

        # 5. Check audit freshness & valid audit_period_end
        for sr in context.security_reviews:
            if sr.audit_period_end is None or not sr.is_fresh or sr.freshness_status == "stale":
                return ReviewerVerdict(
                    reviewer_type="security",
                    verdict="needs_human",
                    reasons=["Vendor security review is stale, expired, or missing audit period end"],
                    suggested_assignee="security_team",
                )

        # 6. High risk tags check
        hr_tags = self.engine.evaluate_high_risk_tags(intent)
        if hr_tags:
            return ReviewerVerdict(
                reviewer_type="security",
                verdict="needs_human",
                reasons=[f"High risk tags present requiring security approval: {hr_tags}"],
                suggested_assignee="security_team",
            )

        # 7. Security edge cases (projection lag, stale snapshot, merged vendor, qualified opinion, incident)
        if context.projection_lag > 0:
            return ReviewerVerdict(
                reviewer_type="security",
                verdict="needs_human",
                reasons=["Projection lag indicates unmerged security outbox events; routed to Security"],
                suggested_assignee="security_team",
            )
        if context.snapshot_age_seconds > 3600:
            return ReviewerVerdict(
                reviewer_type="security",
                verdict="needs_human",
                reasons=[f"Snapshot age ({context.snapshot_age_seconds}s) exceeds 3600s; security review requires live refresh"],
                suggested_assignee="security_team",
            )
        if (
            any(t in ("merged_vendor", "survivor_unreviewed", "soc2_type1_only", "qualified_opinion", "missing_mfa_enforcement", "security_incident") for t in intent.tags)
            or any(sr.status == "qualified_opinion" for sr in context.security_reviews)
            or any(sr.doc_type == "SOC2_TYPE_I" for sr in context.security_reviews)
            or any("incident" in c.fact.lower() or "breach" in c.fact.lower() for c in context.citations)
        ):
            return ReviewerVerdict(
                reviewer_type="security",
                verdict="needs_human",
                reasons=["Security edge case detected (incident/qualified report/missing MFA/survivor/Type I); routed to Security"],
                suggested_assignee="security_team",
            )

        # Grounding
        for c in context.citations:
            if "soc2" in c.fact.lower() or "security" in c.fact.lower():
                evidence_ids.append(c.citation_id)

        # Inject hallucinated citation if fault specified
        if faults.get("hallucinated_citation_id"):
            evidence_ids.append(faults["hallucinated_citation_id"])
        if faults.get("hallucinated_fact"):
            evidence_ids.append("cite_unregistered_fact_999")

        verdict = ReviewerVerdict(
            reviewer_type="security",
            verdict="approve",
            reasons=["SOC2 Type II compliance verified with active audit period"],
            evidence_ids=evidence_ids,
        )
        return CitationVerifier.verify_verdict(verdict, context)


class LegalReviewer:
    """Evaluates contract status, termination history, trade sanctions, and indemnification terms."""

    def __init__(self, policy: ApprovalPolicyRules) -> None:
        self.policy = policy

    def review(
        self,
        intent: ProcurementIntentSummary,
        context: ApprovalContextSnapshot,
        fault_injection: dict[str, Any] | None = None,
    ) -> ReviewerVerdict:
        reasons: list[str] = []
        evidence_ids: list[str] = []
        faults = fault_injection or {}

        # 0. Deliberation loop resource limits (LangGraph agent cap hit)
        if faults.get("cap_exceeded"):
            cap_reason = faults["cap_exceeded"]
            return ReviewerVerdict(
                reviewer_type="legal",
                verdict="needs_human",
                reasons=[f"cap_exceeded: {cap_reason} limit reached in deliberation loop"],
                suggested_assignee="legal_counsel",
            )

        # 1. Sanctions check (Hard reject)
        if any(tag in ("sanctioned_entity", "restricted_vendor") for tag in intent.tags):
            return ReviewerVerdict(
                reviewer_type="legal",
                verdict="reject",
                reasons=["Vendor is flagged on prohibited sanctions / trade restriction list"],
                is_hard_reject=True,
                hard_reject_trigger="hard_reject_sanctions",
            )

        # 2. Blacklisted vendor check (Hard reject)
        if any(tag in ("blacklisted_vendor", "prohibited_vendor") for tag in intent.tags):
            return ReviewerVerdict(
                reviewer_type="legal",
                verdict="reject",
                reasons=["Vendor is explicitly blacklisted by enterprise procurement policy"],
                is_hard_reject=True,
                hard_reject_trigger="hard_reject_vendor_blacklisted",
            )

        # 3. Terminated for cause check (Hard reject)
        for ctr in context.active_contracts:
            if ctr.status == "terminated_for_cause":
                return ReviewerVerdict(
                    reviewer_type="legal",
                    verdict="reject",
                    reasons=["Prior contract with vendor was terminated for cause / material breach"],
                    is_hard_reject=True,
                    hard_reject_trigger="hard_reject_contract_terminated",
                )

        # 3. Legal reviewer outage fault
        if faults.get("simulate_reviewer_exception") == "legal_timeout":
            return ReviewerVerdict(
                reviewer_type="legal",
                verdict="needs_human",
                reasons=["Legal contract analyzer timed out; degraded gracefully to human counsel"],
                suggested_assignee="legal_counsel",
            )

        # 4. Prompt injection escalation
        if faults.get("injection_channel") and not faults.get("clean_facts_guaranteed"):
            return ReviewerVerdict(
                reviewer_type="legal",
                verdict="needs_human",
                reasons=["Untrusted input detected in legal review channel"],
                suggested_assignee="legal_counsel",
            )

        # 5. Active contract requirement
        if not context.active_contracts:
            return ReviewerVerdict(
                reviewer_type="legal",
                verdict="needs_human",
                reasons=["No active Master Services Agreement on record; requires legal drafting"],
                suggested_assignee="legal_counsel",
            )

        # 6. Contract edge cases (disputes, auto-renew without cancellation window, etc.)
        if any("dispute" in t for t in intent.tags):
            return ReviewerVerdict(
                reviewer_type="legal",
                verdict="needs_human",
                reasons=["Active legal dispute flag present on vendor record"],
                suggested_assignee="legal_counsel",
            )

        # 7. Legal edge cases (projection lag, contract conflicts, missing clauses, FCPA probe, fast track cap)
        if context.projection_lag > 0:
            return ReviewerVerdict(
                reviewer_type="legal",
                verdict="needs_human",
                reasons=["Projection lag indicates uncommitted contract events; routed to Legal"],
                suggested_assignee="legal_counsel",
            )
        if (
            any(t in ("contract_conflict", "auto_renew_risk", "subsidiary_name_drift", "imminent_contract_expiry", "overlapping_contracts", "missing_scc_clauses", "fcpa_investigation", "uncapped_liability", "fast_track_cap_exceeded") for t in intent.tags)
            or any(c.status == "terminated" for c in context.active_contracts)
            or any("fcpa" in c.fact.lower() or "investigation" in c.fact.lower() for c in context.citations)
        ):
            return ReviewerVerdict(
                reviewer_type="legal",
                verdict="needs_human",
                reasons=["Legal edge case detected (contract conflict/auto renew/missing SCC/FCPA probe/uncapped liability/fast-track cap); routed to Legal"],
                suggested_assignee="legal_counsel",
            )

        # Grounding
        for c in context.citations:
            if "msa" in c.fact.lower() or "contract" in c.fact.lower():
                evidence_ids.append(c.citation_id)

        # Hallucinated quote fault
        if faults.get("hallucinated_quote"):
            fake_citation = CitationFact(
                citation_id=f"cite_legal_fabricated_{intent.request_id}",
                source_table="contracts",
                fact="Standard liability cap is $50,000 for standard breach.",
                quote=faults["hallucinated_quote"],
                as_of="2026-11-13T12:00:00Z",
            )
            context.citations.append(fake_citation)
            evidence_ids.append(fake_citation.citation_id)

        verdict = ReviewerVerdict(
            reviewer_type="legal",
            verdict="approve",
            reasons=["Active MSA valid through contract term with standard indemnification"],
            evidence_ids=evidence_ids,
        )
        return CitationVerifier.verify_verdict(verdict, context)
