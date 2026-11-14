"""Health check logic for backing services: PostgreSQL, Neo4j, Temporal, and MinIO."""

import asyncio
import time
from datetime import UTC, datetime
from typing import Literal

import aioboto3
from neo4j import AsyncGraphDatabase
from pydantic import BaseModel, Field
from sqlalchemy import text
from temporalio.client import Client

from procure_core.config import Settings, get_settings
from procure_core.db import get_async_engine
from procure_core.log_config import get_logger

logger = get_logger(__name__)


class DependencyStatus(BaseModel):
    """Health status for an individual backing service."""

    name: str
    healthy: bool
    latency_ms: float = Field(ge=0.0)
    error: str | None = None


class HealthResponse(BaseModel):
    """Aggregate health check response."""

    status: Literal["ok", "degraded"]
    timestamp: datetime
    dependencies: dict[str, DependencyStatus]


async def check_postgres(settings: Settings | None = None) -> DependencyStatus:
    """Verify PostgreSQL connectivity and query execution."""
    start = time.perf_counter()

    async def _probe() -> None:
        engine = get_async_engine(settings)
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1;"))

    try:
        await asyncio.wait_for(_probe(), timeout=3.0)
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        return DependencyStatus(name="postgres", healthy=True, latency_ms=latency_ms)
    except Exception as exc:
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.warning(f"Postgres health check failed: {exc}")
        return DependencyStatus(
            name="postgres",
            healthy=False,
            latency_ms=latency_ms,
            error=str(exc),
        )


async def check_neo4j(settings: Settings | None = None) -> DependencyStatus:
    """Verify Neo4j connectivity via bolt protocol."""
    cfg = settings or get_settings()
    start = time.perf_counter()

    async def _probe() -> None:
        driver = AsyncGraphDatabase.driver(
            cfg.NEO4J_URI,
            auth=(cfg.NEO4J_USER, cfg.NEO4J_PASSWORD),
        )
        try:
            await driver.verify_connectivity()
        finally:
            await driver.close()

    try:
        await asyncio.wait_for(_probe(), timeout=3.0)
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        return DependencyStatus(name="neo4j", healthy=True, latency_ms=latency_ms)
    except Exception as exc:
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.warning(f"Neo4j health check failed: {exc}")
        return DependencyStatus(
            name="neo4j",
            healthy=False,
            latency_ms=latency_ms,
            error=str(exc),
        )


async def check_temporal(settings: Settings | None = None) -> DependencyStatus:
    """Verify Temporal Server gRPC connectivity."""
    cfg = settings or get_settings()
    start = time.perf_counter()

    async def _probe() -> None:
        client = await Client.connect(
            cfg.TEMPORAL_TARGET_HOST,
            namespace=cfg.TEMPORAL_NAMESPACE,
        )
        await client.service_client.check_health()

    try:
        await asyncio.wait_for(_probe(), timeout=3.0)
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        return DependencyStatus(name="temporal", healthy=True, latency_ms=latency_ms)
    except Exception as exc:
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.warning(f"Temporal health check failed: {exc}")
        return DependencyStatus(
            name="temporal",
            healthy=False,
            latency_ms=latency_ms,
            error=str(exc),
        )


async def check_minio(settings: Settings | None = None) -> DependencyStatus:
    """Verify MinIO / S3 object storage connectivity."""
    cfg = settings or get_settings()
    start = time.perf_counter()

    async def _probe() -> None:
        session = aioboto3.Session()
        kwargs: dict[str, str] = {
            "service_name": "s3",
            "region_name": cfg.S3_REGION_NAME,
            "aws_access_key_id": cfg.S3_ACCESS_KEY_ID,
            "aws_secret_access_key": cfg.S3_SECRET_ACCESS_KEY,
        }
        if cfg.S3_ENDPOINT_URL:
            kwargs["endpoint_url"] = cfg.S3_ENDPOINT_URL

        async with session.client(**kwargs) as s3:
            await s3.list_buckets()

    try:
        await asyncio.wait_for(_probe(), timeout=3.0)
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        return DependencyStatus(name="minio", healthy=True, latency_ms=latency_ms)
    except Exception as exc:
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.warning(f"MinIO health check failed: {exc}")
        return DependencyStatus(
            name="minio",
            healthy=False,
            latency_ms=latency_ms,
            error=str(exc),
        )


async def check_all_health(settings: Settings | None = None) -> HealthResponse:
    """Perform concurrent health checks across all backing dependencies."""
    cfg = settings or get_settings()

    pg_task = check_postgres(cfg)
    neo_task = check_neo4j(cfg)
    temporal_task = check_temporal(cfg)
    minio_task = check_minio(cfg)

    pg_status, neo_status, temporal_status, minio_status = await asyncio.gather(
        pg_task, neo_task, temporal_task, minio_task
    )

    dependencies = {
        "postgres": pg_status,
        "neo4j": neo_status,
        "temporal": temporal_status,
        "minio": minio_status,
    }

    all_healthy = all(dep.healthy for dep in dependencies.values())
    status: Literal["ok", "degraded"] = "ok" if all_healthy else "degraded"

    return HealthResponse(
        status=status,
        timestamp=datetime.now(UTC),
        dependencies=dependencies,
    )
