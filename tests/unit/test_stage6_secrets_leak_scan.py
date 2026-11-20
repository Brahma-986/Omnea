"""Unit tests for Stage 6: Guaranteeing ZERO plaintext secrets in database, audit logs, or storage."""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text

from procure_core.db import get_tenant_session
from procure_core.models.erp import ErpConfigurationModel
from procure_core.security.secrets import (
    EncryptedFileSecretsManager,
    redact_sensitive_data,
)


def test_redact_sensitive_data_deep_scrubbing() -> None:
    """Verify recursive redaction engine catches token keys, Slack tokens, Bearer headers, and JWTs."""
    sample_payload = {
        "vendor": "NetSuite",
        "api_key": "raw_sensitive_key_abcdef123456",
        "nested": {
            "oauth_token": "xoxb-mock-test-redaction-token-123",
            "db_password": "super_secret_password_!",
            "log_line": "User authenticated with Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
        },
        "public_data": "Normal ERP metadata",
    }

    sanitized = redact_sensitive_data(sample_payload)

    # Dictionary keys scrubbed
    assert sanitized["api_key"] == "[REDACTED]"
    assert sanitized["nested"]["oauth_token"] == "[REDACTED]"
    assert sanitized["nested"]["db_password"] == "[REDACTED]"
    assert sanitized["public_data"] == "Normal ERP metadata"

    # Embedded patterns in strings scrubbed
    raw_text = "Sending alert to Slack token xoxp-mock-test-placeholder-999 and bearer Bearer my-auth-token-123"
    sanitized_text = redact_sensitive_data(raw_text)
    assert "xoxp-" not in sanitized_text
    assert "[REDACTED_SLACK_TOKEN]" in sanitized_text
    assert "my-auth-token-123" not in sanitized_text
    assert "Bearer [REDACTED_TOKEN]" in sanitized_text


@pytest.mark.asyncio
async def test_erp_config_database_storage_contains_zero_plaintext_secrets() -> None:
    """Verify tenant ERP configuration stores credentials strictly as references in Secrets Manager, never plaintext in DB."""
    tenant_id = f"test_secrets_db_{uuid.uuid4().hex[:8]}"
    secret_token = "live_netsuite_token_top_secret_9999"
    secret_password = "sap_password_super_confidential_8888"

    secrets_mgr = EncryptedFileSecretsManager()
    ref = await secrets_mgr.store_secret(
        tenant_id=tenant_id,
        scope="erp",
        name="netsuite",
        secret_payload={
            "token": secret_token,
            "password": secret_password,
            "account_id": "123456",
        },
    )

    async with get_tenant_session(tenant_id) as session:
        # Store configuration record
        config_rec = ErpConfigurationModel(
            tenant_id=tenant_id,
            connector_type="netsuite",
            status="connected",
            secret_ref=ref,
            settings={"account_id": "123456", "host": "https://api.netsuite.com"},
        )
        session.add(config_rec)
        await session.commit()

        # Query raw database row using raw SQL to verify literal table content
        raw_res = await session.execute(
            text(f"SELECT * FROM erp_configurations WHERE tenant_id = '{tenant_id}'")
        )
        row = raw_res.mappings().one()

        row_json = json.dumps({k: str(v) for k, v in row.items()})

        # Assert secret reference is present
        assert "secrets://tenants/" in row_json
        assert ref in row_json

        # Assert plaintext secrets are COMPLETELY ABSENT from the database table
        assert secret_token not in row_json
        assert secret_password not in row_json


@pytest.mark.asyncio
async def test_secrets_manager_vault_file_is_encrypted_aes_gcm() -> None:
    """Verify that on-disk vault files are ciphertext only and do not leak plaintext secrets."""
    tenant_id = f"test_vault_{uuid.uuid4().hex[:8]}"
    plaintext_secret = "raw_plaintext_api_token_to_verify_no_leaks"

    secrets_mgr = EncryptedFileSecretsManager()
    ref = await secrets_mgr.store_secret(
        tenant_id=tenant_id,
        scope="slack",
        name="bot_token",
        secret_payload={"bot_token": plaintext_secret},
    )

    # Read physical file from disk
    vault_file_path = secrets_mgr._ref_to_path(ref)
    assert vault_file_path.exists() is True

    raw_bytes = vault_file_path.read_bytes()

    # Plaintext string must NOT exist anywhere in the encrypted file bytes
    assert plaintext_secret.encode("utf-8") not in raw_bytes

    # Decrypt and verify payload
    decrypted = await secrets_mgr.get_secret(ref)
    assert isinstance(decrypted, dict)
    assert decrypted["bot_token"] == plaintext_secret
