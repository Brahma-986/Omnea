"""Webhook signature verification utilities for Omnea webhooks."""

from __future__ import annotations

import hashlib
import hmac
import time


class WebhookReceiver:
    """Helper for validating and verifying incoming HMAC-SHA256 signed Omnea webhooks."""

    def __init__(self, secret: str, tolerance: int = 300) -> None:
        self.secret = secret
        self.tolerance = tolerance

    def verify_signature(
        self,
        payload_bytes: bytes,
        signature_header: str,
        now: int | None = None,
    ) -> bool:
        """Verify the signature and replay window of an incoming webhook event.

        Args:
            payload_bytes: Exact raw bytes of HTTP request body
            signature_header: The value of the `X-Omnea-Signature` header (t=...,v1=...)
            now: Optional timestamp override (used in unit tests)

        Returns:
            bool: True if signature is cryptographically valid and not replayed.
        """
        if not signature_header or not self.secret:
            return False

        parts = dict(part.split("=", 1) for part in signature_header.split(",") if "=" in part)
        t_str = parts.get("t")
        v1_sig = parts.get("v1")

        if not t_str or not v1_sig:
            return False

        try:
            timestamp = int(t_str)
        except ValueError:
            return False

        current_time = now if now is not None else int(time.time())
        if abs(current_time - timestamp) > self.tolerance:
            return False

        expected_sig = hmac.new(
            self.secret.encode("utf-8"),
            f"{timestamp}.".encode() + payload_bytes,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(expected_sig, v1_sig)
