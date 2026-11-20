"""Stage 8: Desktop Application Integration & Acceptance Test Suite.

Verifies end-to-end integration and desktop capabilities:
1. Live Server Lifecycle: Spins up live uvicorn server in a subprocess and verifies health status.
2. Server Connection: Tests desktop connectivity to live API server and graceful failure on unreachable hosts.
3. Keychain Token Storage & Retrieval: Tests AES-256-GCM / safeStorage encryption, persistence, tampering rejection, and cleanup.
4. Deep Link URL Parsing: Tests deep link parsing (procure://request/<id>, procure://approval/<id>, procure://settings),
   CLI argv extraction, target URL generation, and rejection of foreign/malicious schemes.
5. Approval Creation & Native Notification Dispatch: Seeds request and approval step in PostgreSQL, executes
   ApprovalsMonitor in Node, and validates pending polling, badge updates, native notification dispatch, and cache sync.
6. Offline Cache Persistence: Tests caching requests to disk, retrieving when offline, timestamping, and clearing.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from procure_core.auth.rbac import RbacEngine
from procure_core.db import get_async_session_factory
from procure_core.security.tokens import create_access_token

logger = logging.getLogger("stage8_desktop_acceptance")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DESKTOP_DIR = REPO_ROOT / "desktop"

SERVER_HOST = "127.0.0.1"


def _find_free_port() -> int:
    """Find an available port dynamically to prevent test collisions."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((SERVER_HOST, 0))
        return int(s.getsockname()[1])


SERVER_PORT = _find_free_port()
LIVE_SERVER_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"


def _wait_for_server(url: str, timeout: float = 25.0) -> bool:
    """Poll health endpoint until server responds."""
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
    """Spins up a live uvicorn server in a background subprocess."""
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
    env["LOG_LEVEL"] = "INFO"

    log_path = REPO_ROOT / ".pytest_uvicorn_stage8_desktop.log"
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
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )

    try:
        if not _wait_for_server(LIVE_SERVER_URL, timeout=30.0):
            proc.kill()
            log_file.close()
            with open(log_path, encoding="utf-8", errors="replace") as f:
                logs = f.read()
            raise RuntimeError(f"Server failed to start on {LIVE_SERVER_URL}:\n{logs}")
        yield LIVE_SERVER_URL
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
        except Exception:
            proc.kill()
        log_file.close()


@pytest.fixture
def temp_user_data() -> Iterator[Path]:
    """Provides an isolated temporary user data directory for desktop state."""
    tmp = Path(tempfile.mkdtemp(prefix="omnea-desktop-test-"))
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


def _run_node_script(script_code: str, user_data_dir: Path | None = None) -> dict[str, Any]:
    """Execute a Node.js snippet and parse JSON stdout result."""
    env = os.environ.copy()
    if user_data_dir:
        env["OMNEA_USER_DATA"] = str(user_data_dir)

    result = subprocess.run(
        ["node", "-e", script_code],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Node execution failed (code {result.returncode}):\n{result.stderr}\n{result.stdout}"
        )

    return json.loads(result.stdout.strip())


# ============================================================================
# 1. Server Connection Tests
# ============================================================================


def test_live_server_connection(live_server: str) -> None:
    """Verifies live server is responsive via HTTP and desktop config testConnection."""
    # 1. Direct Python HTTP check
    with httpx.Client(base_url=live_server, timeout=5.0) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        health_data = resp.json()
        assert health_data.get("status") == "ok"

    # 2. Desktop config testConnection check via Node.js
    node_script = f"""
    const config = require('./desktop/src/config');
    (async () => {{
      const reachable = await config.testConnection('{live_server}');
      const unreachable = await config.testConnection('http://127.0.0.1:59998');
      console.log(JSON.stringify({{ reachable, unreachable }}));
    }})();
    """
    res = _run_node_script(node_script)
    assert res["reachable"]["ok"] is True
    assert res["reachable"]["status"] == 200
    assert res["unreachable"]["ok"] is False


# ============================================================================
# 2. Keychain Token Storage & Retrieval Tests
# ============================================================================


