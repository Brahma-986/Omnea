"""Cryptographic tokens: JWT access tokens, rotating refresh tokens, and CSRF tokens."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from procure_core.config import get_settings


def hash_token(raw_token: str) -> str:
    """Compute deterministic SHA-256 hex digest for database storage."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def create_random_token() -> tuple[str, str]:
    """Generate a high-entropy URL-safe random token and its SHA-256 hash.

    Returns:
        tuple[str, str]: (raw_token, token_hash)
    """
    raw_token = secrets.token_urlsafe(32)
    return raw_token, hash_token(raw_token)


def create_refresh_token() -> tuple[str, str]:
    """Generate a high-entropy refresh token and its SHA-256 hash.

    Returns:
        tuple[str, str]: (raw_token, token_hash)
    """
    raw_token = secrets.token_urlsafe(48)
    return raw_token, hash_token(raw_token)


def create_csrf_token() -> str:
    """Generate a secure CSRF token for cookie/header validation."""
    return secrets.token_hex(32)


def create_access_token(
    user_id: str,
    tenant_id: str,
    email: str,
    roles: list[str],
    session_id: str | None = None,
    expires_delta: timedelta | None = None,
) -> str:
    """Create short-lived JWT access token (15 mins by default)."""
    settings = get_settings()
    now = datetime.now(UTC)
    if expires_delta is not None:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    payload: dict[str, Any] = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "email": email,
        "roles": roles,
        "session_id": session_id or "",
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }

    return jwt.encode(
        payload,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and verify JWT access token.

    Raises:
        jwt.PyJWTError: If signature is invalid, token has expired, or claims are malformed.
    """
    settings = get_settings()
    payload = jwt.decode(
        token,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
        options={"require": ["exp", "sub", "tenant_id", "iat"]},
    )
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("Token is not an access token")
    return payload
