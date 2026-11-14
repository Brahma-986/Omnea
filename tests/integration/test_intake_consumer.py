"""Integration tests for IntakeConsumer end-to-end database transactions and row locking."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from procure_agents.llm.client import FakeLLMClient
from procure_agents.models import (
    ClassificationType,
    ExtractedIntentFields,
    IntentClassification,
)
from procure_core.models.intent import ProcurementIntentModel
from procure_core.models.request import RequestModel
from procure_core.models.vendor import IntentVendorResolutionModel
from procure_ingress.models import ChannelType, InboundMessage
from procure_ingress.queue import PostgresInboundQueue
from workers.intake_consumer import IntakeConsumer
from workers.slack_outbound import FakeSlackOutboundClient


@pytest.mark.integration
@pytest.mark.asyncio
async def test_intake_consumer_e2e_two_turn_clarification(db_session: AsyncSession) -> None:
    """Database integration test verifying queue consumption and request creation."""
    queue = PostgresInboundQueue(db_session)
    tenant_id = f"tenant_{uuid.uuid4().hex[:8]}"
    conversation_id = f"C_{uuid.uuid4().hex[:8]}"
    thread_id = "1710000000.888888"
    ordering_key = f"slack:{conversation_id}:{thread_id}"

    # Setup Fake LLM
    fake_llm = FakeLLMClient()
    fake_slack = FakeSlackOutboundClient()
    consumer = IntakeConsumer(
        session=db_session,
        llm_client=fake_llm,
        slack_client=fake_slack,
    )

    # Turn 1: Inbound message missing spend
    fake_llm.set_canned(
        "Figma",
        IntentClassification(
            classification=ClassificationType.NEW_PURCHASE,
            confidence=0.96,
            reasoning="Wants Figma licenses",
        ),
    )
    fake_llm.default_response = ExtractedIntentFields(
        vendor_name="Figma",
        evidence={"vendor_name": "Figma"},
    )

    msg_1 = InboundMessage(
        channel=ChannelType.SLACK,
        user_id="U_BOB",
        conversation_id=conversation_id,
        thread_id=thread_id,
        text="Can we get 5 Figma seats?",
        tenant_id=tenant_id,
        ordering_key=ordering_key,
    )
    item_1 = await queue.enqueue(
        tenant_id=tenant_id,
        channel="slack",
        event_id=f"ev_{uuid.uuid4()}",
        ordering_key=ordering_key,
        payload=msg_1.model_dump(mode="json"),
        normalized_payload=msg_1.model_dump(mode="json"),
    )
    assert item_1 is not None

    # Process Turn 1
    await consumer.process_queue_item(item_1)
    await db_session.flush()

    # Verify intent saved with awaiting_clarification
    stmt = select(ProcurementIntentModel).where(ProcurementIntentModel.tenant_id == tenant_id)
    res = await db_session.execute(stmt)
    intent = res.scalar_one()
    assert intent.status == "awaiting_clarification"
    assert intent.clarification_count == 1
    assert intent.vendor_name == "Figma"
    assert "estimated_spend" in intent.missing_fields

    # Verify 1 question asked
    assert len(fake_slack.sent_messages) == 1
    assert "for Figma" in fake_slack.sent_messages[0]["text"]

    # Turn 2: Clarification reply with spend
    fake_llm.default_response = ExtractedIntentFields(
        vendor_name="Figma",
        raw_amount_str="75",
        raw_currency_str="USD",
        raw_billing_period_str="month",
        evidence={
            "vendor_name": "Figma",
            "raw_amount_str": "75",
            "raw_currency_str": "USD",
            "raw_billing_period_str": "month",
        },
    )

    msg_2 = InboundMessage(
        channel=ChannelType.SLACK,
        user_id="U_BOB",
        conversation_id=conversation_id,
        thread_id=thread_id,
        text="$75 USD / month",
        tenant_id=tenant_id,
        ordering_key=ordering_key,
    )
    item_2 = await queue.enqueue(
        tenant_id=tenant_id,
        channel="slack",
        event_id=f"ev_{uuid.uuid4()}",
        ordering_key=ordering_key,
        payload=msg_2.model_dump(mode="json"),
        normalized_payload=msg_2.model_dump(mode="json"),
    )
    assert item_2 is not None

    # Process Turn 2
    await consumer.process_queue_item(item_2)
    await db_session.flush()

    # Verify intent is now complete
    res = await db_session.execute(stmt)
    updated_intent = res.scalar_one()
    assert updated_intent.status == "complete"
    assert updated_intent.estimated_spend == Decimal("75.00")
    assert updated_intent.annualized_spend == Decimal("900.00")
    assert updated_intent.request_id is not None

    # Verify request record in requests table
    req_stmt = select(RequestModel).where(RequestModel.id == updated_intent.request_id)
    req_res = await db_session.execute(req_stmt)
    req = req_res.scalar_one()
    assert req.status == "draft"
    assert req.payload["vendor_name"] == "Figma"
    assert req.payload["estimated_spend"] == 75.0

    # Verify vendor resolution written to intent_vendor_resolutions
    ivr_stmt = select(IntentVendorResolutionModel).where(
        IntentVendorResolutionModel.tenant_id == tenant_id,
        IntentVendorResolutionModel.intent_id == updated_intent.id,
    )
    ivr_res = await db_session.execute(ivr_stmt)
    ivr = ivr_res.scalar_one()
    assert ivr.role == "vendor_name"
    assert ivr.source_name == "Figma"
    assert ivr.decision_id is not None
    assert ivr.outcome in ("match", "subsidiary_of", "provisional_review", "new_entity")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_intake_consumer_redelivery_idempotency(db_session: AsyncSession) -> None:
    """Redelivery of queue item results in 1 row per (role, source_name) in resolutions."""
    queue = PostgresInboundQueue(db_session)
    tenant_id = f"tenant_{uuid.uuid4().hex[:8]}"
    conversation_id = f"C_{uuid.uuid4().hex[:8]}"
    thread_id = "1710000000.777777"
    ordering_key = f"slack:{conversation_id}:{thread_id}"

    fake_llm = FakeLLMClient()
    fake_slack = FakeSlackOutboundClient()
    consumer = IntakeConsumer(
        session=db_session,
        llm_client=fake_llm,
        slack_client=fake_slack,
    )

    fake_llm.set_canned(
        "Snowflake",
        IntentClassification(
            classification=ClassificationType.RENEWAL,
            confidence=0.98,
            reasoning="Wants renewal",
        ),
    )
    fake_llm.default_response = ExtractedIntentFields(
        vendor_name="Snowflake",
        raw_amount_str="500",
        raw_currency_str="USD",
        raw_billing_period_str="month",
        evidence={"vendor_name": "Snowflake"},
    )

    msg = InboundMessage(
        channel=ChannelType.SLACK,
        user_id="U_ALICE",
        conversation_id=conversation_id,
        thread_id=thread_id,
        text="Need Snowflake renewal for $500/month",
        tenant_id=tenant_id,
        ordering_key=ordering_key,
    )
    item = await queue.enqueue(
        tenant_id=tenant_id,
        channel="slack",
        event_id=f"ev_{uuid.uuid4()}",
        ordering_key=ordering_key,
        payload=msg.model_dump(mode="json"),
        normalized_payload=msg.model_dump(mode="json"),
    )
    assert item is not None

    # First delivery
    await consumer.process_queue_item(item)
    await db_session.flush()

    # Re-delivery of the exact same queue item
    await consumer.process_queue_item(item)
    await db_session.flush()

    # Verify exactly 1 resolution row exists for this intent and role
    stmt = select(IntentVendorResolutionModel).where(
        IntentVendorResolutionModel.tenant_id == tenant_id,
        IntentVendorResolutionModel.role == "vendor_name",
        IntentVendorResolutionModel.source_name == "Snowflake",
    )
    res = await db_session.execute(stmt)
    rows = res.scalars().all()
    assert len(rows) == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_intake_consumer_multi_candidate_and_replaces_vendor(
    db_session: AsyncSession,
) -> None:
    """Intake consumer resolves primary vendor, replaces_vendor, and multiple candidate_vendors."""
    queue = PostgresInboundQueue(db_session)
    tenant_id = f"tenant_{uuid.uuid4().hex[:8]}"
    conversation_id = f"C_{uuid.uuid4().hex[:8]}"
    thread_id = "1710000000.666666"
    ordering_key = f"slack:{conversation_id}:{thread_id}"

    fake_llm = FakeLLMClient()
    fake_slack = FakeSlackOutboundClient()
    consumer = IntakeConsumer(
        session=db_session,
        llm_client=fake_llm,
        slack_client=fake_slack,
    )

    fake_llm.set_canned(
        "Datadog",
        IntentClassification(
            classification=ClassificationType.NEW_PURCHASE,
            confidence=0.98,
            reasoning="Wants Datadog purchase",
        ),
    )
    fake_llm.default_response = ExtractedIntentFields(
        vendor_name="Datadog",
        replaces_vendor="New Relic",
        candidate_vendors=["Dynatrace", "AppDynamics"],
        raw_amount_str="12000",
        raw_currency_str="USD",
        raw_billing_period_str="year",
        evidence={
            "vendor_name": "Datadog",
            "replaces_vendor": "New Relic",
        },
    )

    msg = InboundMessage(
        channel=ChannelType.SLACK,
        user_id="U_CHARLIE",
        conversation_id=conversation_id,
        thread_id=thread_id,
        text=(
            "Switching from New Relic to Datadog for $12k/yr; "
            "also evaluated Dynatrace and AppDynamics"
        ),
        tenant_id=tenant_id,
        ordering_key=ordering_key,
    )
    item = await queue.enqueue(
        tenant_id=tenant_id,
        channel="slack",
        event_id=f"ev_{uuid.uuid4()}",
        ordering_key=ordering_key,
        payload=msg.model_dump(mode="json"),
        normalized_payload=msg.model_dump(mode="json"),
    )
    assert item is not None

    await consumer.process_queue_item(item)
    await db_session.flush()

    # Query all resolutions for this intent
    stmt = (
        select(IntentVendorResolutionModel)
        .where(IntentVendorResolutionModel.tenant_id == tenant_id)
        .order_by(IntentVendorResolutionModel.role, IntentVendorResolutionModel.source_name)
    )
    res = await db_session.execute(stmt)
    resolutions = res.scalars().all()

    # Exactly 4 resolutions expected: 1 vendor_name, 1 replaces_vendor, 2 candidate_vendor
    assert len(resolutions) == 4
    role_map = {(r.role, r.source_name) for r in resolutions}
    assert ("vendor_name", "Datadog") in role_map
    assert ("replaces_vendor", "New Relic") in role_map
    assert ("candidate_vendor", "Dynatrace") in role_map
    assert ("candidate_vendor", "AppDynamics") in role_map