def test_keychain_token_storage_and_retrieval(temp_user_data: Path) -> None:
    """Verifies AES-256-GCM / safeStorage encryption, persistence, tamper check, and clear."""
    token_payload = {
        "access_token": "omnea_access_jwt_" + uuid.uuid4().hex,
        "refresh_token": "omnea_refresh_token_" + uuid.uuid4().hex,
        "tenant_id": "tenant_desktop_corp",
        "email": "lead_approver@omnea.ai",
    }

    # Step 1: Save tokens
    node_save = f"""
    const keychain = require('./desktop/src/keychain');
    const res = keychain.saveTokens({json.dumps(token_payload)});
    const readBack = keychain.getTokens();
    console.log(JSON.stringify({{ res, readBack }}));
    """
    save_result = _run_node_script(node_save, user_data_dir=temp_user_data)
    assert save_result["res"]["success"] is True
    assert save_result["readBack"]["access_token"] == token_payload["access_token"]
    assert save_result["readBack"]["tenant_id"] == token_payload["tenant_id"]

    # Step 2: Ensure file on disk is encrypted (zero plaintext leak)
    tokens_file = temp_user_data / "auth_tokens.enc"
    assert tokens_file.is_file(), "auth_tokens.enc file not found on disk"
    encrypted_bytes = tokens_file.read_bytes()

    assert token_payload["access_token"].encode() not in encrypted_bytes, (
        "Security breach: access token stored in plaintext!"
    )
    assert token_payload["refresh_token"].encode() not in encrypted_bytes, (
        "Security breach: refresh token stored in plaintext!"
    )

    # Step 3: Tampering resilience check
    tampered_bytes = bytearray(encrypted_bytes)
    tampered_bytes[-1] ^= 0xFF  # Flip bit in ciphertext/tag
    tokens_file.write_bytes(tampered_bytes)

    node_tamper = """
    const fs = require('fs');
    const keychain = require('./desktop/src/keychain');
    const tamperedGet = keychain.getTokens();
    let fallbackDecryptThrew = false;
    try {
      const raw = fs.readFileSync(keychain.getTokensFilePath());
      keychain.fallbackDecrypt(raw);
    } catch (e) {
      fallbackDecryptThrew = true;
    }
    console.log(JSON.stringify({ tamperedGet, fallbackDecryptThrew }));
    """
    tamper_result = _run_node_script(node_tamper, user_data_dir=temp_user_data)
    assert tamper_result["tamperedGet"] is None, (
        "Tampered ciphertext must fail-closed and return null"
    )
    assert tamper_result["fallbackDecryptThrew"] is True, "Tampered ciphertext must fail decryption"

    # Step 4: Clear tokens
    tokens_file.write_bytes(encrypted_bytes)  # Restore valid
    node_clear = """
    const keychain = require('./desktop/src/keychain');
    const clearRes = keychain.clearTokens();
    const afterClear = keychain.getTokens();
    console.log(JSON.stringify({ clearRes, afterClear }));
    """
    clear_result = _run_node_script(node_clear, user_data_dir=temp_user_data)
    assert clear_result["clearRes"]["success"] is True
    assert clear_result["afterClear"] is None
    assert not tokens_file.exists(), "Tokens file must be deleted upon logout"


# ============================================================================
# 3. Deep Link URL Parsing Tests
# ============================================================================


def test_deep_link_url_parsing(live_server: str) -> None:
    """Verifies procure:// protocol URL parsing, CLI argv discovery, and foreign link rejection."""
    node_script = f"""
    const deepLink = require('./desktop/src/deep_link');

    const reqLink = deepLink.parseDeepLink('procure://request/req-stage8-12345');
    const queryLink = deepLink.parseDeepLink('procure://requests/req-stage8-999?tab=audit&filter=true');
    const approvalLink = deepLink.parseDeepLink('procure://approval/step-step-777');
    const settingsLink = deepLink.parseDeepLink('procure://settings');
    const emptyLink = deepLink.parseDeepLink('procure://');

    // Foreign / invalid links
    const httpLink = deepLink.parseDeepLink('https://evil-phishing.com/request/123');
    const jsLink = deepLink.parseDeepLink('javascript:alert(1)');

    // Command line discovery
    const argvTest = ['electron.exe', '.', '--inspect', 'procure://request/req-from-argv'];
    const foundArgv = deepLink.findDeepLinkInArgv(argvTest);

    // Target URL builder
    const builtUrl = deepLink.buildServerUrlForDeepLink('{live_server}', reqLink);

    console.log(JSON.stringify({{
      reqLink,
      queryLink,
      approvalLink,
      settingsLink,
      emptyLink,
      httpLink,
      jsLink,
      foundArgv,
      builtUrl
    }}));
    """
    res = _run_node_script(node_script)

    # 1. Request deep link
    assert res["reqLink"]["action"] == "request"
    assert res["reqLink"]["id"] == "req-stage8-12345"
    assert res["reqLink"]["path"] == "/requests/req-stage8-12345"

    # 2. Request deep link with query params
    assert res["queryLink"]["action"] == "request"
    assert res["queryLink"]["id"] == "req-stage8-999"
    assert res["queryLink"]["queryParams"]["tab"] == "audit"

    # 3. Approval deep link
    assert res["approvalLink"]["action"] == "approval"
    assert res["approvalLink"]["id"] == "step-step-777"
    assert res["approvalLink"]["path"] == "/approvals/step-step-777"

    # 4. Settings deep link
    assert res["settingsLink"]["action"] == "settings"
    assert res["settingsLink"]["path"] == "/settings"

    # 5. Empty scheme
    assert res["emptyLink"]["action"] == "home"

    # 6. Reject malicious URLs
    assert res["httpLink"] is None
    assert res["jsLink"] is None

    # 7. Argv discovery
    assert res["foundArgv"] == "procure://request/req-from-argv"

    # 8. Built server URL
    assert res["builtUrl"] == f"{live_server}/requests/req-stage8-12345"


