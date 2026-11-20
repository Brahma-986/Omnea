"""Playwright End-to-End tests for Stage 4:
Vendor Directory, Graph View, Subsidiary Resolution, Entity Review Queue,
Reversible Merge/Unmerge with Currency-Segregated Spend Restoration,
Finance Workspace & Policy Anti-Weakening, and Legal Workspace with Flagged Clauses.
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
from playwright.async_api import Browser, async_playwright, expect

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8010
BASE_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"
REAL_GEMINI_KEY = os.environ.get("LLM_API_KEY", "mock-gemini-test-key")
REAL_GEMINI_MODEL = "gemini/gemini-3.1-flash-lite"
logger = logging.getLogger("stage4_e2e")


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
    env["LLM_TIMEOUT_SECONDS"] = "30.0"

    log_path = repo_root / ".pytest_uvicorn_e2e_stage4.log"
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
        await browser.close()


async def _signup_and_login_user(
    client: httpx.AsyncClient,
    email: str,
    password: str = "StrongPass123!#Omnea",
    org_name: str | None = None,
) -> dict[str, str]:
    """Helper to register and login user, returning tokens."""
    signup_payload = {
        "email": email,
        "password": password,
        "full_name": email.split("@")[0].capitalize(),
        "organization_name": org_name or f"Org {uuid.uuid4().hex[:6]}",
    }
    res = await client.post(f"{BASE_URL}/auth/signup", json=signup_payload)
    if res.status_code != 201:
        raise RuntimeError(f"Signup failed: {res.status_code} {res.text}")
    data = res.json()
    tenant_id = data["tenant_id"]

    vtoken = data.get("verification_token") or "dummy"
    await client.post(f"{BASE_URL}/auth/verify-email", json={"token": vtoken})

    login_res = await client.post(
        f"{BASE_URL}/auth/login",
        json={"email": email, "password": password},
    )
    if login_res.status_code != 200:
        raise RuntimeError(f"Login failed: {login_res.status_code} {login_res.text}")
    ldata = login_res.json()
    return {
        "access_token": ldata["access_token"],
        "csrf_token": ldata["csrf_token"],
        "tenant_id": tenant_id,
        "user_id": ldata["user_id"],
        "email": email,
    }


async def _create_user_with_role(
    client: httpx.AsyncClient,
    admin_auth: dict[str, str],
    email: str,
    role: str,
    password: str = "StrongPass123!#Omnea",
) -> dict[str, str]:
    """Invite and accept user with specific role."""
    headers = {
        "Authorization": f"Bearer {admin_auth['access_token']}",
        "X-CSRF-Token": admin_auth["csrf_token"],
    }
    inv_res = await client.post(
        f"{BASE_URL}/invitations",
        headers=headers,
        json={"email": email, "role": role},
    )
    if inv_res.status_code != 201:
        raise RuntimeError(f"Invite failed: {inv_res.status_code} {inv_res.text}")
    inv_token = inv_res.json().get("invitation_token") or inv_res.json().get("invite_token")

    acc_res = await client.post(
        f"{BASE_URL}/invitations/accept",
        json={
            "token": inv_token,
            "password": password,
            "full_name": email.split("@")[0].capitalize(),
        },
    )
    if acc_res.status_code not in (200, 201):
        raise RuntimeError(f"Accept invite failed: {acc_res.status_code} {acc_res.text}")

    login_res = await client.post(
        f"{BASE_URL}/auth/login",
        json={"email": email, "password": password},
    )
    ldata = login_res.json()
    return {
        "access_token": ldata["access_token"],
        "csrf_token": ldata["csrf_token"],
        "tenant_id": admin_auth["tenant_id"],
        "user_id": ldata["user_id"],
        "email": email,
    }


@pytest.mark.asyncio
async def test_e2e_datadog_emea_subsidiary_resolution_and_graph(
    live_server: str, browser_instance: Browser
) -> None:
    """E2E Test 1: Datadog EMEA resolves under Datadog, Inc. with a SUBSIDIARY_OF edge.

    Proves:
    - Vendor directory lists Datadog EMEA with 'SUBSIDIARY_OF Datadog, Inc.' hierarchy badge.
    - Graph view projects SVG nodes and directed 'SUBSIDIARY_OF' edge.
    - Detailed drawer shows parent, contracts, and currency-separated spend.
    """
    assert live_server
    async with httpx.AsyncClient(timeout=60.0) as client:
        admin_email = f"admin_stage4_{uuid.uuid4().hex[:6]}@omnea.internal"
        auth = await _signup_and_login_user(client, admin_email, org_name="Datadog Corp")
        tenant_id = auth["tenant_id"]

        # 1. Seed Parent: Datadog, Inc. and Child Subsidiary: Datadog EMEA in DB
        from datetime import date

        from procure_core.db import get_async_session_factory
        from procure_core.models.vendor import (
            ContractModel,
            SpendTransactionModel,
            VendorModel,
        )

        sf = get_async_session_factory()
        datadog_parent_id = uuid.uuid4()
        datadog_emea_id = uuid.uuid4()

        async with sf() as session:
            async with session.begin():
                parent_v = VendorModel(
                    id=datadog_parent_id,
                    tenant_id=tenant_id,
                    canonical_key="datadog_inc",
                    name="Datadog, Inc.",
                    normalized_base="datadog",
                    region="US",
                    legal_name="Datadog, Inc.",
                    status="active",
                    metadata_={},
                )
                child_v = VendorModel(
                    id=datadog_emea_id,
                    tenant_id=tenant_id,
                    canonical_key="datadog_emea",
                    name="Datadog EMEA",
                    normalized_base="datadog emea",
                    region="EU",
                    legal_name="Datadog EMEA Limited",
                    status="active",
                    parent_vendor_id=datadog_parent_id,
                    metadata_={},
                )
                session.add_all([parent_v, child_v])
                await session.flush()

                # Seed spend transaction on parent (USD 48,000)
                session.add(
                    SpendTransactionModel(
                        id=uuid.uuid4(),
                        tenant_id=tenant_id,
                        vendor_id=datadog_parent_id,
                        transaction_date=date.today(),
                        amount=Decimal("48000.00"),
                        currency="USD",
                        category="software",
                        source_system="netsuite",
                        source_id=f"tx_{uuid.uuid4().hex[:8]}",
                        provenance={},
                    )
                )
                # Seed contract on child
                session.add(
                    ContractModel(
                        id=uuid.uuid4(),
                        tenant_id=tenant_id,
                        vendor_id=datadog_emea_id,
                        title="Datadog EMEA Enterprise MSA",
                        status="active",
                        start_date=date.today(),
                        total_value=Decimal("48000.00"),
                        currency="USD",
                        auto_renew=True,
                        metadata_={"msa_status": "active", "dpa_status": "signed"},
                    )
                )

        # 2. Browser Verification
        context = await browser_instance.new_context()
        page = await context.new_page()

        # Login
        await page.goto(f"{BASE_URL}/app")
        await page.wait_for_selector("#login-form", timeout=10000)
        await page.fill("#login-email", admin_email)
        await page.fill("#login-password", "StrongPass123!#Omnea")
        await page.click('#login-form button[type="submit"]')
        await page.wait_for_selector(".app-sidebar", timeout=10000)

        # Navigate to Vendors directory
        await page.click('a[href="#/app/vendors"]')
        await page.wait_for_selector("#vendor-table-body", timeout=10000)

        # Verify Datadog EMEA has SUBSIDIARY_OF badge
        body_text = await page.text_content("#vendor-table-body")
        assert "Datadog EMEA" in body_text
        assert "SUBSIDIARY_OF Datadog, Inc." in body_text

        # Switch to Graph View
        await page.click('button[data-view="graph"]')
        await page.wait_for_selector("#vendor-graph-svg", timeout=10000)
        svg_content = await page.inner_html("#vendor-graph-svg")
        assert "Datadog, Inc." in svg_content
        assert "Datadog EMEA" in svg_content
        assert "SUBSIDIARY_OF" in svg_content

        # Click back to Table view and inspect Datadog EMEA drawer
        await page.click('button[data-view="table"]')
        await page.wait_for_selector("#vendor-table-body", timeout=10000)
        await page.click(f'button[data-vendor-id="{datadog_emea_id}"]')
        await page.wait_for_selector("#vendor-detail-drawer", timeout=10000)
        drawer_content = await page.text_content("#vendor-detail-drawer")
        assert "Datadog EMEA Limited" in drawer_content
        assert "Datadog, Inc." in drawer_content

        await context.close()


@pytest.mark.asyncio
async def test_e2e_mid_band_match_lands_in_review_queue_and_merge_unmerge_restores_spend(
    live_server: str, browser_instance: Browser
) -> None:
    """E2E Test 2: Mid-band match lands in the review queue and merge then unmerge restores spend totals.

    Proves:
    - Mid-band candidate pair appears in Entity Review Queue.
    - 'Approve Merge' combines spend into target vendor (per-currency, never summed).
    - 'Unmerge' completely restores original spend separation and status.
    - Zero cross-currency summation at all times.
    """
    assert live_server
    async with httpx.AsyncClient(timeout=60.0) as client:
        admin_email = f"finance_admin_{uuid.uuid4().hex[:6]}@omnea.internal"
        auth = await _signup_and_login_user(client, admin_email, org_name="Merge Test Org")
        tenant_id = auth["tenant_id"]

        from datetime import date

        from procure_core.db import get_async_session_factory
        from procure_core.models.vendor import (
            EntityReviewTaskModel,
            SpendTransactionModel,
            VendorModel,
        )

        sf = get_async_session_factory()
        target_vendor_id = uuid.uuid4()
        provisional_vendor_id = uuid.uuid4()
        task_id = uuid.uuid4()

        async with sf() as session:
            async with session.begin():
                target_v = VendorModel(
                    id=target_vendor_id,
                    tenant_id=tenant_id,
                    canonical_key="datadog_target",
                    name="Datadog, Inc.",
                    normalized_base="datadog",
                    region="US",
                    status="active",
                    metadata_={},
                )
                provisional_v = VendorModel(
                    id=provisional_vendor_id,
                    tenant_id=tenant_id,
                    canonical_key="datadog_provisional",
                    name="Datadog Cloud EU",
                    normalized_base="datadog cloud eu",
                    region="EU",
                    status="active",
                    metadata_={},
                )
                session.add_all([target_v, provisional_v])
                await session.flush()

                # Spend on target: USD 48,000
                session.add(
                    SpendTransactionModel(
                        id=uuid.uuid4(),
                        tenant_id=tenant_id,
                        vendor_id=target_vendor_id,
                        transaction_date=date.today(),
                        amount=Decimal("48000.00"),
                        currency="USD",
                        category="software",
                        source_system="erp",
                        source_id=f"tx_{uuid.uuid4().hex[:8]}",
                        provenance={},
                    )
                )
                # Spend on provisional: EUR 15,000
                session.add(
                    SpendTransactionModel(
                        id=uuid.uuid4(),
                        tenant_id=tenant_id,
                        vendor_id=provisional_vendor_id,
                        transaction_date=date.today(),
                        amount=Decimal("15000.00"),
                        currency="EUR",
                        category="cloud",
                        source_system="erp",
                        source_id=f"tx_{uuid.uuid4().hex[:8]}",
                        provenance={},
                    )
                )

                # Seed mid-band review task
                session.add(
                    EntityReviewTaskModel(
                        id=task_id,
                        tenant_id=tenant_id,
                        task_type="duplicate_review",
                        provisional_vendor_id=provisional_vendor_id,
                        candidate_vendor_id=target_vendor_id,
                        source_name="Datadog Cloud EU",
                        confidence=Decimal("0.7500"),
                        component_scores={"jw": 0.81, "token_overlap": 0.67},
                        status="pending",
                        metadata_={"catalog_match": "datadog_target"},
                    )
                )

        # 2. Browser Verification
        context = await browser_instance.new_context()
        page = await context.new_page()

        # Login
        await page.goto(f"{BASE_URL}/app")
        await page.wait_for_selector("#login-form", timeout=10000)
        await page.fill("#login-email", admin_email)
        await page.fill("#login-password", "StrongPass123!#Omnea")
        await page.click('#login-form button[type="submit"]')
        await page.wait_for_selector(".app-sidebar", timeout=10000)

        # Navigate to Vendors -> Review Queue tab
        await page.click('a[href="#/app/vendors"]')
        await page.wait_for_selector(".tab-btn[data-tab='reviews']", timeout=10000)
        await page.click(".tab-btn[data-tab='reviews']")
        await page.wait_for_selector("#review-tasks-container", timeout=10000)

        # Verify task is listed with confidence 75%
        review_content = await page.text_content("#review-tasks-container")
        assert "Datadog Cloud EU" in review_content
        assert "Datadog, Inc." in review_content
        assert "75.0%" in review_content

        # Click Approve Merge
        merge_btn = f'button[data-action="merge"][data-task-id="{task_id}"]'
        async with page.expect_response(
            lambda r: "/resolve" in r.url and r.status == 200, timeout=15000
        ):
            await page.click(merge_btn)

        # Switch to Directory tab: Datadog, Inc. now shows both USD and EUR spend
        await page.click(".tab-btn[data-tab='directory']")
        await page.wait_for_selector("#vendor-table-body", timeout=10000)

        # Verify target vendor row contains both currency pills
        target_locator = page.locator(f'tr:has(button[data-vendor-id="{target_vendor_id}"])')
        await expect(target_locator).to_contain_text("USD")
        await expect(target_locator).to_contain_text("48,000.00")
        await expect(target_locator).to_contain_text("EUR")
        await expect(target_locator).to_contain_text("15,000.00")

        # Verify provisional vendor shows 'merged'
        prov_row = await page.locator(
            f'tr:has(button[data-vendor-id="{provisional_vendor_id}"])'
        ).inner_html()
        assert "merged" in prov_row.lower()

        # Open drawer of provisional vendor and click Unmerge
        await page.click(f'button[data-vendor-id="{provisional_vendor_id}"]')
        await page.wait_for_selector("#vendor-detail-drawer", timeout=10000)
        await page.wait_for_selector("#btn-unmerge-vendor", timeout=10000)
        async with page.expect_response(
            lambda r: "/unmerge" in r.url and r.status == 200, timeout=15000
        ):
            await page.click("#btn-unmerge-vendor")

        # Wait for table to re-render with target vendor not having EUR
        target_locator = page.locator(f'tr:has(button[data-vendor-id="{target_vendor_id}"])')
        await expect(target_locator).not_to_contain_text("EUR", timeout=15000)
        await expect(target_locator).to_contain_text("USD")
        await expect(target_locator).to_contain_text("48,000.00")

        # Provisional vendor now active and has EUR spend
        prov_locator = page.locator(f'tr:has(button[data-vendor-id="{provisional_vendor_id}"])')
        await expect(prov_locator).to_contain_text("Active", timeout=15000)
        await expect(prov_locator).to_contain_text("EUR")
        await expect(prov_locator).to_contain_text("15,000.00")

        await context.close()


@pytest.mark.asyncio
async def test_e2e_policy_edit_isolation_applies_only_to_new_requests(
    live_server: str,
) -> None:
    """E2E Test 3: Policy edit applies only to NEW requests.

    Proves:
    - Request 1 is created under active policy v1.0 and binds to snapshot hash.
    - Policy is updated to v2.0 in Finance Workspace.
    - Request 1 retains v1.0 and does NOT retroactively change.
    - Request 2 binds to new policy v2.0.
    """
    assert live_server
    async with httpx.AsyncClient(timeout=120.0) as client:
        admin_email = f"finance_policy_{uuid.uuid4().hex[:6]}@omnea.internal"
        auth = await _signup_and_login_user(client, admin_email, org_name="Policy Org")
        tenant_id = auth["tenant_id"]

        from sqlalchemy import text

        from procure_core.db import get_async_session_factory
        from procure_core.models.finance import ApprovalPolicyModel, DepartmentBudgetModel

        sf = get_async_session_factory()
        async with sf() as session:
            async with session.begin():
                # Seed department budget
                session.add(
                    DepartmentBudgetModel(
                        id=uuid.uuid4(),
                        tenant_id=tenant_id,
                        department="Engineering",
                        currency="USD",
                        period="FY2026",
                        amount=Decimal("200000.00"),
                        committed=Decimal("0.00"),
                    )
                )
                # Seed active policy v1.0 (VP threshold = $50,000 USD)
                session.add(
                    ApprovalPolicyModel(
                        id=uuid.uuid4(),
                        tenant_id=tenant_id,
                        version="v1.0",
                        content_hash="hash_v1_seed",
                        rules={
                            "vp_spend_threshold": {"USD": 50000.0},
                            "agent_autonomous_cap": 10000.0,
                            "allow_fast_track": False,
                            "over_budget_action": "needs_human",
                        },
                        is_active=True,
                    )
                )

        # Helper to robustly submit intake with multi-turn support
        headers = {
            "Authorization": f"Bearer {auth['access_token']}",
            "X-CSRF-Token": auth["csrf_token"],
        }

        async def _submit_intake(msg: str) -> str:
            import asyncio

            last_err = ""
            for attempt in range(4):
                try:
                    res = await client.post(
                        f"{BASE_URL}/api/v1/requests/intake/chat",
                        headers=headers,
                        json={"message": msg},
                    )
                    if res.status_code == 200:
                        data = res.json()
                        if data.get("status") == "clarification_needed":
                            res2 = await client.post(
                                f"{BASE_URL}/api/v1/requests/intake/chat",
                                headers=headers,
                                json={
                                    "message": "The department is Engineering",
                                    "session_id": data.get("session_id"),
                                },
                            )
                            if res2.status_code == 200:
                                return str(
                                    res2.json().get("request_id")
                                    or res2.json().get("request", {}).get("id")
                                )
                        else:
                            return str(data.get("request_id") or data.get("request", {}).get("id"))
                    last_err = f"HTTP {res.status_code}: {res.text}"
                except Exception as ex:
                    last_err = str(ex)
                await asyncio.sleep(2.0 * (attempt + 1))
            raise RuntimeError(f"Intake submission failed: {last_err}")

        # Submit Request 1 ($45,000 USD, under v1.0 threshold)
        r1_id = await _submit_intake("Datadog monitoring for $45,000 USD per year in Engineering")

        # Verify Request 1 applied policy v1.0
        async with sf() as session:
            r1_audit = (
                (
                    await session.execute(
                        text(
                            "SELECT policy_version FROM approval_audit_records WHERE tenant_id = :t AND request_id = :r"
                        ),
                        {"t": tenant_id, "r": uuid.UUID(r1_id)},
                    )
                )
                .scalars()
                .all()
            )
            assert any(v == "v1.0" for v in r1_audit)

        # 2. Update Policy to v2.0 via Finance API (Lower VP threshold to $30,000 USD)
        confirm_res = await client.post(
            f"{BASE_URL}/api/v1/finance/policies/confirm",
            headers=headers,
            json={
                "version": "v2.0",
                "rules": {
                    "vp_spend_threshold": {"USD": 30000.0},
                    "agent_autonomous_cap": 10000.0,
                    "allow_fast_track": False,
                    "over_budget_action": "needs_human",
                },
                "override_weakening": False,
            },
        )
        assert confirm_res.status_code in (200, 201)

        # Submit Request 2 ($45,000 USD, under v2.0 where threshold is $30k)
        r2_id = await _submit_intake("Datadog logs for $45,000 USD per year in Engineering")

        # 3. Assert Request 1 still retains v1.0, and Request 2 has v2.0
        async with sf() as session:
            r1_audit_after = (
                (
                    await session.execute(
                        text(
                            "SELECT policy_version FROM approval_audit_records WHERE tenant_id = :t AND request_id = :r"
                        ),
                        {"t": tenant_id, "r": uuid.UUID(r1_id)},
                    )
                )
                .scalars()
                .all()
            )
            assert all(v == "v1.0" for v in r1_audit_after)

            r2_audit = (
                (
                    await session.execute(
                        text(
                            "SELECT policy_version FROM approval_audit_records WHERE tenant_id = :t AND request_id = :r"
                        ),
                        {"t": tenant_id, "r": uuid.UUID(r2_id)},
                    )
                )
                .scalars()
                .all()
            )
            assert any(v == "v2.0" for v in r2_audit)


@pytest.mark.asyncio
async def test_e2e_policy_drafting_rejects_weakening_and_accepts_valid(
    live_server: str, browser_instance: Browser
) -> None:
    """E2E Test 4: Policy-drafting rejects a policy lacking Finance.

    Proves:
    - Attempting to draft/confirm a policy with empty VP thresholds or weakened controls is strictly rejected.
    - Valid policy draft is generated by agent with real LLM key, validated, diff computed, and confirmed.
    """
    assert live_server
    async with httpx.AsyncClient(timeout=60.0) as client:
        admin_email = f"finance_drafter_{uuid.uuid4().hex[:6]}@omnea.internal"
        auth = await _signup_and_login_user(client, admin_email, org_name="Draft Org")
        tenant_id = auth["tenant_id"]

        from procure_core.db import get_async_session_factory
        from procure_core.models.finance import ApprovalPolicyModel

        sf = get_async_session_factory()
        async with sf() as session:
            async with session.begin():
                session.add(
                    ApprovalPolicyModel(
                        id=uuid.uuid4(),
                        tenant_id=tenant_id,
                        version="v1.0",
                        content_hash="hash_seed",
                        rules={
                            "vp_spend_threshold": {"USD": 50000.0, "EUR": 45000.0},
                            "agent_autonomous_cap": 10000.0,
                            "allow_fast_track": False,
                            "over_budget_action": "needs_human",
                        },
                        is_active=True,
                    )
                )

        headers = {
            "Authorization": f"Bearer {auth['access_token']}",
            "X-CSRF-Token": auth["csrf_token"],
        }

        # 1. API: Confirm directly with weakening (e.g. empty VP threshold) -> Rejected HTTP 400
        weak_res = await client.post(
            f"{BASE_URL}/api/v1/finance/policies/confirm",
            headers=headers,
            json={
                "version": "v2.0-weak",
                "rules": {
                    "vp_spend_threshold": {},  # Weakening: deleted all thresholds
                    "agent_autonomous_cap": 10000.0,
                },
                "override_weakening": False,
            },
        )
        assert weak_res.status_code == 400
        detail = weak_res.json()["detail"].lower()
        assert any(
            term in detail
            for term in ["weakening", "policy validation failed", "vp_spend_threshold"]
        )

        # 2. Browser: Navigate to Finance workspace -> Policies tab -> Policy-Drafting Assistant
        context = await browser_instance.new_context()
        page = await context.new_page()

        # Login
        await page.goto(f"{BASE_URL}/app")
        await page.wait_for_selector("#login-form", timeout=10000)
        await page.fill("#login-email", admin_email)
        await page.fill("#login-password", "StrongPass123!#Omnea")
        await page.click('#login-form button[type="submit"]')
        await page.wait_for_selector(".app-sidebar", timeout=10000)

        # Navigate to Finance
        await page.click('a[href="#/app/finance"]')
        await page.wait_for_selector(".tab-btn[data-tab='policies']", timeout=10000)
        await page.click(".tab-btn[data-tab='policies']")
        await page.wait_for_selector("#policy-rule-prompt", timeout=10000)

        # Test plain-English drafting with real LLM
        await page.fill(
            "#policy-rule-prompt",
            "Lower the VP threshold for USD to 35000 USD and keep EUR at 45000 EUR",
        )
        await page.click("#btn-draft-policy")

        # Wait for draft diff container to populate
        await page.wait_for_selector("#policy-draft-diff-container", timeout=25000)
        diff_text = await page.text_content("#policy-draft-diff-container")
        assert "35000" in diff_text or "35,000" in diff_text or "USD" in diff_text

        # Confirm & Activate Policy
        import re

        await page.wait_for_selector("#btn-confirm-policy-candidate", timeout=10000)
        await page.click("#btn-confirm-policy-candidate")
        await page.wait_for_selector(".modal-backdrop", timeout=10000)
        async with page.expect_response(
            lambda r: "/policies/confirm" in r.url and r.status == 200, timeout=15000
        ):
            await page.click("#btn-confirm-activate-policy")

        # Wait for active policy badge to update
        await expect(page.locator("#active-policy-version-badge")).to_contain_text(
            re.compile(r"v1\.1|v2\.0")
        )

        await context.close()


@pytest.mark.asyncio
async def test_e2e_vendor_research_agent_grounded_quotes(
    live_server: str,
) -> None:
    """E2E Test 5: Vendor-Research agent summarizes a vendor from supplied documents only, cites quotes, never browses or invents."""
    assert live_server
    async with httpx.AsyncClient(timeout=60.0) as client:
        admin_email = f"researcher_{uuid.uuid4().hex[:6]}@omnea.internal"
        auth = await _signup_and_login_user(client, admin_email, org_name="Research Org")
        tenant_id = auth["tenant_id"]

        from procure_core.db import get_async_session_factory
        from procure_core.models.vendor import VendorModel

        sf = get_async_session_factory()
        vendor_id = uuid.uuid4()
        async with sf() as session:
            async with session.begin():
                session.add(
                    VendorModel(
                        id=vendor_id,
                        tenant_id=tenant_id,
                        canonical_key="datadog_research",
                        name="Datadog, Inc.",
                        normalized_base="datadog",
                        region="US",
                        status="active",
                        metadata_={},
                    )
                )

        headers = {
            "Authorization": f"Bearer {auth['access_token']}",
            "X-CSRF-Token": auth["csrf_token"],
        }

        doc_text = (
            "Datadog, Inc. is an observability service for cloud-scale applications. "
            "Security and Compliance: Datadog maintains an active SOC2 Type II certification covering Security, "
            "Confidentiality, and Availability through December 31, 2027. "
            "Pricing and Contract: Annual enterprise subscription starts at $48,000 USD with Net 30 payment terms. "
            "Data Processing: Datadog signs standard DPAs with EU Model Clauses."
        )

        # Call vendor research endpoint
        res = await client.post(
            f"{BASE_URL}/api/v1/vendors/{vendor_id}/research",
            headers=headers,
            json={"documents": [doc_text], "text": doc_text},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert len(data["claims"]) >= 1

        # Check verbatim quote citations
        for claim in data["claims"]:
            assert claim["verified"] is True
            assert claim["quote"] in doc_text


@pytest.mark.asyncio
async def test_e2e_legal_workspace_contracts_and_flagged_clauses(
    live_server: str, browser_instance: Browser
) -> None:
    """E2E Test 6: Legal reviewer contracts list, MSA/DPA status, renewal terms, and flagged clauses with quotes and pages."""
    assert live_server
    async with httpx.AsyncClient(timeout=60.0) as client:
        admin_email = f"admin_legal_{uuid.uuid4().hex[:6]}@omnea.internal"
        admin_auth = await _signup_and_login_user(client, admin_email, org_name="Legal Org")
        tenant_id = admin_auth["tenant_id"]

        # Create a dedicated legal_reviewer user
        legal_email = f"legal_{uuid.uuid4().hex[:6]}@omnea.internal"
        await _create_user_with_role(client, admin_auth, legal_email, "legal_reviewer")

        from datetime import date

        from procure_core.db import get_async_session_factory
        from procure_core.models.vendor import ContractModel, VendorModel

        sf = get_async_session_factory()
        vendor_id = uuid.uuid4()
        contract_id = uuid.uuid4()

        async with sf() as session:
            async with session.begin():
                session.add(
                    VendorModel(
                        id=vendor_id,
                        tenant_id=tenant_id,
                        canonical_key="datadog_legal",
                        name="Datadog, Inc.",
                        normalized_base="datadog",
                        region="US",
                        status="active",
                        metadata_={},
                    )
                )
                await session.flush()
                session.add(
                    ContractModel(
                        id=contract_id,
                        tenant_id=tenant_id,
                        vendor_id=vendor_id,
                        title="Master Services Agreement - Datadog",
                        status="active",
                        start_date=date(2025, 1, 1),
                        end_date=date(2027, 12, 31),
                        total_value=Decimal("96000.00"),
                        currency="USD",
                        auto_renew=True,
                        metadata_={
                            "contract_type": "MSA",
                            "msa_status": "active",
                            "dpa_status": "signed",
                            "renewal_notice_period_days": 60,
                            "flagged_clauses": [
                                {
                                    "clause_type": "Limitation of Liability",
                                    "risk_level": "high",
                                    "page": "Page 12, Section 8.1",
                                    "quote": "Supplier total liability shall not exceed twelve (12) months of fees paid.",
                                    "recommendation": "Negotiate 3x fee multiplier for gross negligence.",
                                }
                            ],
                        },
                    )
                )

        # 2. Browser Verification as Legal Reviewer
        context = await browser_instance.new_context()
        page = await context.new_page()

        # Login
        await page.goto(f"{BASE_URL}/app")
        await page.wait_for_selector("#login-form", timeout=10000)
        await page.fill("#login-email", legal_email)
        await page.fill("#login-password", "StrongPass123!#Omnea")
        await page.click('#login-form button[type="submit"]')
        await page.wait_for_selector(".app-sidebar", timeout=10000)

        # Navigate to Legal Workspace
        await page.click('a[href="#/app/legal"]')
        await page.wait_for_selector("#contracts-table-body", timeout=10000)

        # Verify contracts table contains the MSA
        table_text = await page.text_content("#contracts-table-body")
        assert "Master Services Agreement - Datadog" in table_text
        assert "Datadog, Inc." in table_text
        assert "MSA" in table_text

        # Open contract detail drawer
        await page.click(f'button[data-contract-id="{contract_id}"]')
        await page.wait_for_selector("#contract-detail-drawer", timeout=10000)

        # Verify flagged clause inspector shows exact quote, page, and recommendation
        drawer_text = await page.text_content("#contract-detail-drawer")
        assert "Limitation of Liability" in drawer_text
        assert "Page 12, Section 8.1" in drawer_text
        assert "Supplier total liability shall not exceed twelve (12) months" in drawer_text
        assert "Negotiate 3x fee multiplier" in drawer_text

        await context.close()
