"""In-memory Fake Slack Client for deterministic unit and integration tests."""

from __future__ import annotations

import time
from typing import Any

from procure_core.slack.client import ISlackClient, SlackMessageResult


class FakeSlackClient(ISlackClient):
    """Test double recording sent Slack messages, updates, and conversations.history."""

    def __init__(self) -> None:
        # channel -> list of message dicts
        self.channels: dict[str, list[dict[str, Any]]] = {}
        # user_id -> email
        self.user_emails: dict[str, str] = {
            "U_APPROVER_1": "approver@omnea.ai",
            "U_FINANCE_1": "finance@omnea.ai",
            "U_REQUESTER_1": "requester@omnea.ai",
        }
        self.call_log: list[dict[str, Any]] = []

    async def post_message(
        self,
        channel: str,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
    ) -> SlackMessageResult:
        """Record newly posted message with unique timestamp."""
        ts = f"{time.time():.6f}"
        msg = {
            "ts": ts,
            "text": text,
            "blocks": blocks or [],
            "channel": channel,
        }
        if channel not in self.channels:
            self.channels[channel] = []
        self.channels[channel].append(msg)
        self.call_log.append({"action": "postMessage", "channel": channel, "ts": ts, "text": text})
        return SlackMessageResult(ok=True, channel=channel, ts=ts, data=msg)

    async def update_message(
        self,
        channel: str,
        ts: str,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
    ) -> SlackMessageResult:
        """Update existing message in-place without appending a new one."""
        msgs = self.channels.get(channel, [])
        for m in msgs:
            if m.get("ts") == ts:
                m["text"] = text
                if blocks is not None:
                    m["blocks"] = blocks
                m["updated_at"] = time.time()
                self.call_log.append(
                    {"action": "updateMessage", "channel": channel, "ts": ts, "text": text}
                )
                return SlackMessageResult(ok=True, channel=channel, ts=ts, data=m)
        # If not found, create it anyway
        return await self.post_message(channel, text, blocks)

    async def get_conversations_history(
        self,
        channel: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return channel message history in reverse chronological order."""
        msgs = self.channels.get(channel, [])
        return list(reversed(msgs))[:limit]

    async def find_message_by_request_id(
        self,
        channel: str,
        request_id: str,
    ) -> str | None:
        """Inspect channel messages to find existing message timestamp for request_id."""
        for msg in reversed(self.channels.get(channel, [])):
            if request_id in str(msg.get("text", "")) or request_id in str(msg.get("blocks", [])):
                return str(msg.get("ts"))
        return None

    async def get_user_email(self, user_id: str) -> str | None:
        """Return mapped email for Slack user ID."""
        return self.user_emails.get(user_id)

    def set_user_email(self, user_id: str, email: str) -> None:
        """Seed email mapping for testing."""
        self.user_emails[user_id] = email
