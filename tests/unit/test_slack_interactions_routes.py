"""Unit tests for Slack Interactivity Routes: signature security, replay protection, deduplication, and in-place message updates."""

import hashlib
import hmac
import json
import time
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from api.main import app
from procure_core.config import Settings
from workers.approval.activities import _MOCK_SLACK_MESSAGES


def make_slack_headers(body: bytes, secret: str, timestamp_offset: int = 0) -> dict[str, str]:
    """Generate Slack HMAC-SHA256 headers with optional timestamp offset."""
    ts = str(int(time.time()) + timestamp_offset)
    sig_basestring = f"v0:{ts}:{body.decode('utf-8')}".encode()
    sig = "v0=" + hmac.new(secret.encode("utf-8"), sig_basestring, hashlib.sha256).hexdigest()
    return {
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": sig,
    }


@pytest.mark.asyncio
async def test_slack_interactive_bad_signature_returns_401(test_settings: Settings) -> None:
    """1. Invalid HMAC signature is rejected with HTTP 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        body = b'{"type": "block_actions"}'
        headers = {
            "X-Slack-Request-Timestamp": str(int(time.time())),
            "X-Slack-Signature": "v0=invalid_signature_hex_digest_9999",
            "Content-Type": "application/json",
        }
        res = await client.post("/api/v1/slack/interactive", content=body, headers=headers)
        assert res.status_code == 401, (
            f"Expected 401 for bad signature, got {res.status_code}: {res.text}"
        )


@pytest.mark.asyncio
async def test_slack_interactive_replayed_timestamp_returns_401(test_settings: Settings) -> None:
    """2. Valid signature but timestamp >300s old is rejected as replay attack with HTTP 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        body = b'{"type": "block_actions"}'
        # Timestamp 305 seconds in the past
        headers = make_slack_headers(
            body, test_settings.SLACK_SIGNING_SECRET, timestamp_offset=-305
        )
        headers["Content-Type"] = "application/json"

        res = await client.post("/api/v1/slack/interactive", content=body, headers=headers)
        assert res.status_code == 401, (
            f"Expected 401 for timestamp >300s, got {res.status_code}: {res.text}"
        )


