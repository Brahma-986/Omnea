"""CI test verifying OpenAPI schema generation and completeness against the code."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from api.main import create_app


@pytest.mark.asyncio
async def test_openapi_schema_endpoint_returns_valid_spec() -> None:
    """Verify /openapi.json returns valid OpenAPI 3.1.0 schema generated from code."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/openapi.json")
        assert res.status_code == 200, f"Failed to get openapi.json: {res.text}"
        data = res.json()

        assert "openapi" in data
        assert "paths" in data
        assert "info" in data
        assert "omnea" in data["info"]["title"].lower()

        paths = data["paths"]

        # Required Stage 7 Developer Platform REST endpoints
        required_endpoints = [
            "/api/v1/requests",
            "/api/v1/requests/{request_id}",
            "/api/v1/requests/{request_id}/decisions",
            "/api/v1/approvals/pending",
            "/api/v1/approvals/{step_id}/decide",
            "/api/v1/vendors",
            "/api/v1/vendors/{id}/relationships",
            "/api/v1/documents",
            "/api/v1/documents/{doc_id}/assessment",
            "/api/v1/erp/status",
            "/api/v1/erp/purchase-orders",
            "/api/v1/reconciliation/findings",
            "/api/v1/reconciliation/run",
            "/api/v1/audit/export",
            "/api/v1/agents/runs",
            "/api/v1/api-keys",
            "/api/v1/webhooks/endpoints",
            "/api/v1/webhooks/deliveries",
        ]

        for ep in required_endpoints:
            assert ep in paths, f"Required REST endpoint '{ep}' missing from OpenAPI spec!"


@pytest.mark.asyncio
async def test_swagger_docs_endpoint_serves_html() -> None:
    """Verify /docs serves interactive Swagger UI."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/docs")
        assert res.status_code == 200
        assert "swagger-ui" in res.text.lower() or "html" in res.headers.get("content-type", "")
