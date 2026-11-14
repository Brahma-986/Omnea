"""Conversation session store tracking thread context and request linkage with sliding TTL."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from procure_core.log_config import get_logger
from procure_ingress.models import ConversationSessionModel, SessionItem

logger = get_logger(__name__)


class ConversationSessionManager:
    """Manages thread-level conversational continuity across channels."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create_session(
        self,
        tenant_id: str,
        channel_type: str,
        conversation_id: str,
        thread_id: str,
        ttl_hours: int = 48,
        request_id: uuid.UUID | None = None,
        initial_data: dict[str, Any] | None = None,
    ) -> SessionItem:
        """Upsert conversation session keyed by:
        (tenant_id, channel_type, conversation_id, thread_id).
        """
        now = datetime.now(UTC)
        expires_at = now + timedelta(hours=ttl_hours)
        data = initial_data or {}

        # Upsert with sliding TTL on conflict
        stmt = (
            insert(ConversationSessionModel)
            .values(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                channel_type=channel_type,
                conversation_id=conversation_id,
                thread_id=thread_id,
                request_id=request_id,
                session_data=data,
                created_at=now,
                updated_at=now,
                expires_at=expires_at,
            )
            .on_conflict_do_update(
                index_elements=["tenant_id", "channel_type", "conversation_id", "thread_id"],
                set_={
                    "updated_at": now,
                    "expires_at": expires_at,
                },
            )
            .returning(ConversationSessionModel)
        )

        result = await self.session.execute(stmt)
        record = result.scalar_one()
        return SessionItem.model_validate(record)

    async def get_session(
        self,
        tenant_id: str,
        channel_type: str,
        conversation_id: str,
        thread_id: str,
    ) -> SessionItem | None:
        """Retrieve active non-expired session for a thread."""
        now = datetime.now(UTC)
        stmt = select(ConversationSessionModel).where(
            (ConversationSessionModel.tenant_id == tenant_id)
            & (ConversationSessionModel.channel_type == channel_type)
            & (ConversationSessionModel.conversation_id == conversation_id)
            & (ConversationSessionModel.thread_id == thread_id)
            & (ConversationSessionModel.expires_at > now)
        )
        result = await self.session.execute(stmt)
        record = result.scalar_one_or_none()
        if not record:
            return None
        return SessionItem.model_validate(record)

    async def link_request_to_session(
        self,
        session_id: uuid.UUID,
        request_id: uuid.UUID,
    ) -> None:
        """Associate a created procurement request with the thread session."""
        stmt = select(ConversationSessionModel).where(ConversationSessionModel.id == session_id)
        result = await self.session.execute(stmt)
        record = result.scalar_one_or_none()
        if record:
            record.request_id = request_id
            record.updated_at = datetime.now(UTC)
            await self.session.flush()
            logger.info(f"Linked request {request_id} to conversation session {session_id}")