@pytest.mark.asyncio
async def test_slack_interactive_tampered_body_returns_401(test_settings: Settings) -> None:
    """3. Signature generated for payload A but payload B sent (tampered body) is rejected with HTTP 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        original_body = b'{"type": "block_actions", "action": "reject"}'
        tampered_body = b'{"type": "block_actions", "action": "approve"}'
        headers = make_slack_headers(original_body, test_settings.SLACK_SIGNING_SECRET)
        headers["Content-Type"] = "application/json"

        res = await client.post("/api/v1/slack/interactive", content=tampered_body, headers=headers)
        assert res.status_code == 401, (
            f"Expected 401 for tampered body, got {res.status_code}: {res.text}"
        )


@pytest.mark.asyncio
async def test_slack_interactive_duplicate_button_click_already_decided(
    test_settings: Settings,
    session_factory,
) -> None:
    """4. Duplicate button click on already decided request returns 'already_decided' with 0 state change."""
    req_id = uuid.uuid4()
    tenant_id = f"test_tenant_{uuid.uuid4().hex[:6]}"

    # Pre-seed request in Postgres starting with 'draft' as enforced by trigger, then update to 'approved'
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                text(
                    "INSERT INTO requests (id, tenant_id, requester_id, status) "
                    "VALUES (:id, :tenant_id, 'alice@omnea.internal', 'draft')"
                ),
                {"id": req_id, "tenant_id": tenant_id},
            )
            await session.execute(
                text(
                    "UPDATE requests SET status = 'in_review' WHERE id = :id AND tenant_id = :tenant_id"
                ),
                {"id": req_id, "tenant_id": tenant_id},
            )
            await session.execute(
                text(
                    "UPDATE requests SET status = 'approved' WHERE id = :id AND tenant_id = :tenant_id"
                ),
                {"id": req_id, "tenant_id": tenant_id},
            )
            await session.execute(
                text(
                    "INSERT INTO approval_steps (id, tenant_id, request_id, step_name, reviewer_type, round, status) "
                    "VALUES (:id, :tenant_id, :req_id, 'finance_review', 'human', 1, 'approved')"
                ),
                {"id": uuid.uuid4(), "tenant_id": tenant_id, "req_id": req_id},
            )

    payload = {
        "type": "block_actions",
        "user": {"id": "U_FINANCE_LEAD", "username": "bob"},
        "actions": [
            {
                "action_id": "approve",
                "value": json.dumps(
                    {
                        "tenant_id": tenant_id,
                        "request_id": str(req_id),
                        "requester_id": "alice@omnea.internal",
                        "assigned_approver_id": "U_FINANCE_LEAD",
                        "step_name": "finance_review",
                        "round": 1,
                    }
                ),
            }
        ],
    }

    body = json.dumps(payload).encode("utf-8")
    headers = make_slack_headers(body, test_settings.SLACK_SIGNING_SECRET)
    headers["Content-Type"] = "application/json"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # First call on already decided
        res1 = await client.post("/api/v1/slack/interactive", content=body, headers=headers)
        assert res1.status_code == 200
        data1 = res1.json()
        assert data1["status"] == "already_decided"
        assert "already been decided" in data1["text"]

        # Second call (duplicate click)
        res2 = await client.post("/api/v1/slack/interactive", content=body, headers=headers)
        assert res2.status_code == 200
        data2 = res2.json()
        assert data2["status"] == "already_decided"

    # Assert 0 state changes in DB (no audit records, no step status changes)
    async with session_factory() as session:
        audit_count = await session.scalar(
            text(
                "SELECT count(*) FROM approval_audit_records WHERE tenant_id = :t AND request_id = :id"
            ),
            {"t": tenant_id, "id": req_id},
        )
        assert audit_count == 0, (
            f"Expected 0 audit records on already-decided duplicate click, got {audit_count}"
        )

        req_status = await session.scalar(
            text("SELECT status FROM requests WHERE tenant_id = :t AND id = :id"),
            {"t": tenant_id, "id": req_id},
        )
        assert req_status == "approved"


@pytest.mark.asyncio
async def test_slack_interactive_message_update_chat_update_instead_of_repost(
    test_settings: Settings,
    session_factory,
) -> None:
    """5. Interactive decision updates existing Slack message in place via chat.update rather than posting a new message."""
    req_id = uuid.uuid4()
    tenant_id = f"test_tenant_{uuid.uuid4().hex[:6]}"
    slack_ts = "1710000000.123456"

    # Pre-seed request starting in draft, then update to in_review
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                text(
                    "INSERT INTO requests (id, tenant_id, requester_id, status) "
                    "VALUES (:id, :tenant_id, 'alice@omnea.internal', 'draft')"
                ),
                {"id": req_id, "tenant_id": tenant_id},
            )
            await session.execute(
                text(
                    "UPDATE requests SET status = 'in_review' WHERE id = :id AND tenant_id = :tenant_id"
                ),
                {"id": req_id, "tenant_id": tenant_id},
            )
            await session.execute(
                text(
                    "INSERT INTO approval_steps (id, tenant_id, request_id, step_name, reviewer_type, round, status, slack_message_ts) "
                    "VALUES (:id, :tenant_id, :req_id, 'finance_review', 'human', 1, 'awaiting_human', :slack_ts)"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant_id": tenant_id,
                    "req_id": req_id,
                    "slack_ts": slack_ts,
                },
            )

    # Pre-seed mock message store with original Slack notification
    msg_key = f"{tenant_id}:{req_id}:finance_review:msg_001"
    _MOCK_SLACK_MESSAGES[msg_key] = {
        "ts": slack_ts,
        "text": "Initial pending approval request for Alice",
        "updated": False,
    }

    payload = {
        "type": "block_actions",
        "user": {"id": "U_AUTHORIZED_VP", "username": "vp_approver"},
        "container": {"message_ts": slack_ts},
        "actions": [
            {
                "action_id": "approve",
                "value": json.dumps(
                    {
                        "tenant_id": tenant_id,
                        "request_id": str(req_id),
                        "requester_id": "alice@omnea.internal",
                        "assigned_approver_id": "U_AUTHORIZED_VP",
                        "step_name": "finance_review",
                        "round": 1,
                        "comment": "Looks good, approved",
                    }
                ),
            }
        ],
    }

    body = json.dumps(payload).encode("utf-8")
    headers = make_slack_headers(body, test_settings.SLACK_SIGNING_SECRET)
    headers["Content-Type"] = "application/json"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post("/api/v1/slack/interactive", content=body, headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "accepted"
        # Verifies response tells Slack client to update message in place
        assert data["replace_original"] is True
        assert "*APPROVE*" in data["text"]

    # Verify underlying Slack message was updated in-place (chat.update) rather than appending new message
    stored = _MOCK_SLACK_MESSAGES[msg_key]
    assert stored["updated"] is True, (
        "Expected mock Slack message to be marked updated=True via chat.update"
    )
    assert "APPROVE" in stored["text"]
    assert stored["ts"] == slack_ts, "Slack timestamp must remain identical for in-place update"
