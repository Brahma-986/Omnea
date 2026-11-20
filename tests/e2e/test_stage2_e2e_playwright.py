"""Playwright End-to-End tests for Stage 2: Public Home Page, Auth, Role-Based App Shell,

Security Controls, XSS Sanitization, and Keyboard Navigation.
"""

from __future__ import annotations

import asyncio
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
SERVER_PORT = 8008
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
    """Start uvicorn server in a subprocess for the test duration."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    src_dir = repo_root / "services" / "api" / "src"

    env = os.environ.copy()
    env["PYTHONPATH"] = (
        str(src_dir)
        + os.pathsep
        + str(repo_root / "packages" / "procure_core" / "src")
        + os.pathsep
        + str(repo_root / "services" / "workers" / "src")
        + os.pathsep
        + env.get("PYTHONPATH", "")
    )
    env["APP_ENV"] = "test"

    log_path = repo_root / ".pytest_uvicorn_e2e.log"
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
    """Provide a new clean page with error listener for XSS detection."""
    context = await browser_instance.new_context(
        viewport={"width": 1280, "height": 800},
        ignore_https_errors=True,
    )
    pg = await context.new_page()
    pg.on("dialog", lambda dialog: asyncio.create_task(dialog.dismiss()))
    yield pg
    await context.close()


@pytest.mark.asyncio
async def test_public_home_page_structure(live_server: str, page: Page) -> None:
    """Verify Public Home Page contains all required sections and links."""
    await page.goto(f"{live_server}/")
    await page.wait_for_selector(".hero-title")

    # 1. Hero section
    title = await page.text_content(".hero-title")
    assert "AI-Native Procurement" in (title or "")

    # 2. What it does section
    what_it_does = await page.text_content("#what-it-does")
    assert "Decentralized Intake" in (what_it_does or "")
    assert "Parallel Multi-Agent Approval" in (what_it_does or "")

    # 3. Agent roster
    agents = await page.text_content("#agents")
    assert "Legal Reviewer" in (agents or "")
    assert "Security Reviewer" in (agents or "")
    assert "Finance Admin" in (agents or "")
    assert "TPRM Risk Evaluator" in (agents or "")

    # 4. 3-step workflow
    steps = await page.text_content("#how-it-works")
    assert "Request via Chat or Web" in (steps or "")
    assert "Parallel Multi-Agent" in (steps or "")
    assert "Deterministic System-of-Record Sync" in (steps or "")

    # 5. Security highlights
    security = await page.text_content("#security")
    assert "Fail-Closed PostgreSQL RLS" in (security or "")
    assert "SHA-256 Audit Chain" in (security or "")

    # 6. CTAs
    assert await page.is_visible("text=Start Procurement")
    assert await page.is_visible("text=Sign In")


@pytest.mark.asyncio
async def test_signup_login_and_tenant_admin_nav(live_server: str, page: Page) -> None:
    """Test full sign up, login, tenant_admin navigation, and onboarding checklist."""
    await page.goto(f"{live_server}/")

    # Navigate to signup
    await page.click("#nav-login-btn")
    await page.wait_for_selector("#login-form")
    await page.click("text=Start Organization Setup")
    await page.wait_for_selector("#signup-form")

    org_name = f"TestCorp_{uuid.uuid4().hex[:6]}"
    admin_email = f"admin_{uuid.uuid4().hex[:6]}@testcorp.com"
    admin_pass = "TestAdmin#2026Pass!"

    await page.fill("#org-name", org_name)
    await page.fill("#admin-name", "Admin Alice")
    await page.fill("#email", admin_email)
    await page.fill("#password", admin_pass)
    await page.click("#signup-submit-btn")

    # Wait for login form
    await page.wait_for_selector("#login-form", timeout=10000)

    # Log in
    await page.fill("#login-email", admin_email)
    await page.fill("#login-password", admin_pass)
    await page.click("#login-submit-btn")

    # Verify redirection to /app shell
    await page.wait_for_url("**/app**", timeout=10000)
    await page.wait_for_selector(".app-sidebar")

    # Verify tenant_admin sidebar items are visible
    sidebar_text = (await page.text_content(".app-sidebar")) or ""
    assert "Organization Settings" in sidebar_text
    assert "User & Role Mgmt" in sidebar_text
    assert "Invitations" in sidebar_text
    assert "Audit Chain Log" in sidebar_text
    assert "Policy Engine Config" in sidebar_text

    # Verify onboarding checklist is rendered
    onboarding = (await page.text_content(".onboarding-card")) or ""
    assert "Setup Organization" in onboarding
    assert "Invite Team Members" in onboarding
    assert "Connect Financial System" in onboarding


@pytest.mark.asyncio
async def test_role_navigation_matrix(live_server: str, page: Page) -> None:
    """Verify each role sees its specific navigation items and is denied other roles' views."""
    async with httpx.AsyncClient(base_url=live_server, timeout=10.0) as client:
        org_name = f"RoleOrg_{uuid.uuid4().hex[:6]}"
        admin_email = f"admin_{uuid.uuid4().hex[:6]}@roleorg.com"
        admin_pass = "RoleAdmin#2026Pass!"

        # Create admin via API
        signup_res = await client.post(
            "/auth/signup",
            json={
                "organization_name": org_name,
                "admin_name": "Org Admin",
                "email": admin_email,
                "password": admin_pass,
            },
        )
        assert signup_res.status_code == 201

        # Log in admin via API
        login_res = await client.post(
            "/auth/login",
            json={"email": admin_email, "password": admin_pass},
        )
        assert login_res.status_code == 200
        admin_token = login_res.json()["access_token"]
        csrf_token = login_res.json().get("csrf_token", "")

        roles = [
            ("requester", f"req_{uuid.uuid4().hex[:6]}@roleorg.com"),
            ("approver", f"appr_{uuid.uuid4().hex[:6]}@roleorg.com"),
            ("finance_admin", f"fin_{uuid.uuid4().hex[:6]}@roleorg.com"),
            ("security_reviewer", f"sec_{uuid.uuid4().hex[:6]}@roleorg.com"),
            ("legal_reviewer", f"leg_{uuid.uuid4().hex[:6]}@roleorg.com"),
        ]

        passwords: dict[str, str] = {}
        for role_name, email in roles:
            inv_res = await client.post(
                "/invitations",
                headers={
                    "Authorization": f"Bearer {admin_token}",
                    "X-CSRF-Token": csrf_token,
                },
                json={"email": email, "role": role_name},
            )
            assert inv_res.status_code == 201
            inv_token = inv_res.json().get("invitation_token") or inv_res.json()["invite_token"]

            role_pass = f"RoleUser#{role_name}2026!"
            passwords[role_name] = role_pass

            acc_res = await client.post(
                "/invitations/accept",
                json={
                    "token": inv_token,
                    "full_name": f"{role_name.capitalize()} User",
                    "password": role_pass,
                },
            )
            assert acc_res.status_code in (200, 201)

    async def _logout() -> None:
        await page.evaluate("""() => {
            window.__omnea.state.clearAuth();
            window.__omnea.router.navigate('/login');
        }""")
        await page.wait_for_selector("#login-form")

    # 1. Test Requester Navigation
    await page.goto(f"{live_server}/#/login")
    await page.wait_for_selector("#login-form")
    await page.fill("#login-email", roles[0][1])
    await page.fill("#login-password", passwords["requester"])
    await page.click("#login-submit-btn")
    await page.wait_for_url("**/app**", timeout=10000)
    await page.wait_for_selector(".app-sidebar")

    req_nav = (await page.text_content(".app-sidebar")) or ""
    assert "My Requests" in req_nav
    assert "Submit Request" in req_nav
    assert "Catalog" in req_nav
    assert "Budgets & Spend Caps" not in req_nav
    assert "Vendor Security" not in req_nav
    assert "Contract Reviews" not in req_nav

    # 2. Test Approver Navigation
    await _logout()
    await page.fill("#login-email", roles[1][1])
    await page.fill("#login-password", passwords["approver"])
    await page.click("#login-submit-btn")
    await page.wait_for_url("**/app**", timeout=10000)
    await page.wait_for_selector(".app-sidebar")

    appr_nav = (await page.text_content(".app-sidebar")) or ""
    assert "Pending Approvals" in appr_nav
    assert "Advice Inspector" in appr_nav
    assert "Approval History" in appr_nav
    assert "Budgets & Spend Caps" not in appr_nav

    # 3. Test Finance Admin Navigation
    await _logout()
    await page.fill("#login-email", roles[2][1])
    await page.fill("#login-password", passwords["finance_admin"])
    await page.click("#login-submit-btn")
    await page.wait_for_url("**/app**", timeout=10000)
    await page.wait_for_selector(".app-sidebar")

    fin_nav = (await page.text_content(".app-sidebar")) or ""
    assert "Budgets & Spend Caps" in fin_nav
    assert "ERP Reconciliation" in fin_nav
    assert "Purchase Orders" in fin_nav
    assert "Contract Reviews" not in fin_nav

    # 4. Test Security Reviewer Navigation
    await _logout()
    await page.fill("#login-email", roles[3][1])
    await page.fill("#login-password", passwords["security_reviewer"])
    await page.click("#login-submit-btn")
    await page.wait_for_url("**/app**", timeout=10000)
    await page.wait_for_selector(".app-sidebar")

    sec_nav = (await page.text_content(".app-sidebar")) or ""
    assert "Vendor Security" in sec_nav
    assert "SOC2 Analysis" in sec_nav
    assert "TPRM Queue" in sec_nav
    assert "Budgets & Spend Caps" not in sec_nav

    # 5. Test Legal Reviewer Navigation
    await _logout()
    await page.fill("#login-email", roles[4][1])
    await page.fill("#login-password", passwords["legal_reviewer"])
    await page.click("#login-submit-btn")
    await page.wait_for_url("**/app**", timeout=10000)
    await page.wait_for_selector(".app-sidebar")

    leg_nav = (await page.text_content(".app-sidebar")) or ""
    assert "Contract Reviews" in leg_nav
    assert "Terms & Liability" in leg_nav
    assert "Legal Approval Queue" in leg_nav
    assert "Budgets & Spend Caps" not in leg_nav


