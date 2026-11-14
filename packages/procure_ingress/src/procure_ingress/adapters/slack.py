"""Slack adapter implementing signature verification, event filtering, and normalization."""

import hashlib
import hmac
import time
from typing import Any

from fastapi import HTTPException, Request, status

from procure_ingress.adapters.base import BaseIngressAdapter
from procure_ingress.models import ChannelType, InboundMessage


async def verify_slack_signature(
    request: Request,
    signing_secret: str,
) -> bytes:
    """FastAPI dependency to verify Slack request signature over raw bytes.

    Enforces replay protection within 300s, uses constant-time comparison,
    and returns generic HTTP 401 on failure without logging sensitive data.
    """
    timestamp = request.headers.get("x-slack-request-timestamp")
    signature = request.headers.get("x-slack-signature")

    if not timestamp or not signature:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )

    # Replay protection: reject requests older than 300 seconds
    try:
        ts_float = float(timestamp)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        ) from None

    if abs(time.time() - ts_float) > 300:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )

    # Read raw body bytes
    raw_body = await request.body()

    # Compute expected HMAC signature
    sig_basestring = b"v0:" + timestamp.encode("utf-8") + b":" + raw_body
    computed_hash = hmac.new(
        signing_secret.encode("utf-8"),
        sig_basestring,
        hashlib.sha256,
    ).hexdigest()
    expected_signature = f"v0={computed_hash}"

    # Constant-time comparison
    if not hmac.compare_digest(expected_signature, signature):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )

    return raw_body


class SlackIngressAdapter(BaseIngressAdapter):
    """Adapter for Slack Events API and Slash Commands."""

    def should_ignore(self, payload: dict[str, Any]) -> bool:
        """Filter out bot echoes, message changes, message deletions, and file_shared duplicates."""
        event = payload.get("event", {})
        if not event and "command" not in payload:
            return True

        # Slash commands are never ignored
        if "command" in payload:
            return False

        # Ignore bot's own messages
        if event.get("bot_id") or event.get("subtype") == "bot_message":
            return True

        # Ignore message modifications and deletions
        subtype = event.get("subtype")
        if subtype in ("message_changed", "message_deleted"):
            return True

        # Ignore file_shared event if a message event with files already handles it
        event_type = event.get("type")
        if event_type == "file_shared":
            # Canonical upload handling is attached to the parent message event
            return True

        # Subscribe only to app_mention, message.im, and messages with thread_ts
        if event_type not in ("app_mention", "message"):
            return True

        # For regular messages: accept DMs (channel starts with 'D')
        # or replies in active threads
        channel = str(event.get("channel", ""))
        is_dm = channel.startswith("D")
        is_thread_reply = bool(event.get("thread_ts") and event.get("thread_ts") != event.get("ts"))
        is_app_mention = event_type == "app_mention"

        if not (is_dm or is_thread_reply or is_app_mention):
            return True

        return False

    def extract_dedup_key(self, payload: dict[str, Any]) -> str:
        """Extract unique idempotency key for Slack payload.

        Slash commands use trigger_id; Events API use event_id.
        """
        if "trigger_id" in payload:
            return f"slack:cmd:{payload['trigger_id']}"
        if "event_id" in payload:
            return f"slack:event:{payload['event_id']}"
        # Fallback to ts
        event = payload.get("event", {})
        ts = event.get("ts", "")
        channel = event.get("channel", "")
        return f"slack:raw:{channel}:{ts}"

    def extract_ordering_key(self, payload: dict[str, Any]) -> str:
        """Extract thread or conversation key for FIFO ordering."""
        if "command" in payload:
            return f"slack:{payload.get('channel_id', '')}:{payload.get('user_id', '')}"
        event = payload.get("event", {})
        channel = event.get("channel", "")
        thread_ts = event.get("thread_ts") or event.get("ts", "")
        return f"slack:{channel}:{thread_ts}"

    def normalize(self, payload: dict[str, Any], tenant_id: str) -> InboundMessage:
        """Normalize Slack payload into canonical InboundMessage."""
        if "command" in payload:
            # Handle Slash Command
            conversation_id = str(payload.get("channel_id", ""))
            user_id = str(payload.get("user_id", ""))
            text = str(payload.get("text", "")).strip()
            trigger_id = str(payload.get("trigger_id", ""))
            response_url = payload.get("response_url")

            return InboundMessage(
                channel=ChannelType.SLACK,
                user_id=user_id,
                thread_id=trigger_id,  # Initial interaction thread anchor
                conversation_id=conversation_id,
                text=text,
                tenant_id=tenant_id,
                ordering_key=self.extract_ordering_key(payload),
                response_url=response_url,
                raw_metadata={
                    "command": payload.get("command"),
                    "team_id": payload.get("team_id"),
                },
            )

        # Handle Events API Callback
        event = payload.get("event", {})
        conversation_id = str(event.get("channel", ""))
        user_id = str(event.get("user", ""))
        text = str(event.get("text", "")).strip()
        thread_ts = str(event.get("thread_ts") or event.get("ts", ""))

        return InboundMessage(
            channel=ChannelType.SLACK,
            user_id=user_id,
            thread_id=thread_ts,
            conversation_id=conversation_id,
            text=text,
            tenant_id=tenant_id,
            ordering_key=self.extract_ordering_key(payload),
            response_url=None,
            raw_metadata={
                "event_id": payload.get("event_id"),
                "event_type": event.get("type"),
                "team_id": payload.get("team_id"),
                "files": event.get("files", []),
            },
        )
