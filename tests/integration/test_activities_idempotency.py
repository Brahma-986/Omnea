"""Integration tests asserting activity-level idempotency for audit logs and outbox events."""

from __future__ import annotations

import uuid
import pytest
from sqlalchemy import text

from procure_core.db import get_async_session_factory
from workers.approval.activities import (
    publish_outbox_event_activity,
    record_audit_log_activity,
)


@pytest.mark.asyncio
async def test_audit_activity_idempotency_prevents_duplicate_insert() -> None:
    """Proves: record_audit_log_activity idempotency guard prevents duplicate INSERT exceptions on replay."""
    session_factory = get_async_session_factory()
    tenant_id = f"t_idem_{uuid.uuid4().hex[:6]}"
    req_id = uuid.uuid4()

    # Seed parent request row for FK satisfaction
    async with session_factory() as session:
        async with session.begin():
            await session.execute(
                text("INSERT INTO requests (id, tenant_id, requester_id, status) VALUES (:id, :t, 'alice', 'draft')"),
                {"id": req_id, "t": tenant_id},
            )

    idem_key = f"{tenant_id}:{req_id}:audit_test_idem_1"

    # First execution succeeds
    res1 = await record_audit_log_activity(
        tenant_id=tenant_id,
        request_id_str=str(req_id),
        step_name="finance_review",
        round_num=1,
        actor_id="agent_finance",
        actor_type="agent",
        action="review_completed",
        verdict="approve",
        reasons=["Budget compliant"],
        evidence_ids=[],
        prompt_version=None,
        model=None,
        policy_version=None,
        context_snapshot_hash="h1",
        slack_message_ts="123456.000000",
        idempotency_key=idem_key,
    )
    assert res1 is True

    # Second execution with same idempotency key MUST succeed idempotently without throwing UniqueViolation
    res2 = await record_audit_log_activity(
        tenant_id=tenant_id,
        request_id_str=str(req_id),
        step_name="finance_review",
        round_num=1,
        actor_id="agent_finance",
        actor_type="agent",
        action="review_completed",
        verdict="approve",
        reasons=["Budget compliant"],
        evidence_ids=[],
        prompt_version=None,
        model=None,
        policy_version=None,
        context_snapshot_hash="h1",
        slack_message_ts="123456.000000",
        idempotency_key=idem_key,
    )
    assert res2 is True


@pytest.mark.asyncio
async def test_outbox_activity_idempotency_prevents_duplicate_insert() -> None:
    """Proves: publish_outbox_event_activity idempotency guard prevents duplicate INSERT on replay."""
    tenant_id = f"t_idem_{uuid.uuid4().hex[:6]}"
    req_id = uuid.uuid4()
    idem_key = f"{tenant_id}:{req_id}:outbox_test_idem_1"

    res1 = await publish_outbox_event_activity(
        tenant_id=tenant_id,
        request_id_str=str(req_id),
        event_type="request.approved",
        payload={"approved_by": "vp_finance"},
        idempotency_key=idem_key,
    )
    assert res1 is True

    # Second execution with same idempotency key MUST succeed idempotently
    res2 = await publish_outbox_event_activity(
        tenant_id=tenant_id,
        request_id_str=str(req_id),
        event_type="request.approved",
        payload={"approved_by": "vp_finance"},
        idempotency_key=idem_key,
    )
    assert res2 is True