# ============================================================================
# 4. Approval Creation & Native Notification Dispatch Tests
# ============================================================================


@pytest.mark.asyncio
async def test_approval_creation_and_native_notification_dispatch(
    live_server: str,
    temp_user_data: Path,
) -> None:
    """Seeds approval in live database, runs ApprovalsMonitor in Node, verifies notification dispatch."""
    if sys.platform == "win32":
        import asyncio

        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    unique_id = uuid.uuid4().hex[:8]
    tenant_id = f"tenant_desktop_{unique_id}"
    approver_email = f"desktop_approver_{unique_id}@omnea.ai"
    vendor_name = "Datadog Cloud Monitoring"
    estimated_spend = 64000.00

    # 1. Register approver role in RBAC
    RbacEngine.assign_fallback_role(tenant_id, approver_email, "approver")

    # 2. Seed Request and Approval Step into PostgreSQL
    req_id = uuid.uuid4()
    step_id = uuid.uuid4()
    factory = get_async_session_factory()

    async with factory() as session:
        # State machine transition: draft -> in_review -> awaiting_human
        await session.execute(
            text(
                "INSERT INTO requests (id, tenant_id, requester_id, status, payload) "
                "VALUES (:id, :t, :req, 'draft', :payload)"
            ),
            {
                "id": req_id,
                "t": tenant_id,
                "req": "requester@omnea.ai",
                "payload": json.dumps(
                    {
                        "vendor_name": vendor_name,
                        "estimated_spend": estimated_spend,
                        "currency": "USD",
                        "department": "Infrastructure",
                    }
                ),
            },
        )
        await session.execute(
            text("UPDATE requests SET status = 'in_review' WHERE id = :id"),
            {"id": req_id},
        )
        await session.execute(
            text("UPDATE requests SET status = 'awaiting_human' WHERE id = :id"),
            {"id": req_id},
        )
        await session.execute(
            text(
                "INSERT INTO approval_steps (id, request_id, tenant_id, step_name, round, reviewer_type, status, assignee) "
                "VALUES (:id, :req_id, :t, :step_name, 1, :rev_type, :status, :assignee)"
            ),
            {
                "id": step_id,
                "req_id": req_id,
                "t": tenant_id,
                "step_name": "Security & Architecture Review",
                "rev_type": "human",
                "status": "awaiting_human",
                "assignee": approver_email,
            },
        )
        await session.commit()

    # 3. Generate cryptographic JWT access token for approver
    token = create_access_token(
        user_id=approver_email,
        tenant_id=tenant_id,
        email=approver_email,
        roles=["approver"],
    )

    # 4. Verify API returns pending approvals via HTTP
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=live_server, timeout=5.0) as client:
        resp = await client.get("/api/v1/approvals/pending", headers=headers)
        assert resp.status_code == 200
        api_data = resp.json()
        assert api_data["count"] >= 1
        matched_step = next(
            (s for s in api_data["pending_approvals"] if s["step_id"] == str(step_id)), None
        )
        assert matched_step is not None
        assert matched_step["vendor_name"] == vendor_name

    # 5. Store token in keychain and configure desktop client to poll live server
    node_runner = f"""
    const keychain = require('./desktop/src/keychain');
    const config = require('./desktop/src/config');
    const cache = require('./desktop/src/offline_cache');
    const {{ ApprovalsMonitor }} = require('./desktop/src/notifications');

    (async () => {{
      // Save auth tokens & config
      keychain.saveTokens({{ access_token: '{token}', tenant_id: '{tenant_id}', email: '{approver_email}' }});
      config.saveConfig({{ serverUrl: '{live_server}', pollIntervalMs: 5000 }});

      let updatedBadgeCount = -1;
      let capturedNotification = null;

      const monitor = new ApprovalsMonitor({{
        onBadgeUpdate: (count) => {{
          updatedBadgeCount = count;
        }},
        onNotification: (notif) => {{
          capturedNotification = notif;
        }},
      }});

      // Execute poll check
      const pollResult = await monitor.checkNow();
      const cached = cache.getCachedRequests();

      console.log(JSON.stringify({{
        pollResult,
        updatedBadgeCount,
        capturedNotification,
        cachedCount: cached.count,
        firstCachedVendor: cached.requests.length > 0 ? cached.requests[0].vendor_name : null,
      }}));
    }})();
    """
    desktop_res = _run_node_script(node_runner, user_data_dir=temp_user_data)

    assert desktop_res["pollResult"]["success"] is True
    assert desktop_res["pollResult"]["count"] >= 1

    # Verify badge count update
    assert desktop_res["updatedBadgeCount"] >= 1, "Badge count must reflect pending approvals"

    # Verify notification dispatch
    notification = desktop_res["capturedNotification"]
    assert notification is not None, "ApprovalsMonitor must dispatch native notification"
    assert "Omnea" in notification["title"] or "Approval" in notification["title"]
    assert (
        "pending approval" in notification["body"].lower()
        or vendor_name.lower() in notification["body"].lower()
    )
    assert notification["step"]["step_id"] == str(step_id)

    # Verify automatic offline cache synchronization on poll
    assert desktop_res["cachedCount"] >= 1
    assert desktop_res["firstCachedVendor"] == vendor_name


