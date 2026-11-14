"""Unit tests for health checker models and evaluation logic."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from procure_core.health import DependencyStatus, HealthResponse, check_all_health


@pytest.mark.unit
async def test_check_all_health_all_ok() -> None:
    with (
        patch("procure_core.health.check_postgres", new_callable=AsyncMock) as mock_pg,
        patch("procure_core.health.check_neo4j", new_callable=AsyncMock) as mock_neo,
        patch("procure_core.health.check_temporal", new_callable=AsyncMock) as mock_temporal,
        patch("procure_core.health.check_minio", new_callable=AsyncMock) as mock_minio,
    ):
        mock_pg.return_value = DependencyStatus(name="postgres", healthy=True, latency_ms=1.5)
        mock_neo.return_value = DependencyStatus(name="neo4j", healthy=True, latency_ms=2.1)
        mock_temporal.return_value = DependencyStatus(name="temporal", healthy=True, latency_ms=3.0)
        mock_minio.return_value = DependencyStatus(name="minio", healthy=True, latency_ms=1.2)

        result: HealthResponse = await check_all_health()

        assert result.status == "ok"
        assert result.dependencies["postgres"].healthy is True
        assert result.dependencies["neo4j"].healthy is True
        assert result.dependencies["temporal"].healthy is True
        assert result.dependencies["minio"].healthy is True


@pytest.mark.unit
async def test_check_all_health_degraded() -> None:
    with (
        patch("procure_core.health.check_postgres", new_callable=AsyncMock) as mock_pg,
        patch("procure_core.health.check_neo4j", new_callable=AsyncMock) as mock_neo,
        patch("procure_core.health.check_temporal", new_callable=AsyncMock) as mock_temporal,
        patch("procure_core.health.check_minio", new_callable=AsyncMock) as mock_minio,
    ):
        mock_pg.return_value = DependencyStatus(name="postgres", healthy=True, latency_ms=1.5)
        mock_neo.return_value = DependencyStatus(
            name="neo4j",
            healthy=False,
            latency_ms=2.0,
            error="Connection refused",
        )
        mock_temporal.return_value = DependencyStatus(name="temporal", healthy=True, latency_ms=3.0)
        mock_minio.return_value = DependencyStatus(name="minio", healthy=True, latency_ms=1.2)

        result: HealthResponse = await check_all_health()

        assert result.status == "degraded"
        assert result.dependencies["postgres"].healthy is True
        assert result.dependencies["neo4j"].healthy is False
        assert result.dependencies["neo4j"].error == "Connection refused"


@pytest.mark.unit
async def test_health_route_ok(async_client: AsyncClient) -> None:
    with (
        patch("procure_core.health.check_postgres", new_callable=AsyncMock) as mock_pg,
        patch("procure_core.health.check_neo4j", new_callable=AsyncMock) as mock_neo,
        patch("procure_core.health.check_temporal", new_callable=AsyncMock) as mock_temporal,
        patch("procure_core.health.check_minio", new_callable=AsyncMock) as mock_minio,
    ):
        mock_pg.return_value = DependencyStatus(name="postgres", healthy=True, latency_ms=1.5)
        mock_neo.return_value = DependencyStatus(name="neo4j", healthy=True, latency_ms=2.1)
        mock_temporal.return_value = DependencyStatus(name="temporal", healthy=True, latency_ms=3.0)
        mock_minio.return_value = DependencyStatus(name="minio", healthy=True, latency_ms=1.2)

        resp = await async_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "postgres" in data["dependencies"]
        assert "neo4j" in data["dependencies"]
        assert "temporal" in data["dependencies"]
        assert "minio" in data["dependencies"]


@pytest.mark.unit
async def test_health_route_degraded(async_client: AsyncClient) -> None:
    with (
        patch("procure_core.health.check_postgres", new_callable=AsyncMock) as mock_pg,
        patch("procure_core.health.check_neo4j", new_callable=AsyncMock) as mock_neo,
        patch("procure_core.health.check_temporal", new_callable=AsyncMock) as mock_temporal,
        patch("procure_core.health.check_minio", new_callable=AsyncMock) as mock_minio,
    ):
        mock_pg.return_value = DependencyStatus(
            name="postgres",
            healthy=False,
            latency_ms=10.0,
            error="Timeout",
        )
        mock_neo.return_value = DependencyStatus(name="neo4j", healthy=True, latency_ms=2.1)
        mock_temporal.return_value = DependencyStatus(name="temporal", healthy=True, latency_ms=3.0)
        mock_minio.return_value = DependencyStatus(name="minio", healthy=True, latency_ms=1.2)

        resp = await async_client.get("/health")
        assert resp.status_code == 503
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["dependencies"]["postgres"]["healthy"] is False
