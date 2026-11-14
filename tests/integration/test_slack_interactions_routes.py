"""Integration tests for Slack interactive webhook routes with real signature validation, DB authorization, and 403 auditing."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from decimal import Decimal
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from api.main import create_app
from procure_core.config import get_settings
from procure_core.db import get_async_session_factory


def _generate_slack_signature(body: bytes, timestamp: int, secret: str) -> str:
    """Generate Slack v0 HMAC-SHA256 signature."""
    sig_basestring = f"v0:{timestamp}:{body.decode('utf-8')}".encode("utf-8")
    hex_hash = hmac.new(secret.encode("utf-8"), sig_basestring, hashlib.sha256).hexdigest()
    return f"v0={hex_hash}"


async def _seed_request_with_status(session, req_id: uuid.UUID, tenant_id: str, requester_id: str, target_status: str) -> None:
    """Seed request following the strict state machine transition path from draft."""
    await session.execute(
        text("INSERT INTO requests (id, tenant_id, requester_id, status) VALUES (:id, :t, :req, 'draft')"),
        {"id": req_id, "t": tenant_id, "req": requester_id},
    )
    if target_status == "draft":
        return
    await session.execute(
        text("UPDATE requests SET status = 'in_review' WHERE id = :id"),
        {"id": req_id},
    )
    if target_status == "in_review":
        return
    await session.execute(
        text("UPDATE requests SET status = 'awaiting_human' WHERE id = :id"),
        {"id": req_id},
    )
    if target_status == "awaiting_human":
        return
    await session.execute(
        text("UPDATE requests SET status = :status WHERE id = :id"),
        {"id": req_id, "status": target_status},
    )


@pytest.mark.asyncio
async def test_slack_route_invalid_signature_returns_401() -> None:
    """Proves: Requests with invalid HMAC-SHA256 signature are rejected with 401."""
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ts = int(time.time())
        body = json.dumps({"type": "block_actions"}).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "X-Slack-Request-Timestamp": str(ts),
            "X-Slack-Signature": "v0=badbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadbadb",
        }
        resp = await client.post("/api/v1/slack/interactive", content=body, headers=headers)
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Invalid Slack signature"
        print("\n[SLACK ROUTE SIGNATURE EVIDENCE]")
        print("  Invalid signature rejected with HTTP 401: Invalid Slack signature")


@pytest.mark.asyncio
async def test_slack_route_expired_timestamp_returns_401() -> None:
    """Proves: Requests older than 5 minutes (replay attack defense) are rejected with 401."""
    app = create_app()
    settings = get_settings()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        expired_ts = int(time.time()) - 400  # > 300s old
        body = json.dumps({"type": "block_actions"}).encode("utf-8")
        sig = _generate_slack_signature(body, expired_ts, settings.SLACK_SIGNING_SECRET)
        headers = {
            "Content-Type": "application/json",
            "X-Slack-Request-Timestamp": str(expired_ts),
            "X-Slack-Signature": sig,
        }
        resp = await client.post("/api/v1/slack/interactive", content=body, headers=headers)
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Invalid Slack signature"
        print("\n[SLACK ROUTE REPLAY PROTECTION EVIDENCE]")
        print(f"  Expired timestamp ({expired_ts}) rejected with HTTP 401")


@pytest.mark.asyncio
async def test_slack_route_unauthorized_self_approval_returns_403_and_audits_refusal() -> None:
    """Proves: Self-approval attempt returns HTTP 403 and writes authorization_refused audit record with idempotency key."""
    app = create_app()
    settings = get_settings()
    session_factory = get_async_session_factory()

    tenant_id = f"t_slack_{uuid.uuid4().hex[:6]}"
    req_id = uuid.uuid4()
    requester_id = "alice@omnea.internal"

    # Seed parent request in DB following state machine
    async with session_factory() as session:
        async with session.begin():
            await _seed_request_with_status(session, req_id, tenant_id, requester_id, "awaiting_human")

    payload = {
        "user": {"id": requester_id},
        "container": {"message_ts": "1731518000.123456"},
        "actions": [
            {
                "action_id": "approve",
                "value": json.dumps({
                    "tenant_id": tenant_id,
                    "request_id": str(req_id),
                    "requester_id": requester_id,
                    "assigned_approver_id": "bob_vp@omnea.internal",
                    "step_name": "finance_review",
                    "round": 1,
                }),
            }
        ],
    }

    body = json.dumps(payload).encode("utf-8")
    ts = int(time.time())
    sig = _generate_slack_signature(body, ts, settings.SLACK_SIGNING_SECRET)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/slack/interactive",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Slack-Request-Timestamp": str(ts),
                "X-Slack-Signature": sig,
            },
        )
        assert resp.status_code == 403
        data = resp.json()
        assert data["status"] == "refused"
        assert f"User '{requester_id}' is not authorized" in data["message"]

    # Verify audit record in PostgreSQL
    async with session_factory() as session:
        audit_row = (await session.execute(
            text(
                "SELECT actor_id, action, idempotency_key, reasons "
                "FROM approval_audit_records "
                "WHERE tenant_id = :t AND request_id = :id AND action = 'authorization_refused'"
            ),
            {"t": tenant_id, "id": req_id},
        )).mappings().one()

        assert audit_row["actor_id"] == requester_id
        assert audit_row["action"] == "authorization_refused"
        from procure_agents.approval.fallback import format_auth_refused_idempotency_key
        expected_key = format_auth_refused_idempotency_key(
            tenant_id, str(req_id), requester_id, "finance_review", "approve", 1
        )
        assert audit_row["idempotency_key"] == expected_key
        assert "Unauthorized approver attempted sign-off" in str(audit_row["reasons"])
        print("\n[SLACK ROUTE 403 AUDITING EVIDENCE]")
        print(f"  Actor: {audit_row['actor_id']}")
        print(f"  Action: {audit_row['action']}")
        print(f"  Idempotency Key: {audit_row['idempotency_key']}")


@pytest.mark.asyncio
async def test_slack_route_authorized_approver_returns_200() -> None:
    """Proves: Authorized approver sign-off succeeds through Slack endpoint with HTTP 200."""
    app = create_app()
    settings = get_settings()
    session_factory = get_async_session_factory()

    tenant_id = f"t_slack_{uuid.uuid4().hex[:6]}"
    req_id = uuid.uuid4()
    requester_id = "alice@omnea.internal"
    approver_id = "bob_vp@omnea.internal"

    async with session_factory() as session:
        async with session.begin():
            await _seed_request_with_status(session, req_id, tenant_id, requester_id, "awaiting_human")

    payload = {
        "user": {"id": approver_id},
        "container": {"message_ts": "1731518000.654321"},
        "actions": [
            {
                "action_id": "approve",
                "value": json.dumps({
                    "tenant_id": tenant_id,
                    "request_id": str(req_id),
                    "requester_id": requester_id,
                    "assigned_approver_id": approver_id,
                    "step_name": "finance_review",
                    "round": 1,
                    "comment": "Budget verified and approved",
                }),
            }
        ],
    }

    body = json.dumps(payload).encode("utf-8")
    ts = int(time.time())
    sig = _generate_slack_signature(body, ts, settings.SLACK_SIGNING_SECRET)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/slack/interactive",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Slack-Request-Timestamp": str(ts),
                "X-Slack-Signature": sig,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "accepted"
        assert f"Action 'approve' successfully recorded" in data["message"]
        print("\n[SLACK ROUTE AUTHORIZED 200 EVIDENCE]")
        print(f"  Status: {data['status']}, Message: {data['message']}")


@pytest.mark.asyncio
async def test_slack_route_already_decided_request_returns_200_no_op() -> None:
    """Proves: Duplicate clicks on already terminal requests return HTTP 200 with already_decided status."""
    app = create_app()
    settings = get_settings()
    session_factory = get_async_session_factory()

    tenant_id = f"t_slack_{uuid.uuid4().hex[:6]}"
    req_id = uuid.uuid4()
    requester_id = "alice@omnea.internal"
    approver_id = "bob_vp@omnea.internal"

    # Seed terminal request following state machine
    async with session_factory() as session:
        async with session.begin():
            await _seed_request_with_status(session, req_id, tenant_id, requester_id, "approved")

    payload = {
        "user": {"id": approver_id},
        "container": {"message_ts": "1731518000.999999"},
        "actions": [
            {
                "action_id": "approve",
                "value": json.dumps({
                    "tenant_id": tenant_id,
                    "request_id": str(req_id),
                    "requester_id": requester_id,
                    "assigned_approver_id": approver_id,
                    "step_name": "finance_review",
                    "round": 1,
                }),
            }
        ],
    }

    body = json.dumps(payload).encode("utf-8")
    ts = int(time.time())
    sig = _generate_slack_signature(body, ts, settings.SLACK_SIGNING_SECRET)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/slack/interactive",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Slack-Request-Timestamp": str(ts),
                "X-Slack-Signature": sig,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "already_decided"
        assert "is already decided (status=approved)" in data["message"]
        print("\n[SLACK ROUTE ALREADY DECIDED IDEMPOTENCY EVIDENCE]")
        print(f"  Status: {data['status']}, Message: {data['message']}")
