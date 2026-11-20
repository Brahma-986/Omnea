"""Fake stream ticket store for unit testing without database dependencies."""

from __future__ import annotations

import secrets
import time

from procure_core.auth.tickets import StreamTicketStore


class FakeStreamTicketStore(StreamTicketStore):
    """In-memory thread-safe fake store for tests."""

    def __init__(self) -> None:
        self._tickets: dict[str, tuple[str, str, list[str], float]] = {}

    async def create_ticket(
        self,
        user_id: str,
        tenant_id: str,
        roles: list[str],
        ttl_seconds: float = 30.0,
    ) -> str:
        ticket = secrets.token_urlsafe(32)
        expires_at = time.time() + ttl_seconds
        self._tickets[ticket] = (user_id, tenant_id, list(roles), expires_at)
        return ticket

    async def consume_ticket(
        self,
        ticket: str,
    ) -> tuple[str, str, list[str]] | None:
        ticket_data = self._tickets.pop(ticket.strip(), None)
        if not ticket_data:
            return None
        user_id, tenant_id, roles, expires_at = ticket_data
        if time.time() > expires_at:
            return None
        return (user_id, tenant_id, roles)

    async def cleanup_expired(self) -> int:
        now = time.time()
        expired = [t for t, v in self._tickets.items() if v[3] <= now]
        for t in expired:
            self._tickets.pop(t, None)
        return len(expired)

    def clear(self) -> None:
        self._tickets.clear()

    def __contains__(self, ticket: str) -> bool:
        return ticket in self._tickets

    def __getitem__(self, ticket: str) -> tuple[str, str, list[str], float]:
        return self._tickets[ticket]
