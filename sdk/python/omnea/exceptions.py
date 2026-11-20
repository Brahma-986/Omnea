"""Exception hierarchy for Omnea Python SDK."""

from __future__ import annotations


class OmneaError(Exception):
    """Base exception for all Omnea SDK operations."""

    def __init__(
        self, message: str, status_code: int | None = None, response_data: dict | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.response_data = response_data or {}


class AuthenticationError(OmneaError):
    """Raised when authentication credentials (API key) are invalid, revoked, or expired."""


class PermissionDeniedError(OmneaError):
    """Raised when the API key lacks the required scope for the action."""


class RateLimitError(OmneaError):
    """Raised when the per-key or per-tenant rate limit ceiling has been reached."""

    def __init__(
        self, message: str, retry_after: int = 60, response_data: dict | None = None
    ) -> None:
        super().__init__(message, status_code=429, response_data=response_data)
        self.retry_after = retry_after


class NotFoundError(OmneaError):
    """Raised when the requested resource does not exist."""