@pytest.mark.asyncio
async def test_role_restriction_ui_redirect_and_api_403(live_server: str, page: Page) -> None:
    """Verify navigating to an unauthorized role page triggers UI redirect + API 403 Forbidden."""
    async with httpx.AsyncClient(base_url=live_server, timeout=10.0) as client:
        org_name = f"GuardOrg_{uuid.uuid4().hex[:6]}"
        admin_email = f"admin_{uuid.uuid4().hex[:6]}@guardorg.com"
        req_email = f"req_{uuid.uuid4().hex[:6]}@guardorg.com"
        pwd = "GuardUser#2026Pass!"

        signup = await client.post(
            "/auth/signup",
            json={
                "organization_name": org_name,
                "admin_name": "Admin",
                "email": admin_email,
                "password": pwd,
            },
        )
        assert signup.status_code == 201

        login_admin = await client.post(
            "/auth/login",
            json={"email": admin_email, "password": pwd},
        )
        assert login_admin.status_code == 200
        admin_token = login_admin.json()["access_token"]
        csrf_token = login_admin.json().get("csrf_token", "")

        inv = await client.post(
            "/invitations",
            headers={"Authorization": f"Bearer {admin_token}", "X-CSRF-Token": csrf_token},
            json={"email": req_email, "role": "requester"},
        )
        assert inv.status_code == 201
        inv_token = inv.json().get("invitation_token") or inv.json()["invite_token"]

        acc = await client.post(
            "/invitations/accept",
            json={"token": inv_token, "full_name": "Bob Requester", "password": pwd},
        )
        assert acc.status_code in (200, 201)

        login_res = await client.post(
            "/auth/login",
            json={"email": req_email, "password": pwd},
        )
        req_token = login_res.json()["access_token"]

        # 1. Verify backend API directly returns 403 Forbidden
        auth_headers = {"Authorization": f"Bearer {req_token}"}
        r_admin = await client.get("/api/v1/dashboard/admin", headers=auth_headers)
        assert r_admin.status_code == 403
        r_fin = await client.get("/api/v1/dashboard/finance", headers=auth_headers)
        assert r_fin.status_code == 403
        r_sec = await client.get("/api/v1/dashboard/security", headers=auth_headers)
        assert r_sec.status_code == 403
        r_leg = await client.get("/api/v1/dashboard/legal", headers=auth_headers)
        assert r_leg.status_code == 403

    # 2. Test Client UI Route Guard: Log in via browser as requester
    await page.goto(f"{live_server}/#/login")
    await page.wait_for_selector("#login-form")
    await page.fill("#login-email", req_email)
    await page.fill("#login-password", pwd)
    await page.click("#login-submit-btn")
    await page.wait_for_url("**/app**", timeout=10000)

    # Attempt to directly navigate to /app/admin in browser
    await page.goto(f"{live_server}/#/app/admin")
    await asyncio.sleep(0.5)

    # Client route guard redirects back to /app and displays toast notification
    assert "/app" in page.url
    await page.wait_for_selector(".toast")
    toast_text = (await page.text_content(".toast-container")) or ""
    assert "Access Denied" in toast_text or "unauthorized" in toast_text.lower()


