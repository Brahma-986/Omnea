"""Captured-outbox fake email sender for unit and integration tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class CapturedEmail:
    """Captured email payload."""

    to: str
    subject: str
    html_content: str
    text_content: str | None = None
    sent_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class FakeEmailSender:
    """In-memory outbox email sender test double.

    Lives strictly under tests/ according to architectural product rules.
    """

    def __init__(self) -> None:
        self.outbox: list[CapturedEmail] = []

    async def send_email(
        self,
        to: str,
        subject: str,
        html_content: str,
        text_content: str | None = None,
    ) -> bool:
        email = CapturedEmail(
            to=to,
            subject=subject,
            html_content=html_content,
            text_content=text_content,
        )
        self.outbox.append(email)
        return True

    def clear(self) -> None:
        """Clear all captured outbox items."""
        self.outbox.clear()

    @property
    def latest(self) -> CapturedEmail | None:
        """Get the most recently sent email, if any."""
        return self.outbox[-1] if self.outbox else None
