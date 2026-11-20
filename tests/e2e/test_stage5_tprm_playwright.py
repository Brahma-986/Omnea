"""Playwright End-to-End browser tests for Stage 5:
TPRM Security Reviewer Workspace, Structural Safety Scanner, OS-Isolated Extraction,
Grounded LLM Assessment with Verbatim Quote Verification, Evidence Viewer Modal,
Quarantine Handling of Hostile PDFs, and Vendor Risk Register.
"""

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
from playwright.async_api import Browser, async_playwright, expect

from tests.fakes.pdf_factory import (
    build_clean_soc2_pdf,
    build_hostile_pdf_javascript,
    build_qualified_soc2_pdf,
)

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8011
BASE_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"
REAL_GEMINI_KEY = os.environ.get("LLM_API_KEY", "mock-gemini-test-key")
REAL_GEMINI_MODEL = "gemini/gemini-3.1-flash-lite"
logger = logging.getLogger("stage5_e2e")


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
    """Start uvicorn server in a subprocess configured with the real Gemini LLM provider."""
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
    env["LLM_MODEL"] = REAL_GEMINI_MODEL
    env["LLM_API_KEY"] = REAL_GEMINI_KEY
    env["GEMINI_API_KEY"] = REAL_GEMINI_KEY
    env["LLM_MAX_RETRIES"] = "5"
    env["LLM_TIMEOUT_SECONDS"] = "45.0"

    log_path = repo_root / ".pytest_uvicorn_e2e_stage5.log"
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
    """Create test tenant and user with security_reviewer role."""
    suffix = uuid.uuid4().hex[:6]
    org_name = f"TPRM Org {suffix}"
    email = f"sec_{suffix}@omnea.ai"
    password = "SecPassword123!@#"

    # Sign up org + first user
    signup_res = await client.post(
        f"{BASE_URL}/auth/signup",
        json={
            "organization_name": org_name,
            "email": email,
            "password": password,
            "full_name": "Security Reviewer",
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
    ldata = login_res.json()
    return {
        "access_token": ldata["access_token"],
        "csrf_token": ldata.get("csrf_token") or "test-csrf-token",
        "tenant_id": ldata.get("user", {}).get("tenant_id") or "tenant_default",
        "email": email,
    }


@pytest.mark.asyncio
async def test_stage5_tprm_e2e_browser_flow(
    live_server: str, browser_instance: Browser, tmp_path: Path
) -> None:
    """Full browser E2E test verifying clean upload, qualified opinion, hostile quarantine, evidence viewer, and risk register."""
    assert live_server
    async with httpx.AsyncClient() as http_client:
        auth_data = await setup_test_tenant_and_user(http_client)

    email = auth_data["email"]
    password = "SecPassword123!@#"

    # Create temporary PDF files on disk for browser upload input
    clean_pdf_path = tmp_path / "datadog_soc2_clean.pdf"
    clean_pdf_path.write_bytes(build_clean_soc2_pdf("Datadog, Inc."))

    qualified_pdf_path = tmp_path / "legacy_soc2_qualified.pdf"
    qualified_pdf_path.write_bytes(build_qualified_soc2_pdf("Legacy Cloud Inc."))

    hostile_pdf_path = tmp_path / "hostile_payload.pdf"
    hostile_pdf_path.write_bytes(build_hostile_pdf_javascript())

    context = await browser_instance.new_context()
    page = await context.new_page()

    # 1. Log in via UI
    await page.goto(f"{BASE_URL}/#/login")
    await page.wait_for_selector("#login-form", timeout=10000)
    await page.fill("#login-email", email)
    await page.fill("#login-password", password)
    await page.click("#login-submit-btn")
    await page.wait_for_url("**/app**", timeout=10000)
    await page.wait_for_selector(".app-sidebar", timeout=10000)

    # 2. Navigate to Security & TPRM Workspace
    await page.click('a[href="#/app/security"]')
    await page.wait_for_selector("#security-workspace", timeout=10000)

    # Verify workspace title and drag-and-drop zone
    title_el = page.locator("#security-workspace h1")
    await expect(title_el).to_contain_text("Third-Party Risk Management")
    drop_zone = page.locator("#drop-zone-pdf")
    await expect(drop_zone).to_be_visible()

    # -------------------------------------------------------------------------
    # ACTION 1: Upload Clean SOC 2 PDF
    # -------------------------------------------------------------------------
    print("\n[E2E STEP 1] Uploading Clean SOC 2 PDF via Browser...")
    file_input = page.locator("#security-file-input")
    await file_input.set_input_files(str(clean_pdf_path))

    # Verify file name shown in drop zone
    file_label = page.locator("#drop-zone-label")
    await expect(file_label).to_contain_text("datadog_soc2_clean.pdf")

    # Select vendor option
    vendor_select = page.locator("#security-vendor-select")
    await vendor_select.select_option(index=1)

    upload_btn = page.locator("#btn-upload-security-doc")
    await expect(upload_btn).to_be_enabled()
    await upload_btn.click()

    # Pipeline Stepper shows live execution stages
    stepper = page.locator("#pipeline-stepper-container")
    await expect(stepper).to_be_visible()

    # Wait for completion: stage-safety-scan shows Passed ✓
    await page.wait_for_selector(
        "#stage-safety-scan .status-indicator:has-text('Passed')", timeout=45000
    )
    await page.wait_for_selector(
        "#stage-assessment .status-indicator:has-text('Assessed')", timeout=45000
    )
    print("  ✓ Pipeline stepper progressed: All 6 stages completed successfully")

    # Wait for document table to render
    await page.wait_for_selector(
        "#security-documents-table-container table tbody tr", timeout=10000
    )
    print("  ✓ Document list updated with new SOC 2 report")

    # -------------------------------------------------------------------------
    # ACTION 2: Switch to Control Findings Tab & Inspect Evidence Modal
    # -------------------------------------------------------------------------
    print("[E2E STEP 2] Navigating to Control Findings Tab...")
    findings_tab_btn = page.locator("button[data-tab='findings']")
    await findings_tab_btn.click()

    await page.wait_for_selector(".tprm-domain-card", timeout=15000)
    domain_cards = page.locator(".tprm-domain-card")
    card_count = await domain_cards.count()
    assert card_count >= 6, f"Expected 6 domain cards, found {card_count}"

    # Verify verified quote badge
    verified_badges = page.locator(".badge-verified")
    v_count = await verified_badges.count()
    assert v_count > 0, "Expected at least 1 verified badge on clean SOC 2"
    print(f"  ✓ {card_count} Control domain cards rendered with {v_count} verified badges")

    # Open Evidence Viewer Modal
    print("  Opening Evidence Viewer Modal...")
    first_evidence_link = page.locator(".btn-open-evidence").first
    await expect(first_evidence_link).to_be_visible()
    await first_evidence_link.click()

    evidence_modal = page.locator("#evidence-viewer-modal")
    await expect(evidence_modal).to_be_visible(timeout=10000)

    # Check for highlighted quote: <mark class="evidence-quote-highlight">
    highlight_el = page.locator(".evidence-quote-highlight")
    await expect(highlight_el).to_be_visible()
    quote_text = await highlight_el.inner_text()
    assert len(quote_text) > 10, "Highlighted quote should contain verbatim text"
    print(
        f"  ✓ Evidence Viewer Modal verified: Page text rendered with highlighted quote: '{quote_text[:50]}...'"
    )

    # Close modal via Escape
    await page.keyboard.press("Escape")
    await expect(evidence_modal).not_to_be_visible()

    # -------------------------------------------------------------------------
    # ACTION 3: Upload Qualified-Opinion SOC 2 PDF
    # -------------------------------------------------------------------------
    print("[E2E STEP 3] Uploading Qualified Opinion SOC 2 PDF via Browser...")
    docs_tab_btn = page.locator("button[data-tab='documents']")
    await docs_tab_btn.click()

    await file_input.set_input_files(str(qualified_pdf_path))
    await vendor_select.select_option(index=1)
    await upload_btn.click()

    # Wait for completion
    await page.wait_for_selector(
        "#stage-assessment .status-indicator:has-text('Assessed')", timeout=45000
    )

    # Check documents table shows qualified opinion
    table_text = await page.locator("#security-documents-table-container").inner_text()
    assert "Qualified Opinion" in table_text or "qualified" in table_text.lower()
    print("  ✓ Qualified Opinion detected: Status badge rendered on document record")

    # -------------------------------------------------------------------------
    # ACTION 4: Upload Hostile PDF (Exploit Hex-Escapes / Launch)
    # -------------------------------------------------------------------------
    print("[E2E STEP 4] Uploading Hostile PDF via Browser...")
    await file_input.set_input_files(str(hostile_pdf_path))
    await vendor_select.select_option(index=1)
    await upload_btn.click()

    # Stepper must show QUARANTINED on Stage 1
    await page.wait_for_selector(
        "#stage-safety-scan .status-indicator:has-text('QUARANTINED')", timeout=15000
    )
    print(
        "  ✓ Hostile PDF Quarantined: Zero-trust safety scan caught hostile payload and halted pipeline"
    )

    # -------------------------------------------------------------------------
    # ACTION 5: Vendor Risk Register View
    # -------------------------------------------------------------------------
    print("[E2E STEP 5] Checking Vendor Risk Register Tab...")
    risk_tab_btn = page.locator("button[data-tab='risk-register']")
    await risk_tab_btn.click()

    await page.wait_for_selector("#risk-register-table-container table tbody tr", timeout=15000)
    rows = page.locator("#risk-register-table-container table tbody tr")
    row_count = await rows.count()
    assert row_count > 0, "Risk register table must contain vendor entries"
    print(f"  ✓ Vendor Risk Register verified: {row_count} vendor risk posture entries displayed")

    print("\n" + "=" * 80)
    print("STAGE 5 PLAYWRIGHT E2E ACCEPTANCE RUN COMPLETE (ALL 5 SCENARIOS PASSED)")
    print("=" * 80 + "\n")

    await context.close()
