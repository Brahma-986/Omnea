"""Unit tests for object storage key generation, size limits, and content-type policies."""

import uuid
from datetime import UTC, datetime

import pytest

from procure_core.config import Settings
from procure_core.storage.base import generate_storage_key


@pytest.mark.unit
def test_generate_storage_key_format() -> None:
    tenant_id = "tenant_enterprise_1"
    file_id = uuid.UUID("12345678-1234-5678-1234-567812345678")
    now = datetime.now(UTC)

    key = generate_storage_key(tenant_id, file_id)

    # Key format must be {tenant}/{yyyy}/{mm}/{uuid}
    expected_prefix = (
        f"{tenant_id}/{now.year:04d}/{now.month:02d}/12345678-1234-5678-1234-567812345678"
    )
    assert key == expected_prefix
    # Must never include dangerous or user-provided filenames
    assert ".pdf" not in key
    assert "invoice.pdf" not in key


@pytest.mark.unit
def test_attachment_policies() -> None:
    settings = Settings()

    # Allowed content types
    assert "application/pdf" in settings.ALLOWED_ATTACHMENT_TYPES
    assert "image/png" in settings.ALLOWED_ATTACHMENT_TYPES
    assert "text/csv" in settings.ALLOWED_ATTACHMENT_TYPES
    assert "application/x-msdownload" not in settings.ALLOWED_ATTACHMENT_TYPES
    assert "application/x-sh" not in settings.ALLOWED_ATTACHMENT_TYPES

    # Size cap at 50 MB
    assert settings.ATTACHMENT_MAX_SIZE_BYTES == 50 * 1024 * 1024
