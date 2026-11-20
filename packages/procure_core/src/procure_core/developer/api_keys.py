"""API key generation, secure SHA-256 hashing, scope validation, and rate limiting."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from procure_core.models.developer import ApiKeyModel

VALID_SCOPES: tuple[str, ...] = (
    "requests:read",
    "requests:create",
    "requests:write",
    "approvals:read",
    "approvals:decide",
    "vendors:read",
    "vendors:manage",
    "documents:read",
    "documents:review",
    "erp:read",
    "erp:manage",
    "reconciliation:read",
    "reconciliation:manage",
    "audit:export",
    "webhooks:read",
    "webhooks:manage",
    "agents:read",
    "admin:read",
    "admin:write",
    "*",
)


def hash_api_key(raw_key: str) -> str:
    """Compute constant-time verified SHA-256 hash of an API key secret."""
    clean = raw_key.strip()
    return hashlib.sha256(clean.encode("utf-8")).hexdigest()


def verify_api_key_hash(raw_key: str, stored_hash: str) -> bool:
    """Verify raw API key matches stored hash using constant-time comparison."""
    computed = hash_api_key(raw_key)
    return hmac.compare_digest(computed, stored_hash)


def generate_api_key(
    tenant_id: str,
    name: str,
    scopes: list[str],
    rate_limit_per_minute: int = 60,
    created_by: str = "system",
    expires_in_days: int | None = None,
) -> tuple[str, ApiKeyModel]:
    """Generate a new scoped API key with high entropy.

    Returns:
        (raw_secret, ApiKeyModel): raw_secret must only be displayed once.
    """
    if not name or not name.strip():
        raise ValueError("API key name cannot be empty")

    invalid_scopes = [s for s in scopes if s not in VALID_SCOPES]
    if invalid_scopes:
        raise ValueError(f"Invalid scopes specified: {invalid_scopes}")

    # Generate 16 bytes (32 hex characters) of cryptographic entropy
    token_entropy = secrets.token_hex(16)
    prefix_ident = token_entropy[:8]
    secret_body = token_entropy[8:]

    key_prefix = f"omnea_live_{prefix_ident}"
    raw_key = f"{key_prefix}_{secret_body}"
    key_hash = hash_api_key(raw_key)

    expires_at = None
    if expires_in_days and expires_in_days > 0:
        expires_at = datetime.now(UTC) + timedelta(days=expires_in_days)

    model = ApiKeyModel(
        tenant_id=tenant_id,
        name=name.strip(),
        key_prefix=key_prefix,
        key_hash=key_hash,
        scopes=scopes,
        rate_limit_per_minute=max(1, rate_limit_per_minute),
        created_at=datetime.now(UTC),
        expires_at=expires_at,
        created_by=created_by,
    )
    return raw_key, model


@dataclass
class RateLimitWindow:
    timestamps: list[float] = field(default_factory=list)


class ApiKeyRateLimiter:
    """In-memory sliding window rate limiter enforcing per-key and per-tenant ceilings."""

    def __init__(self, tenant_multiplier: int = 5) -> None:
        self.tenant_multiplier = tenant_multiplier
        self._key_windows: dict[str, RateLimitWindow] = defaultdict(RateLimitWindow)
        self._tenant_windows: dict[str, RateLimitWindow] = defaultdict(RateLimitWindow)

    def check_and_consume(
        self,
        key_hash: str,
        tenant_id: str,
        limit_per_minute: int,
        now: float | None = None,
    ) -> tuple[bool, int, int]:
        """Check sliding 60-second window for both key and tenant limits.

        Returns:
            (allowed: bool, remaining_for_key: int, retry_after_seconds: int)
        """
        current_time = now if now is not None else time.time()
        cutoff = current_time - 60.0

        # Tenant limit ceiling
        tenant_limit = max(limit_per_minute * self.tenant_multiplier, 300)

        # 1. Clean up expired timestamps
        k_window = self._key_windows[key_hash]
        k_window.timestamps = [t for t in k_window.timestamps if t > cutoff]

        t_window = self._tenant_windows[tenant_id]
        t_window.timestamps = [t for t in t_window.timestamps if t > cutoff]

        # 2. Check Key limit
        if len(k_window.timestamps) >= limit_per_minute:
            oldest = k_window.timestamps[0]
            retry_after = max(1, int(oldest + 60.0 - current_time))
            return False, 0, retry_after

        # 3. Check Tenant limit
        if len(t_window.timestamps) >= tenant_limit:
            oldest = t_window.timestamps[0]
            retry_after = max(1, int(oldest + 60.0 - current_time))
            return False, 0, retry_after

        # 4. Consume request slot
        k_window.timestamps.append(current_time)
        t_window.timestamps.append(current_time)

        remaining = max(0, limit_per_minute - len(k_window.timestamps))
        return True, remaining, 0

    def reset(self) -> None:
        """Reset all rate limiter windows (useful for testing)."""
        self._key_windows.clear()
        self._tenant_windows.clear()


_global_rate_limiter: ApiKeyRateLimiter | None = None


def get_api_key_rate_limiter() -> ApiKeyRateLimiter:
    """Singleton getter for ApiKeyRateLimiter."""
    global _global_rate_limiter
    if _global_rate_limiter is None:
        _global_rate_limiter = ApiKeyRateLimiter()
    return _global_rate_limiter
