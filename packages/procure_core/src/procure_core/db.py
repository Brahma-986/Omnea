"""Database engine and session management for PostgreSQL."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from procure_core.config import Settings, get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_async_engine(settings: Settings | None = None) -> AsyncEngine:
    """Initialize or return the cached async SQLAlchemy engine."""
    global _engine
    if _engine is None:
        cfg = settings or get_settings()
        _engine = create_async_engine(
            cfg.async_database_url,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
            echo=(cfg.APP_ENV == "development" and cfg.LOG_LEVEL == "DEBUG"),
        )
    return _engine


def get_async_session_factory(
    settings: Settings | None = None,
) -> async_sessionmaker[AsyncSession]:
    """Initialize or return the cached async session factory."""
    global _session_factory
    if _session_factory is None:
        engine = get_async_engine(settings)
        _session_factory = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False,
        )
    return _session_factory


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for yielding transactional async database sessions."""
    session_factory = get_async_session_factory()
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def get_tenant_session(
    tenant_id: str,
    settings: Settings | None = None,
) -> AsyncGenerator[AsyncSession, None]:
    """Yield a tenant-scoped async database session with session RLS variable set.

    Args:
        tenant_id: Non-empty tenant identifier strictly required for tenant scoping.
        settings: Optional custom Settings instance.

    Yields:
        AsyncSession: Scoped database session with app.tenant_id configured.

    Raises:
        ValueError: If tenant_id is missing, null, or empty whitespace.
    """
    if not tenant_id or not str(tenant_id).strip():
        raise ValueError("tenant_id is strictly required for tenant-scoped database sessions")

    session_factory = get_async_session_factory(settings)
    async with session_factory() as session:
        try:
            await session.execute(
                text("SELECT set_config('app.tenant_id', :t, true);"),
                {"t": str(tenant_id).strip()},
            )
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_async_engine() -> None:
    """Dispose of the connection pool gracefully during shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