# ============================================================================
# 5. Offline Cache Persistence Tests
# ============================================================================


def test_offline_cache_persistence(temp_user_data: Path) -> None:
    """Verifies requests caching to disk, reading cached data, and clearing cache."""
    test_items = [
        {
            "step_id": "step-offline-001",
            "request_id": "req-offline-101",
            "step_name": "VP Review",
            "vendor_name": "Stripe Payments",
            "estimated_spend": 12000,
            "currency": "USD",
            "sla_status": "on_track",
        },
        {
            "step_id": "step-offline-002",
            "request_id": "req-offline-102",
            "step_name": "Security Signoff",
            "vendor_name": "Snyk Security",
            "estimated_spend": 28000,
            "currency": "USD",
            "sla_status": "escalated",
        },
    ]

    node_script = f"""
    const fs = require('fs');
    const cache = require('./desktop/src/offline_cache');

    // 1. Initial state
    const initial = cache.getCachedRequests();

    // 2. Save items
    const saved = cache.saveCachedRequests({json.dumps(test_items)});
    const fileExistsWhileSaved = fs.existsSync(cache.getCacheFilePath());
    const rawDiskData = fileExistsWhileSaved ? JSON.parse(fs.readFileSync(cache.getCacheFilePath(), 'utf8')) : null;

    // 3. Read back
    const retrieved = cache.getCachedRequests();

    // 4. Clear cache
    const clearRes = cache.clearCache();
    const afterClear = cache.getCachedRequests();
    const fileExistsAfterClear = fs.existsSync(cache.getCacheFilePath());

    console.log(JSON.stringify({{
      initial,
      saved,
      fileExistsWhileSaved,
      rawDiskData,
      retrieved,
      clearRes,
      afterClear,
      fileExistsAfterClear,
    }}));
    """
    res = _run_node_script(node_script, user_data_dir=temp_user_data)

    # Verify initial
    assert res["initial"]["count"] == 0
    assert res["initial"]["requests"] == []

    # Verify saved
    assert res["saved"]["count"] == 2
    assert res["saved"]["cachedAt"] is not None

    # Verify retrieved matches saved
    assert res["retrieved"]["count"] == 2
    assert res["retrieved"]["requests"][0]["vendor_name"] == "Stripe Payments"
    assert res["retrieved"]["requests"][1]["vendor_name"] == "Snyk Security"

    # Verify file existed on disk with expected JSON structure
    assert res["fileExistsWhileSaved"] is True, (
        "offline_requests.json must exist on disk while cached"
    )
    assert res["rawDiskData"]["count"] == 2

    # Verify clearing
    assert res["clearRes"]["success"] is True
    assert res["afterClear"]["count"] == 0
    assert res["afterClear"]["requests"] == []
    assert res["fileExistsAfterClear"] is False, "Cache file must be removed after clearCache"
