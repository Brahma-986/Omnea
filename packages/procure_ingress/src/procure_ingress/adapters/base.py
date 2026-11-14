"""Base contract for ingress channel adapters."""

from typing import Any, Protocol

from procure_ingress.models import InboundMessage


class BaseIngressAdapter(Protocol):
    """Protocol defining inbound channel normalization and deduplication key extraction."""

    def extract_dedup_key(self, payload: dict[str, Any]) -> str:
        """Extract unique idempotency key for the raw inbound event."""
        ...

    def extract_ordering_key(self, payload: dict[str, Any]) -> str:
        """Extract thread or conversation key to guarantee FIFO ordering."""
        ...

    def should_ignore(self, payload: dict[str, Any]) -> bool:
        """Determine whether the event is an internal/bot echo or ignored subtype."""
        ...

    def normalize(self, payload: dict[str, Any], tenant_id: str) -> InboundMessage:
        """Convert channel-specific payload into canonical InboundMessage."""
        ...
