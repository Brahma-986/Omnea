"""Stage 9 deployment hardening, public status, rate limiting, and spend cap tests."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import BaseModel

from api.main import create_app
from api.rate_limiting import SlidingWindowRateLimiter
from procure_agents.llm.client import FakeLLMClient
from procure_agents.llm.spend_cap import (
    LLMSpendBudgetExceededError,
    LLMSpendTracker,
)
from procure_core.config import Settings
from procure_core.log_config import tenant_id_ctx


class SimpleResponse(BaseModel):
    message: str = "ok"


@pytest.fixture
def custom_settings() -> Settings:
    return Settings(
        APP_ENV="test",
        RATE_LIMIT_GLOBAL_PER_MIN=100,
        RATE_LIMIT_TENANT_PER_MIN=20,
        RATE_LIMIT_IP_PER_MIN=10,
        LLM_GLOBAL_DAILY_BUDGET_USD=2.00,
        LLM_TENANT_DAILY_BUDGET_USD=0.50,
    )


@pytest.fixture
def spend_tracker(custom_settings: Settings) -> LLMSpendTracker:
    tracker = LLMSpendTracker(custom_settings)
    tracker.reset()
    return tracker


def test_llm_spend_tracker_global_cap_enforcement(spend_tracker: LLMSpendTracker) -> None:
    """Verify that cumulative LLM spend exceeding the global cap blocks subsequent calls."""
    # Within budget
    spend_tracker.check_budget("tenant_1", estimated_cost=0.01)

    # Accumulate spend near ceiling ($2.00)
    spend_tracker.record_spend("tenant_1", 1.50)
    spend_tracker.record_spend("tenant_2", 0.45)

    summary = spend_tracker.get_summary()
    assert summary["global_spent_usd"] == 1.95
    assert summary["status"] == "within_budget"

    # Attempt call that exceeds $2.00 global limit
    with pytest.raises(LLMSpendBudgetExceededError) as exc_info:
        spend_tracker.check_budget("tenant_3", estimated_cost=0.10)
    assert "Global LLM daily spend cap" in str(exc_info.value)


def test_llm_spend_tracker_tenant_cap_enforcement(spend_tracker: LLMSpendTracker) -> None:
    """Verify that per-tenant LLM daily spend caps block only the exceeding tenant."""
    # Tenant 1 incurs $0.48 (near $0.50 limit)
    spend_tracker.record_spend("tenant_alpha", 0.48)

    # Tenant 1 tries to spend another $0.05 -> blocked
    with pytest.raises(LLMSpendBudgetExceededError) as exc_info:
        spend_tracker.check_budget("tenant_alpha", estimated_cost=0.05)
    assert "Tenant 'tenant_alpha' LLM daily spend cap" in str(exc_info.value)

    # Tenant 2 is completely unblocked
    spend_tracker.check_budget("tenant_beta", estimated_cost=0.05)
    spend_tracker.record_spend("tenant_beta", 0.10)

    t2_summary = spend_tracker.get_summary("tenant_beta")
    assert t2_summary["tenant_spent_usd"] == 0.10
    assert t2_summary["status"] == "within_budget"


@pytest.mark.asyncio
async def test_fake_llm_client_spend_cap_integration(custom_settings: Settings) -> None:
    """Verify FakeLLMClient raises LLMSpendBudgetExceededError when budget is exhausted."""
    tracker = LLMSpendTracker(custom_settings)
    tracker.reset()

    client = FakeLLMClient(
        default_response=SimpleResponse(message="generated"),
        spend_tracker=tracker,
        cost_per_call=0.30,  # Tenant limit is $0.50
    )

    token = tenant_id_ctx.set("tenant_test")
    try:
        # First call: $0.30 incurred
        resp1 = await client.call_structured("system", "user 1", SimpleResponse)
        assert resp1.message == "generated"

        # Second call: $0.30 + $0.30 = $0.60 > $0.50 tenant limit -> blocked
        with pytest.raises(LLMSpendBudgetExceededError):
            await client.call_structured("system", "user 2", SimpleResponse)
    finally:
        tenant_id_ctx.reset(token)


def test_sliding_window_rate_limiter_enforcement() -> None:
    """Verify sliding-window rate limiter blocks excessive requests per IP and tenant."""
    settings = Settings(
        RATE_LIMIT_GLOBAL_PER_MIN=50,
        RATE_LIMIT_TENANT_PER_MIN=10,
        RATE_LIMIT_IP_PER_MIN=3,
    )
    limiter = SlidingWindowRateLimiter(settings)
    now = 1000.0

    # 3 requests from IP 192.168.1.5 succeed
    for _ in range(3):
        allowed, _, _ = limiter.check_and_consume("192.168.1.5", "tenant_a", now=now)
        assert allowed is True

    # 4th request from same IP is blocked
    allowed, reason, retry_after = limiter.check_and_consume("192.168.1.5", "tenant_a", now=now)
    assert allowed is False
    assert "Client IP rate limit" in reason
    assert retry_after == 60

    # Different IP succeeds
    allowed, _, _ = limiter.check_and_consume("10.0.0.9", "tenant_a", now=now)
    assert allowed is True

    # After window expiration (61s later), original IP succeeds again
    allowed, _, _ = limiter.check_and_consume("192.168.1.5", "tenant_a", now=now + 61.0)
    assert allowed is True


def test_public_status_endpoint_telemetry() -> None:
    """Verify public status endpoint returns 200/503 operational health and spend metrics."""
    app = create_app()
    client = TestClient(app)

    resp = client.get("/api/v1/status")
    assert resp.status_code in (200, 503)
    data = resp.json()

    assert "overall_status" in data
    assert "services" in data
    assert "api" in data["services"]
    assert "spend_budget" in data
    assert "rate_limits" in data
    assert data["rate_limits"]["global_per_min"] > 0


def test_public_status_html_page_rendering() -> None:
    """Verify public status HTML page renders cleanly with strict security headers."""
    app = create_app()
    client = TestClient(app)

    resp = client.get("/status")
    assert resp.status_code in (200, 503)
    assert "text/html" in resp.headers.get("content-type", "")

    # Security headers verification
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "script-src 'self'" in csp
    assert "unsafe-inline" not in csp.split("script-src")[1].split(";")[0]

    body = resp.text
    assert "OMNEA PROCURE OS" in body
    assert "System Infrastructure" in body
    assert "Public Deployment Safeguards" in body


def test_render_yaml_specification() -> None:
    """Validate render.yaml blueprint structure and required environment bindings."""
    render_file = Path("render.yaml")
    assert render_file.exists(), "render.yaml must exist at repository root"

    content = yaml.safe_load(render_file.read_text(encoding="utf-8"))
    assert "services" in content
    assert "databases" in content

    # Database validation
    db_names = [db["name"] for db in content["databases"]]
    assert "omnea-postgres" in db_names

    # Services validation
    service_names = {s["name"]: s for s in content["services"]}
    assert "omnea-api" in service_names
    assert "omnea-worker" in service_names

    api_svc = service_names["omnea-api"]
    assert api_svc["type"] == "web"
    assert api_svc["healthCheckPath"] == "/health"
    assert "preDeployCommand" in api_svc
    assert "alembic" in api_svc["preDeployCommand"]

    # Verify critical environment variables
    env_keys = {e["key"] for e in api_svc["envVars"]}
    required_keys = {
        "POSTGRES_HOST",
        "TEMPORAL_TARGET_HOST",
        "S3_BUCKET_ATTACHMENTS",
        "EMAIL_PROVIDER",
        "RATE_LIMIT_GLOBAL_PER_MIN",
        "LLM_GLOBAL_DAILY_BUDGET_USD",
        "LLM_TENANT_DAILY_BUDGET_USD",
    }
    assert required_keys.issubset(env_keys)
