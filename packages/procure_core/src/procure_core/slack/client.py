"""Slack Client Protocol and Real HTTP implementation with in-place updates and history lookup."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx

logger = logging.getLogger(__name__)


@dataclass
class SlackMessageResult:
    """Slack API response representation."""

    ok: bool
    channel: str
    ts: str
    error: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ISlackClient(Protocol):
    """Protocol defining standard Slack operations with in-place updates and history lookups."""

    async def post_message(
        self,
        channel: str,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
    ) -> SlackMessageResult:
        """Post a new message to a channel."""
        ...

    async def update_message(
        self,
        channel: str,
        ts: str,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
    ) -> SlackMessageResult:
        """Update an existing message in-place (chat.update) instead of reposting."""
        ...

    async def get_conversations_history(
        self,
        channel: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Retrieve message history for idempotency inspection."""
        ...

    async def find_message_by_request_id(
        self,
        channel: str,
        request_id: str,
    ) -> str | None:
        """Query conversations.history to find existing message timestamp for request_id."""
        ...

    async def get_user_email(self, user_id: str) -> str | None:
        """Fetch user email via Slack users.info API for identity mapping."""
        ...


class RealSlackClient:
    """Live Slack API client using Bearer bot tokens."""

    def __init__(self, bot_token: str, base_url: str = "https://slack.com/api") -> None:
        self.bot_token = bot_token
        self.base_url = base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.bot_token}",
            "Content-Type": "application/json; charset=utf-8",
        }

    async def post_message(
        self,
        channel: str,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
    ) -> SlackMessageResult:
        """Call chat.postMessage."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            payload: dict[str, Any] = {"channel": channel, "text": text}
            if blocks:
                payload["blocks"] = blocks
            res = await client.post(
                f"{self.base_url}/chat.postMessage", headers=self._headers(), json=payload
            )
            data = res.json()
            if not data.get("ok"):
                logger.error("Slack postMessage error: %s", data.get("error"))
                return SlackMessageResult(ok=False, channel=channel, ts="", error=data.get("error"))
            return SlackMessageResult(ok=True, channel=channel, ts=data.get("ts", ""), data=data)

    async def update_message(
        self,
        channel: str,
        ts: str,
        text: str,
        blocks: list[dict[str, Any]] | None = None,
    ) -> SlackMessageResult:
        """Call chat.update (message update, never repost!)."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            payload: dict[str, Any] = {"channel": channel, "ts": ts, "text": text}
            if blocks:
                payload["blocks"] = blocks
            res = await client.post(
                f"{self.base_url}/chat.update", headers=self._headers(), json=payload
            )
            data = res.json()
            if not data.get("ok"):
                logger.error("Slack updateMessage error: %s", data.get("error"))
                return SlackMessageResult(ok=False, channel=channel, ts=ts, error=data.get("error"))
            return SlackMessageResult(ok=True, channel=channel, ts=ts, data=data)

    async def get_conversations_history(
        self,
        channel: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Call conversations.history."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(
                f"{self.base_url}/conversations.history",
                headers=self._headers(),
                params={"channel": channel, "limit": limit},
            )
            data = res.json()
            if data.get("ok"):
                return list(data.get("messages", []))
            logger.warning("Slack conversations.history failed: %s", data.get("error"))
            return []

    async def find_message_by_request_id(
        self,
        channel: str,
        request_id: str,
    ) -> str | None:
        """Inspect conversations.history messages to check if an approval message exists."""
        messages = await self.get_conversations_history(channel=channel, limit=30)
        for msg in messages:
            msg_text = msg.get("text", "")
            # Check blocks for request_id in button value or metadata
            if request_id in msg_text:
                return str(msg.get("ts"))
            for block in msg.get("blocks", []):
                block_str = str(block)
                if request_id in block_str:
                    return str(msg.get("ts"))
        return None

    async def get_user_email(self, user_id: str) -> str | None:
        """Call users.info to retrieve user's SSO email."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(
                f"{self.base_url}/users.info",
                headers=self._headers(),
                params={"user": user_id},
            )
            data = res.json()
            if data.get("ok"):
                profile = data.get("user", {}).get("profile", {})
                return profile.get("email")
            return None
