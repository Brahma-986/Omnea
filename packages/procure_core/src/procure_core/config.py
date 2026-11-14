"""Application configuration powered by pydantic-settings."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central settings definition for the procurement platform."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application
    APP_NAME: str = "omnea-procure-os"
    APP_ENV: Literal["development", "staging", "production", "test"] = "development"
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # Multi-tenancy
    DEFAULT_TENANT_ID: str = Field(
        default="tenant_default",
        description="Default tenant identifier",
    )

    # PostgreSQL (App DB)
    POSTGRES_USER: str = Field(default="postgres")
    POSTGRES_PASSWORD: str = Field(default="postgres")
    POSTGRES_HOST: str = Field(default="localhost")
    POSTGRES_PORT: int = Field(default=5432)
    POSTGRES_DB: str = Field(default="procure_os")

    # Neo4j
    NEO4J_URI: str = Field(default="bolt://localhost:7687")
    NEO4J_USER: str = Field(default="neo4j")
    NEO4J_PASSWORD: str = Field(default="neo4j_password")

    # Temporal
    TEMPORAL_TARGET_HOST: str = Field(default="localhost:7233")
    TEMPORAL_NAMESPACE: str = Field(default="default")

    # Object Storage (S3 / MinIO)
    S3_ENDPOINT_URL: str = Field(default="http://localhost:9000")
    S3_ACCESS_KEY_ID: str = Field(default="minioadmin")
    S3_SECRET_ACCESS_KEY: str = Field(default="minioadmin")
    S3_REGION_NAME: str = Field(default="us-east-1")
    S3_BUCKET_ATTACHMENTS: str = Field(default="procure-attachments")
    S3_PRESIGNED_EXPIRATION_SECONDS: int = Field(default=3600)

    # Slack Ingress
    SLACK_SIGNING_SECRET: str = Field(default="test_slack_signing_secret")
    SLACK_BOT_TOKEN: str = Field(default="xoxb-test-token")

    # Feature Flags for Teams and Email Ingress (default False)
    ENABLE_TEAMS_INGRESS: bool = Field(default=False)
    TEAMS_APP_ID: str = Field(default="")
    TEAMS_APP_PASSWORD: str = Field(default="")

    ENABLE_EMAIL_INGRESS: bool = Field(default=False)
    EMAIL_INBOUND_SECRET: str = Field(default="")

    # LLM & Extraction Settings
    LLM_MODEL: str = Field(default="gemini/gemini-1.5-flash")
    LLM_API_KEY: str = Field(default="")
    LLM_API_BASE: str | None = Field(default=None)
    LLM_MAX_TOKENS: int = Field(default=1024)
    LLM_TIMEOUT_SECONDS: float = Field(default=20.0)
    LLM_MAX_RETRIES: int = Field(default=3)
    PROMPT_VERSION: str = Field(default="v1")

    # Queue & Storage Policies
    QUEUE_LOCK_TIMEOUT_SECONDS: int = Field(default=300)
    QUEUE_MAX_ATTEMPTS: int = Field(default=5)
    QUEUE_RETENTION_DAYS: int = Field(default=7)
    ATTACHMENT_MAX_SIZE_BYTES: int = Field(default=50 * 1024 * 1024)  # 50 MB
    ALLOWED_ATTACHMENT_TYPES: list[str] = Field(
        default=[
            "application/pdf",
            "image/png",
            "image/jpeg",
            "image/webp",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.ms-excel",
            "text/csv",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/msword",
            "text/plain",
        ]
    )

    @property
    def async_database_url(self) -> str:
        """SQLAlchemy async database connection string using asyncpg."""
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def sync_database_url(self) -> str:
        """SQLAlchemy sync database connection string using psycopg (for Alembic)."""
        return (
            f"postgresql+psycopg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached instance of application settings."""
    return Settings()
