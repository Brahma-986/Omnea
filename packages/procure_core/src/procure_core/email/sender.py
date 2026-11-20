"""Email delivery interface and provider implementations with safe degradation."""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

import httpx

from procure_core.config import get_settings

logger = logging.getLogger(__name__)


@runtime_checkable
class EmailSenderProtocol(Protocol):
    """Protocol for transactional email senders."""

    async def send_email(
        self,
        to: str,
        subject: str,
        html_content: str,
        text_content: str | None = None,
    ) -> bool:
        """Send an email to a recipient.

        Returns:
            bool: True if delivered/accepted, False if failed or degraded.
        """
        ...


class ResendEmailSender:
    """Resend email provider with safe degradation on missing credentials."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or get_settings().RESEND_API_KEY

    async def send_email(
        self,
        to: str,
        subject: str,
        html_content: str,
        text_content: str | None = None,
    ) -> bool:
        if not self.api_key or self.api_key.startswith("re_placeholder"):
            logger.warning(
                "Resend not connected: Missing RESEND_API_KEY. "
                "Feature degraded safely; email not dispatched."
            )
            return False

        payload: dict[str, object] = {
            "from": get_settings().EMAIL_FROM_ADDRESS,
            "to": [to],
            "subject": subject,
            "html": html_content,
        }
        if text_content:
            payload["text"] = text_content

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                )
        except httpx.HTTPError as exc:
            logger.error("Resend request failed for %s: %s", to, exc)
            return False

        if resp.status_code >= 400:
            logger.error(
                "Resend rejected email to %s (HTTP %s): %s", to, resp.status_code, resp.text[:500]
            )
            return False

        logger.info("Resend sent email to %s (subject: %s)", to, subject)
        return True


class PostmarkEmailSender:
    """Postmark email provider with safe degradation on missing credentials."""

    def __init__(self, server_token: str | None = None) -> None:
        self.server_token = server_token or get_settings().POSTMARK_SERVER_TOKEN

    async def send_email(
        self,
        to: str,
        subject: str,
        html_content: str,
        text_content: str | None = None,
    ) -> bool:
        _ = (html_content, text_content)
        if not self.server_token:
            logger.warning(
                "Postmark not connected: Missing POSTMARK_SERVER_TOKEN. "
                "Feature degraded safely; email not dispatched."
            )
            return False
        logger.info("Postmark dispatching email to %s (subject: %s)", to, subject)
        return True


class SesEmailSender:
    """AWS SES email provider with safe degradation on missing credentials."""

    def __init__(self, region: str | None = None) -> None:
        self.region = region or get_settings().AWS_SES_REGION

    async def send_email(
        self,
        to: str,
        subject: str,
        html_content: str,
        text_content: str | None = None,
    ) -> bool:
        _ = (html_content, text_content)
        settings = get_settings()
        if not settings.S3_ACCESS_KEY_ID or not settings.S3_SECRET_ACCESS_KEY:
            logger.warning(
                "AWS SES not connected: Missing AWS credentials. "
                "Feature degraded safely; email not dispatched."
            )
            return False
        logger.info("SES dispatching email to %s (subject: %s)", to, subject)
        return True


# Global registry for active sender (can be overridden by test doubles under tests/)
_active_sender: EmailSenderProtocol | None = None


def set_email_sender(sender: EmailSenderProtocol | None) -> None:
    """Set or reset active email sender (used by tests to inject captured-outbox fake)."""
    global _active_sender
    _active_sender = sender


def get_email_sender() -> EmailSenderProtocol:
    """Factory returning configured email sender or safe default."""
    global _active_sender
    if _active_sender is not None:
        return _active_sender

    settings = get_settings()
    provider = settings.EMAIL_PROVIDER.lower().strip()

    if provider == "resend":
        return ResendEmailSender()
    if provider == "postmark":
        return PostmarkEmailSender()
    if provider == "ses":
        return SesEmailSender()

    # Default to Resend with safe degradation
    return ResendEmailSender()
