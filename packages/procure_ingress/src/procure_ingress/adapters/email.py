"""Inbound email adapter with feature flag enforcement and Message-ID deduplication."""

from typing import Any

from fastapi import HTTPException, status

from procure_core.config import Settings, get_settings
from procure_ingress.adapters.base import BaseIngressAdapter
from procure_ingress.models import ChannelType, InboundMessage


class EmailIngressAdapter(BaseIngressAdapter):
    """Adapter for inbound email webhooks (SendGrid, Postmark, Mailgun)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def check_enabled(self) -> None:
        """Verify the email ingress feature flag is enabled."""
        if not self.settings.ENABLE_EMAIL_INGRESS:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Email ingress is disabled",
            )

    def extract_dedup_key(self, payload: dict[str, Any]) -> str:
        """Extract RFC 822 Message-ID as canonical idempotency key."""
        msg_id = str(
            payload.get("Message-ID") or payload.get("message_id") or payload.get("id", "")
        )
        return f"email:msg:{msg_id}"

    def extract_ordering_key(self, payload: dict[str, Any]) -> str:
        """Extract conversation/thread ordering key from In-Reply-To or Subject."""
        reply_to = payload.get("In-Reply-To") or payload.get("in_reply_to")
        if reply_to:
            return f"email:thread:{reply_to}"
        subject = str(payload.get("subject", "")).lower().replace("re:", "").strip()
        from_email = str(payload.get("from", "")).lower()
        return f"email:subject:{from_email}:{subject}"

    def should_ignore(self, payload: dict[str, Any]) -> bool:
        """Ignore bounce or auto-reply emails."""
        headers = payload.get("headers", {})
        if headers.get("Auto-Submitted") == "auto-replied":
            return True
        return False

    def normalize(self, payload: dict[str, Any], tenant_id: str) -> InboundMessage:
        """Normalize email payload into canonical InboundMessage."""
        self.check_enabled()

        from_email = str(payload.get("from") or payload.get("sender", ""))
        subject = str(payload.get("subject", ""))
        body_text = str(payload.get("text") or payload.get("body", "")).strip()
        full_text = f"Subject: {subject}\n\n{body_text}" if subject else body_text

        msg_id = str(
            payload.get("Message-ID") or payload.get("message_id") or payload.get("id", "")
        )
        thread_id = str(payload.get("In-Reply-To") or payload.get("in_reply_to") or msg_id)

        return InboundMessage(
            channel=ChannelType.EMAIL,
            user_id=from_email,
            thread_id=thread_id,
            conversation_id=from_email,
            text=full_text,
            tenant_id=tenant_id,
            ordering_key=self.extract_ordering_key(payload),
            raw_metadata={
                "message_id": msg_id,
                "subject": subject,
                "to": payload.get("to"),
            },
        )
