"""Unit tests for Stage 7 API keys: scopes, rate limits, revocation, expiry, idempotency, and error shape."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from api.main import create_app
from procure_core.db import get_async_session_factory
from procure_core.developer.api_keys import (
    generate_api_key,
    get_api_key_rate_limiter,
    hash_api_key,
)
from procure_core.models.auth import OrganizationModel


@pytest.fixture
async def setup_test_tenant_and_keys():
    factory = get_async_session_factory()
    tenant_id = f"tenant_{uuid.uuid4().hex[:8]}"

    async with factory() as session:
        # Create organization
        org = OrganizationModel(id=tenant_id, name=f"Test Org {tenant_id}")
        session.add(org)

        # 1. Full access key
        raw_full_key, full_key_model = generate_api_key(
            tenant_id=tenant_id,
            name="Full Access Key",
            scopes=["*"],
            rate_limit_per_minute=100,
        )
        session.add(full_key_model)

        # 2. Read-only requests key
        raw_readonly_key, readonly_key_model = generate_api_key(
            tenant_id=tenant_id,
            name="Read-Only Key",
            scopes=["requests:read"],
            rate_limit_per_minute=5,
        )
        session.add(readonly_key_model)

        # 3. Revoked key
        raw_revoked_key, revoked_key_model = generate_api_key(
            tenant_id=tenant_id,
            name="Revoked Key",
            scopes=["requests:read", "requests:create"],
            rate_limit_per_minute=60,
        )
        revoked_key_model.revoked_at = datetime.now(UTC)
        session.add(revoked_key_model)

        # 4. Expired key
        raw_expired_key, expired_key_model = generate_api_key(
            tenant_id=tenant_id,
            name="Expired Key",
            scopes=["requests:read"],
            rate_limit_per_minute=60,
        )
        expired_key_model.expires_at = datetime.now(UTC) - timedelta(hours=1)
        session.add(expired_key_model)

        await session.commit()

    return {
        "tenant_id": tenant_id,
        "full_key": raw_full_key,
        "full_key_id": full_key_model.id,
        "readonly_key": raw_readonly_key,
        "revoked_key": raw_revoked_key,
        "expired_key": raw_expired_key,
    }


@pytest.mark.asyncio
async def test_api_key_generation_and_hashing(setup_test_tenant_and_keys) -> None:
    """Verify API keys have secure high entropy, prefix, and only SHA-256 hash is persisted."""
    data = setup_test_tenant_and_keys
    tenant_id = data["tenant_id"]

    raw_key, model = generate_api_key(
        tenant_id=tenant_id,
        name="Security Test Key",
        scopes=["requests:read", "vendors:read"],
        rate_limit_per_minute=60,
    )

    assert raw_key.startswith("omnea_live_")
    assert model.key_prefix.startswith("omnea_live_")
    assert model.key_hash == hash_api_key(raw_key)
    assert raw_key not in model.key_hash  # Raw secret must not equal hash
    assert len(model.key_hash) == 64  # SHA-256 hex digest length


@pytest.mark.asyncio
async def test_revoked_and_expired_keys_return_401(setup_test_tenant_and_keys) -> None:
    """Verify revoked and expired keys are strictly rejected with 401 Unauthorized."""
    data = setup_test_tenant_and_keys
    app = create_app()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Revoked key test
        rev_res = await client.get(
            "/api/v1/requests",
            headers={"Authorization": f"Bearer {data['revoked_key']}"},
        )
        assert rev_res.status_code == 401
        assert "revoked" in rev_res.json()["detail"].lower()
        assert rev_res.json()["error"]["code"] == "UNAUTHORIZED"

        # Expired key test
        exp_res = await client.get(
            "/api/v1/requests",
            headers={"Authorization": f"Bearer {data['expired_key']}"},
        )
        assert exp_res.status_code == 401
        assert "expired" in exp_res.json()["detail"].lower()
        assert exp_res.json()["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_scope_enforcement_blocks_unauthorized_actions(setup_test_tenant_and_keys) -> None:
    """Verify a read-only key cannot submit a procurement request (403 Forbidden)."""
    data = setup_test_tenant_and_keys
    app = create_app()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Read-only key can read requests
        read_res = await client.get(
            "/api/v1/requests",
            headers={"Authorization": f"Bearer {data['readonly_key']}"},
        )
        assert read_res.status_code == 200

        # Read-only key attempting to POST a request must be rejected with 403 Forbidden
        post_res = await client.post(
            "/api/v1/requests",
            headers={"Authorization": f"Bearer {data['readonly_key']}"},
            json={
                "vendor_name": "Datadog, Inc.",
                "estimated_spend": "50000.00",
                "currency": "USD",
                "department": "Engineering",
            },
        )
        assert post_res.status_code == 403
        assert "scope" in post_res.json()["detail"].lower()
        assert post_res.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_rate_limiting_enforcement(setup_test_tenant_and_keys) -> None:
    """Verify rate limiter triggers 429 Too Many Requests when ceiling is exceeded."""
    data = setup_test_tenant_and_keys
    app = create_app()
    transport = ASGITransport(app=app)
    limiter = get_api_key_rate_limiter()
    limiter.reset()

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Readonly key has limit of 5 req/min
        headers = {"Authorization": f"Bearer {data['readonly_key']}"}

        # Make 5 allowed requests
        for _ in range(5):
            res = await client.get("/api/v1/requests", headers=headers)
            assert res.status_code == 200

        # 6th request must trigger 429 Too Many Requests
        rate_res = await client.get("/api/v1/requests", headers=headers)
        assert rate_res.status_code == 429
        assert "Retry-After" in rate_res.headers
        assert rate_res.json()["error"]["code"] == "TOO_MANY_REQUESTS"


@pytest.mark.asyncio
async def test_idempotency_key_prevents_duplicate_writes(setup_test_tenant_and_keys) -> None:
    """Verify Idempotency-Key on mutating writes returns cached response and suppresses duplicate creation."""
    data = setup_test_tenant_and_keys
    app = create_app()
    transport = ASGITransport(app=app)

    idem_key = f"idem_test_{uuid.uuid4().hex}"
    payload = {
        "vendor_name": "Snowflake, Inc.",
        "estimated_spend": "75000.00",
        "currency": "USD",
        "department": "Engineering",
        "description": "Enterprise Data Warehouse Compute",
    }
    headers = {
        "Authorization": f"Bearer {data['full_key']}",
        "Idempotency-Key": idem_key,
        "X-Tenant-ID": data["tenant_id"],
    }

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # First write
        res1 = await client.post("/api/v1/requests", headers=headers, json=payload)
        assert res1.status_code == 201
        body1 = res1.json()

        # Replayed write with identical Idempotency-Key
        res2 = await client.post("/api/v1/requests", headers=headers, json=payload)
        assert res2.status_code == 201
        assert res2.headers.get("X-Cache") == "HIT-IDEMPOTENT"
        body2 = res2.json()

        assert body1["id"] == body2["id"]
        assert body1["vendor_name"] == body2["vendor_name"]


@pytest.mark.asyncio
async def test_consistent_error_shape_contract(setup_test_tenant_and_keys) -> None:
    """Verify consistent error shape { error: { code, message, status_code, details } } across 4xx responses."""
    app = create_app()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 401 error
        res_401 = await client.get(
            "/api/v1/requests", headers={"Authorization": "Bearer omnea_live_invalid_token_123"}
        )
        assert res_401.status_code == 401
        data_401 = res_401.json()
        assert "error" in data_401
        assert data_401["error"]["code"] == "UNAUTHORIZED"
        assert "message" in data_401["error"]
        assert data_401["error"]["status_code"] == 401

        # 404 error
        data = setup_test_tenant_and_keys
        res_404 = await client.get(
            f"/api/v1/requests/{uuid.uuid4()}",
            headers={"Authorization": f"Bearer {data['full_key']}"},
        )
        assert res_404.status_code == 404
        data_404 = res_404.json()
        assert "error" in data_404
        assert data_404["error"]["code"] == "NOT_FOUND"
        assert data_404["error"]["status_code"] == 404
