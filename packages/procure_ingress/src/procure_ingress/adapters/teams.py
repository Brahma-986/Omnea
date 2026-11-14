"""Microsoft Teams adapter with feature flag enforcement and Bot Framework normalization."""

from typing import Any

from fastapi import HTTPException, status

from procure_core.config import Settings, get_settings
from procure_ingress.adapters.base import BaseIngressAdapter
from procure_ingress.models import ChannelType, InboundMessage


class TeamsIngressAdapter(BaseIngressAdapter):
    """Adapter for Microsoft Teams Bot Framework activities."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def check_enabled(self) -> None:
        """Verify the feature flag is enabled before processing."""
        if not self.settings.ENABLE_TEAMS_INGRESS:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Teams ingress is disabled",
            )

    def extract_dedup_key(self, payload: dict[str, Any]) -> str:
        """Extract Teams activity id as idempotency key."""
        activity_id = str(payload.get("id", ""))
        return f"teams:activity:{activity_id}"

    def extract_ordering_key(self, payload: dict[str, Any]) -> str:
        """Extract conversation id for ordering."""
        conversation = payload.get("conversation", {})
        conv_id = str(conversation.get("id", ""))
        return f"teams:{conv_id}"

    def should_ignore(self, payload: dict[str, Any]) -> bool:
        """Ignore non-message activities or bot echoes."""
        activity_type = payload.get("type")
        if activity_type != "message":
            return True
        from_field = payload.get("from", {})
        if from_field.get("role") == "bot":
            return True
        return False

    def normalize(self, payload: dict[str, Any], tenant_id: str) -> InboundMessage:
        """Normalize Teams Activity payload into InboundMessage."""
        self.check_enabled()

        conversation = payload.get("conversation", {})
        conversation_id = str(conversation.get("id", ""))
        from_user = payload.get("from", {})
        user_id = str(from_user.get("id", ""))
        text = str(payload.get("text", "")).strip()

        # Thread id from conversation details or activity id
        thread_id = str(conversation.get("conversationType", "")) or str(payload.get("id", ""))

        return InboundMessage(
            channel=ChannelType.TEAMS,
            user_id=user_id,
            thread_id=thread_id,
            conversation_id=conversation_id,
            text=text,
            tenant_id=tenant_id,
            ordering_key=self.extract_ordering_key(payload),
            raw_metadata={
                "activity_id": payload.get("id"),
                "channel_id": payload.get("channelId"),
            },
        )
