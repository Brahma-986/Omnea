"""Unit tests for Stage 4: Finance, Legal, Vendor APIs, and Agents."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from procure_agents.approval.models import ApprovalPolicyRules
from procure_agents.policy.drafter import (
    DeterministicPolicyValidator,
    PolicyDraftingAgent,
)
from procure_agents.research.vendor_researcher import (
    GroundedClaim,
    VendorResearchAgent,
    VendorResearchExtraction,
)
from procure_core.auth.rbac import RbacEngine
from procure_core.config import Settings
from procure_core.models.vendor import (
    ContractModel,
    SpendTransactionModel,
    VendorModel,
)


@pytest.fixture(autouse=True)
def setup_stage4_test_roles() -> Iterator[None]:
    """Configure RBAC roles for Stage 4 unit tests."""
    RbacEngine.clear_fallback_roles()

    RbacEngine.assign_fallback_role("*", "finance@omnea.ai", "finance_admin")
    RbacEngine.assign_fallback_role("*", "legal@omnea.ai", "legal_reviewer")
    RbacEngine.assign_fallback_role("*", "approver@omnea.ai", "approver")
    RbacEngine.assign_fallback_role("*", "admin@omnea.ai", "tenant_admin")
    RbacEngine.assign_fallback_role("*", "unauthorized@omnea.ai", "requester")

    yield

    RbacEngine.clear_fallback_roles()


# =============================================================================
# 1. Deterministic Policy Validator & Anti-Weakening Checks
# =============================================================================


@pytest.mark.unit
def test_policy_validator_schema_extra_fields_forbidden() -> None:
    """Extra unexpected fields in candidate rules are strictly rejected."""
    validator = DeterministicPolicyValidator()
    result = validator.validate({"arbitrary_unknown_key": "malicious_override"})
    assert result.valid is False
    assert any("Schema violation" in r for r in result.reasons)


@pytest.mark.unit
def test_policy_validator_autonomous_cap_hard_ceiling_and_weakening() -> None:
    """Autonomous cap cannot exceed 100,000 USD and cannot increase without admin override."""
    validator = DeterministicPolicyValidator()

    # Exceed hard ceiling (150,000 > 100,000)
    res_ceiling = validator.validate({"agent_autonomous_cap": Decimal("150000.00")})
    assert res_ceiling.valid is False
    assert any("exceeds hard ceiling" in r for r in res_ceiling.reasons)

    # Increase above existing without override
    existing = ApprovalPolicyRules(agent_autonomous_cap=Decimal("50000.00"))
    res_increase = validator.validate(
        {"agent_autonomous_cap": Decimal("75000.00")},
        existing_rules=existing,
        admin_override=False,
    )
    assert res_increase.valid is False
    assert any("cannot be increased" in r for r in res_increase.reasons)

    # Increase allowed with admin override
    res_override = validator.validate(
        {"agent_autonomous_cap": Decimal("75000.00")},
        existing_rules=existing,
        admin_override=True,
    )
    assert res_override.valid is True
    assert res_override.validated_rules is not None
    assert Decimal(str(res_override.validated_rules["agent_autonomous_cap"])) == Decimal("75000.00")


@pytest.mark.unit
def test_policy_validator_vp_spend_threshold_anti_weakening() -> None:
    """Existing currency thresholds cannot be deleted or weakened without admin confirmation."""
    validator = DeterministicPolicyValidator()

    # Empty threshold map rejected
    res_empty = validator.validate({"vp_spend_threshold": {}})
    assert res_empty.valid is False
    assert any("must NOT be empty" in r for r in res_empty.reasons)

    # Currency deleted check
    existing = ApprovalPolicyRules(
        vp_spend_threshold={"USD": Decimal("50000.00"), "EUR": Decimal("45000.00")}
    )
    # Candidate missing EUR
    res_del = validator.validate(
        {"vp_spend_threshold": {"USD": Decimal("50000.00")}},
        existing_rules=existing,
        admin_override=False,
    )
    assert res_del.valid is False
    assert any(
        "cannot be deleted without explicit human confirmation" in r for r in res_del.reasons
    )

    # Weakening currency threshold (raising threshold allows larger spend without VP)
    res_weaken = validator.validate(
        {"vp_spend_threshold": {"USD": Decimal("80000.00"), "EUR": Decimal("45000.00")}},
        existing_rules=existing,
        admin_override=False,
    )
    assert res_weaken.valid is False
    assert any("cannot be weakened" in r for r in res_weaken.reasons)

    # Override permits change
    res_allow = validator.validate(
        {"vp_spend_threshold": {"USD": Decimal("80000.00"), "EUR": Decimal("45000.00")}},
        existing_rules=existing,
        admin_override=True,
    )
    assert res_allow.valid is True


@pytest.mark.unit
def test_policy_validator_fast_track_and_over_budget_guards() -> None:
    """Fast track cap cannot exceed 3 and over_budget_action must be valid."""
    validator = DeterministicPolicyValidator()

    # Fast track cap > 3 rejected
    res_ft = validator.validate({"allow_fast_track": True, "fast_track_consecutive_cap": 5})
    assert res_ft.valid is False
    assert any("fast_track_consecutive_cap cannot exceed 3" in r for r in res_ft.reasons)

    # Invalid over budget action rejected by schema
    res_act = validator.validate({"over_budget_action": "bypass_budget"})
    assert res_act.valid is False


@pytest.mark.unit
def test_policy_diff_computation() -> None:
    """Deterministic diff correctly partitions added, modified, and removed fields."""
    existing = ApprovalPolicyRules(
        agent_autonomous_cap=Decimal("50000.00"),
        allow_fast_track=False,
    )
    candidate = ApprovalPolicyRules(
        agent_autonomous_cap=Decimal("40000.00"),
        allow_fast_track=True,
    )
    diff = DeterministicPolicyValidator.compute_diff(existing, candidate)
    assert "agent_autonomous_cap" in diff["modified"]
    assert diff["modified"]["agent_autonomous_cap"]["old"] == 50000.0
    assert diff["modified"]["agent_autonomous_cap"]["new"] == 40000.0
    assert diff["modified"]["allow_fast_track"]["old"] is False
    assert diff["modified"]["allow_fast_track"]["new"] is True


# =============================================================================
# 2. Policy Drafting Agent
# =============================================================================


@pytest.mark.unit
async def test_policy_drafting_agent_unconfigured_error() -> None:
    """Agent returns error when LLM API key is unconfigured."""
    settings = Settings(LLM_API_KEY="")
    agent = PolicyDraftingAgent(settings=settings)
    res = await agent.draft_policy("Lower autonomous cap to 25k")
    assert res.success is False
    assert "LLM provider is not configured" in (res.error or "")


@pytest.mark.unit
async def test_policy_drafting_agent_with_mock_client() -> None:
    """Agent drafts valid candidate rules and produces diff using structured LLM mock."""
    mock_llm = AsyncMock()
    mock_llm.call_structured.return_value = ApprovalPolicyRules(
        agent_autonomous_cap=Decimal("25000.00"),
        allow_fast_track=True,
        fast_track_consecutive_cap=3,
    )
    settings = Settings(LLM_API_KEY="test_key")
    agent = PolicyDraftingAgent(llm_client=mock_llm, settings=settings)

    existing = ApprovalPolicyRules(agent_autonomous_cap=Decimal("50000.00"))
    res = await agent.draft_policy(
        "Reduce cap to 25k and enable fast track", existing_rules=existing
    )

    assert res.success is True
    assert res.proposed_rules is not None
    assert Decimal(str(res.proposed_rules["agent_autonomous_cap"])) == Decimal("25000.00")
    assert "agent_autonomous_cap" in res.diff["modified"]


# =============================================================================
# 3. Vendor Research Agent & Verbatim Quote Verification
# =============================================================================


@pytest.mark.unit
async def test_vendor_research_agent_unconfigured_error() -> None:
    """Vendor research agent returns error when LLM API key is missing."""
    settings = Settings(LLM_API_KEY="")
    agent = VendorResearchAgent(settings=settings)
    res = await agent.research(documents=["Some doc"])
    assert res.success is False
    assert "LLM provider is not configured" in (res.error or "")


@pytest.mark.unit
async def test_vendor_research_agent_quote_verification() -> None:
    """Agent verifies verbatim quotes against source documents and captures unverified claims."""
    source_document = (
        "Datadog provides enterprise cloud monitoring. SOC2 Type II report completed on October 2026. "
        "Standard enterprise annual pricing starts at $48,000 USD."
    )

    mock_llm = AsyncMock()
    mock_llm.call_structured.return_value = VendorResearchExtraction(
        summary="Datadog is a cloud monitoring vendor.",
        claims=[
            # Valid verbatim quote from source
            GroundedClaim(
                fact="Datadog has SOC2 Type II certification",
                quote="SOC2 Type II report completed on October 2026",
            ),
            # Hallucinated quote NOT in source document
            GroundedClaim(
                fact="Datadog offers 99.999% uptime SLA",
                quote="guaranteed 99.999% uptime with immediate payout",
            ),
        ],
        products_services=["cloud monitoring"],
        security_certifications=["SOC2 Type II"],
        pricing_terms="annual pricing starts at $48,000 USD",
        not_covered=["FedRAMP High status"],
    )

    settings = Settings(LLM_API_KEY="test_key")
    agent = VendorResearchAgent(llm_client=mock_llm, settings=settings)

    report = await agent.research(documents=[source_document], vendor_name="Datadog")

    assert report.success is True
    # Exactly 1 verified claim
    assert len(report.claims) == 1
    assert report.claims[0].verified is True
    assert report.claims[0].fact == "Datadog has SOC2 Type II certification"

    # Exactly 1 unverified/hallucinated claim captured
    assert len(report.unverified_claims) == 1
    assert report.unverified_claims[0].verified is False
    assert "99.999%" in report.unverified_claims[0].quote


# =============================================================================
# 4. Spend Per Currency Aggregation (USD and EUR are NEVER summed together)
# =============================================================================


@pytest.mark.unit
async def test_spend_per_currency_never_summed_across_currencies(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """USD and EUR spend are aggregated strictly per currency and NEVER added together."""
    tenant = f"tenant_spend_{uuid.uuid4().hex[:8]}"

    # Create vendor
    v = VendorModel(
        tenant_id=tenant,
        canonical_key=f"acme_corp_{uuid.uuid4().hex[:6]}",
        name="Acme Multi-Currency Corp",
        normalized_base="acme",
        status="active",
    )
    db_session.add(v)
    await db_session.flush()

    # Spend in USD ($48,000.00)
    tx_usd = SpendTransactionModel(
        tenant_id=tenant,
        vendor_id=v.id,
        transaction_date=date(2026, 11, 1),
        amount=Decimal("48000.0000"),
        currency="USD",
        category="software",
        source_system="erp_mock",
        source_id=f"tx_usd_{uuid.uuid4().hex[:6]}",
    )
    # Spend in EUR (€12,000.00)
    tx_eur = SpendTransactionModel(
        tenant_id=tenant,
        vendor_id=v.id,
        transaction_date=date(2026, 11, 2),
        amount=Decimal("12000.0000"),
        currency="EUR",
        category="software",
        source_system="erp_mock",
        source_id=f"tx_eur_{uuid.uuid4().hex[:6]}",
    )
    db_session.add_all([tx_usd, tx_eur])
    await db_session.commit()

    headers = {
        "Authorization": f"Bearer finance@omnea.ai:{tenant}",
        "x-tenant-id": tenant,
    }

    # 1. Check Spend Dashboard
    dash_resp = await async_client.get("/api/v1/finance/spend-dashboard", headers=headers)
    assert dash_resp.status_code == 200
    dash_data = dash_resp.json()

    spend_by_curr = dash_data["spend_by_currency"]
    # Currencies must be kept separate
    assert spend_by_curr["USD"] == 48000.0
    assert spend_by_curr["EUR"] == 12000.0
    # Must NEVER have a single combined total of 60,000
    assert "total" not in spend_by_curr

    totals = {c["currency"]: c["total"] for c in dash_data["currency_totals"]}
    assert totals["USD"] == 48000.0
    assert totals["EUR"] == 12000.0

    # 2. Check Vendors list
    vendors_resp = await async_client.get("/api/v1/vendors", headers=headers)
    assert vendors_resp.status_code == 200
    vendors = vendors_resp.json()
    acme_v = next(item for item in vendors if item["id"] == str(v.id))
    assert acme_v["spend_by_currency"] == {"EUR": 12000.0, "USD": 48000.0}


# =============================================================================
# 5. Vendor Merge and Unmerge Restoring Spend Separation
# =============================================================================


@pytest.mark.unit
async def test_vendor_merge_and_unmerge_restores_spend_separation(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Merging a vendor aggregates spend into target; unmerging restores original separation."""
    tenant = f"tenant_merge_{uuid.uuid4().hex[:8]}"

    v_target = VendorModel(
        tenant_id=tenant,
        canonical_key=f"target_corp_{uuid.uuid4().hex[:6]}",
        name="Target Corp",
        normalized_base="target_corp",
        status="active",
    )
    v_source = VendorModel(
        tenant_id=tenant,
        canonical_key=f"source_corp_{uuid.uuid4().hex[:6]}",
        name="Source Corp",
        normalized_base="source_corp",
        status="active",
    )
    db_session.add_all([v_target, v_source])
    await db_session.flush()

    # Target spend: 20k USD
    tx_target = SpendTransactionModel(
        tenant_id=tenant,
        vendor_id=v_target.id,
        transaction_date=date(2026, 10, 1),
        amount=Decimal("20000.0000"),
        currency="USD",
        category="software",
        source_system="erp_mock",
        source_id=f"tx_t_{uuid.uuid4().hex[:6]}",
    )
    # Source spend: 30k USD
    tx_source = SpendTransactionModel(
        tenant_id=tenant,
        vendor_id=v_source.id,
        transaction_date=date(2026, 10, 2),
        amount=Decimal("30000.0000"),
        currency="USD",
        category="software",
        source_system="erp_mock",
        source_id=f"tx_s_{uuid.uuid4().hex[:6]}",
    )
    db_session.add_all([tx_target, tx_source])
    await db_session.commit()

    headers = {
        "Authorization": f"Bearer finance@omnea.ai:{tenant}",
        "x-tenant-id": tenant,
        "X-CSRF-Token": "test-csrf-token",
    }

    # Verify initial spend separation
    res_init = await async_client.get("/api/v1/vendors", headers=headers)
    v_list = res_init.json()
    init_target = next(item for item in v_list if item["id"] == str(v_target.id))
    init_source = next(item for item in v_list if item["id"] == str(v_source.id))
    assert init_target["spend_by_currency"]["USD"] == 20000.0
    assert init_source["spend_by_currency"]["USD"] == 30000.0

    # 1. Merge source into target
    merge_resp = await async_client.post(
        "/api/v1/vendors/merge",
        headers=headers,
        json={"source_id": str(v_source.id), "target_id": str(v_target.id)},
    )
    assert merge_resp.status_code == 200
    assert merge_resp.json()["status"] == "merged"

    # Verify target absorbed source spend (20k + 30k = 50k)
    res_merged = await async_client.get("/api/v1/vendors", headers=headers)
    v_list_merged = res_merged.json()
    m_target = next(item for item in v_list_merged if item["id"] == str(v_target.id))
    m_source = next(item for item in v_list_merged if item["id"] == str(v_source.id))
    assert m_target["spend_by_currency"]["USD"] == 50000.0
    assert m_source["status"] == "merged"
    assert m_source["merged_into_id"] == str(v_target.id)
    assert m_source["spend_by_currency"] == {}

    # 2. Reversibly unmerge source vendor
    unmerge_resp = await async_client.post(
        f"/api/v1/vendors/{v_source.id}/unmerge",
        headers=headers,
    )
    assert unmerge_resp.status_code == 200
    assert unmerge_resp.json()["status"] == "unmerged"
    assert unmerge_resp.json()["restored_status"] == "active"

    # Verify spend separation is RESTORED
    res_unmerged = await async_client.get("/api/v1/vendors", headers=headers)
    v_list_unmerged = res_unmerged.json()
    u_target = next(item for item in v_list_unmerged if item["id"] == str(v_target.id))
    u_source = next(item for item in v_list_unmerged if item["id"] == str(v_source.id))
    assert u_target["spend_by_currency"]["USD"] == 20000.0
    assert u_source["spend_by_currency"]["USD"] == 30000.0
    assert u_source["status"] == "active"
    assert u_source["merged_into_id"] is None


