"""Script to generate 16 post-freeze canary test cases for approval evaluation."""

from __future__ import annotations

import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.eval.generate_approval_golden import make_case

def generate_canaries():
    cases = []

    # 6 Unseen Vendor Clean Approvals (Different currencies: CHF, JPY, SGD, GBP, EUR, CAD)
    cases.append(
        make_case(
            case_id="canary_01_chf_cloud_storage",
            category="canary_approve",
            path="reviewer",
            desc="Unseen Swiss cloud vendor with CHF budget in Engineering",
            spend=18000.0,
            annualized=18000.0,
            currency="CHF",
            dept="Engineering",
            vendor="AlpineCloud AG",
            budget_amt=80000.0,
            budget_comm=15000.0,
            expected_status="approved",
        )
    )
    cases.append(
        make_case(
            case_id="canary_02_jpy_dev_tools",
            category="canary_approve",
            path="reviewer",
            desc="Unseen Japanese dev tools provider with JPY budget",
            spend=500000.0,
            annualized=500000.0,
            currency="JPY",
            dept="Engineering",
            vendor="TokyoDev KK",
            budget_amt=5000000.0,
            budget_comm=1000000.0,
            policy_vp_threshold=10000000.0,
            expected_status="approved",
        )
    )
    cases.append(
        make_case(
            case_id="canary_03_sgd_consulting",
            category="canary_approve",
            path="reviewer",
            desc="Singaporean advisory consultancy with SGD budget",
            spend=22000.0,
            annualized=22000.0,
            currency="SGD",
            dept="Product",
            vendor="MarinaTech Pte",
            budget_amt=120000.0,
            budget_comm=25000.0,
            expected_status="approved",
        )
    )
    cases.append(
        make_case(
            case_id="canary_04_gbp_observability",
            category="canary_approve",
            path="reviewer",
            desc="British observability platform with GBP budget",
            spend=35000.0,
            annualized=35000.0,
            currency="GBP",
            dept="Infrastructure",
            vendor="ThamesMetrics Ltd",
            budget_amt=150000.0,
            budget_comm=40000.0,
            expected_status="approved",
        )
    )
    cases.append(
        make_case(
            case_id="canary_05_eur_compliance_tool",
            category="canary_approve",
            path="reviewer",
            desc="German compliance automation vendor with EUR budget",
            spend=12500.0,
            annualized=12500.0,
            currency="EUR",
            dept="Legal",
            vendor="BerlinRegTech GmbH",
            budget_amt=90000.0,
            budget_comm=10000.0,
            expected_status="approved",
        )
    )
    cases.append(
        make_case(
            case_id="canary_06_cad_security_scanner",
            category="canary_approve",
            path="reviewer",
            desc="Canadian AppSec vulnerability scanner with CAD budget",
            spend=45000.0,
            annualized=45000.0,
            currency="CAD",
            dept="Security",
            vendor="MontrealSec Inc",
            budget_amt=200000.0,
            budget_comm=30000.0,
            expected_status="approved",
        )
    )

    # 8 Edge Escalations (awaiting_human)
    cases.append(
        make_case(
            case_id="canary_07_chf_over_budget",
            category="canary_escalate",
            path="reviewer",
            desc="Swiss vendor spend breaches department remaining budget",
            spend=65000.0,
            annualized=65000.0,
            currency="CHF",
            dept="Sales",
            vendor="ZurichData SA",
            budget_amt=70000.0,
            budget_comm=30000.0,  # 70k - 30k = 40k remaining < 65k spend
            expected_finance="needs_human",
            expected_status="awaiting_human",
            must_not_auto_approve=True,
        )
    )
    cases.append(
        make_case(
            case_id="canary_08_jpy_vp_threshold",
            category="canary_escalate",
            path="reviewer",
            desc="Spend breaches VP threshold in JPY",
            spend=12000000.0,
            annualized=12000000.0,
            currency="JPY",
            dept="Operations",
            vendor="OsakaRobotics KK",
            budget_amt=50000000.0,
            budget_comm=5000000.0,
            policy_vp_threshold=10000000.0,
            expected_finance="needs_human",
            expected_status="awaiting_human",
            must_not_auto_approve=True,
        )
    )
    cases.append(
        make_case(
            case_id="canary_09_sgd_no_sec_review",
            category="canary_escalate",
            path="reviewer",
            desc="Vendor has no security review on file",
            spend=15000.0,
            annualized=15000.0,
            currency="SGD",
            dept="Marketing",
            vendor="SentosaAI",
            budget_amt=60000.0,
            budget_comm=5000.0,
            sec_status="none",
            sec_freshness="none",
            sec_is_fresh=False,
            expected_security="needs_human",
            expected_status="awaiting_human",
            must_not_auto_approve=True,
        )
    )
    cases.append(
        make_case(
            case_id="canary_10_gbp_stale_soc2",
            category="canary_escalate",
            path="reviewer",
            desc="Vendor security audit report is stale (> 365 days)",
            spend=28000.0,
            annualized=28000.0,
            currency="GBP",
            dept="SupplyChain",
            vendor="AlbionLogistics Ltd",
            budget_amt=100000.0,
            budget_comm=20000.0,
            audit_period_end="2025-01-01",
            sec_freshness="stale",
            sec_is_fresh=False,
            expected_security="needs_human",
            expected_status="awaiting_human",
            must_not_auto_approve=True,
        )
    )
    cases.append(
        make_case(
            case_id="canary_11_eur_contract_missing",
            category="canary_escalate",
            path="reviewer",
            desc="No active contract on file for European vendor",
            spend=19000.0,
            annualized=19000.0,
            currency="EUR",
            dept="Finance",
            vendor="ParisAnalytics SAS",
            budget_amt=80000.0,
            budget_comm=10000.0,
            active_contract=False,
            expected_legal="needs_human",
            expected_status="awaiting_human",
            must_not_auto_approve=True,
        )
    )
    cases.append(
        make_case(
            case_id="canary_12_usd_contract_terminated",
            category="canary_escalate",
            path="reviewer",
            desc="Vendor active contract shows terminated status",
            spend=25000.0,
            annualized=25000.0,
            currency="USD",
            dept="Engineering",
            vendor="DefunctCloud Inc",
            budget_amt=100000.0,
            budget_comm=10000.0,
            active_contract=True,
            contract_status="terminated",
            expected_legal="needs_human",
            expected_status="awaiting_human",
            must_not_auto_approve=True,
        )
    )
    cases.append(
        make_case(
            case_id="canary_13_usd_pii_tag_escalation",
            category="canary_escalate",
            path="reviewer",
            desc="Intent contains pii_access high-risk tag requiring human review",
            spend=30000.0,
            annualized=30000.0,
            currency="USD",
            dept="Product",
            vendor="CustomerInsights Corp",
            budget_amt=120000.0,
            budget_comm=20000.0,
            tags=["pii_access"],
            high_risk_tags=["pii_access"],
            expected_security="needs_human",
            expected_status="awaiting_human",
            must_not_auto_approve=True,
        )
    )
    cases.append(
        make_case(
            case_id="canary_14_cad_self_approval",
            category="canary_escalate",
            path="reviewer",
            desc="Requester is self-approver for Engineering spend",
            spend=14000.0,
            annualized=14000.0,
            currency="CAD",
            dept="Engineering",
            vendor="MontrealDev Ltd",
            budget_amt=90000.0,
            budget_comm=10000.0,
            actor="alice@omnea.internal",
            requester="alice@omnea.internal",
            expected_finance="needs_human",
            expected_status="awaiting_human",
            must_not_auto_approve=True,
        )
    )

    # 2 Hard Rejects (rejected)
    cases.append(
        make_case(
            case_id="canary_15_sanctioned_entity",
            category="canary_reject",
            path="reviewer",
            desc="Vendor flagged on OFAC / international sanctions list",
            spend=50000.0,
            annualized=50000.0,
            currency="USD",
            dept="Engineering",
            vendor="ProhibitedTrade Group",
            budget_amt=200000.0,
            budget_comm=10000.0,
            tags=["sanctioned_entity"],
            expected_legal="reject",
            expected_status="rejected",
            is_smoke_test=True,
            must_not_auto_approve=True,
        )
    )
    cases.append(
        make_case(
            case_id="canary_16_blacklisted_vendor",
            category="canary_reject",
            path="reviewer",
            desc="Vendor explicitly blacklisted due to prior fraud flag",
            spend=40000.0,
            annualized=40000.0,
            currency="USD",
            dept="IT",
            vendor="BlacklistTech Global",
            budget_amt=150000.0,
            budget_comm=10000.0,
            tags=["blacklisted_vendor"],
            expected_legal="reject",
            expected_status="rejected",
            is_smoke_test=True,
            must_not_auto_approve=True,
        )
    )

    out_path = Path("tests/eval/canary_cases.jsonl")
    with open(out_path, "w", encoding="utf-8") as f:
        for c in cases:
            f.write(json.dumps(c) + "\n")
    print(f"Generated {len(cases)} canary cases in {out_path}")

if __name__ == "__main__":
    generate_canaries()
