"""Python SDK for the Omnea AI-Native Procurement Orchestration Platform."""

from omnea.client import OmneaClient
from omnea.exceptions import (
    AuthenticationError,
    NotFoundError,
    OmneaError,
    PermissionDeniedError,
    RateLimitError,
)
from omnea.webhook import WebhookReceiver

__version__ = "0.1.0"
__all__ = [
    "OmneaClient",
    "WebhookReceiver",
    "OmneaError",
    "AuthenticationError",
    "PermissionDeniedError",
    "RateLimitError",
    "NotFoundError",
]
