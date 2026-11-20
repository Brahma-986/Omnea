"""Unit tests for Stage 3: Requests API, Approvals API, Self-Approval Guards, and SLA."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from procure_agents.models import (
    BillingPeriod,
    ExtractedIntentFields,
    IntentClassification,
    SpendParseResult,
)
from procure_core.auth.rbac import RbacEngine
from procure_core.config import get_settings


@pytest.fixture(autouse=True)
def setup_stage3_test_roles() -> Iterator[None]:
    """Configure RBAC roles for Stage 3 unit tests."""
    RbacEngine.clear_fallback_roles()

    RbacEngine.assign_fallback_role("test_tenant", "requester@omnea.ai", "requester")
    RbacEngine.assign_fallback_role("test_tenant", "approver@omnea.ai", "approver")
    RbacEngine.assign_fallback_role("test_tenant", "finance@omnea.ai", "finance_admin")
    RbacEngine.assign_fallback_role("test_tenant", "security@omnea.ai", "security_reviewer")
    RbacEngine.assign_fallback_role("test_tenant", "legal@omnea.ai", "legal_reviewer")
    RbacEngine.assign_fallback_role("test_tenant", "admin@omnea.ai", "tenant_admin")
    RbacEngine.assign_fallback_role("test_tenant", "delegate_user@omnea.ai", "approver")

    yield

    RbacEngine.clear_fallback_roles()


async def _seed_test_request(
    session: AsyncSession,
    tenant_id: str,
    requester_id: str,
    vendor_name: str = "Datadog Inc",
    amount: Decimal = Decimal("15000.00"),
    status: str = "awaiting_human",
) -> tuple[uuid.UUID, uuid.UUID]:
    """Helper to seed a request, budget reservation, and an approval step."""
    req_id = uuid.uuid4()
    step_id = uuid.uuid4()

    # Step through state machine: draft -> in_review -> awaiting_human
    await session.execute(
        text(
            "INSERT INTO requests (id, tenant_id, requester_id, status, payload) "
            "VALUES (:id, :t, :req, 'draft', :payload)"
        ),
        {
            "id": req_id,
            "t": tenant_id,
            "req": requester_id,
            "payload": json.dumps(
                {
                    "vendor_name": vendor_name,
                    "estimated_spend": float(amount),
                    "currency": "USD",
                    "department": "Engineering",
                    "comments": [],
                }
            ),
        },
    )
    if status != "draft":
        await session.execute(
            text("UPDATE requests SET status = 'in_review' WHERE id = :id"),
            {"id": req_id},
        )
        if status != "in_review":
            await session.execute(
                text("UPDATE requests SET status = 'awaiting_human' WHERE id = :id"),
                {"id": req_id},
            )
            if status not in ("draft", "in_review", "awaiting_human"):
                await session.execute(
                    text("UPDATE requests SET status = :st WHERE id = :id"),
                    {"id": req_id, "st": status},
                )

    # Ensure department budget exists and has headroom for reservation/commit
    await session.execute(
        text(
            "INSERT INTO department_budgets (id, tenant_id, department, currency, period, amount, committed) "
            "VALUES (:b_id, :t, 'Engineering', 'USD', 'FY2026', 100000.0000, 0.0000) "
            "ON CONFLICT (tenant_id, department, currency, period) "
            "DO UPDATE SET committed = 0.0000, amount = 100000.0000"
        ),
        {"b_id": uuid.uuid4(), "t": tenant_id},
    )

    # Seed active budget reservation
    await session.execute(
        text(
            "INSERT INTO budget_reservations ("
            "id, tenant_id, request_id, round, department, currency, period, amount, status, idempotency_key"
            ") VALUES ("
            ":id, :t, :req_id, 1, 'Engineering', 'USD', 'FY2026', :amt, 'active', :idem"
            ") ON CONFLICT (tenant_id, idempotency_key) DO NOTHING"
        ),
        {
            "id": uuid.uuid4(),
            "t": tenant_id,
            "req_id": req_id,
            "amt": amount,
            "idem": f"bgt_seed_{req_id}",
        },
    )

    # Seed approval step
    await session.execute(
        text(
            "INSERT INTO approval_steps ("
            "id, tenant_id, request_id, step_name, round, reviewer_type, status, assignee, agent_verdict, metadata"
            ") VALUES ("
            ":id, :t, :req_id, 'finance', 1, 'agent', 'awaiting_human', 'approver@omnea.ai', :agent_v, '{}'::jsonb"
            ")"
        ),
        {
            "id": step_id,
            "t": tenant_id,
            "req_id": req_id,
            "agent_v": json.dumps(
                {
                    "reviewer_type": "finance",
                    "verdict": "needs_human",
                    "reasons": ["Manual review required by policy"],
                    "cited_evidence": [
                        {"citation_id": "cite_spend", "status": "verified", "quote": "$15,000"}
                    ],
                }
            ),
        },
    )

    await session.commit()
    return req_id, step_id


# =============================================================================
# 1. Self-Approval Refusal Tests
# =============================================================================


@pytest.mark.unit
async def test_self_approval_refusal_returns_403_and_audits(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Self-approval attempt returns HTTP 403 Forbidden and appends immutable audit events."""
    requester = "approver@omnea.ai"
    tenant_id = "test_tenant"

    req_id, step_id = await _seed_test_request(db_session, tenant_id, requester_id=requester)

    headers = {
        "Authorization": f"Bearer {requester}:{tenant_id}",
        "X-CSRF-Token": "test-csrf-token",
    }
    payload = {
        "action": "approve",
        "reason": "Approving my own purchase request",
        "idempotency_key": f"idem_self_{uuid.uuid4()}",
    }

    # Execute request
    resp = await async_client.post(
        f"/api/v1/approvals/{step_id}/decide",
        headers=headers,
        json=payload,
    )

    # Assert HTTP 403 Forbidden
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Self-approval is strictly forbidden"

    # Verify audit_log entry
    audit_row = (
        (
            await db_session.execute(
                text(
                    "SELECT event_type, actor, entity_id FROM audit_log "
                    "WHERE tenant_id = :t AND entity_id = :id AND event_type = 'auth.self_approval_refused'"
                ),
                {"t": tenant_id, "id": str(req_id)},
            )
        )
        .mappings()
        .first()
    )

    assert audit_row is not None
    assert audit_row["actor"] == requester
    assert audit_row["event_type"] == "auth.self_approval_refused"

    # Verify approval_audit_records entry
    appr_audit_row = (
        (
            await db_session.execute(
                text(
                    "SELECT action, actor_id, verdict FROM approval_audit_records "
                    "WHERE tenant_id = :t AND request_id = :req_id AND action = 'auth.self_approval_refused'"
                ),
                {"t": tenant_id, "req_id": req_id},
            )
        )
        .mappings()
        .first()
    )

    assert appr_audit_row is not None
    assert appr_audit_row["actor_id"] == requester
    assert appr_audit_row["verdict"] == "reject"


