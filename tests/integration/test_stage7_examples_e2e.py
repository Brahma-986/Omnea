"""End-to-End Acceptance Tests for Stage 7: Examples program execution and Developer Platform UI."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
from playwright.async_api import Browser, async_playwright

from examples.end_to_end_example import run_acceptance_example
from procure_core.db import get_async_session_factory
from procure_core.developer.api_keys import generate_api_key
from procure_core.models.auth import OrganizationModel

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8013
BASE_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"
logger = logging.getLogger("stage7_e2e")


def _wait_for_server(url: str, timeout: float = 25.0) -> bool:
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
    """Start uvicorn server in a subprocess for Stage 7 E2E."""
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
        + str(repo_root / "sdk" / "python")
        + os.pathsep
        + env.get("PYTHONPATH", "")
    )
    env["APP_ENV"] = "test"

    log_path = repo_root / ".pytest_uvicorn_e2e_stage7.log"
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
            "info",
        ],
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )

    try:
        if not _wait_for_server(BASE_URL, timeout=25.0):
            proc.kill()
            log_file.close()
            with open(log_path, encoding="utf-8", errors="replace") as f:
                logs = f.read()
            raise RuntimeError(f"Server failed to start on {BASE_URL}:\n{logs}")
        yield BASE_URL
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_file.close()


@pytest.fixture
async def browser_instance() -> AsyncIterator[Browser]:
    """Launch headless browser for Playwright E2E UI verification."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-web-security"],
        )
        yield browser
        await browser.close()


@pytest.mark.asyncio
async def test_acceptance_examples_program_creates_request_and_verifies_webhook(
    live_server: str,  # noqa: ARG001
) -> None:
    """ACCEPTANCE: examples/ program calls the API with a scoped key, creates a request, and receives a signed webhook."""
    factory = get_async_session_factory()
    tenant_id = f"tenant_example_{uuid.uuid4().hex[:8]}"

    async with factory() as session:
        org = OrganizationModel(id=tenant_id, name="Examples Acceptance Org")
        session.add(org)

        # Scoped key for requests creation and webhooks management
        raw_key, key_model = generate_api_key(
            tenant_id=tenant_id,
            name="Acceptance Example Key",
            scopes=[
                "requests:create",
                "requests:read",
                "webhooks:manage",
                "admin:write",
                "admin:read",
            ],
        )
        session.add(key_model)
        await session.commit()

    # Run the examples/ acceptance program!
    success = run_acceptance_example(api_key=raw_key, base_url=BASE_URL)
    assert success is True, (
        "examples/end_to_end_example.py failed to execute and verify signed webhook!"
    )


@pytest.mark.asyncio
async def test_developer_platform_ui_playwright_e2e(
    live_server: str,  # noqa: ARG001
    browser_instance: Browser,
) -> None:
    """Verify Developer Platform UI: API keys table, create key modal with scopes, webhooks tab, and copy-paste cURL docs."""
    # 1. Create tenant and admin user
    suffix = uuid.uuid4().hex[:6]
    email = f"admin_{suffix}@example.com"
    password = "CorrectHorseBatteryStaple123!"
    org_name = f"Developer Corp {suffix}"

    async with httpx.AsyncClient() as client:
        signup_res = await client.post(
            f"{BASE_URL}/auth/signup",
            json={
                "organization_name": org_name,
                "email": email,
                "password": password,
                "full_name": "Developer Platform Admin",
            },
        )
        assert signup_res.status_code == 201, f"Signup failed: {signup_res.text}"
        data = signup_res.json()
        verify_token = data.get("verification_token")
        if verify_token:
            await client.post(f"{BASE_URL}/auth/verify-email", json={"token": verify_token})

        login_res = await client.post(
            f"{BASE_URL}/auth/login",
            json={"email": email, "password": password},
        )
        assert login_res.status_code == 200, f"Login failed: {login_res.text}"

    page = await browser_instance.new_page()

    # 2. Log in via UI
    await page.goto(f"{BASE_URL}/app")
    await page.wait_for_selector("#login-form", timeout=10000)
    await page.fill("#login-email", email)
    await page.fill("#login-password", password)
    await page.click('#login-form button[type="submit"]')
    await page.wait_for_selector(".app-sidebar", timeout=10000)

    # 3. Navigate to Developer Platform
    await page.click('a[href="#/app/admin/developers"]')
    await page.wait_for_selector(".page-title", timeout=10000)

    title_text = await page.inner_text(".page-title")
    assert "Developer Platform & API" in title_text

    # 4. Verify API Keys Tab & Generate Key Modal
    await page.wait_for_selector("#btn-create-api-key", timeout=5000)
    await page.click("#btn-create-api-key")
    await page.wait_for_selector(".modal-dialog", state="visible", timeout=5000)

    modal_text = await page.inner_text(".modal-dialog")
    assert "Generate Scoped API Key" in modal_text
    assert "Granted Permissions & Scopes" in modal_text

    await page.fill("#new-key-name", "Playwright Test Key")
    await page.click("#btn-submit-key")

    # Verify generated secret display
    await page.wait_for_selector("#raw-key-input", timeout=8000)
    raw_key_val = await page.input_value("#raw-key-input")
    assert raw_key_val.startswith("omnea_live_")

    await page.click("#btn-done-modal")
    await page.wait_for_selector(".modal-backdrop", state="detached", timeout=5000)

    # Verify key row appears in table
    await page.wait_for_selector("#api-keys-table-container .data-table", timeout=5000)
    table_text = await page.inner_text("#api-keys-table-container")
    assert "Playwright Test Key" in table_text
    assert "Active" in table_text

    # 5. Switch to Outbound Webhooks Tab
    await page.click('button[data-tab="webhooks"]')
    await page.wait_for_selector("#btn-add-webhook-endpoint", timeout=5000)
    webhooks_text = await page.inner_text("#dev-tab-content")
    assert "Outbound Webhooks" in webhooks_text
    assert "Delivery Logs" in webhooks_text

    # 6. Switch to cURL & SDK Docs Tab
    await page.click('button[data-tab="curl-docs"]')
    await page.wait_for_selector(".btn-copy-curl", timeout=5000)
    docs_text = await page.inner_text("#dev-tab-content")
    assert "cURL Code Examples" in docs_text
    assert "POST /api/v1/requests" in docs_text
    assert "Idempotency-Key" in docs_text

    await page.close()
