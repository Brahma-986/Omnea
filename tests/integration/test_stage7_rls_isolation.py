"""Integration tests for Stage 7 Cross-Tenant RLS Isolation and Dual-Wall Defense."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from api.main import create_app
from procure_core.db import get_async_session_factory
from procure_core.developer.api_keys import generate_api_key
from procure_core.models.auth import OrganizationModel


@pytest.fixture
async def setup_dual_tenants():
    factory = get_async_session_factory()
    tenant_a = f"tenant_a_{uuid.uuid4().hex[:8]}"
    tenant_b = f"tenant_b_{uuid.uuid4().hex[:8]}"

    async with factory() as session:
        # Create organizations
        session.add(OrganizationModel(id=tenant_a, name="Alpha Corp"))
        session.add(OrganizationModel(id=tenant_b, name="Beta Global"))

        # Generate API key for Tenant A
        raw_key_a, model_a = generate_api_key(
            tenant_id=tenant_a,
            name="Alpha Production Key",
            scopes=["*"],
        )
        session.add(model_a)

        # Generate API key for Tenant B
        raw_key_b, model_b = generate_api_key(
            tenant_id=tenant_b,
            name="Beta Production Key",
            scopes=["*"],
        )
        session.add(model_b)

        await session.commit()

    return {
        "tenant_a": tenant_a,
        "key_a": raw_key_a,
        "tenant_b": tenant_b,
        "key_b": raw_key_b,
    }


@pytest.mark.asyncio
async def test_two_api_keys_from_two_tenants_cannot_see_each_others_data(
    setup_dual_tenants,
) -> None:
    """Verify that two API keys from separate tenants have strict bidirectional isolation."""
    data = setup_dual_tenants
    key_a = data["key_a"]
    key_b = data["key_b"]

    app = create_app()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Tenant A creates two requests
        res_a1 = await client.post(
            "/api/v1/requests",
            headers={"Authorization": f"Bearer {key_a}"},
            json={
                "vendor_name": "Alpha Cloud Services",
                "estimated_spend": "15000.00",
                "currency": "USD",
                "department": "Engineering",
                "description": "Alpha confidential infra",
            },
        )
        assert res_a1.status_code == 201
        req_a1_id = res_a1.json()["id"]

        res_a2 = await client.post(
            "/api/v1/requests",
            headers={"Authorization": f"Bearer {key_a}"},
            json={
                "vendor_name": "Alpha Security Tooling",
                "estimated_spend": "30000.00",
                "currency": "USD",
                "department": "Security",
            },
        )
        assert res_a2.status_code == 201
        req_a2_id = res_a2.json()["id"]

        # 2. Tenant B creates two requests
        res_b1 = await client.post(
            "/api/v1/requests",
            headers={"Authorization": f"Bearer {key_b}"},
            json={
                "vendor_name": "Beta Database Cluster",
                "estimated_spend": "55000.00",
                "currency": "USD",
                "department": "Data",
                "description": "Beta confidential data pipeline",
            },
        )
        assert res_b1.status_code == 201
        req_b1_id = res_b1.json()["id"]

        res_b2 = await client.post(
            "/api/v1/requests",
            headers={"Authorization": f"Bearer {key_b}"},
            json={
                "vendor_name": "Beta Analytics Platform",
                "estimated_spend": "80000.00",
                "currency": "USD",
                "department": "Marketing",
            },
        )
        assert res_b2.status_code == 201
        req_b2_id = res_b2.json()["id"]

        # 3. Tenant A lists requests: MUST only see Alpha requests
        list_a = await client.get(
            "/api/v1/requests",
            headers={"Authorization": f"Bearer {key_a}"},
        )
        assert list_a.status_code == 200
        items_a = list_a.json().get("items", [])
        ids_a = {item["id"] for item in items_a}

        assert req_a1_id in ids_a
        assert req_a2_id in ids_a
        assert req_b1_id not in ids_a, "CRITICAL LEAK: Tenant A can see Tenant B request!"
        assert req_b2_id not in ids_a, "CRITICAL LEAK: Tenant A can see Tenant B request!"

        # 4. Tenant B lists requests: MUST only see Beta requests
        list_b = await client.get(
            "/api/v1/requests",
            headers={"Authorization": f"Bearer {key_b}"},
        )
        assert list_b.status_code == 200
        items_b = list_b.json().get("items", [])
        ids_b = {item["id"] for item in items_b}

        assert req_b1_id in ids_b
        assert req_b2_id in ids_b
        assert req_a1_id not in ids_b, "CRITICAL LEAK: Tenant B can see Tenant A request!"
        assert req_a2_id not in ids_b, "CRITICAL LEAK: Tenant B can see Tenant A request!"

        # 5. Cross-tenant item detail lookup MUST return 404 Not Found
        cross_b_res = await client.get(
            f"/api/v1/requests/{req_b1_id}",
            headers={"Authorization": f"Bearer {key_a}"},
        )
        assert cross_b_res.status_code == 404, "Tenant A was able to read Tenant B request by ID!"

        cross_a_res = await client.get(
            f"/api/v1/requests/{req_a1_id}",
            headers={"Authorization": f"Bearer {key_b}"},
        )
        assert cross_a_res.status_code == 404, "Tenant B was able to read Tenant A request by ID!"


@pytest.mark.asyncio
async def test_api_key_spoofing_tenant_header_is_rejected(setup_dual_tenants) -> None:
    """Verify an API key cannot spoof another tenant by passing X-Tenant-ID header."""
    data = setup_dual_tenants
    key_a = data["key_a"]
    tenant_b = data["tenant_b"]

    app = create_app()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Key A attempts to access Tenant B using X-Tenant-ID header
        res = await client.get(
            "/api/v1/requests",
            headers={
                "Authorization": f"Bearer {key_a}",
                "X-Tenant-ID": tenant_b,
            },
        )
        assert res.status_code == 403
        assert "not match" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_postgresql_kernel_level_rls_filters_database_rows(setup_dual_tenants) -> None:
    """Verify that PostgreSQL Row Level Security (RLS) strictly filters queries at the SQL engine level."""
    data = setup_dual_tenants
    tenant_a = data["tenant_a"]
    tenant_b = data["tenant_b"]
    factory = get_async_session_factory()

    async with factory() as session:
        # Switch to application role to evaluate non-admin RLS enforcement
        await session.execute(text("SET ROLE procure_workflow;"))
        try:
            # 1. Query under Tenant A context
            await session.execute(
                text("SELECT set_config('app.tenant_id', :t, true);"),
                {"t": tenant_a},
            )
            keys_a = (await session.execute(text("SELECT id, tenant_id FROM api_keys;"))).fetchall()
            for k in keys_a:
                assert k[1] == tenant_a, f"RLS leak: Found tenant {k[1]} under tenant_a session!"

            # 2. Query under Tenant B context
            await session.execute(
                text("SELECT set_config('app.tenant_id', :t, true);"),
                {"t": tenant_b},
            )
            keys_b = (await session.execute(text("SELECT id, tenant_id FROM api_keys;"))).fetchall()
            for k in keys_b:
                assert k[1] == tenant_b, f"RLS leak: Found tenant {k[1]} under tenant_b session!"
        finally:
            await session.execute(text("RESET ROLE;"))
