"""Global pytest fixtures."""

import os

os.environ["APP_ENV"] = "test"

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

import procure_core.db as core_db
from api.main import app
from procure_core.config import Settings


@pytest.fixture
def test_settings() -> Settings:
    """Fixture providing isolated test settings."""
    return Settings(
        APP_ENV="test",
        LOG_LEVEL="DEBUG",
        DEFAULT_TENANT_ID="test_tenant",
        POSTGRES_USER=os.getenv("POSTGRES_USER", "postgres"),
        POSTGRES_PASSWORD=os.getenv("POSTGRES_PASSWORD", "postgres"),
        POSTGRES_HOST=os.getenv("POSTGRES_HOST", "localhost"),
        POSTGRES_PORT=int(os.getenv("POSTGRES_PORT", "5432")),
        POSTGRES_DB=os.getenv("POSTGRES_DB", "procure_os"),
        NEO4J_URI=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        NEO4J_USER=os.getenv("NEO4J_USER", "neo4j"),
        NEO4J_PASSWORD=os.getenv("NEO4J_PASSWORD", "neo4j_password"),
        TEMPORAL_TARGET_HOST=os.getenv("TEMPORAL_TARGET_HOST", "localhost:7233"),
        S3_ENDPOINT_URL=os.getenv("S3_ENDPOINT_URL", "http://localhost:9000"),
        S3_ACCESS_KEY_ID=os.getenv("S3_ACCESS_KEY_ID", "minioadmin"),
        S3_SECRET_ACCESS_KEY=os.getenv("S3_SECRET_ACCESS_KEY", "minioadmin"),
        SLACK_SIGNING_SECRET=os.getenv("SLACK_SIGNING_SECRET", "test_slack_signing_secret"),
    )


@pytest.fixture(autouse=True)
async def _setup_test_db(test_settings: Settings) -> AsyncGenerator[None, None]:
    """Ensure each async test uses a fresh NullPool engine tied to its own event loop."""
    engine = create_async_engine(
        test_settings.async_database_url,
        poolclass=NullPool,
    )
    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    old_engine = core_db._engine
    old_factory = core_db._session_factory
    core_db._engine = engine
    core_db._session_factory = session_factory

    yield

    await engine.dispose()
    core_db._engine = old_engine
    core_db._session_factory = old_factory


@pytest.fixture
def session_factory() -> async_sessionmaker[AsyncSession]:
    """Provide an async session factory."""
    return core_db.get_async_session_factory()


@pytest.fixture
async def db_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """Provide an isolated transactional async database session."""
    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP client for testing FastAPI endpoints."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
