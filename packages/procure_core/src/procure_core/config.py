"""Application configuration powered by pydantic-settings."""

import re
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
    DATABASE_URL: str | None = Field(
        default=None,
        description="Full PostgreSQL connection URL (e.g. from Supabase, Neon, or Vercel Postgres)",
    )
    POSTGRES_URL: str | None = Field(
        default=None,
        description="Alias for DATABASE_URL as commonly injected by Vercel Postgres",
    )
    DB_SERVERLESS_POOL: bool = Field(
        default=False,
        description="Force NullPool for serverless environments (Vercel / AWS Lambda)",
    )

    # Neo4j
    NEO4J_URI: str = Field(default="bolt://localhost:7687")
    NEO4J_USER: str = Field(default="neo4j")
    NEO4J_PASSWORD: str = Field(default="neo4j_password")

    # Temporal
    TEMPORAL_TARGET_HOST: str = Field(default="localhost:7233")
    TEMPORAL_NAMESPACE: str = Field(default="default")

    # Object Storage (S3 / MinIO)
    S3_ENDPOINT_URL: str = Field(default="http://localhost:9000")
    S3_ENDPOINT: str | None = Field(default=None, description="Alias for S3_ENDPOINT_URL")
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
    GEMINI_API_KEY: str | None = Field(default=None, description="Alias for LLM_API_KEY")
    LLM_API_BASE: str | None = Field(default=None)
    LLM_MAX_TOKENS: int = Field(default=1024)
    LLM_TIMEOUT_SECONDS: float = Field(default=45.0)
    LLM_MAX_RETRIES: int = Field(default=3)
    PROMPT_VERSION: str = Field(default="v1")

    # Queue & Storage Policies
    QUEUE_LOCK_TIMEOUT_SECONDS: int = Field(default=300)
    QUEUE_MAX_ATTEMPTS: int = Field(default=5)
    QUEUE_RETENTION_DAYS: int = Field(default=7)
    ATTACHMENT_MAX_SIZE_BYTES: int = Field(default=50 * 1024 * 1024)  # 50 MB

    # Authentication & JWT
    JWT_SECRET_KEY: str = Field(default="test_jwt_secret_key_change_in_production_min32bytes")
    JWT_SECRET: str | None = Field(default=None, description="Alias for JWT_SECRET_KEY")
    CORS_ORIGINS: str | None = Field(default=None, description="Comma-separated allowed origins")
    FRONTEND_URL: str | None = Field(default=None, description="Deployed frontend URL")
    JWT_ALGORITHM: str = Field(default="HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=15)
    REFRESH_TOKEN_EXPIRE_DAYS: int = Field(default=7)

    # Rate Limits & LLM Spend Caps (Public Deployment Protection)
    RATE_LIMIT_GLOBAL_PER_MIN: int = Field(default=1200, description="Global rate limit per minute")
    RATE_LIMIT_TENANT_PER_MIN: int = Field(
        default=300, description="Per-tenant rate limit per minute"
    )
    RATE_LIMIT_IP_PER_MIN: int = Field(default=120, description="Per-IP rate limit per minute")
    LLM_GLOBAL_DAILY_BUDGET_USD: float = Field(
        default=25.00, description="Global daily cap on LLM costs across all tenants"
    )
    LLM_TENANT_DAILY_BUDGET_USD: float = Field(
        default=5.00, description="Per-tenant daily cap on LLM costs"
    )

    # Email Delivery Provider (resend, postmark, ses, fake)
    EMAIL_PROVIDER: str = Field(default="fake")
    RESEND_API_KEY: str = Field(default="")
    POSTMARK_SERVER_TOKEN: str = Field(default="")
    AWS_SES_REGION: str = Field(default="us-east-1")
    EMAIL_FROM_ADDRESS: str = Field(default="noreply@omnea.ai")

    # SSO / OIDC Configuration
    GOOGLE_CLIENT_ID: str = Field(default="")
    GOOGLE_CLIENT_SECRET: str = Field(default="")
    MICROSOFT_CLIENT_ID: str = Field(default="")
    MICROSOFT_CLIENT_SECRET: str = Field(default="")

    # Vercel Services Private Bindings
    WORKERS_URL: str | None = Field(
        default=None,
        description="Private internal URL for workers service injected by Vercel service binding",
    )
    API_URL: str | None = Field(
        default=None,
        description="Private internal URL for api service injected by Vercel service binding",
    )
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
    def effective_llm_api_key(self) -> str:
        """Effective LLM API key, accepting GEMINI_API_KEY as an alias."""
        return self.LLM_API_KEY or self.GEMINI_API_KEY or ""

    @property
    def effective_jwt_secret_key(self) -> str:
        """Effective JWT secret key, accepting JWT_SECRET as an alias."""
        return self.JWT_SECRET or self.JWT_SECRET_KEY

    @property
    def effective_s3_endpoint_url(self) -> str:
        """Effective S3 endpoint URL, accepting S3_ENDPOINT as an alias."""
        return self.S3_ENDPOINT or self.S3_ENDPOINT_URL

    @property
    def async_database_url(self) -> str:
        """SQLAlchemy async database connection string using asyncpg.

        Supports full connection strings from Supabase, Neon, and Vercel Postgres,
        automatically adapting schemas and ssl parameters.
        """
        raw_url = self.DATABASE_URL or self.POSTGRES_URL
        if raw_url:
            url = raw_url.strip()
            if url.startswith("postgres://"):
                url = "postgresql+asyncpg://" + url[len("postgres://") :]
            elif url.startswith("postgresql://"):
                url = "postgresql+asyncpg://" + url[len("postgresql://") :]
            elif not url.startswith("postgresql+asyncpg://"):
                parts = url.split("://", 1)
                if len(parts) == 2:
                    url = f"postgresql+asyncpg://{parts[1]}"
            if "sslmode=" in url:
                url = re.sub(r"([?&])sslmode=([a-zA-Z0-9_-]+)", r"\1ssl=\2", url)
            return url

        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def sync_database_url(self) -> str:
        """SQLAlchemy sync database connection string using psycopg (for Alembic)."""
        raw_url = self.DATABASE_URL or self.POSTGRES_URL
        if raw_url:
            url = raw_url.strip()
            if url.startswith("postgres://"):
                url = "postgresql+psycopg://" + url[len("postgres://") :]
            elif url.startswith("postgresql://"):
                url = "postgresql+psycopg://" + url[len("postgresql://") :]
            elif url.startswith("postgresql+asyncpg://"):
                url = "postgresql+psycopg://" + url[len("postgresql+asyncpg://") :]
            return url

        return (
            f"postgresql+psycopg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached instance of application settings."""
    return Settings()
