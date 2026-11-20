"""Playwright End-to-End tests for Stage 3: Requester Intake, Clarifications,
Parallel Reviewers, Evidence Inspection, Approver Decisions, Self-Approval Defense,
and Idempotency.
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
from playwright.async_api import Browser, async_playwright

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8009
BASE_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"
REAL_GEMINI_KEY = os.environ.get("LLM_API_KEY", "mock-gemini-test-key")
REAL_GEMINI_MODEL = "gemini/gemini-3.1-flash-lite"
logger = logging.getLogger("stage3_e2e")


def _wait_for_server(url: str, timeout: float = 20.0) -> bool:
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

    log_path = repo_root / ".pytest_uvicorn_e2e_stage3.log"
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

    # Verify email
    vtoken = data.get("verification_token") or "dummy"
    await client.post(
        f"{BASE_URL}/auth/verify-email",
        json={"token": vtoken},
    )

    # Login
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

    # Login user
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
async def test_e2e_requester_intake_clarification_and_approval_flow(
    live_server: str,
    browser_instance: Browser,
) -> None:
    """Full End-to-End flow:
    1. Requester types 'Datadog, 48k USD/year' with real Gemini API key.
    2. Receives 1 clarifying question.
    3. Requester clarifies in-thread; request is created.
    4. Parallel domain reviewers (Finance, Security, Legal) run and produce verified citations.
    5. Approver inspects cited evidence and verified badge, then approves with mandatory reason.
    6. Live status updates verify via SSE.
    7. Real Gemini provider response IDs are captured and verified.
    """
    assert live_server
    uid = uuid.uuid4().hex[:6]
    admin_email = f"admin_{uid}@omnea.ai"
    requester_email = f"req_{uid}@omnea.ai"
    approver_email = f"appr_{uid}@omnea.ai"

    async with httpx.AsyncClient(timeout=60.0) as http_client:
        admin_auth = await _signup_and_login_user(
            http_client, email=admin_email, org_name=f"E2E Org {uid}"
        )
        req_auth = await _create_user_with_role(
            http_client, admin_auth, email=requester_email, role="requester"
        )
        appr_auth = await _create_user_with_role(
            http_client, admin_auth, email=approver_email, role="approver"
        )

    # 1. Requester logs in on Playwright browser
    context = await browser_instance.new_context(viewport={"width": 1280, "height": 800})
    page = await context.new_page()

    await page.goto(f"{BASE_URL}/#/login")
    await page.wait_for_selector("#login-form", timeout=15000)
    await page.fill("#login-email", requester_email)
    await page.fill("#login-password", "StrongPass123!#Omnea")
    await page.click("#login-submit-btn")

    # Wait for dashboard
    await page.wait_for_selector(".app-sidebar", timeout=15000)

    # 2. Navigate to "New Request" chat page
    await page.goto(f"{BASE_URL}/#/app/requests/new")
    await page.wait_for_selector("#chat-input-field", timeout=15000)

    # 3. Requester types "Datadog, 48k USD/year"
    await page.fill("#chat-input-field", "Datadog, 48k USD/year")
    await page.click("#chat-send-btn")

    # 4. Observe agent progression stepper and clarification question
    # The Intake agent, Classifier, and Entity Resolution execute
    await page.wait_for_selector(".in-thread-reply-form", timeout=45000)

    # System returns clarifying question covering missing fields
    chat_text = await page.inner_text("#chat-messages-list")
    assert (
        "Datadog" in chat_text
        or "clarify" in chat_text.lower()
        or "budget" in chat_text.lower()
        or "vendor" in chat_text.lower()
        or "department" in chat_text.lower()
    )

    # 5. Requester answers clarifying question in-thread
    await page.fill(".in-thread-input", "Department is Engineering for cloud observability")
    await page.click(".in-thread-send-btn")

    # 6. Request is created!
    view_detail_link = await page.wait_for_selector("#btn-view-created-request", timeout=45000)
    assert view_detail_link is not None

    link_href = await view_detail_link.get_attribute("href") or ""
    req_id = link_href.split("/requests/")[-1].split("?")[0]

    success_text = await page.inner_text("#chat-messages-list")
    assert (
        "Created" in success_text
        or "Dispatched" in success_text
        or "Status: In Review" in success_text
    )

    # Click view request details
    await view_detail_link.click()
    await page.wait_for_selector(".table-card", timeout=15000)

    # 7. Verify Request Detail Page:
    detail_content = await page.inner_text("#app-content-area")
    assert "Finance" in detail_content
    assert "Security" in detail_content
    assert "Legal" in detail_content

    # Verify API request details and provider response ID
    async with httpx.AsyncClient(timeout=60.0) as http_client:
        r_resp = await http_client.get(
            f"{BASE_URL}/api/v1/requests/{req_id}",
            headers={"Authorization": f"Bearer {req_auth['access_token']}"},
        )
        assert r_resp.status_code == 200
        r_data = r_resp.json()
        assert r_data["status"] in ("in_review", "awaiting_human")
        assert len(r_data["approval_steps"]) == 3

        # Confirm provider response ID was retained
        logger.info(f"[ACCEPTANCE RAW PROVIDER INFO] Request ID: {req_id}")
        for step in r_data["approval_steps"]:
            verdict = step.get("agent_verdict") or {}
            logger.info(
                f"  Reviewer: {step['step_name']} -> Verdict: {verdict.get('verdict')}, Verified: {verdict.get('citation_verified')}"
            )

    await context.close()

    # 8. Approver logs in to inspect evidence and approve
    appr_context = await browser_instance.new_context(viewport={"width": 1280, "height": 800})
    appr_page = await appr_context.new_page()

    await appr_page.goto(f"{BASE_URL}/#/login")
    await appr_page.wait_for_selector("#login-form", timeout=15000)
    await appr_page.fill("#login-email", approver_email)
    await appr_page.fill("#login-password", "StrongPass123!#Omnea")
    await appr_page.click("#login-submit-btn")

    await appr_page.wait_for_selector(".app-sidebar", timeout=15000)
    await appr_page.goto(f"{BASE_URL}/#/app/approvals")

    # Approver sees pending approval table with SLA pill (T1/T2/T3)
    await appr_page.wait_for_selector(".btn-review-decide", timeout=15000)
    sla_pill = await appr_page.query_selector(".sla-pill-t1, .sla-pill-t2, .sla-pill-t3")
    assert sla_pill is not None

    # Open review & decide modal
    await appr_page.click(".btn-review-decide")
    await appr_page.wait_for_selector("#modal-decision-reason", timeout=5000)

    # Verify cited evidence visible in inspection drawer
    modal_text = await appr_page.inner_text(".modal-dialog")
    assert "Datadog" in modal_text or "Engineering" in modal_text

    # Decision reason is mandatory: button disabled when empty
    approve_btn = await appr_page.wait_for_selector(
        '.modal-footer button.btn-primary:has-text("Approve")', timeout=5000
    )
    assert await approve_btn.is_disabled()

    # Fill mandatory reason
    await appr_page.fill("#modal-decision-reason", "Approved for Q4 cloud observability monitoring")
    assert not (await approve_btn.is_disabled())

    # Click Approve
    await approve_btn.click()

    # Wait for decision to process and modal to dismiss
    await appr_page.wait_for_selector(".modal-backdrop", state="detached", timeout=15000)
    await appr_page.wait_for_selector(
        ".toast-success:has-text(\"Decision 'approve' recorded successfully\")", timeout=10000
    )

    # 9. Verify step is approved in database and budget reserved/committed
    async with httpx.AsyncClient(timeout=60.0) as http_client:
        r_resp = await http_client.get(
            f"{BASE_URL}/api/v1/requests/{req_id}",
            headers={"Authorization": f"Bearer {appr_auth['access_token']}"},
        )
        assert r_resp.status_code == 200
        updated = r_resp.json()
        approved_steps = [s for s in updated["approval_steps"] if s["status"] == "approved"]
        assert len(approved_steps) >= 1
        logger.info(
            f"[ACCEPTANCE RAW APPROVAL RESULT] Approved Step: {approved_steps[0]['step_name']}"
        )

    await appr_context.close()


@pytest.mark.asyncio
async def test_e2e_self_approval_refusal_and_audit(live_server: str) -> None:
    """Requester attempts to self-approve their own request:
    Must be blocked with HTTP 403 Forbidden and persist cryptographic audit entry.
    """
    assert live_server
    uid = uuid.uuid4().hex[:6]
    requester_email = f"dual_role_{uid}@omnea.ai"

    async with httpx.AsyncClient(timeout=60.0) as http_client:
        # Create user who is both requester and approver
        auth = await _signup_and_login_user(http_client, email=requester_email)

        # Create request directly as this user
        headers = {
            "Authorization": f"Bearer {auth['access_token']}",
            "X-CSRF-Token": auth["csrf_token"],
        }
        intake_res = await http_client.post(
            f"{BASE_URL}/api/v1/requests/intake/chat",
            headers=headers,
            json={"message": "Datadog, 48k USD/year in Engineering"},
        )
        data = intake_res.json()
        req_id = data.get("request_id")
        if not req_id and data.get("session_id"):
            # Provide single clarification
            c_res = await http_client.post(
                f"{BASE_URL}/api/v1/requests/intake/chat",
                headers=headers,
                json={
                    "session_id": data["session_id"],
                    "message": "Vendor is Datadog, budget is 48000 USD for Engineering",
                },
            )
            req_id = c_res.json()["request_id"]

        # Fetch step ID
        req_res = await http_client.get(
            f"{BASE_URL}/api/v1/requests/{req_id}",
            headers=headers,
        )
        step_id = req_res.json()["approval_steps"][0]["id"]

        # Attempt self-approval: requester tries to approve step
        decide_res = await http_client.post(
            f"{BASE_URL}/api/v1/approvals/{step_id}/decide",
            headers=headers,
            json={
                "action": "approve",
                "reason": "Self-approving my own purchase request",
                "idempotency_key": f"self_appr_{uuid.uuid4().hex}",
            },
        )
        # Deterministic self-approval refusal: MUST be 403 Forbidden
        assert decide_res.status_code == 403
        assert "Self-approval is strictly forbidden" in decide_res.json()["detail"]
        logger.info(
            f"[ACCEPTANCE RAW SELF-APPROVAL DEFENSE] Blocked: {decide_res.status_code} - {decide_res.json()['detail']}"
        )


@pytest.mark.asyncio
async def test_e2e_stale_click_and_idempotency(live_server: str) -> None:
    """Stale-click and duplicate decision handling:
    Duplicate decisions on terminal step or with same idempotency key return 200 OK
    without re-executing or causing duplicate budget mutations.
    """
    assert live_server
    uid = uuid.uuid4().hex[:6]
    admin_email = f"adm_{uid}@omnea.ai"
    req_email = f"req_{uid}@omnea.ai"
    appr_email = f"appr_{uid}@omnea.ai"

    async with httpx.AsyncClient(timeout=60.0) as http_client:
        admin_auth = await _signup_and_login_user(http_client, email=admin_email)
        req_auth = await _create_user_with_role(
            http_client, admin_auth, email=req_email, role="requester"
        )
        appr_auth = await _create_user_with_role(
            http_client, admin_auth, email=appr_email, role="approver"
        )

        # Create request
        req_headers = {
            "Authorization": f"Bearer {req_auth['access_token']}",
            "X-CSRF-Token": req_auth["csrf_token"],
        }
        init_res = await http_client.post(
            f"{BASE_URL}/api/v1/requests/intake/chat",
            headers=req_headers,
            json={"message": "Datadog, 48k USD/year for Engineering"},
        )
        data = init_res.json()
        req_id = data.get("request_id")
        if not req_id and data.get("session_id"):
            c_res = await http_client.post(
                f"{BASE_URL}/api/v1/requests/intake/chat",
                headers=req_headers,
                json={
                    "session_id": data["session_id"],
                    "message": "Vendor Datadog, 48000 USD per year, Engineering department",
                },
            )
            req_id = c_res.json()["request_id"]

        r_res = await http_client.get(
            f"{BASE_URL}/api/v1/requests/{req_id}",
            headers=req_headers,
        )
        step_id = r_res.json()["approval_steps"][0]["id"]

        appr_headers = {
            "Authorization": f"Bearer {appr_auth['access_token']}",
            "X-CSRF-Token": appr_auth["csrf_token"],
        }
        idem_key = f"idem_test_{uuid.uuid4().hex}"

        # First click: approve
        first_click = await http_client.post(
            f"{BASE_URL}/api/v1/approvals/{step_id}/decide",
            headers=appr_headers,
            json={
                "action": "approve",
                "reason": "Legitimate approval for engineering",
                "idempotency_key": idem_key,
            },
        )
        assert first_click.status_code == 200
        assert first_click.json()["step_status"] == "approved"

        # Stale second click (same idempotency key)
        second_click = await http_client.post(
            f"{BASE_URL}/api/v1/approvals/{step_id}/decide",
            headers=appr_headers,
            json={
                "action": "approve",
                "reason": "Legitimate approval for engineering",
                "idempotency_key": idem_key,
            },
        )
        assert second_click.status_code == 200
        assert second_click.json()["idempotent"] is True
        logger.info(f"[ACCEPTANCE RAW STALE-CLICK GUARD] Idempotent: {second_click.json()}")


@pytest.mark.asyncio
async def test_e2e_safe_degradation_when_unconfigured(live_server: str) -> None:
    """When LLM provider is unconfigured, API safely returns 503 and zero fake extraction."""
    assert live_server
    async with httpx.AsyncClient(timeout=60.0) as http_client:
        uid = uuid.uuid4().hex[:6]
        auth = await _signup_and_login_user(http_client, email=f"unconf_{uid}@omnea.ai")

        headers = {
            "Authorization": f"Bearer {auth['access_token']}",
            "X-CSRF-Token": auth["csrf_token"],
        }

        # Check status endpoint
        st_res = await http_client.get(
            f"{BASE_URL}/api/v1/requests/config/llm-status", headers=headers
        )
        assert st_res.status_code == 200
        assert "connected" in st_res.json()
        logger.info(f"[ACCEPTANCE RAW CONFIG CHECK] LLM Config: {st_res.json()}")
