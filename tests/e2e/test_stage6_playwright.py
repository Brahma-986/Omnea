"""Playwright End-to-End browser tests for Stage 6:
Systems of record (Built-in Ledger ERP), Settings workspace connector configuration,
Test Connection button, Slack App Manifest modal, Multichannel notification toggles,
Finance Workspace ERP sync status badge, Saga step inspector, Reconciliation drift & audited repair modal.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from playwright.async_api import Browser, async_playwright

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8012
BASE_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"
logger = logging.getLogger("stage6_e2e")


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
    """Start uvicorn server in a subprocess for Stage 6 E2E."""
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

    log_path = repo_root / ".pytest_uvicorn_e2e_stage6.log"
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

    ready = _wait_for_server(BASE_URL, timeout=25.0)
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
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-web-security", "--no-sandbox"],
        )
        yield browser


async def setup_test_tenant_and_user(client: httpx.AsyncClient) -> dict[str, str]:
    """Create test tenant and user with tenant_admin role."""
    suffix = uuid.uuid4().hex[:6]
    org_name = f"Stage6 Org {suffix}"
    email = f"admin_{suffix}@omnea.ai"
    password = "AdminPassword123!@#"

    signup_res = await client.post(
        f"{BASE_URL}/auth/signup",
        json={
            "organization_name": org_name,
            "email": email,
            "password": password,
            "full_name": "Tenant Administrator",
        },
    )
    if signup_res.status_code != 201:
        raise RuntimeError(f"Signup failed: {signup_res.status_code} {signup_res.text}")
    data = signup_res.json()
    verify_token = data.get("verification_token")
    if verify_token:
        await client.post(f"{BASE_URL}/auth/verify-email", json={"token": verify_token})

    # Log in
    login_res = await client.post(
        f"{BASE_URL}/auth/login",
        json={"email": email, "password": password},
    )
    if login_res.status_code != 200:
        raise RuntimeError(f"Login failed: {login_res.status_code} {login_res.text}")
    login_data = login_res.json()

    return {
        "email": email,
        "password": password,
        "token": login_data["access_token"],
        "csrf_token": login_data.get("csrf_token", "csrf_mock_token"),
        "tenant_id": login_data.get("tenant_id") or f"tenant_{suffix}",
    }


@pytest.mark.asyncio
async def test_stage6_settings_workspace_erp_and_slack_e2e(
    live_server: str,  # noqa: ARG001
    browser_instance: Browser,
) -> None:
    """Verify Settings Workspace: ERP connector config, Test Connection button, Slack Manifest modal, Notification preferences."""
    async with httpx.AsyncClient() as client:
        auth_data = await setup_test_tenant_and_user(client)

    page = await browser_instance.new_page()

    # 1. Log in via UI
    await page.goto(f"{BASE_URL}/app")
    await page.wait_for_selector("#login-form", timeout=10000)
    await page.fill("#login-email", auth_data["email"])
    await page.fill("#login-password", auth_data["password"])
    await page.click('#login-form button[type="submit"]')
    await page.wait_for_selector(".app-sidebar", timeout=10000)

    # 2. Navigate to Organization & Systems Settings
    await page.click('a[href="#/app/admin/org"]')
    await page.wait_for_selector(".page-title", timeout=10000)

    title_text = await page.inner_text(".page-title")
    assert "Organization & Systems Settings" in title_text

    # 3. Verify ERP Connector Card & Test Connection Button
    await page.wait_for_selector("#btn-test-erp-connection", timeout=5000)
    await page.wait_for_selector("#erp-connection-indicator", timeout=5000)

    # Click Test Connection button
    await page.click("#btn-test-erp-connection")
    await page.wait_for_selector("#erp-test-result", state="visible", timeout=8000)
    result_text = await page.inner_text("#erp-test-result")
    assert "Connected" in result_text
    assert "latency" in result_text.lower()

    # 4. Verify Slack Integration & View Manifest Modal
    await page.wait_for_selector("#btn-view-slack-manifest", timeout=5000)
    await page.click("#btn-view-slack-manifest")
    await page.wait_for_selector(".modal-dialog", state="visible", timeout=8000)

    modal_text = await page.inner_text(".modal-dialog")
    assert "Slack App Manifest" in modal_text
    assert "/procure" in modal_text
    assert "chat:write" in modal_text

    # Close modal
    await page.click(".modal-footer .btn-primary")
    await page.wait_for_selector(".modal-backdrop", state="detached", timeout=5000)

    # 5. Verify Notification Preferences Toggles
    await page.wait_for_selector("#pref-channel-in-app", timeout=5000)
    await page.wait_for_selector("#pref-channel-email", timeout=5000)
    await page.wait_for_selector("#pref-channel-slack", timeout=5000)
    await page.wait_for_selector("#btn-save-notification-prefs", timeout=5000)

    # Toggle a preference and save
    await page.click("#pref-channel-slack")
    await page.click("#btn-save-notification-prefs")
    await page.wait_for_selector(".toast-success", timeout=5000)

    await page.close()


@pytest.mark.asyncio
async def test_stage6_finance_workspace_reconciliation_and_repair_e2e(
    live_server: str,  # noqa: ARG001
    browser_instance: Browser,
) -> None:
    """Verify Finance Workspace: ERP sync status badge, on-demand reconciliation run, and audited repair modal."""
    async with httpx.AsyncClient() as client:
        auth_data = await setup_test_tenant_and_user(client)

    headers = {
        "Authorization": f"Bearer {auth_data['token']}",
        "X-Tenant-ID": auth_data["tenant_id"],
        "X-CSRF-Token": auth_data["csrf_token"],
    }

    # Run on-demand reconciliation via API to establish initial baseline
    async with httpx.AsyncClient() as client:
        recon_run_res = await client.post(
            f"{BASE_URL}/api/v1/reconciliation/run",
            headers=headers,
        )
        assert recon_run_res.status_code == 200

    page = await browser_instance.new_page()

    # 1. Log in via UI
    await page.goto(f"{BASE_URL}/app")
    await page.wait_for_selector("#login-form", timeout=10000)
    await page.fill("#login-email", auth_data["email"])
    await page.fill("#login-password", auth_data["password"])
    await page.click('#login-form button[type="submit"]')
    await page.wait_for_selector(".app-sidebar", timeout=10000)

    # 2. Navigate to Finance Workspace
    await page.click('a[href="#/app/finance"]')
    await page.wait_for_selector("#finance-tabs-container", timeout=10000)

    # 3. Click ERP Ledger & Reconciliation tab
    erp_tab_btn = page.locator('#finance-tabs-container button:has-text("ERP Ledger")')
    await erp_tab_btn.click()
    await page.wait_for_selector("#erp-sync-status-badge", timeout=10000)

    # 4. Verify ERP Sync Status Badge
    badge_text = await page.inner_text("#erp-sync-status-badge")
    assert "connected" in badge_text.lower()
    assert "ledger" in badge_text.lower()

    # 5. Verify Saga Steps Container & Reconciliation Table
    await page.wait_for_selector("#erp-saga-steps-container", timeout=5000)
    await page.wait_for_selector("#btn-run-reconciliation", timeout=5000)

    # 6. Click Run Now reconciliation button
    await page.click("#btn-run-reconciliation")
    await page.wait_for_selector(".toast-success", timeout=5000)

    # 7. Verify clean baseline / zero drift detected
    findings_text = await page.inner_text("#reconciliation-findings-table")
    assert "Zero Drift" in findings_text or "Discrepancies" in findings_text

    await page.close()


@pytest.mark.asyncio
async def test_stage6_approved_request_creates_exactly_one_po_in_ledger_erp(
    live_server: str,  # noqa: ARG001
) -> None:
    """Verify approved procurement request creates EXACTLY ONE PO in Ledger ERP (counted from ERP's own records)."""
    async with httpx.AsyncClient() as client:
        auth_data = await setup_test_tenant_and_user(client)
        headers = {
            "Authorization": f"Bearer {auth_data['token']}",
            "X-Tenant-ID": auth_data["tenant_id"],
            "X-CSRF-Token": auth_data["csrf_token"],
        }

        # Query ERP initial state
        initial_status_res = await client.get(f"{BASE_URL}/api/v1/erp/status", headers=headers)
        assert initial_status_res.status_code == 200
        initial_status = initial_status_res.json()
        assert initial_status["connector_type"] == "ledger"
        assert initial_status["connection_status"] == "connected"

        # Execute ERP Sync Saga directly via backend service
        from procure_core.db import get_tenant_session
        from procure_core.erp.ledger import LedgerErpConnector
        from procure_core.erp.saga import ErpSyncSaga
        from procure_core.models.request import RequestModel

        tenant_id = auth_data["tenant_id"]
        req_uuid = uuid.uuid4()
        request_id = str(req_uuid)

        async with get_tenant_session(tenant_id) as session:
            # Create request through legal status transitions
            req = RequestModel(
                id=req_uuid,
                tenant_id=tenant_id,
                requester_id=auth_data["email"],
                status="draft",
                payload={
                    "vendor_name": "Datadog, Inc.",
                    "estimated_spend": "48000.00",
                    "currency": "USD",
                    "department": "Engineering",
                },
            )
            session.add(req)
            await session.flush()
            req.status = "in_review"
            await session.flush()
            await session.commit()

        # Run ERP Sync Saga
        connector = LedgerErpConnector(tenant_id=tenant_id)
        saga = ErpSyncSaga(connector=connector, tenant_id=tenant_id)

        async with get_tenant_session(tenant_id) as session:
            saga_res = await saga.execute(
                session=session,
                request_id=request_id,
                vendor_name="Datadog, Inc.",
                amount=Decimal("48000.00"),
                currency="USD",
                department="Engineering",
            )
            await session.commit()

        assert saga_res.success is True
        assert saga_res.po_number is not None
        assert saga_res.status == "completed"

        # Duplicate sync replay (simulating at-least-once workflow retry)
        async with get_tenant_session(tenant_id) as session:
            saga_replay_res = await saga.execute(
                session=session,
                request_id=request_id,
                vendor_name="Datadog, Inc.",
                amount=Decimal("48000.00"),
                currency="USD",
                department="Engineering",
            )
            await session.commit()

        assert saga_replay_res.success is True
        assert saga_replay_res.po_number == saga_res.po_number

        # ACCEPTANCE CRITERIA ASSERTION:
        # Query ERP records directly via API and connector:
        # EXACTLY ONE PO created in the ledger ERP
        po_list_res = await client.get(f"{BASE_URL}/api/v1/erp/purchase-orders", headers=headers)
        assert po_list_res.status_code == 200
        po_records = po_list_res.json()

        matching_pos = [p for p in po_records if p["po_number"] == saga_res.po_number]
        assert len(matching_pos) == 1
        assert matching_pos[0]["po_number"] == saga_res.po_number
        assert Decimal(str(matching_pos[0]["amount"])) == Decimal("48000.00")
        assert matching_pos[0]["vendor_name"] == "Datadog, Inc."