# =============================================================================
# 6. Legal Contracts & Flagged Clauses API
# =============================================================================


@pytest.mark.unit
async def test_legal_contracts_and_flagged_clauses_lifecycle(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Ingest contract with terms, list with legal filters, and add flagged clauses."""
    tenant = f"tenant_legal_{uuid.uuid4().hex[:8]}"

    # Create vendor
    v = VendorModel(
        tenant_id=tenant,
        canonical_key=f"saas_cloud_{uuid.uuid4().hex[:6]}",
        name="SaaS Cloud Inc",
        normalized_base="saas_cloud",
        status="active",
    )
    db_session.add(v)
    await db_session.commit()

    legal_headers = {
        "Authorization": f"Bearer legal@omnea.ai:{tenant}",
        "x-tenant-id": tenant,
        "X-CSRF-Token": "test-csrf-token",
    }

    # 1. Ingest contract with initial flagged clause and terms
    ingest_payload = {
        "vendor_id": str(v.id),
        "title": "Master Services Agreement 2026",
        "start_date": "2026-01-01",
        "end_date": "2026-12-31",
        "total_value": 75000.00,
        "currency": "USD",
        "auto_renew": True,
        "msa_status": "executed",
        "dpa_status": "pending",
        "renewal_terms": "60 days written notice required",
        "flagged_clauses": [
            {
                "quote": "Vendor liability shall be unlimited for service interruption.",
                "page": 14,
                "clause_type": "unlimited_liability",
                "risk_level": "critical",
                "recommendation": "Cap liability at 12 months fees paid.",
            }
        ],
    }

    ingest_resp = await async_client.post(
        "/api/v1/legal/contracts",
        headers=legal_headers,
        json=ingest_payload,
    )
    assert ingest_resp.status_code == 200
    contract_data = ingest_resp.json()
    contract_id = contract_data["id"]
    assert contract_data["vendor_name"] == "SaaS Cloud Inc"
    assert contract_data["msa_status"] == "executed"
    assert len(contract_data["flagged_clauses"]) == 1
    assert contract_data["flagged_clauses"][0]["risk_level"] == "critical"

    # 2. Filter contracts list by msa_status
    list_resp = await async_client.get(
        "/api/v1/legal/contracts?msa_status=executed",
        headers=legal_headers,
    )
    assert list_resp.status_code == 200
    items = list_resp.json()
    assert any(c["id"] == contract_id for c in items)

    # 3. Add a second flagged clause to the contract
    flag_resp = await async_client.post(
        f"/api/v1/legal/contracts/{contract_id}/flag-clause",
        headers=legal_headers,
        json={
            "quote": "Contract automatically renews for 2-year periods unless cancelled 90 days prior.",
            "page": 22,
            "clause_type": "auto_renewal",
            "risk_level": "high",
            "recommendation": "Negotiate 1-year annual renewal with 30-day notice.",
        },
    )
    assert flag_resp.status_code == 200
    clauses = flag_resp.json()
    assert len(clauses) == 2
    clause_types = [c["clause_type"] for c in clauses]
    assert "unlimited_liability" in clause_types
    assert "auto_renewal" in clause_types

    # 4. Fetch detail view and verify audit trail entry
    detail_resp = await async_client.get(
        f"/api/v1/legal/contracts/{contract_id}",
        headers=legal_headers,
    )
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert len(detail["flagged_clauses"]) == 2
    assert detail["renewal_notice_terms"] == "60 days written notice required"
    assert len(detail["audit_details"]) >= 2  # contract_ingested + clause_flagged


# =============================================================================
# 7. Department Budgets & Reservations API
# =============================================================================


@pytest.mark.unit
async def test_department_budgets_and_headroom_calculation(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Department budgets calculate headroom subtracting committed and active reservations."""
    tenant = f"tenant_bgt_{uuid.uuid4().hex[:8]}"

    finance_headers = {
        "Authorization": f"Bearer finance@omnea.ai:{tenant}",
        "x-tenant-id": tenant,
        "X-CSRF-Token": "test-csrf-token",
    }

    # 1. Create department budget ($100,000 total, $20,000 committed)
    b_resp = await async_client.post(
        "/api/v1/finance/budgets",
        headers=finance_headers,
        json={
            "department": "Engineering",
            "period": "FY2026",
            "currency": "USD",
            "amount": 100000.00,
            "committed": 20000.00,
        },
    )
    assert b_resp.status_code == 200
    data = b_resp.json()
    assert data["amount"] == 100000.0
    assert data["committed"] == 20000.0
    assert data["headroom"] == 80000.0

    # 2. Add an active budget reservation for $15,000
    req_id = uuid.uuid4()
    # Seed minimal request row so FK holds
    await db_session.execute(
        text(
            "INSERT INTO requests (id, tenant_id, requester_id, status, payload) "
            "VALUES (:id, :t, 'requester@omnea.ai', 'draft', '{}'::jsonb)"
        ),
        {"id": req_id, "t": tenant},
    )
    await db_session.execute(
        text(
            "INSERT INTO budget_reservations ("
            "id, tenant_id, request_id, round, department, currency, period, amount, status, idempotency_key"
            ") VALUES ("
            ":id, :t, :req_id, 1, 'Engineering', 'USD', 'FY2026', 15000.0000, 'active', :idem"
            ")"
        ),
        {
            "id": uuid.uuid4(),
            "t": tenant,
            "req_id": req_id,
            "idem": f"bgt_res_{uuid.uuid4().hex[:8]}",
        },
    )
    await db_session.commit()

    # 3. List budgets and assert headroom = 100,000 - 20,000 - 15,000 = 65,000
    list_b_resp = await async_client.get(
        "/api/v1/finance/budgets?department=Engineering",
        headers=finance_headers,
    )
    assert list_b_resp.status_code == 200
    b_list = list_b_resp.json()
    eng_budget = next(b for b in b_list if b["department"] == "Engineering")
    assert eng_budget["amount"] == 100000.0
    assert eng_budget["committed"] == 20000.0
    assert eng_budget["active_reserved"] == 15000.0
    assert eng_budget["headroom"] == 65000.0


# =============================================================================
# 8. Vendor Graph Projection API
# =============================================================================


@pytest.mark.unit
async def test_vendor_graph_projection(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Graph projection produces vendor, contract nodes and subsidiary, contract edges."""
    tenant = f"tenant_graph_{uuid.uuid4().hex[:8]}"

    parent = VendorModel(
        tenant_id=tenant,
        canonical_key=f"parent_v_{uuid.uuid4().hex[:6]}",
        name="Alphabet Inc",
        normalized_base="alphabet",
        status="active",
    )
    db_session.add(parent)
    await db_session.flush()

    child = VendorModel(
        tenant_id=tenant,
        canonical_key=f"child_v_{uuid.uuid4().hex[:6]}",
        name="Google LLC",
        normalized_base="google",
        status="active",
        parent_vendor_id=parent.id,
    )
    db_session.add(child)
    await db_session.flush()

    contract = ContractModel(
        tenant_id=tenant,
        vendor_id=child.id,
        title="Google Cloud Contract",
        status="active",
        start_date=date(2026, 1, 1),
        total_value=Decimal("120000.00"),
        currency="USD",
    )
    db_session.add(contract)
    await db_session.commit()

    headers = {
        "Authorization": f"Bearer finance@omnea.ai:{tenant}",
        "x-tenant-id": tenant,
    }

    graph_resp = await async_client.get("/api/v1/vendors/graph", headers=headers)
    assert graph_resp.status_code == 200
    graph = graph_resp.json()

    # Find nodes
    node_ids = {n["id"] for n in graph["nodes"]}
    assert str(parent.id) in node_ids
    assert str(child.id) in node_ids
    assert str(contract.id) in node_ids

    # Find edges
    edge_types = {(e["source"], e["target"], e["type"]) for e in graph["edges"]}
    assert (str(child.id), str(parent.id), "SUBSIDIARY_OF") in edge_types
    assert (str(contract.id), str(child.id), "CONTRACT_WITH") in edge_types


# =============================================================================
# 9. Policy Confirm & Version Immutability API
# =============================================================================


@pytest.mark.unit
async def test_policy_confirm_immutability_and_anti_weakening_api(
    async_client: AsyncClient,
) -> None:
    """Activating a policy creates an immutable version and deactivates prior versions."""
    tenant = f"tenant_pol_{uuid.uuid4().hex[:8]}"

    finance_headers = {
        "Authorization": f"Bearer finance@omnea.ai:{tenant}",
        "x-tenant-id": tenant,
        "X-CSRF-Token": "test-csrf-token",
    }

    # 1. Confirm version v1.0
    rules_v1 = {
        "agent_autonomous_cap": 50000.0,
        "allow_fast_track": False,
        "fast_track_consecutive_cap": 3,
        "vp_spend_threshold": {"USD": 50000.0, "EUR": 45000.0},
        "over_budget_action": "needs_human",
    }
    resp1 = await async_client.post(
        "/api/v1/finance/policies/confirm",
        headers=finance_headers,
        json={"version": "v1.0", "rules": rules_v1},
    )
    assert resp1.status_code == 200
    p1 = resp1.json()
    assert p1["version"] == "v1.0"
    assert p1["is_active"] is True
    assert p1["content_hash"]

    # 2. Attempt to weaken policy without admin override (raise autonomous cap from 50k to 80k) -> 400
    weak_rules = dict(rules_v1)
    weak_rules["agent_autonomous_cap"] = 80000.0
    resp_weak = await async_client.post(
        "/api/v1/finance/policies/confirm",
        headers=finance_headers,
        json={"version": "v1.1", "rules": weak_rules, "admin_override": False},
    )
    assert resp_weak.status_code == 400
    assert "agent_autonomous_cap cannot be increased" in resp_weak.json()["detail"]

    # 3. Confirm with admin_override=True -> succeeds as v2.0
    resp2 = await async_client.post(
        "/api/v1/finance/policies/confirm",
        headers=finance_headers,
        json={"version": "v2.0", "rules": weak_rules, "admin_override": True},
    )
    assert resp2.status_code == 200
    p2 = resp2.json()
    assert p2["version"] == "v2.0"
    assert p2["is_active"] is True

    # 4. List policies and verify v2.0 is active and v1.0 is deactivated
    list_resp = await async_client.get("/api/v1/finance/policies", headers=finance_headers)
    assert list_resp.status_code == 200
    all_policies = list_resp.json()
    assert len(all_policies) == 2
    active_pol = next(p for p in all_policies if p["is_active"] is True)
    inactive_pol = next(p for p in all_policies if p["is_active"] is False)
    assert active_pol["version"] == "v2.0"
    assert inactive_pol["version"] == "v1.0"


# =============================================================================
# 10. Role Authorization Negative Tests (RBAC Guard)
# =============================================================================


@pytest.mark.unit
async def test_unauthorized_user_forbidden_on_protected_stage4_routes(
    async_client: AsyncClient,
) -> None:
    """Unauthorized user lacking manage permissions is rejected with 403 Forbidden."""
    tenant = "test_tenant"

    unauth_headers = {
        "Authorization": f"Bearer unauthorized@omnea.ai:{tenant}",
        "x-tenant-id": tenant,
        "X-CSRF-Token": "test-csrf-token",
    }

    # 1. Budget creation requires finance:manage
    resp_bgt = await async_client.post(
        "/api/v1/finance/budgets",
        headers=unauth_headers,
        json={"department": "IT", "period": "FY2026", "currency": "USD", "amount": 10000.0},
    )
    assert resp_bgt.status_code == 403

    # 2. Contract creation requires legal:manage
    resp_contract = await async_client.post(
        "/api/v1/legal/contracts",
        headers=unauth_headers,
        json={
            "vendor_id": str(uuid.uuid4()),
            "title": "Unauthorized contract",
            "start_date": "2026-01-01",
            "total_value": 1000.0,
            "currency": "USD",
        },
    )
    assert resp_contract.status_code == 403

    # 3. Vendor merge requires vendors:manage
    resp_merge = await async_client.post(
        "/api/v1/vendors/merge",
        headers=unauth_headers,
        json={"source_id": str(uuid.uuid4()), "target_id": str(uuid.uuid4())},
    )
    assert resp_merge.status_code == 403