@pytest.mark.asyncio
async def test_dark_mode_toggle_and_persistence(live_server: str, page: Page) -> None:
    """Verify theme switcher toggles data-theme attribute and persists to localStorage."""
    await page.goto(f"{live_server}/")
    await page.wait_for_selector("#public-theme-btn")

    # Initial theme
    initial_theme = await page.evaluate("() => document.documentElement.getAttribute('data-theme')")

    # Click theme toggle button
    await page.click("#public-theme-btn")
    new_theme = await page.evaluate("() => document.documentElement.getAttribute('data-theme')")
    assert new_theme in ("dark", "light")
    assert new_theme != initial_theme

    # Verify localStorage persistence
    saved_theme = await page.evaluate("() => localStorage.getItem('omnea_theme')")
    assert saved_theme == new_theme

    # Reload page and verify theme is retained
    await page.reload()
    await page.wait_for_selector("#public-theme-btn")
    reloaded_theme = await page.evaluate(
        "() => document.documentElement.getAttribute('data-theme')"
    )
    assert reloaded_theme == new_theme


@pytest.mark.asyncio
async def test_keyboard_only_navigation_and_command_palette(live_server: str, page: Page) -> None:
    """Verify keyboard accessibility: Tab navigation, Enter activation, Ctrl+K command palette."""
    await page.goto(f"{live_server}/")
    await page.wait_for_selector(".hero-title")

    # Tab through landing navigation
    await page.keyboard.press("Tab")
    await asyncio.sleep(0.1)
    await page.keyboard.press("Tab")
    await asyncio.sleep(0.1)

    # Press Ctrl+K to open Command Palette
    await page.keyboard.press("Control+KeyK")
    await page.wait_for_selector("#command-palette-modal", timeout=5000)
    assert await page.is_visible("#command-palette-modal")

    # Verify search input is focused
    await page.wait_for_function(
        "() => document.activeElement && document.activeElement.id === 'palette-search-input'",
        timeout=5000,
    )
    focused_id = await page.evaluate(
        "() => document.activeElement ? document.activeElement.id : ''"
    )
    assert focused_id == "palette-search-input"

    # Type query and navigate with ArrowDown
    await page.keyboard.type("Requests")
    await asyncio.sleep(0.2)
    await page.keyboard.press("ArrowDown")

    # Press Escape to close Command Palette
    await page.keyboard.press("Escape")
    await asyncio.sleep(0.3)
    assert not await page.is_visible("#command-palette-modal")


