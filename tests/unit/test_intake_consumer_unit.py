"""Unit tests for IntakeConsumer orchestration, classification, and clarification loop."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from procure_agents.llm.client import FakeLLMClient
from procure_agents.models import (
    ClassificationType,
    ExtractedIntentFields,
    IntentClassification,
)
from procure_context.models import ResolutionResult
from procure_core.models.intent import ProcurementIntentModel
from procure_core.models.request import RequestModel
from procure_ingress.models import ChannelType, InboundMessage, QueueItem
from workers.intake_consumer import IntakeConsumer
from workers.slack_outbound import FakeSlackOutboundClient


def make_mock_resolver(
    outcome: str = "MATCH",
    matched_id: uuid.UUID | None = None,
    needs_review: bool = False,
) -> MagicMock:
    """Create a mock entity resolution service for unit tests."""
    resolver = MagicMock()
    v_id = matched_id or uuid.uuid4()
    resolver.resolve_transactional = AsyncMock(
        return_value=ResolutionResult(
            outcome=outcome,  # type: ignore[arg-type]
            matched_vendor_id=v_id,
            confidence=1.0,
            rule_path="alias",
            decision_id=uuid.uuid4(),
            needs_review=needs_review,
        )
    )
    return resolver


def make_inbound_message(
    text: str,
    thread_id: str = "1710000000.123456",
    conversation_id: str = "C12345",
) -> InboundMessage:
    """Helper to create test inbound message."""
    return InboundMessage(
        channel=ChannelType.SLACK,
        user_id="U_TEST_USER",
        conversation_id=conversation_id,
        thread_id=thread_id,
        text=text,
        tenant_id="test_tenant",
        ordering_key=f"slack:{conversation_id}:{thread_id}",
    )


def make_queue_item(message: InboundMessage) -> QueueItem:
    """Helper to create queue item wrapping inbound message."""
    now = datetime.now(UTC)
    return QueueItem(
        id=uuid.uuid4(),
        tenant_id=message.tenant_id,
        channel=message.channel.value,
        event_id=f"ev_{uuid.uuid4()}",
        ordering_key=message.ordering_key,
        payload=message.model_dump(mode="json"),
        normalized_payload=message.model_dump(mode="json"),
        status="pending",
        attempts=0,
        max_attempts=5,
        available_at=now,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_consumer_routes_status_check() -> None:
    """Status check query replies with status info and does not create an intent."""
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock()
    mock_session.flush = AsyncMock()

    fake_llm = FakeLLMClient(
        default_response=IntentClassification(
            classification=ClassificationType.STATUS_CHECK,
            confidence=0.95,
            request_ref="REQ-4092",
            reasoning="Status query",
        )
    )
    fake_slack = FakeSlackOutboundClient()
    consumer = IntakeConsumer(
        session=mock_session,
        llm_client=fake_llm,
        slack_client=fake_slack,
        resolver=make_mock_resolver(),
    )

    # Mock no pending intent
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result

    msg = make_inbound_message("What is the status of request REQ-4092?")
    item = make_queue_item(msg)

    # Mock queue mark_complete
    consumer.queue.mark_complete = AsyncMock()  # type: ignore[method-assign]
    consumer.session_manager.get_or_create_session = AsyncMock(  # type: ignore[method-assign]
        return_value=MagicMock(id=uuid.uuid4())
    )

    await consumer.process_queue_item(item)

    assert len(fake_slack.sent_messages) == 1
    sent = fake_slack.sent_messages[0]
    assert "REQ-4092" in sent["text"]
    assert "Under review" in sent["text"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_consumer_routes_vendor_question() -> None:
    """Vendor question routes to specialist reply."""
    mock_session = AsyncMock()
    fake_llm = FakeLLMClient(
        default_response=IntentClassification(
            classification=ClassificationType.VENDOR_QUESTION,
            confidence=0.90,
            reasoning="Vendor inquiry",
        )
    )
    fake_slack = FakeSlackOutboundClient()
    consumer = IntakeConsumer(
        session=mock_session,
        llm_client=fake_llm,
        slack_client=fake_slack,
        resolver=make_mock_resolver(),
    )

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result
    consumer.queue.mark_complete = AsyncMock()  # type: ignore[method-assign]
    consumer.session_manager.get_or_create_session = AsyncMock(  # type: ignore[method-assign]
        return_value=MagicMock(id=uuid.uuid4())
    )

    msg = make_inbound_message("Does Datadog support SOC2?")
    item = make_queue_item(msg)

    await consumer.process_queue_item(item)

    assert len(fake_slack.sent_messages) == 1
    assert "specialist will follow up" in fake_slack.sent_messages[0]["text"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_consumer_initial_missing_fields_posts_one_question() -> None:
    """Initial request with missing budget asks exactly 1 question and persists pending intent."""
    mock_session = AsyncMock()
    added_records: list[object] = []
    mock_session.add = MagicMock(side_effect=lambda rec: added_records.append(rec))
    mock_session.flush = AsyncMock()

    # Classification: NEW_PURCHASE
    # Extraction: Notion, but missing spend and currency
    fake_llm = FakeLLMClient()
    fake_llm.set_canned(
        "Notion",
        IntentClassification(
            classification=ClassificationType.NEW_PURCHASE,
            confidence=0.95,
            reasoning="Wants licenses",
        ),
    )
    fake_llm.default_response = ExtractedIntentFields(
        vendor_name="Notion",
        evidence={"vendor_name": "Notion"},
    )
    fake_slack = FakeSlackOutboundClient()

    consumer = IntakeConsumer(
        session=mock_session,
        llm_client=fake_llm,
        slack_client=fake_slack,
        resolver=make_mock_resolver(),
    )

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result
    consumer.queue.mark_complete = AsyncMock()  # type: ignore[method-assign]
    session_id = uuid.uuid4()
    consumer.session_manager.get_or_create_session = AsyncMock(  # type: ignore[method-assign]
        return_value=MagicMock(id=session_id)
    )

    msg = make_inbound_message("Can we get 10 licenses for Notion?")
    item = make_queue_item(msg)

    await consumer.process_queue_item(item)

    # 1. Check pending intent added to DB
    assert len(added_records) == 1
    intent = added_records[0]
    assert isinstance(intent, ProcurementIntentModel)
    assert intent.status == "awaiting_clarification"
    assert intent.clarification_count == 1
    assert intent.vendor_name == "Notion"
    assert "estimated_spend" in intent.missing_fields
    assert "currency" in intent.missing_fields

    # 2. Check exactly ONE clarifying question posted to Slack
    assert len(fake_slack.sent_messages) == 1
    question = fake_slack.sent_messages[0]["text"]
    assert "for Notion" in question
    assert "estimated budget or spend" in question


@pytest.mark.unit
@pytest.mark.asyncio
async def test_consumer_initial_complete_creates_request() -> None:
    """Canonical complete request creates RequestModel and sends confirmation without questions."""
    mock_session = AsyncMock()
    added_records: list[object] = []
    mock_session.add = MagicMock(side_effect=lambda rec: added_records.append(rec))
    mock_session.flush = AsyncMock()

    text = "48k USD per year for Datadog"
    fake_llm = FakeLLMClient()
    fake_llm.set_canned(
        "Datadog",
        IntentClassification(
            classification=ClassificationType.NEW_PURCHASE,
            confidence=0.98,
            reasoning="Purchase request",
        ),
    )
    fake_llm.default_response = ExtractedIntentFields(
        vendor_name="Datadog",
        raw_amount_str="48k",
        raw_currency_str="USD",
        raw_billing_period_str="per year",
        evidence={
            "vendor_name": "Datadog",
            "raw_amount_str": "48k",
            "raw_currency_str": "USD",
            "raw_billing_period_str": "per year",
        },
    )
    fake_slack = FakeSlackOutboundClient()

    consumer = IntakeConsumer(
        session=mock_session,
        llm_client=fake_llm,
        slack_client=fake_slack,
        resolver=make_mock_resolver(),
    )

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result
    consumer.queue.mark_complete = AsyncMock()  # type: ignore[method-assign]
    consumer.session_manager.get_or_create_session = AsyncMock(  # type: ignore[method-assign]
        return_value=MagicMock(id=uuid.uuid4())
    )
    consumer.session_manager.link_request_to_session = AsyncMock()  # type: ignore[method-assign]

    msg = make_inbound_message(text)
    item = make_queue_item(msg)

    await consumer.process_queue_item(item)

    # Check both RequestModel and ProcurementIntentModel added
    assert len(added_records) == 2
    req = added_records[0]
    intent = added_records[1]

    assert isinstance(req, RequestModel)
    assert isinstance(intent, ProcurementIntentModel)
    assert intent.status == "complete"
    assert intent.vendor_name == "Datadog"
    assert intent.estimated_spend == Decimal("48000.00")
    assert intent.annualized_spend == Decimal("48000.00")
    assert intent.currency == "USD"
    assert intent.clarification_count == 0

    # Confirmation posted to Slack
    assert len(fake_slack.sent_messages) == 1
    confirmation = fake_slack.sent_messages[0]["text"]
    assert "Datadog" in confirmation
    assert "48,000.00" in confirmation
    assert "Procurement request submitted" in confirmation


@pytest.mark.unit
@pytest.mark.asyncio
async def test_consumer_clarification_turn_resolves() -> None:
    """Follow-up turn in thread merges text, resolves missing fields, and completes request."""
    mock_session = AsyncMock()
    added_records: list[object] = []
    mock_session.add = MagicMock(side_effect=lambda rec: added_records.append(rec))
    mock_session.flush = AsyncMock()

    # Create pending intent
    session_id = uuid.uuid4()
    pending_intent = ProcurementIntentModel(
        id=uuid.uuid4(),
        tenant_id="test_tenant",
        session_id=session_id,
        source_event_id="ev_original",
        intent_type="new_purchase",
        status="awaiting_clarification",
        clarification_count=1,
        vendor_name="Notion",
        missing_fields=["estimated_spend", "currency"],
        raw_text="Can we get 10 licenses for Notion?",
    )

    # Return pending intent from DB query
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = pending_intent
    mock_session.execute.return_value = mock_result

    # LLM extraction on merged text
    fake_llm = FakeLLMClient(
        default_response=ExtractedIntentFields(
            vendor_name="Notion",
            raw_amount_str="80",
            raw_currency_str="USD",
            raw_billing_period_str="monthly",
            evidence={
                "vendor_name": "Notion",
                "raw_amount_str": "80",
                "raw_currency_str": "USD",
                "raw_billing_period_str": "monthly",
            },
        )
    )
    fake_slack = FakeSlackOutboundClient()

    consumer = IntakeConsumer(
        session=mock_session,
        llm_client=fake_llm,
        slack_client=fake_slack,
        resolver=make_mock_resolver(),
    )
    consumer.queue.mark_complete = AsyncMock()  # type: ignore[method-assign]
    consumer.session_manager.get_or_create_session = AsyncMock(  # type: ignore[method-assign]
        return_value=MagicMock(id=session_id)
    )
    consumer.session_manager.link_request_to_session = AsyncMock()  # type: ignore[method-assign]

    reply_msg = make_inbound_message("$80 USD monthly")
    item = make_queue_item(reply_msg)

    await consumer.process_queue_item(item)

    # Status completed
    assert pending_intent.status == "complete"
    assert pending_intent.estimated_spend == Decimal("80.00")
    assert pending_intent.annualized_spend == Decimal("960.00")  # 80 * 12
    assert pending_intent.currency == "USD"
    assert pending_intent.missing_fields == []

    # Confirmation sent
    assert len(fake_slack.sent_messages) == 1
    assert "Details confirmed" in fake_slack.sent_messages[0]["text"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_consumer_clarification_still_incomplete_human_handoff() -> None:
    """Rule 6: If follow-up reply still lacks fields, hand off to human. No second question."""
    mock_session = AsyncMock()
    mock_session.flush = AsyncMock()

    session_id = uuid.uuid4()
    pending_intent = ProcurementIntentModel(
        id=uuid.uuid4(),
        tenant_id="test_tenant",
        session_id=session_id,
        source_event_id="ev_original",
        intent_type="new_purchase",
        status="awaiting_clarification",
        clarification_count=1,
        vendor_name="Notion",
        missing_fields=["estimated_spend", "currency"],
        raw_text="Can we get 10 licenses for Notion?",
    )

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = pending_intent
    mock_session.execute.return_value = mock_result

    # Re-extraction still missing amount and currency
    fake_llm = FakeLLMClient(
        default_response=ExtractedIntentFields(
            vendor_name="Notion",
            raw_amount_str=None,
            raw_currency_str=None,
            evidence={"vendor_name": "Notion"},
        )
    )
    fake_slack = FakeSlackOutboundClient()

    consumer = IntakeConsumer(
        session=mock_session,
        llm_client=fake_llm,
        slack_client=fake_slack,
        resolver=make_mock_resolver(),
    )
    consumer.queue.mark_complete = AsyncMock()  # type: ignore[method-assign]
    consumer.session_manager.get_or_create_session = AsyncMock(  # type: ignore[method-assign]
        return_value=MagicMock(id=session_id)
    )

    reply_msg = make_inbound_message("I am not sure about the price yet")
    item = make_queue_item(reply_msg)

    await consumer.process_queue_item(item)

    # State transitions to needs_human_review with needs_review=True
    assert pending_intent.status == "needs_human_review"
    assert pending_intent.needs_review is True

    # Exactly 0 new clarifying questions asked; human hand-off notification sent
    assert len(fake_slack.sent_messages) == 1
    handoff_text = fake_slack.sent_messages[0]["text"]
    assert "Some details are still unconfirmed" in handoff_text
    assert "procurement team has been notified" in handoff_text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_consumer_vendor_resolution_links_and_flags_review() -> None:
    """IntakeConsumer resolves primary, replaces, and candidate vendors and honors needs_review."""
    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()

    mock_resolver = MagicMock()
    v_primary_id = uuid.uuid4()
    v_replaces_id = uuid.uuid4()
    v_cand_id = uuid.uuid4()

    async def resolve_side_effect(
        _session: object, name: str, commit_if_auto: bool = True
    ) -> ResolutionResult:
        del commit_if_auto
        if name == "PrimaryVendor":
            return ResolutionResult(
                outcome="PROVISIONAL_REVIEW",
                matched_vendor_id=v_primary_id,
                confidence=0.88,
                rule_path="fuzzy_review",
                decision_id=uuid.uuid4(),
                needs_review=True,
            )
        elif name == "OldVendor":
            return ResolutionResult(
                outcome="MATCH",
                matched_vendor_id=v_replaces_id,
                confidence=1.0,
                rule_path="alias",
                decision_id=uuid.uuid4(),
            )
        else:
            return ResolutionResult(
                outcome="MATCH",
                matched_vendor_id=v_cand_id,
                confidence=1.0,
                rule_path="canonical_key",
                decision_id=uuid.uuid4(),
            )

    mock_resolver.resolve_transactional.side_effect = resolve_side_effect

    consumer = IntakeConsumer(
        session=mock_session,
        resolver=mock_resolver,
    )

    intent = ProcurementIntentModel(
        id=uuid.uuid4(),
        tenant_id="tenant_abc",
        session_id=uuid.uuid4(),
        vendor_name="PrimaryVendor",
        replaces_vendor="OldVendor",
        candidate_vendors=["CandidateOne"],
        intent_type="new_purchase",
        needs_review=False,
    )
    req = RequestModel(id=uuid.uuid4(), tenant_id="tenant_abc", requester_id="U1")

    await consumer._resolve_vendors(intent, request_record=req)

    # 1. Assert needs_review flagged on intent
    assert intent.needs_review is True

    # 2. Assert request_record linked to primary vendor_id
    assert req.vendor_id == v_primary_id

    # 3. Assert resolve_transactional called for all 3 vendors
    assert mock_resolver.resolve_transactional.call_count == 3
