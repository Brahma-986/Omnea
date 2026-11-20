"""Integration tests for Review API authentication, authorization, and tenant isolation."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from procure_core.models.vendor import EntityReviewTaskModel, VendorModel


@pytest.mark.integration
async def test_review_api_authentication_required(async_client: AsyncClient) -> None:
    """Missing or invalid Authorization header returns 401 Unauthorized."""
    # 1. No Authorization header -> 401
    resp = await async_client.get("/api/v1/reviews/tasks")
    assert resp.status_code == 401
    assert "Missing or invalid Authorization header" in resp.json()["detail"]

    # 2. Invalid Bearer scheme -> 401
    resp2 = await async_client.get(
        "/api/v1/reviews/tasks",
        headers={"Authorization": "Basic dXNlcjpwYXNz"},
    )
    assert resp2.status_code == 401

    # 3. Empty Bearer -> 401
    resp3 = await async_client.get(
        "/api/v1/reviews/tasks",
        headers={"Authorization": "Bearer "},
    )
    assert resp3.status_code == 401


@pytest.mark.integration
async def test_review_api_tenant_authorization_guard(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Attempting to resolve or access a task belonging to another tenant returns 403 Forbidden."""
    tenant_a = f"tenant_a_{uuid.uuid4().hex[:8]}"
    tenant_b = f"tenant_b_{uuid.uuid4().hex[:8]}"

    # Create task belonging to Tenant B
    v_prov = VendorModel(
        tenant_id=tenant_b,
        name="Tenant B Provisional",
        canonical_key=f"prov_{uuid.uuid4().hex[:8]}",
        normalized_base="tb_prov",
        status="pending_review",
    )
    v_cand = VendorModel(
        tenant_id=tenant_b,
        name="Tenant B Canonical",
        canonical_key=f"cand_{uuid.uuid4().hex[:8]}",
        normalized_base="tb_cand",
        status="active",
    )
    db_session.add_all([v_prov, v_cand])
    await db_session.flush()

    task_b = EntityReviewTaskModel(
        tenant_id=tenant_b,
        task_type="duplicate_review",
        provisional_vendor_id=v_prov.id,
        candidate_vendor_id=v_cand.id,
        source_name="Tenant B Raw",
        confidence=Decimal("0.8500"),
        status="pending",
    )
    db_session.add(task_b)
    await db_session.commit()

    # User from Tenant A attempts to resolve Tenant B's task -> 403 Forbidden!
    auth_header_a = {
        "Authorization": f"Bearer reviewer_a@{tenant_a}.ai:{tenant_a}",
        "X-CSRF-Token": "test-csrf",
    }
    resp = await async_client.post(
        f"/api/v1/reviews/tasks/{task_b.id}/resolve",
        headers=auth_header_a,
        json={"action": "reject"},
    )
    assert resp.status_code == 403
    assert "Cannot resolve task belonging to another tenant" in resp.json()["detail"]

    # Header tenant mismatch -> 403 Forbidden
    resp_mismatch = await async_client.get(
        "/api/v1/reviews/tasks",
        headers={
            "Authorization": f"Bearer reviewer_a@{tenant_a}.ai:{tenant_a}",
            "x-tenant-id": tenant_b,
        },
    )
    assert resp_mismatch.status_code == 403
    assert "does not match requested tenant" in resp_mismatch.json()["detail"]


@pytest.mark.integration
async def test_review_api_resolve_populates_resolved_by_from_auth(
    async_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Resolving a review task successfully populates resolved_by from authenticated user."""
    tenant = f"tenant_rev_{uuid.uuid4().hex[:8]}"
    actor_email = f"security_lead_{uuid.uuid4().hex[:4]}@omnea.ai"

    # Create vendors and review task in DB
    v_prov = VendorModel(
        tenant_id=tenant,
        name="Datadog Cloud EU",
        canonical_key=f"prov_{uuid.uuid4().hex[:8]}",
        normalized_base="datadog",
        status="pending_review",
    )
    v_cand = VendorModel(
        tenant_id=tenant,
        name="Datadog, Inc.",
        canonical_key=f"cand_{uuid.uuid4().hex[:8]}",
        normalized_base="datadog",
        status="active",
    )
    db_session.add_all([v_prov, v_cand])
    await db_session.flush()

    task = EntityReviewTaskModel(
        tenant_id=tenant,
        task_type="duplicate_review",
        provisional_vendor_id=v_prov.id,
        candidate_vendor_id=v_cand.id,
        source_name="Datadog Cloud EU",
        confidence=Decimal("0.8900"),
        status="pending",
    )
    db_session.add(task)
    await db_session.commit()

    # Authenticate as security lead and resolve by merge
    headers = {
        "Authorization": f"Bearer {actor_email}:{tenant}",
        "x-tenant-id": tenant,
        "X-CSRF-Token": "test-csrf",
    }

    # 1. List tasks
    list_resp = await async_client.get("/api/v1/reviews/tasks", headers=headers)
    assert list_resp.status_code == 200
    task_items = list_resp.json()
    assert any(t["id"] == str(task.id) for t in task_items)

    # 2. Resolve task via merge
    resolve_resp = await async_client.post(
        f"/api/v1/reviews/tasks/{task.id}/resolve",
        headers=headers,
        json={"action": "merge", "target_vendor_id": str(v_cand.id)},
    )
    assert resolve_resp.status_code == 200
    data = resolve_resp.json()
    assert data["status"] == "approved_merge"
    assert data["resolved_by"] == actor_email
