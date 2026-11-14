"""Slack outbound messaging client interface and implementations."""

from typing import Any, Protocol

import httpx

from procure_core.log_config import get_logger

logger = get_logger(__name__)


class SlackOutboundClientProtocol(Protocol):
    """Contract for posting outbound messages and clarifying questions to Slack."""

    async def post_message(
        self,
        channel: str,
        text: str,
        thread_ts: str | None = None,
        response_url: str | None = None,
        bot_token: str | None = None,
    ) -> bool:
        """Post message to thread or via response_url."""
        ...


class SlackOutboundClient:
    """Outbound client sending messages to Slack Web API or Slash Command response_urls."""

    def __init__(self, default_token: str = "") -> None:
        self.default_token = default_token

    async def post_message(
        self,
        channel: str,
        text: str,
        thread_ts: str | None = None,
        response_url: str | None = None,
        bot_token: str | None = None,
    ) -> bool:
        """Deliver message to Slack."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            # 1. Use response_url if slash command interaction
            if response_url:
                resp = await client.post(
                    response_url,
                    json={
                        "text": text,
                        "response_type": "in_channel" if thread_ts else "ephemeral",
                    },
                )
                return resp.status_code == 200

            # 2. Use chat.postMessage Web API
            token = bot_token or self.default_token
            if not token:
                logger.error("Cannot post Slack message: missing bot token")
                return False

            payload: dict[str, Any] = {
                "channel": channel,
                "text": text,
            }
            if thread_ts:
                payload["thread_ts"] = thread_ts

            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            }

            resp = await client.post(
                "https://slack.com/api/chat.postMessage",
                headers=headers,
                json=payload,
            )
            data = resp.json()
            if not data.get("ok"):
                logger.error(f"Slack chat.postMessage failed: {data.get('error')}")
                return False

            return True


class FakeSlackOutboundClient:
    """Fake outbound client recording sent messages for testing."""

    def __init__(self) -> None:
        self.sent_messages: list[dict[str, Any]] = []

    async def post_message(
        self,
        channel: str,
        text: str,
        thread_ts: str | None = None,
        response_url: str | None = None,
        bot_token: str | None = None,
    ) -> bool:
        self.sent_messages.append(
            {
                "channel": channel,
                "text": text,
                "thread_ts": thread_ts,
                "response_url": response_url,
                "bot_token": bot_token,
            }
        )
        return True
