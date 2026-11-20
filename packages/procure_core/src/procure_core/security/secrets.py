"""Secure Reference-Based Secrets Manager with Hardware-Grade Encryption and Leak Prevention.

Guarantees:
1. Secrets are never stored in DB plaintext - only URI references like `secrets://tenants/{tenant_id}/{scope}/{name}`.
2. Cryptographic encryption using AES-256-GCM.
3. Automated redaction for logs, audit histories, and outbound API payloads.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from procure_core.config import get_settings

logger = logging.getLogger(__name__)

# Patterns for sensitive credentials to scrub from logs, audit records, and histories
SECRET_KEY_REGEX = re.compile(
    r"(?i)(password|secret|token|api[_-]?key|client[_-]?secret|private[_-]?key|access[_-]?token|refresh[_-]?token|signing[_-]?secret|auth(?:orization)?)"
)
SLACK_TOKEN_REGEX = re.compile(r"xox[baprs]-[0-9a-zA-Z-]{10,72}")
BEARER_TOKEN_REGEX = re.compile(r"\b(?:Bearer\s+)+([A-Za-z0-9\-._~+/]+=*)", re.IGNORECASE)
JWT_PATTERN_REGEX = re.compile(r"eyJ[A-Za-z0-9-_=]+\.[A-Za-z0-9-_=]+\.?[A-Za-z0-9-_.+/=]*")


def redact_sensitive_data(val: Any) -> Any:
    """Recursively scrub secrets and token patterns from dicts, lists, and strings."""
    if isinstance(val, dict):
        sanitized: dict[str, Any] = {}
        for k, v in val.items():
            if SECRET_KEY_REGEX.search(k):
                sanitized[k] = "[REDACTED]"
            else:
                sanitized[k] = redact_sensitive_data(v)
        return sanitized
    elif isinstance(val, list):
        return [redact_sensitive_data(item) for item in val]
    elif isinstance(val, str):
        # Scrub Slack tokens
        scrubbed = SLACK_TOKEN_REGEX.sub("[REDACTED_SLACK_TOKEN]", val)
        # Scrub Bearer headers
        scrubbed = BEARER_TOKEN_REGEX.sub("Bearer [REDACTED_TOKEN]", scrubbed)
        # Scrub full JWT strings
        if len(scrubbed) > 40 and JWT_PATTERN_REGEX.search(scrubbed):
            scrubbed = JWT_PATTERN_REGEX.sub("[REDACTED_JWT]", scrubbed)
        return scrubbed
    return val


@runtime_checkable
class SecretsManagerProtocol(Protocol):
    """Protocol for reference-based secrets management."""

    async def store_secret(
        self,
        tenant_id: str,
        scope: str,
        name: str,
        secret_payload: dict[str, Any] | str,
    ) -> str:
        """Store secret payload securely and return reference URI."""
        ...

    async def get_secret(self, secret_ref: str) -> dict[str, Any] | str | None:
        """Retrieve and decrypt secret payload by reference URI."""
        ...

    async def delete_secret(self, secret_ref: str) -> bool:
        """Delete secret payload by reference URI."""
        ...


class EncryptedFileSecretsManager:
    """Zero-external-dependency AES-256-GCM encrypted secrets vault on disk with tenant isolation."""

    def __init__(
        self, storage_dir: str | Path | None = None, master_key: bytes | None = None
    ) -> None:
        settings = get_settings()
        self.storage_dir = Path(storage_dir or "storage/secrets")
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        # Derive 256-bit AES key from JWT_SECRET_KEY or provided master key
        if master_key:
            self._key = master_key[:32].ljust(32, b"\0")
        else:
            raw_key = (settings.JWT_SECRET_KEY or "omnea_default_master_key_32bytes").encode(
                "utf-8"
            )
            self._key = hashlib.sha256(raw_key).digest()
        self._aesgcm = AESGCM(self._key)

    def _ref_to_path(self, secret_ref: str) -> Path:
        """Parse secrets://tenants/{tenant_id}/{scope}/{name} to safe filesystem path."""
        if not secret_ref.startswith("secrets://"):
            raise ValueError(f"Invalid secret reference format: '{secret_ref}'")
        clean_path = secret_ref.replace("secrets://", "").strip("/")
        # Path traversal guard
        parts = clean_path.split("/")
        if ".." in parts or any(p.startswith(".") for p in parts):
            raise ValueError(f"Path traversal detected in secret_ref: {secret_ref}")
        return self.storage_dir.joinpath(*parts).with_suffix(".enc")

    async def store_secret(
        self,
        tenant_id: str,
        scope: str,
        name: str,
        secret_payload: dict[str, Any] | str,
    ) -> str:
        """Encrypt payload with AES-256-GCM and save to isolated vault file."""
        secret_ref = f"secrets://tenants/{tenant_id}/{scope}/{name}"
        target_path = self._ref_to_path(secret_ref)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        raw_str = (
            json.dumps(secret_payload)
            if isinstance(secret_payload, (dict, list))
            else str(secret_payload)
        )
        data = raw_str.encode("utf-8")
        nonce = os.urandom(12)  # 96-bit nonce for AES-GCM
        ciphertext = self._aesgcm.encrypt(nonce, data, None)

        # Write [12-byte nonce] + [ciphertext + 16-byte tag]
        payload = nonce + ciphertext
        if target_path.exists():
            try:
                import stat

                os.chmod(target_path, stat.S_IWRITE | stat.S_IREAD)
            except Exception:
                pass
        target_path.write_bytes(payload)

        try:
            os.chmod(target_path, 0o600)
        except Exception:
            pass

        logger.info("Encrypted secret stored under reference: %s", secret_ref)
        return secret_ref

    async def get_secret(self, secret_ref: str) -> dict[str, Any] | str | None:
        """Retrieve and decrypt secret payload by reference."""
        try:
            target_path = self._ref_to_path(secret_ref)
            if not target_path.exists():
                return None
            raw_payload = target_path.read_bytes()
            if len(raw_payload) < 28:
                return None
            nonce = raw_payload[:12]
            ciphertext = raw_payload[12:]
            decrypted = self._aesgcm.decrypt(nonce, ciphertext, None).decode("utf-8")
            try:
                res = json.loads(decrypted)
                if isinstance(res, dict):
                    return res
                return str(res)
            except Exception:
                return decrypted
        except Exception as exc:
            logger.error("Failed to decrypt secret reference %s: %s", secret_ref, exc)
            return None

    async def delete_secret(self, secret_ref: str) -> bool:
        """Delete encrypted secret."""
        try:
            target_path = self._ref_to_path(secret_ref)
            if target_path.exists():
                target_path.unlink()
                return True
            return False
        except Exception:
            return False


_global_secrets_manager: SecretsManagerProtocol | None = None


def get_secrets_manager() -> SecretsManagerProtocol:
    """Singleton getter for application secrets manager."""
    global _global_secrets_manager
    if _global_secrets_manager is None:
        _global_secrets_manager = EncryptedFileSecretsManager()
    return _global_secrets_manager