# =============================================================================
# 2. Stale-Click / Duplicate Idempotency Guard Tests
# =============================================================================


@pytest.mark.unit
async def test_stale_click_idempotency_on_terminal_step(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """If step is already terminal (approved/rejected), return existing verdict idempotently without re-processing."""
    requester = "requester@omnea.ai"
    approver = "approver@omnea.ai"
    tenant_id = "test_tenant"

    req_id, step_id = await _seed_test_request(db_session, tenant_id, requester_id=requester)

    # Set step to approved directly
    await db_session.execute(
        text(
            "UPDATE approval_steps SET status = 'approved', "
            'human_verdict = \'{"action": "approve", "reason": "Initial approval"}\'::jsonb '
            "WHERE id = :id"
        ),
        {"id": step_id},
    )
    await db_session.commit()

    headers = {
        "Authorization": f"Bearer {approver}:{tenant_id}",
        "X-CSRF-Token": "test-csrf-token",
    }
    payload = {
        "action": "approve",
        "reason": "Duplicate second click on approved step",
        "idempotency_key": f"idem_stale_{uuid.uuid4()}",
    }

    resp = await async_client.post(
        f"/api/v1/approvals/{step_id}/decide",
        headers=headers,
        json=payload,
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "idempotent_success"
    assert data["verdict"] == "approved"
    assert "stale-click guard" in data["message"]


@pytest.mark.unit
async def test_stale_click_idempotency_on_duplicate_key(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Duplicate decision submission with same idempotency key succeeds idempotently."""
    requester = "requester@omnea.ai"
    approver = "approver@omnea.ai"
    tenant_id = "test_tenant"

    req_id, step_id = await _seed_test_request(db_session, tenant_id, requester_id=requester)

    headers = {
        "Authorization": f"Bearer {approver}:{tenant_id}",
        "X-CSRF-Token": "test-csrf-token",
    }
    idem_key = f"idem_key_dup_{uuid.uuid4()}"
    payload = {
        "action": "approve",
        "reason": "Legitimate business software purchase",
        "idempotency_key": idem_key,
    }

    # 1. First decision succeeds
    resp1 = await async_client.post(
        f"/api/v1/approvals/{step_id}/decide",
        headers=headers,
        json=payload,
    )
    assert resp1.status_code == 200
    assert resp1.json()["status"] == "decided"

    # 2. Second duplicate click with same idempotency key returns idempotent success
    resp2 = await async_client.post(
        f"/api/v1/approvals/{step_id}/decide",
        headers=headers,
        json=payload,
    )
    assert resp2.status_code == 200
    data = resp2.json()
    assert data["status"] == "idempotent_success"


# =============================================================================
# 3. Reason Validation Tests
# =============================================================================


@pytest.mark.unit
async def test_decision_requires_minimum_reason_length(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Decide endpoint validates reason min_length=3 and rejects short reasons with 422."""
    requester = "requester@omnea.ai"
    approver = "approver@omnea.ai"
    tenant_id = "test_tenant"

    _, step_id = await _seed_test_request(db_session, tenant_id, requester_id=requester)

    headers = {
        "Authorization": f"Bearer {approver}:{tenant_id}",
        "X-CSRF-Token": "test-csrf-token",
    }

    # Reason too short ("ok" has length 2 < 3)
    resp_short = await async_client.post(
        f"/api/v1/approvals/{step_id}/decide",
        headers=headers,
        json={
            "action": "approve",
            "reason": "ok",
            "idempotency_key": "k1",
        },
    )
    assert resp_short.status_code == 422

    # Reason empty string
    resp_empty = await async_client.post(
        f"/api/v1/approvals/{step_id}/decide",
        headers=headers,
        json={
            "action": "approve",
            "reason": "",
            "idempotency_key": "k2",
        },
    )
    assert resp_empty.status_code == 422


# =============================================================================
# 4. LLM Status & Connectivity Guard Tests
# =============================================================================


@pytest.mark.unit
async def test_llm_status_endpoint(async_client: AsyncClient) -> None:
    """GET /api/v1/requests/config/llm-status accurately reflects LLM API key status."""
    headers = {"Authorization": "Bearer approver@omnea.ai:test_tenant"}

    settings = get_settings()
    with patch.object(settings, "LLM_API_KEY", ""):
        resp_unconnected = await async_client.get(
            "/api/v1/requests/config/llm-status", headers=headers
        )
        assert resp_unconnected.status_code == 200
        assert resp_unconnected.json()["connected"] is False

    with patch.object(settings, "LLM_API_KEY", "real-or-test-key-123"):
        resp_connected = await async_client.get(
            "/api/v1/requests/config/llm-status", headers=headers
        )
        assert resp_connected.status_code == 200
        assert resp_connected.json()["connected"] is True


@pytest.mark.unit
async def test_intake_chat_unconfigured_llm_returns_503(async_client: AsyncClient) -> None:
    """POST /api/v1/requests/intake/chat fails with 503 when LLM provider is not configured."""
    headers = {
        "Authorization": "Bearer requester@omnea.ai:test_tenant",
        "X-CSRF-Token": "test-csrf-token",
    }
    payload = {
        "message": "I need Datadog APM for $15,000 USD",
    }

    settings = get_settings()
    with patch.object(settings, "LLM_API_KEY", ""):
        resp = await async_client.post(
            "/api/v1/requests/intake/chat", headers=headers, json=payload
        )
        assert resp.status_code == 503
        assert "LLM provider is not configured" in resp.json()["detail"]


# =============================================================================
# 5. Conversational Intake: Missing Fields & Clarification
# =============================================================================


@pytest.mark.unit
async def test_intake_chat_missing_fields_generates_clarifying_question(
    async_client: AsyncClient,
) -> None:
    """Intake chat generates single clarifying question and saves intent when fields are missing."""
    headers = {
        "Authorization": "Bearer requester@omnea.ai:test_tenant",
        "X-CSRF-Token": "test-csrf-token",
    }

    settings = get_settings()
    mock_extracted = ExtractedIntentFields(
        vendor_name="Figma",
        raw_amount_str=None,
        raw_currency_str=None,
        justification="Design team licenses",
        evidence={"vendor_name": "Figma"},
    )
    mock_spend = SpendParseResult(
        amount=None,
        currency=None,
        billing_period=BillingPeriod.ANNUAL,
    )
    mock_class = IntentClassification(
        classification="new_purchase",
        confidence=0.95,
        reasoning="Purchase request",
    )

    with (
        patch.object(settings, "LLM_API_KEY", "test-key"),
        patch(
            "api.routes.requests.IntakeExtractor.extract", new_callable=AsyncMock
        ) as mock_extract,
        patch(
            "api.routes.requests.IntentClassifier.classify", new_callable=AsyncMock
        ) as mock_classify,
    ):
        mock_extract.return_value = (
            mock_extracted,
            mock_spend,
            ["estimated_spend", "currency"],
            {"vendor_name": "Figma"},
        )
        mock_classify.return_value = mock_class

        resp = await async_client.post(
            "/api/v1/requests/intake/chat",
            headers=headers,
            json={"message": "We need Figma licenses for the UX team."},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "clarification_needed"
        assert "estimated_spend" in data["missing_fields"]
        assert "currency" in data["missing_fields"]
        assert data["clarifying_question"] is not None
        assert "Figma" in data["clarifying_question"]


@pytest.mark.unit
async def test_intake_chat_complete_creates_request_and_approval_steps(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Intake chat with complete fields creates request, active budget reservation, and 3 reviewer steps."""
    headers = {
        "Authorization": "Bearer requester@omnea.ai:test_tenant",
        "X-CSRF-Token": "test-csrf-token",
    }

    settings = get_settings()
    mock_extracted = ExtractedIntentFields(
        vendor_name="Figma",
        raw_amount_str="$12,000",
        raw_currency_str="USD",
        raw_billing_period_str="annual",
        justification="Design tooling",
        department="Engineering",
        evidence={
            "vendor_name": "Figma",
            "raw_amount_str": "$12,000",
            "raw_currency_str": "USD",
        },
    )
    mock_spend = SpendParseResult(
        amount=Decimal("12000.00"),
        currency="USD",
        billing_period=BillingPeriod.ANNUAL,
        annualized_spend=Decimal("12000.00"),
    )
    mock_class = IntentClassification(
        classification="new_purchase",
        confidence=0.98,
        reasoning="New purchase request",
    )

    with (
        patch.object(settings, "LLM_API_KEY", "test-key"),
        patch(
            "api.routes.requests.IntakeExtractor.extract", new_callable=AsyncMock
        ) as mock_extract,
        patch(
            "api.routes.requests.IntentClassifier.classify", new_callable=AsyncMock
        ) as mock_classify,
    ):
        mock_extract.return_value = (
            mock_extracted,
            mock_spend,
            [],
            {"vendor_name": "Figma", "raw_amount_str": "$12,000"},
        )
        mock_classify.return_value = mock_class

        resp = await async_client.post(
            "/api/v1/requests/intake/chat",
            headers=headers,
            json={"message": "Please procure Figma for $12,000 USD annually"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "created"
        assert "request_id" in data
        assert len(data["verdicts"]) == 3  # finance, security, legal

        req_uuid = uuid.UUID(data["request_id"])

        # Verify active budget reservation
        res_row = (
            (
                await db_session.execute(
                    text(
                        "SELECT amount, status FROM budget_reservations "
                        "WHERE tenant_id = 'test_tenant' AND request_id = :req_id"
                    ),
                    {"req_id": req_uuid},
                )
            )
            .mappings()
            .first()
        )
        assert res_row is not None
        assert res_row["status"] == "active"
        assert res_row["amount"] == Decimal("12000.00")

        # Verify 3 approval steps created
        steps = (
            (
                await db_session.execute(
                    text(
                        "SELECT step_name, status, agent_verdict FROM approval_steps "
                        "WHERE tenant_id = 'test_tenant' AND request_id = :req_id"
                    ),
                    {"req_id": req_uuid},
                )
            )
            .mappings()
            .all()
        )
        assert len(steps) == 3
        step_names = {s["step_name"] for s in steps}
        assert step_names == {"finance", "security", "legal"}


# =============================================================================
# 6. Approvals Pending & SLA Status Tests
# =============================================================================


@pytest.mark.unit
async def test_get_pending_approvals_with_sla_computation(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Pending approvals endpoint lists assigned steps and computes correct SLA status."""
    requester = "requester@omnea.ai"
    approver = "approver@omnea.ai"
    tenant_id = "test_tenant"

    req_id, step_id = await _seed_test_request(db_session, tenant_id, requester_id=requester)

    # Update step created_at to trigger SLA reminder (>24h ago)
    past_26h = datetime.now(UTC) - timedelta(hours=26)
    await db_session.execute(
        text("UPDATE approval_steps SET created_at = :dt WHERE id = :id"),
        {"dt": past_26h, "id": step_id},
    )
    await db_session.commit()

    headers = {"Authorization": f"Bearer {approver}:{tenant_id}"}
    resp = await async_client.get("/api/v1/approvals/pending", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] >= 1

    matched = next((s for s in data["pending_approvals"] if s["step_id"] == str(step_id)), None)
    assert matched is not None
    assert matched["sla_status"] == "reminder_due"


# =============================================================================
# 7. Approvals Delegation & OOF Management Tests
# =============================================================================


@pytest.mark.unit
async def test_approvals_delegation_flow(
    async_client: AsyncClient,
) -> None:
    """Configuring OOF delegate, checking delegation, and clearing delegation."""
    approver = "approver@omnea.ai"
    delegate = "delegate_user@omnea.ai"
    tenant_id = "test_tenant"
    headers = {
        "Authorization": f"Bearer {approver}:{tenant_id}",
        "X-CSRF-Token": "test-csrf-token",
    }

    now = datetime.now(UTC)
    v_from = now
    v_until = now + timedelta(days=7)

    # 1. Reject self-delegation
    resp_self = await async_client.post(
        "/api/v1/approvals/delegation",
        headers=headers,
        json={
            "role": "approver",
            "delegate_id": approver,
            "delegate_valid_from": v_from.isoformat(),
            "delegate_valid_until": v_until.isoformat(),
        },
    )
    assert resp_self.status_code == 400
    assert "Cannot delegate approvals to yourself" in resp_self.json()["detail"]

    # 2. Reject inverted dates
    resp_inv = await async_client.post(
        "/api/v1/approvals/delegation",
        headers=headers,
        json={
            "role": "approver",
            "delegate_id": delegate,
            "delegate_valid_from": v_until.isoformat(),
            "delegate_valid_until": v_from.isoformat(),
        },
    )
    assert resp_inv.status_code == 422

    # 3. Valid delegation configuration
    resp_set = await async_client.post(
        "/api/v1/approvals/delegation",
        headers=headers,
        json={
            "role": "approver",
            "delegate_id": delegate,
            "delegate_valid_from": v_from.isoformat(),
            "delegate_valid_until": v_until.isoformat(),
        },
    )
    assert resp_set.status_code == 200
    assert resp_set.json()["delegate_id"] == delegate

    # 4. Check GET /api/v1/approvals/delegation
    resp_get = await async_client.get("/api/v1/approvals/delegation", headers=headers)
    assert resp_get.status_code == 200
    dels = resp_get.json()["delegations"]
    assert any(d["delegate_id"] == delegate for d in dels)

    # 5. Clear delegation
    resp_clear = await async_client.post(
        "/api/v1/approvals/delegation",
        headers=headers,
        json={"role": "approver", "delegate_id": None},
    )
    assert resp_clear.status_code == 200
    assert resp_clear.json()["delegate_id"] is None


# =============================================================================
# 8. Request Comments & Detail Verification
# =============================================================================


@pytest.mark.unit
async def test_request_comments_and_full_detail(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Adding thread comment and reading full request detail."""
    requester = "requester@omnea.ai"
    tenant_id = "test_tenant"

    req_id, _ = await _seed_test_request(db_session, tenant_id, requester_id=requester)

    headers = {
        "Authorization": f"Bearer {requester}:{tenant_id}",
        "X-CSRF-Token": "test-csrf-token",
    }

    # Add comment
    comment_resp = await async_client.post(
        f"/api/v1/requests/{req_id}/comments",
        headers=headers,
        json={"comment": "Please expedite this request as vendor pricing expires Friday."},
    )
    assert comment_resp.status_code == 200
    assert (
        comment_resp.json()["comment"]
        == "Please expedite this request as vendor pricing expires Friday."
    )

    # Fetch full detail
    detail_resp = await async_client.get(f"/api/v1/requests/{req_id}", headers=headers)
    assert detail_resp.status_code == 200
    data = detail_resp.json()
    assert data["id"] == str(req_id)
    assert len(data["timeline"]) >= 1
    assert len(data["comments"]) == 1
    assert (
        data["comments"][0]["comment"]
        == "Please expedite this request as vendor pricing expires Friday."
    )
    assert "budget_reservations" in data
    assert "policy_version" in data
    assert "policy_content_hash" in data