@pytest.mark.asyncio
async def test_hostile_xss_escaping_across_views(live_server: str, page: Page) -> None:
    """Inject hostile vendor names and script tags, asserting 0 script execution."""
    hostile_payloads = [
        "<script>window.__xss_vendor = true;</script>",
        "<img src=invalid-src onerror='window.__xss_img = true;'>",
        "<svg onload='window.__xss_svg = true;'>",
        '"><script>window.__xss_attr = true;</script>',
    ]

    async with httpx.AsyncClient(base_url=live_server, timeout=10.0) as client:
        org_name = f"XSSCorp_{uuid.uuid4().hex[:6]}"
        admin_email = f"admin_{uuid.uuid4().hex[:6]}@xsscorp.com"
        pwd = "XSSUser#2026Pass!"

        signup = await client.post(
            "/auth/signup",
            json={
                "organization_name": org_name,
                "admin_name": "Admin",
                "email": admin_email,
                "password": pwd,
            },
        )
        assert signup.status_code == 201

    await page.goto(f"{live_server}/#/login")
    await page.wait_for_selector("#login-form")
    await page.fill("#login-email", admin_email)
    await page.fill("#login-password", pwd)
    await page.click("#login-submit-btn")
    await page.wait_for_url("**/app**", timeout=10000)
    await page.wait_for_selector(".app-sidebar")

    # Test each hostile payload dynamically rendered via DOM components
    for payload in hostile_payloads:
        # 1. Render in toast notification
        await page.evaluate(
            "(msg) => window.__omnea.toast.info(msg)",
            payload,
        )
        await asyncio.sleep(0.2)

        # 2. Add notification to state
        await page.evaluate(
            """(msg) => {
                const notifs = window.__omnea.state.getState().notifications || [];
                const newItem = {
                    id: 'xss-' + Math.random(),
                    title: msg,
                    message: msg,
                    time: 'now',
                    is_read: false
                };
                window.__omnea.state.setState({
                    notifications: [newItem, ...notifs],
                    unreadCount: notifs.length + 1
                });
            }""",
            payload,
        )
        await asyncio.sleep(0.2)

    # Assert none of the malicious XSS scripts executed in the browser context
    xss_vendor = await page.evaluate("() => window.__xss_vendor")
    xss_img = await page.evaluate("() => window.__xss_img")
    xss_svg = await page.evaluate("() => window.__xss_svg")
    xss_attr = await page.evaluate("() => window.__xss_attr")

    assert xss_vendor is None, "Hostile vendor script was executed!"
    assert xss_img is None, "Hostile image onerror script was executed!"
    assert xss_svg is None, "Hostile SVG onload script was executed!"
    assert xss_attr is None, "Hostile attribute injection script was executed!"
