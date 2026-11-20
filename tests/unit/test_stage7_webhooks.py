"""Unit tests for Stage 7 outbound webhooks: HMAC signing, replay protection, retries, and delivery logging."""

from __future__ import annotations

import json
import time
import uuid

import httpx
import pytest

from procure_core.db import get_async_session_factory
from procure_core.developer.webhooks import (
    WebhookDispatcher,
    sign_webhook_payload,
    verify_webhook_signature,
)
from procure_core.models.auth import OrganizationModel
from procure_core.models.developer import (
    WebhookDeliveryModel,
    WebhookSubscriptionModel,
)


@pytest.fixture
async def setup_webhook_test_data():
    factory = get_async_session_factory()
    tenant_id = f"tenant_{uuid.uuid4().hex[:8]}"
    secret = "whsec_test_secret_for_webhook_verification_987654"

    async with factory() as session:
        org = OrganizationModel(id=tenant_id, name=f"Webhook Test Org {tenant_id}")
        session.add(org)

        sub = WebhookSubscriptionModel(
            tenant_id=tenant_id,
            url="http://mock-webhook-receiver/webhook",
            secret=secret,
            description="Mock Receiver",
            events=["request.created", "request.approved", "vendor.flagged"],
            is_active=True,
        )
        session.add(sub)
        await session.commit()

    return {
        "tenant_id": tenant_id,
        "subscription_id": sub.id,
        "secret": secret,
        "url": sub.url,
    }


def test_hmac_signing_and_signature_verification() -> None:
    """Verify HMAC-SHA256 signing and cryptographic signature verification."""
    secret = "whsec_my_super_secret_webhook_key_12345"
    payload = json.dumps({"event": "request.created", "id": "req_123"}).encode("utf-8")
    now = int(time.time())

    # 1. Sign
    sig_header = sign_webhook_payload(secret, now, payload)
    assert sig_header.startswith(f"t={now},v1=")

    # 2. Verify valid
    is_valid = verify_webhook_signature(
        secret=secret,
        payload_bytes=payload,
        signature_header=sig_header,
        tolerance=300,
        now=now,
    )
    assert is_valid is True

    # 3. Tampered payload fails
    tampered_payload = json.dumps({"event": "request.created", "id": "req_123_hacked"}).encode(
        "utf-8"
    )
    assert verify_webhook_signature(secret, tampered_payload, sig_header, now=now) is False

    # 4. Tampered secret fails
    assert verify_webhook_signature("whsec_wrong_secret", payload, sig_header, now=now) is False


def test_replay_attack_prevention_rejects_expired_timestamps() -> None:
    """Verify replay protection rejects signatures older than tolerance window (300s)."""
    secret = "whsec_replay_test_key"
    payload = b'{"status":"approved"}'
    now = int(time.time())

    # 1. Timestamp from 6 minutes ago (360s > 300s)
    old_timestamp = now - 360
    old_sig = sign_webhook_payload(secret, old_timestamp, payload)

    assert (
        verify_webhook_signature(
            secret=secret,
            payload_bytes=payload,
            signature_header=old_sig,
            tolerance=300,
            now=now,
        )
        is False
    )

    # 2. Timestamp within tolerance (60s ago <= 300s)
    recent_timestamp = now - 60
    recent_sig = sign_webhook_payload(secret, recent_timestamp, payload)
    assert (
        verify_webhook_signature(
            secret=secret,
            payload_bytes=payload,
            signature_header=recent_sig,
            tolerance=300,
            now=now,
        )
        is True
    )


@pytest.mark.asyncio
async def test_webhook_dispatcher_and_delivery_logging(setup_webhook_test_data) -> None:
    """Verify dispatcher delivers to matching subscriptions and logs execution outcomes."""
    data = setup_webhook_test_data
    factory = get_async_session_factory()
    tenant_id = data["tenant_id"]

    # Mock HTTP transport
    mock_responses = [
        httpx.Response(200, json={"received": True}),
    ]

    def mock_handler(_request: httpx.Request) -> httpx.Response:
        return mock_responses.pop(0)

    transport = httpx.MockTransport(mock_handler)
    mock_client = httpx.AsyncClient(transport=transport)

    dispatcher = WebhookDispatcher(max_retries=1)

    async with factory() as session:
        deliveries = await dispatcher.dispatch(
            session=session,
            tenant_id=tenant_id,
            event_type="request.created",
            payload={"request_id": "req_abc_123", "vendor": "Datadog"},
            client=mock_client,
        )
        await session.commit()

        assert len(deliveries) == 1
        d = deliveries[0]
        assert d.event_type == "request.created"
        assert d.success is True
        assert d.status == "success"
        assert d.response_status == 200
        assert d.attempt == 1


@pytest.mark.asyncio
async def test_webhook_redeliver_updates_attempt_log(setup_webhook_test_data) -> None:
    """Verify manual redelivery increases attempt counter and updates delivery log."""
    data = setup_webhook_test_data
    factory = get_async_session_factory()
    tenant_id = data["tenant_id"]
    delivery_id = uuid.uuid4()

    # Pre-populate failed delivery
    async with factory() as session:
        failed_del = WebhookDeliveryModel(
            id=delivery_id,
            tenant_id=tenant_id,
            subscription_id=data["subscription_id"],
            event_type="vendor.flagged",
            payload={"vendor_id": "v_123", "flag": "sanctions"},
            status="failed",
            response_status=500,
            attempt=1,
            success=False,
            duration_ms=45,
        )
        session.add(failed_del)
        await session.commit()

    # Mock HTTP transport returning 200 on redelivery
    def mock_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"acknowledged": True})

    transport = httpx.MockTransport(mock_handler)
    mock_client = httpx.AsyncClient(transport=transport)

    dispatcher = WebhookDispatcher()
    async with factory() as session:
        updated = await dispatcher.redeliver(
            session=session,
            delivery_id=delivery_id,
            tenant_id=tenant_id,
            http_client=mock_client,
        )
        await session.commit()

        assert updated.id == delivery_id
        assert updated.attempt == 2
        assert updated.success is True
        assert updated.status == "success"
        assert updated.response_status == 200
