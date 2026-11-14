"""Unit tests for configuration loading and validation."""

import pytest

from procure_core.config import Settings


@pytest.mark.unit
def test_default_settings() -> None:
    settings = Settings()
    assert settings.APP_NAME == "omnea-procure-os"
    assert settings.DEFAULT_TENANT_ID == "tenant_default"
    assert "postgresql+asyncpg://" in settings.async_database_url
    assert "postgresql+psycopg://" in settings.sync_database_url
    assert settings.POSTGRES_DB in settings.async_database_url


@pytest.mark.unit
def test_custom_settings() -> None:
    custom = Settings(
        POSTGRES_USER="custom_user",
        POSTGRES_PASSWORD="custom_password",
        POSTGRES_HOST="custom_host",
        POSTGRES_PORT=5433,
        POSTGRES_DB="custom_db",
        DEFAULT_TENANT_ID="tenant_xyz",
    )
    assert custom.DEFAULT_TENANT_ID == "tenant_xyz"
    assert "custom_user:custom_password@custom_host:5433/custom_db" in custom.async_database_url
    assert "custom_user:custom_password@custom_host:5433/custom_db" in custom.sync_database_url
