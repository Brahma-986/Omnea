"""Server-Sent Events (SSE) Stream Ticket interface and implementations.

Provides persistent multi-worker storage and atomic single-use ticket consumption
for real-time SSE connections across restarts and multiple replicas.
"""

from __future__ import annotations

import secrets
from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import and_, delete

from procure_core.db import get_async_session_factory
from procure_core.models.auth import StreamTicketModel

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class StreamTicketStore(ABC):
    """Abstract interface for single-use SSE stream ticket lifecycle management."""

    @abstractmethod
    async def create_ticket(
        self,
        user_id: str,
        tenant_id: str,
        roles: list[str],
        ttl_seconds: float = 30.0,
    ) -> str:
        """Create and store a short-lived single-use ticket."""
        ...

    @abstractmethod
    async def consume_ticket(
        self,
        ticket: str,
    ) -> tuple[str, str, list[str]] | None:
        """Atomically retrieve and consume (delete) the ticket if valid and not expired.

        Returns:
            (user_id, tenant_id, roles) or None if invalid/expired/already consumed.
        """
        ...

    @abstractmethod
    async def cleanup_expired(self) -> int:
        """Delete all expired tickets and return the count removed."""
        ...


class PostgresStreamTicketStore(StreamTicketStore):
    """PostgreSQL-backed persistent ticket store for multi-worker and restart resilience."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._session_factory = session_factory

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        if self._session_factory is None:
            self._session_factory = get_async_session_factory()
        return self._session_factory

    async def create_ticket(
        self,
        user_id: str,
        tenant_id: str,
        roles: list[str],
        ttl_seconds: float = 30.0,
    ) -> str:
        ticket = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        async with self.session_factory() as session:
            row = StreamTicketModel(
                ticket=ticket,
                user_id=user_id,
                tenant_id=tenant_id,
                roles=roles,
                expires_at=expires_at,
            )
            session.add(row)
            await session.commit()
        return ticket

    async def consume_ticket(
        self,
        ticket: str,
    ) -> tuple[str, str, list[str]] | None:
        clean_ticket = ticket.strip()
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            stmt = (
                delete(StreamTicketModel)
                .where(
                    and_(
                        StreamTicketModel.ticket == clean_ticket,
                        StreamTicketModel.expires_at > now,
                    )
                )
                .returning(
                    StreamTicketModel.user_id,
                    StreamTicketModel.tenant_id,
                    StreamTicketModel.roles,
                )
            )
            res = await session.execute(stmt)
            await session.commit()
            row = res.first()
            if not row:
                return None
            return (row[0], row[1], list(row[2]))

    async def cleanup_expired(self) -> int:
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            stmt = delete(StreamTicketModel).where(StreamTicketModel.expires_at <= now)
            res = await session.execute(stmt)
            await session.commit()
            rc = getattr(res, "rowcount", 0)
            return int(rc if rc is not None else 0)


_ticket_store_override: StreamTicketStore | None = None


def get_stream_ticket_store() -> StreamTicketStore:
    """Dependency provider returning configured StreamTicketStore."""
    if _ticket_store_override is not None:
        return _ticket_store_override
    return PostgresStreamTicketStore()


def set_stream_ticket_store_override(store: StreamTicketStore | None) -> None:
    """Override ticket store (used by tests or custom backends)."""
    global _ticket_store_override
    _ticket_store_override = store
