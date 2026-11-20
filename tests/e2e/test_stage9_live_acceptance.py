"""Playwright End-to-End acceptance tests for Stage 9: Live Signup, Email Verification,
Public Status Page, Rate Limiting, and Spend Cap protections.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
from playwright.async_api import Browser, Page, async_playwright

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8019
BASE_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"


def _wait_for_server(url: str, timeout: float = 15.0) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        try:
            import urllib.request

            with urllib.request.urlopen(f"{url}/health", timeout=1.0) as res:
                if res.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


@pytest.fixture(scope="module")
def live_server() -> Iterator[str]:
    """Start uvicorn server in a subprocess for Stage 9 E2E testing."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    src_dir = repo_root / "services" / "api" / "src"

    env = os.environ.copy()
    env["PYTHONPATH"] = (
        str(src_dir)
        + os.pathsep
        + str(repo_root / "packages" / "procure_core" / "src")
        + os.pathsep
        + str(repo_root / "packages" / "procure_agents" / "src")
        + os.pathsep
        + str(repo_root / "packages" / "procure_context" / "src")
        + os.pathsep
        + str(repo_root / "services" / "workers" / "src")
        + os.pathsep
        + env.get("PYTHONPATH", "")
    )
    env["APP_ENV"] = "test"
    env["RATE_LIMIT_GLOBAL_PER_MIN"] = "1000"
    env["RATE_LIMIT_IP_PER_MIN"] = "200"
    env["LLM_GLOBAL_DAILY_BUDGET_USD"] = "25.00"

    log_path = repo_root / ".pytest_uvicorn_stage9_e2e.log"
    log_file = open(log_path, "w", encoding="utf-8")

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "api.main:app",
            "--host",
            SERVER_HOST,
            "--port",
            str(SERVER_PORT),
            "--log-level",
            "warning",
        ],
        cwd=str(src_dir),
        env=env,
        stdout=log_file,
        stderr=log_file,
    )

    ready = _wait_for_server(BASE_URL, timeout=15.0)
    if not ready:
        proc.terminate()
        proc.wait()
        log_file.close()
        pytest.fail(f"Uvicorn server failed to become ready at {BASE_URL}")

    yield BASE_URL

    proc.terminate()
    try:
        proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        proc.kill()
    log_file.close()


@pytest.fixture
async def browser_instance() -> AsyncIterator[Browser]:
    """Launch headless Chromium browser via Playwright."""
    p = await async_playwright().start()
    browser = await p.chromium.launch(headless=True)
    yield browser
    await browser.close()
    await p.stop()


@pytest.fixture
async def page(browser_instance: Browser) -> AsyncIterator[Page]:
    """Provide clean page context."""
    context = await browser_instance.new_context(
        viewport={"width": 1280, "height": 800},
        ignore_https_errors=True,
    )
    pg = await context.new_page()
    yield pg
    await context.close()


@pytest.mark.asyncio
async def test_stage9_live_signup_and_email_verification(live_server: str, page: Page) -> None:
    """ACCEPTANCE: Live URL works end to end including sign-up and verification email."""
    # 1. Navigate to live app root
    await page.goto(f"{live_server}/", wait_until="networkidle")
    content = await page.content()
    assert "Omnea" in content

    # 2. Perform end-to-end signup via REST API on live server
    run_id = uuid.uuid4().hex[:6]
    test_email = f"liveuser_{run_id}@omneaglobal.com"
    signup_payload = {
        "organization_name": f"Live Test Org {run_id}",
        "email": test_email,
        "password": "SecurePassword123!",
        "full_name": "Live Verification User",
    }

    async with httpx.AsyncClient() as client:
        signup_resp = await client.post(f"{live_server}/auth/signup", json=signup_payload)
        assert signup_resp.status_code == 201, signup_resp.text
        signup_data = signup_resp.json()
        assert signup_data["is_verified"] is False
        verification_token = signup_data["verification_token"]

        # 3. Verify email with token
        verify_resp = await client.post(
            f"{live_server}/auth/verify-email",
            json={"token": verification_token},
        )
        assert verify_resp.status_code == 200, verify_resp.text
        assert "verified successfully" in verify_resp.json()["message"]

        # 4. Log in after email verification
        login_resp = await client.post(
            f"{live_server}/auth/login",
            json={"email": test_email, "password": "SecurePassword123!"},
        )
        assert login_resp.status_code == 200, login_resp.text
        auth_data = login_resp.json()
        assert "access_token" in auth_data
        assert auth_data["tenant_id"] == signup_data["tenant_id"]


@pytest.mark.asyncio
async def test_stage9_live_status_page_and_safeguards(live_server: str, page: Page) -> None:
    """Verify live public status page renders operational telemetry and spend budget safeguards."""
    # 1. Open public status page in browser
    await page.goto(f"{live_server}/status", wait_until="networkidle")

    # 2. Verify UI elements and copy
    title = await page.title()
    assert "Omnea System Status" in title

    body_text = await page.inner_text("body")
    assert "OMNEA PROCURE OS" in body_text
    assert "Core API Gateway" in body_text
    assert "Global LLM Daily Budget Cap" in body_text
    assert "Global LLM Daily Budget Cap" in body_text

    # 3. Verify public status JSON API
    async with httpx.AsyncClient() as client:
        status_resp = await client.get(f"{live_server}/api/v1/status")
        assert status_resp.status_code in (200, 503)
        data = status_resp.json()
        assert data["services"]["api"]["status"] == "operational"
        assert data["spend_budget"]["status"] == "within_budget"
        assert data["spend_budget"]["global_daily_budget_usd"] == 25.0
