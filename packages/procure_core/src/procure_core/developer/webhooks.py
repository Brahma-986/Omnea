"""Outbound Webhook engine: HMAC-SHA256 signing, replay verification, retries, and delivery logging."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from procure_core.models.developer import WebhookDeliveryModel, WebhookSubscriptionModel

logger = logging.getLogger(__name__)

SUPPORTED_WEBHOOK_EVENTS: tuple[str, ...] = (
    "request.created",
    "request.approved",
    "request.rejected",
    "vendor.flagged",
    "document.assessed",
    "erp.synced",
)


def sign_webhook_payload(secret: str, timestamp: int, payload_bytes: bytes) -> str:
    """Generate HMAC-SHA256 signature header string t=<timestamp>,v1=<hex_hmac>."""
    basestring = f"{timestamp}.".encode() + payload_bytes
    digest = hmac.new(secret.encode("utf-8"), basestring, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def verify_webhook_signature(
    secret: str,
    payload_bytes: bytes,
    signature_header: str,
    tolerance: int = 300,
    now: int | None = None,
) -> bool:
    """Verify HMAC-SHA256 signature with anti-replay timestamp verification.

    Args:
        secret: Endpoint shared secret
        payload_bytes: Exact raw bytes of HTTP request body
        signature_header: X-Omnea-Signature header (t=...,v1=...)
        tolerance: Allowed clock skew in seconds (default 300s / 5m)
        now: Optional current timestamp override for testing
    """
    if not signature_header or not secret:
        return False

    parts = dict(part.split("=", 1) for part in signature_header.split(",") if "=" in part)
    t_str = parts.get("t")
    v1_sig = parts.get("v1")

    if not t_str or not v1_sig:
        return False

    try:
        timestamp = int(t_str)
    except ValueError:
        return False

    current_time = now if now is not None else int(time.time())
    if abs(current_time - timestamp) > tolerance:
        logger.warning(
            f"Webhook signature replay rejected: age {abs(current_time - timestamp)}s > {tolerance}s"
        )
        return False

    expected_sig = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.".encode() + payload_bytes,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected_sig, v1_sig)


class WebhookDispatcher:
    """Outbound webhook dispatcher handling signing, retries, and delivery persistence."""

    def __init__(self, max_retries: int = 3, timeout_seconds: float = 10.0) -> None:
        self.max_retries = max_retries
        self.timeout_seconds = timeout_seconds

    async def dispatch(
        self,
        session: AsyncSession,
        tenant_id: str,
        event_type: str,
        payload: dict[str, Any],
        event_id: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> list[WebhookDeliveryModel]:
        """Dispatch event to all matching subscribed endpoints for tenant."""
        stmt = select(WebhookSubscriptionModel).where(
            and_(
                WebhookSubscriptionModel.tenant_id == tenant_id,
                WebhookSubscriptionModel.is_active.is_(True),
            )
        )
        res = await session.execute(stmt)
        subscriptions = res.scalars().all()

        matching_subs = [
            s for s in subscriptions if "*" in (s.events or []) or event_type in (s.events or [])
        ]

        if not matching_subs:
            return []

        deliveries: list[WebhookDeliveryModel] = []
        created_client = client is None
        http_client = client or httpx.AsyncClient(timeout=self.timeout_seconds)

        try:
            for sub in matching_subs:
                delivery = await self._send_to_subscription(
                    session=session,
                    subscription=sub,
                    event_type=event_type,
                    payload=payload,
                    event_id=event_id or str(uuid.uuid4()),
                    http_client=http_client,
                )
                deliveries.append(delivery)
        finally:
            if created_client:
                await http_client.aclose()

        return deliveries

    async def _send_to_subscription(
        self,
        session: AsyncSession,
        subscription: WebhookSubscriptionModel,
        event_type: str,
        payload: dict[str, Any],
        event_id: str,
        http_client: httpx.AsyncClient,
    ) -> WebhookDeliveryModel:
        """Send payload with HMAC signature and exponential backoff retry."""
        delivery_id = uuid.uuid4()
        timestamp = int(time.time())
        json_body = json.dumps(payload, separators=(",", ":"), default=str)
        payload_bytes = json_body.encode("utf-8")
        signature = sign_webhook_payload(subscription.secret, timestamp, payload_bytes)

        headers = {
            "Content-Type": "application/json",
            "X-Omnea-Delivery": str(delivery_id),
            "X-Omnea-Event": event_type,
            "X-Omnea-Timestamp": str(timestamp),
            "X-Omnea-Signature": signature,
        }

        attempt = 0
        success = False
        status_code = None
        response_text = None
        start_time = time.time()

        for attempt_idx in range(1, self.max_retries + 1):
            attempt = attempt_idx
            try:
                resp = await http_client.post(
                    subscription.url,
                    content=payload_bytes,
                    headers=headers,
                )
                status_code = resp.status_code
                response_text = resp.text[:2000]
                if 200 <= status_code < 300:
                    success = True
                    break
            except Exception as exc:
                response_text = f"Delivery error: {str(exc)}"[:2000]

            # Brief backoff if retrying
            if attempt_idx < self.max_retries:
                backoff = 0.05 * (2 ** (attempt_idx - 1))
                await asyncio.sleep(backoff)

        duration_ms = int((time.time() - start_time) * 1000)
        delivery_status = "success" if success else "failed"

        delivery = WebhookDeliveryModel(
            id=delivery_id,
            tenant_id=subscription.tenant_id,
            subscription_id=subscription.id,
            event_type=event_type,
            event_id=event_id,
            payload=payload,
            status=delivery_status,
            response_status=status_code,
            response_body=response_text,
            attempt=attempt,
            success=success,
            duration_ms=duration_ms,
            created_at=datetime.now(UTC),
            delivered_at=datetime.now(UTC) if success else None,
        )
        session.add(delivery)
        await session.flush()
        return delivery

    async def redeliver(
        self,
        session: AsyncSession,
        delivery_id: uuid.UUID,
        tenant_id: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> WebhookDeliveryModel:
        """Trigger immediate manual redelivery of a prior delivery record."""
        stmt = select(WebhookDeliveryModel).where(
            and_(
                WebhookDeliveryModel.id == delivery_id,
                WebhookDeliveryModel.tenant_id == tenant_id,
            )
        )
        res = await session.execute(stmt)
        delivery = res.scalar_one_or_none()
        if not delivery:
            raise ValueError(f"Webhook delivery '{delivery_id}' not found")

        sub_stmt = select(WebhookSubscriptionModel).where(
            and_(
                WebhookSubscriptionModel.id == delivery.subscription_id,
                WebhookSubscriptionModel.tenant_id == tenant_id,
            )
        )
        sub_res = await session.execute(sub_stmt)
        subscription = sub_res.scalar_one_or_none()
        if not subscription:
            raise ValueError(f"Associated subscription for delivery '{delivery_id}' not found")

        created_client = http_client is None
        client = http_client or httpx.AsyncClient(timeout=self.timeout_seconds)

        try:
            timestamp = int(time.time())
            json_body = json.dumps(delivery.payload, separators=(",", ":"), default=str)
            payload_bytes = json_body.encode("utf-8")
            signature = sign_webhook_payload(subscription.secret, timestamp, payload_bytes)

            headers = {
                "Content-Type": "application/json",
                "X-Omnea-Delivery": str(delivery.id),
                "X-Omnea-Event": delivery.event_type,
                "X-Omnea-Timestamp": str(timestamp),
                "X-Omnea-Signature": signature,
                "X-Omnea-Redelivery": "true",
            }

            start_time = time.time()
            success = False
            status_code = None
            response_text = None

            try:
                resp = await client.post(
                    subscription.url,
                    content=payload_bytes,
                    headers=headers,
                )
                status_code = resp.status_code
                response_text = resp.text[:2000]
                success = 200 <= status_code < 300
            except Exception as exc:
                response_text = f"Redelivery error: {str(exc)}"[:2000]

            duration_ms = int((time.time() - start_time) * 1000)

            delivery.attempt += 1
            delivery.success = success
            delivery.status = "success" if success else "failed"
            delivery.response_status = status_code
            delivery.response_body = response_text
            delivery.duration_ms = duration_ms
            if success:
                delivery.delivered_at = datetime.now(UTC)

            await session.flush()
            return delivery
        finally:
            if created_client:
                await client.aclose()
