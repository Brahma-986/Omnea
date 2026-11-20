"""Playwright E2E test verifying /docs loads self-hosted Swagger UI with zero CSP violations."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

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
        + str(REPO_ROOT / "sdk" / "python")
        + os.pathsep
        + env.get("PYTHONPATH", "")
    )
    env["APP_ENV"] = "test"
    env["LOG_LEVEL"] = "WARNING"

    log_path = REPO_ROOT / ".pytest_uvicorn_swagger_playwright.log"
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
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    yield browser
    await browser.close()
    await p.stop()


@pytest.mark.asyncio
async def test_swagger_ui_loads_in_browser_with_zero_csp_violations(
    live_server: str,
    browser_instance: Browser,
) -> None:
    """Verifies that /docs opens in a real browser, triggers zero CSP errors, has zero external network requests, and renders Authorize and operation blocks."""
    page = await browser_instance.new_page()

    console_messages: list[str] = []
    csp_violations: list[str] = []
    external_requests: list[str] = []
    all_requests: list[str] = []
    page_errors: list[str] = []

    def on_console(msg: Any) -> None:
        text = msg.text
        # Ignore browser extension noise as requested in the requirements
        if any(
            ign in text
            for ign in ["contentscript.js", "MaxListenersExceededWarning", "ObjectMultiplex"]
        ):
            return
        console_messages.append(f"[{msg.type}] {text}")
        lower = text.lower()
        if "content security policy" in lower or "violates" in lower or "refused to" in lower:
            csp_violations.append(text)

    def on_request(req: Any) -> None:
        url = req.url
        all_requests.append(url)
        # Verify request does not leave the origin (allow data: URIs and same-origin URLs)
        if not url.startswith(live_server) and not url.startswith("data:"):
            external_requests.append(url)

    def on_page_error(exc: Any) -> None:
        err_msg = str(exc)
        if any(
            ign in err_msg
            for ign in ["contentscript.js", "MaxListenersExceededWarning", "ObjectMultiplex"]
        ):
            return
        page_errors.append(err_msg)

    page.on("console", on_console)
    page.on("request", on_request)
    page.on("pageerror", on_page_error)

    # 1. Navigate to /docs
    response = await page.goto(f"{live_server}/docs", wait_until="networkidle", timeout=15000)
    assert response is not None
    assert response.status == 200, f"/docs returned HTTP {response.status}"

    # 2. Wait for Swagger UI root container
    await page.wait_for_selector("#swagger-ui", timeout=10000)

    # 3. Assert Authorize button is rendered
    authorize_btn = await page.wait_for_selector(
        ".auth-wrapper .authorize, button.authorize",
        timeout=15000,
        state="visible",
    )
    assert authorize_btn is not None, "Swagger UI 'Authorize' button failed to render!"
    auth_btn_text = await authorize_btn.inner_text()
    assert "Authorize" in auth_btn_text, f"Unexpected authorize button text: {auth_btn_text}"

    # 4. Assert at least one operation block renders
    opblock = await page.wait_for_selector(".opblock", timeout=15000, state="visible")
    assert opblock is not None, "Swagger UI operation blocks failed to render!"

    # Count rendered API operation blocks
    opblocks = await page.query_selector_all(".opblock")
    assert len(opblocks) >= 5, f"Expected at least 5 rendered API endpoints, found {len(opblocks)}"

    # 5. Output and assert network isolation: NO requests left origin!
    print("\n" + "=" * 60)
    print("NETWORK LOG: Verifying same-origin asset loading for /docs")
    print("=" * 60)
    for r in all_requests:
        print(f"  [SAME-ORIGIN] {r}")
    print("=" * 60)

    assert len(external_requests) == 0, f"Requests left the origin! External: {external_requests}"

    # 6. Assert zero CSP violations
    if csp_violations:
        print("\nCSP Violations captured:")
        for v in csp_violations:
            print(f"  - {v}")
    assert len(csp_violations) == 0, f"CSP violations detected on /docs: {csp_violations}"

    # 7. Assert zero fatal page errors
    assert len(page_errors) == 0, f"Page errors encountered: {page_errors}"

    await page.close()
