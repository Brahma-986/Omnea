"""Integration test verifying end-to-end connectivity via /health."""

import pytest
from httpx import AsyncClient


@pytest.mark.integration
async def test_live_health_endpoint(async_client: AsyncClient) -> None:
    """Verify live connectivity when services are running via docker-compose."""
    response = await async_client.get("/health")

    # In integration test environment with live services, all should be healthy
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["dependencies"]["postgres"]["healthy"] is True
    assert data["dependencies"]["neo4j"]["healthy"] is True
    assert data["dependencies"]["temporal"]["healthy"] is True
