"""Application-level idempotency enforcement across outbox, reservations, and audit records."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class DuplicateIdempotencyKeyError(Exception):
    """Raised when an idempotency key collision is detected at the application level."""


async def check_idempotency_seen(
    session: AsyncSession,
    tenant_id: str,
    idempotency_key: str,
    table_name: str = "approval_audit_records",
) -> bool:
    """Check whether an idempotency key was already recorded in table_name for tenant."""
    query = text(
        f"SELECT COUNT(*) FROM {table_name} "
        "WHERE tenant_id = :tenant_id AND idempotency_key = :idempotency_key"
    )
    res = await session.scalar(
        query,
        {"tenant_id": tenant_id, "idempotency_key": idempotency_key},
    )
    return bool(res and res > 0)


async def enforce_idempotency_key(
    session: AsyncSession,
    tenant_id: str,
    idempotency_key: str,
    table_name: str = "budget_reservations",
) -> None:
    """Application-level idempotency guard checking whether key was already processed."""
    query = text(
        f"SELECT COUNT(*) FROM {table_name} "
        "WHERE tenant_id = :tenant_id AND idempotency_key = :idempotency_key"
    )
    res = await session.scalar(
        query,
        {"tenant_id": tenant_id, "idempotency_key": idempotency_key},
    )
    if res and res > 0:
        raise DuplicateIdempotencyKeyError(
            f"Idempotency key '{idempotency_key}' already processed for tenant '{tenant_id}' in {table_name}"
        )
