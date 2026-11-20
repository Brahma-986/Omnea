"""Login attempt tracking, rate-limiting, and account lockout protection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from procure_core.models.auth import LoginAttemptModel

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_WINDOW_MINUTES = 15


async def check_lockout(session: AsyncSession, email: str) -> tuple[bool, int]:
    """Check if the account is currently locked out due to excessive failed attempts.

    Returns:
        tuple[bool, int]: (is_locked, retry_after_seconds)
    """
    if not email:
        return False, 0

    normalized_email = email.strip().lower()
    window_start = datetime.now(UTC) - timedelta(minutes=LOCKOUT_WINDOW_MINUTES)

    # Find the most recent successful login
    success_stmt = select(func.max(LoginAttemptModel.attempted_at)).where(
        LoginAttemptModel.email == normalized_email,
        LoginAttemptModel.success.is_(True),
    )
    last_success_res = await session.execute(success_stmt)
    last_success = last_success_res.scalar_one_or_none()

    # Count consecutive failures after the last successful login (and within window)
    cutoff = max(window_start, last_success) if last_success else window_start

    stmt = (
        select(LoginAttemptModel)
        .where(
            LoginAttemptModel.email == normalized_email,
            LoginAttemptModel.success.is_(False),
            LoginAttemptModel.attempted_at >= cutoff,
        )
        .order_by(LoginAttemptModel.attempted_at.asc())
    )
    res = await session.execute(stmt)
    failures = list(res.scalars().all())

    if len(failures) >= MAX_FAILED_ATTEMPTS:
        # Lockout duration is 15 minutes from the 5th failure
        fifth_failure = failures[MAX_FAILED_ATTEMPTS - 1]
        unlock_time = fifth_failure.attempted_at + timedelta(minutes=LOCKOUT_WINDOW_MINUTES)
        now = datetime.now(UTC)
        if now < unlock_time:
            remaining = int((unlock_time - now).total_seconds())
            return True, max(remaining, 1)

    return False, 0


async def record_login_attempt(
    session: AsyncSession,
    email: str,
    ip_address: str,
    success: bool,
    reason: str | None = None,
) -> None:
    """Record an audit trail row for an authentication attempt."""
    normalized_email = email.strip().lower()
    attempt = LoginAttemptModel(
        email=normalized_email,
        ip_address=ip_address or "",
        success=success,
        reason=reason,
        attempted_at=datetime.now(UTC),
    )
    session.add(attempt)
    await session.flush()
