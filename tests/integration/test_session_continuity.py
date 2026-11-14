"""Integration tests for thread conversation session continuity and request linkage."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from procure_core.models.request import RequestModel
from procure_ingress.session import ConversationSessionManager


@pytest.mark.integration
async def test_session_continuity_across_thread_replies(db_session: AsyncSession) -> None:
    """Verify that thread replies upsert and preserve the existing session and request link."""
    manager = ConversationSessionManager(db_session)
    tenant_id = "tenant_test"
    channel = "slack"
    conversation_id = "C_FINANCE_123"
    thread_id = "1710000000.123456"

    # 1. Initial message creates session
    session_1 = await manager.get_or_create_session(
        tenant_id=tenant_id,
        channel_type=channel,
        conversation_id=conversation_id,
        thread_id=thread_id,
        initial_data={"initial_prompt": "I need 5 monitors"},
    )
    assert session_1.id is not None
    assert session_1.thread_id == thread_id
    assert session_1.request_id is None

    # 2. Simulate workflow linking an initial procurement request
    mock_request = RequestModel(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        requester_id="U_ALICE",
        status="draft",
        payload={"item": "monitors", "quantity": 5},
    )
    db_session.add(mock_request)
    await db_session.flush()

    await manager.link_request_to_session(session_1.id, mock_request.id)

    # 3. Follow-up reply in the same thread hits get_or_create_session
    session_2 = await manager.get_or_create_session(
        tenant_id=tenant_id,
        channel_type=channel,
        conversation_id=conversation_id,
        thread_id=thread_id,  # Same thread_ts
    )

    # Must preserve the exact same session_id and linked request_id
    assert session_2.id == session_1.id
    assert session_2.request_id == mock_request.id
    assert session_2.updated_at >= session_1.updated_at
