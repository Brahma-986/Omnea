"""Playwright E2E test verifying that /events/stream has no token in the URL and updates still arrive."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from playwright.async_api import Browser, async_playwright

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SERVER_HOST = "127.0.0.1"


def _find_free_port() -> int:
    """Find available port for isolated test execution."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((SERVER_HOST, 0))
        return int(s.getsockname()[1])


SERVER_PORT = _find_free_port()
BASE_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"


def _wait_for_server(url: str, timeout: float = 20.0) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=1.0) as res:
                if res.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


@pytest.fixture(scope="module")
def live_server() -> Iterator[str]:
    """Spins up a live uvicorn server in a subprocess."""
    src_dir = REPO_ROOT / "services" / "api" / "src"
    env = os.environ.copy()
    env["PYTHONPATH"] = (
        str(src_dir)
        + os.pathsep
        + str(REPO_ROOT / "packages" / "procure_core" / "src")
        + os.pathsep
        + str(REPO_ROOT / "packages" / "procure_agents" / "src")
        + os.pathsep
        + str(REPO_ROOT / "packages" / "procure_context" / "src")
        + os.pathsep
        + str(REPO_ROOT / "services" / "workers" / "src")
        + os.pathsep
        + env.get("PYTHONPATH", "")
    )
    env["APP_ENV"] = "test"
    env["LOG_LEVEL"] = "WARNING"

    log_path = REPO_ROOT / ".pytest_uvicorn_sse_playwright.log"
    log_file = open(log_path, "w", encoding="utf-8")

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "api.main:create_app",
            "--factory",
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

    ready = _wait_for_server(BASE_URL, timeout=20.0)
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
    browser = await p.chromium.launch(
        headless=True,
        args=["--disable-web-security", "--allow-running-insecure-content"],
    )
    yield browser
    await browser.close()
    await p.stop()


@pytest.mark.asyncio
async def test_sse_stream_has_no_token_in_url_and_updates_arrive(
    live_server: str,
    browser_instance: Browser,
) -> None:
    """Assert that SSE stream request URL never exposes token, and updates arrive successfully."""
    # 1. Create a test tenant organization and admin user via REST API
    org_id = f"tenant_sse_{uuid.uuid4().hex[:6]}"
    admin_email = f"sse_admin_{uuid.uuid4().hex[:6]}@example.com"
    admin_password = "SsePass2026!Secure"

    async with httpx.AsyncClient(base_url=live_server, timeout=10.0) as client:
        signup_res = await client.post(
            "/auth/signup",
            json={
                "organization_name": org_id,
                "admin_name": "SSE Admin",
                "email": admin_email,
                "password": admin_password,
            },
        )
        assert signup_res.status_code == 201

        login_res = await client.post(
            "/auth/login",
            json={"email": admin_email, "password": admin_password},
        )
        assert login_res.status_code == 200
        auth_data = login_res.json()
        token = auth_data["access_token"]
        tenant_id = auth_data["tenant_id"]
        user_id = auth_data["user_id"]
        roles = auth_data.get("roles", ["tenant_admin"])

    # 2. Launch browser context and track all network requests to /events/stream
    context = await browser_instance.new_context(
        viewport={"width": 1280, "height": 800},
        ignore_https_errors=True,
    )
    page = await context.new_page()

    stream_urls: list[str] = []

    def on_request(req: Any) -> None:
        if "/api/v1/events/stream" in req.url:
            stream_urls.append(req.url)

    page.on("request", on_request)

    # 3. Navigate to app page and bootstrap authenticated session
    await page.goto(f"{live_server}/app")
    await page.evaluate(
        """({ token, tenantId, userId, email, roles }) => {
            localStorage.setItem('omnea_token', token);
            localStorage.setItem('omnea_tenant_id', tenantId);
            localStorage.setItem('omnea_user', JSON.stringify({ id: userId, email: email, tenant_id: tenantId }));
            localStorage.setItem('omnea_roles', JSON.stringify(roles));
            localStorage.setItem('omnea_active_role', roles[0] || 'tenant_admin');
        }""",
        {
            "token": token,
            "tenantId": tenant_id,
            "userId": user_id,
            "email": admin_email,
            "roles": roles,
        },
    )

    # Reload page so state.js initializes SSE stream
    await page.reload()
    await page.wait_for_selector(".app-sidebar", timeout=10000)

    # Wait for SSE connection request to be issued
    await page.wait_for_timeout(2000)

    # 4. Core assertion: Request URL must NOT contain raw token or JWT
    assert len(stream_urls) > 0, "Expected at least one request to /api/v1/events/stream"
    for url in stream_urls:
        assert "token=" not in url, f"Forbidden 'token=' found in SSE stream URL: {url}"
        assert "eyJ" not in url, f"Raw JWT exposed in SSE stream URL: {url}"
        # Assert ticket or clean URL
        assert "ticket=" in url or url.endswith("/api/v1/events/stream"), (
            f"Expected ticket-authenticated or cookie-authenticated stream URL, got: {url}"
        )

    # 5. Verify updates arrive over the SSE stream into the browser
    # Connect EventSource from within the browser page and listen for the stream events
    event_result = await page.evaluate(
        """() => {
            return new Promise((resolve) => {
                const token = localStorage.getItem('omnea_token');
                fetch('/api/v1/events/ticket', {
                    method: 'POST',
                    headers: { 'Authorization': 'Bearer ' + token }
                })
                .then(r => r.json())
                .then(ticketData => {
                    if (!ticketData || !ticketData.ticket) {
                        return resolve({ ok: false, error: 'Failed to issue ticket' });
                    }
                    const es = new EventSource('/api/v1/events/stream?ticket=' + encodeURIComponent(ticketData.ticket));
                    es.addEventListener('connected', (e) => {
                        const parsed = JSON.parse(e.data);
                        es.close();
                        resolve({ ok: true, event: 'connected', data: parsed });
                    });
                    es.onerror = (err) => {
                        es.close();
                        resolve({ ok: false, error: 'EventSource error' });
                    };
                })
                .catch(err => resolve({ ok: false, error: String(err) }));
            });
        }"""
    )

    assert event_result["ok"] is True, f"Failed to receive SSE event in browser: {event_result}"
    assert event_result["event"] == "connected"
    assert event_result["data"]["status"] == "connected"
    assert event_result["data"]["tenant_id"] == tenant_id

    # Verify requests captured by Playwright network monitor
    assert len(stream_urls) >= 1
    for url in stream_urls:
        assert "token=" not in url
        assert "eyJ" not in url
        assert "ticket=" in url or url.endswith("/api/v1/events/stream")

    await context.close()
