"""Unit tests for Stage 8: Web UI Desktop Bridge & Offline UI Integration."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC_DIR = (
    Path(__file__).resolve().parent.parent.parent / "services" / "api" / "src" / "api" / "static"
)


@pytest.mark.unit
def test_app_js_desktop_bridge_integration() -> None:
    """Verify app.js integrates deep-link listener, token lifecycle, badge count, and server settings."""
    app_js_path = STATIC_DIR / "js" / "app.js"
    assert app_js_path.is_file(), "app.js must exist"
    content = app_js_path.read_text(encoding="utf-8")

    # 1. Desktop container detection
    assert "window.procureDesktop" in content

    # 2. Deep-link handler
    assert "onDeepLink" in content
    assert "#/app/requests/" in content

    # 3. Token lifecycle: auto-restore session on startup
    assert "getAuthTokens" in content
    assert "state.setSession" in content

    # 4. Token lifecycle: clear tokens on logout
    assert "clearAuthTokens" in content

    # 5. Tray / app badge sync
    assert "setBadgeCount" in content

    # 6. Server Settings button / action
    assert "Server Settings" in content
    assert "openServerSettings" in content


@pytest.mark.unit
def test_api_js_offline_cache_and_desktop_bridge() -> None:
    """Verify api.js handles offline cache dispatch and desktop auth token synchronization."""
    api_js_path = STATIC_DIR / "js" / "api.js"
    assert api_js_path.is_file(), "api.js must exist"
    content = api_js_path.read_text(encoding="utf-8")

    # Offline requests cache dispatch on /api/v1/requests success
    assert "saveOfflineRequests" in content

    # Offline requests cache retrieval on network failure / offline
    assert "getOfflineRequests" in content

    # Desktop token lifecycle synchronization
    assert "saveAuthTokens" in content
    assert "clearAuthTokens" in content

    # Approvals tray/badge count synchronization
    assert "setBadgeCount" in content


@pytest.mark.unit
def test_requests_views_offline_mode_and_banner() -> None:
    """Verify requests_list.js and requests_views.js render the exact offline warning banner and handle cache."""
    list_js_path = STATIC_DIR / "js" / "views" / "requests_list.js"
    assert list_js_path.is_file(), "requests_list.js must exist"
    list_content = list_js_path.read_text(encoding="utf-8")

    expected_banner = (
        '<div class="banner banner-warning"><span class="icon">⚠️</span> '
        "You are currently offline. Displaying cached read-only requests. "
        "Changes cannot be submitted until connection is restored.</div>"
    )

    # Must contain exact warning banner HTML
    assert expected_banner in list_content

    # Must call getOfflineRequests and saveOfflineRequests
    assert "getOfflineRequests" in list_content
    assert "saveOfflineRequests" in list_content

    # Must sanitize rendered fields with escapeHtml
    assert "escapeHtml" in list_content

    # Verify requests_views.js re-exports and helpers
    views_js_path = STATIC_DIR / "js" / "views" / "requests_views.js"
    assert views_js_path.is_file(), "requests_views.js must exist"
    views_content = views_js_path.read_text(encoding="utf-8")

    assert "renderRequestsList" in views_content
    assert "renderRequestDetail" in views_content
    assert expected_banner in views_content
    assert "getOfflineRequests" in views_content
    assert "saveOfflineRequests" in views_content


@pytest.mark.unit
def test_css_components_banner_styling() -> None:
    """Verify components.css defines .banner and .banner-warning design tokens."""
    css_path = STATIC_DIR / "css" / "components.css"
    assert css_path.is_file(), "components.css must exist"
    content = css_path.read_text(encoding="utf-8")

    assert ".banner" in content
    assert ".banner-warning" in content
    assert "--warning-bg" in content
    assert "--warning-border" in content
    assert "--warning-text" in content


@pytest.mark.unit
def test_csp_zero_inline_scripts_intact() -> None:
    """Verify strict CSP compliance with zero inline scripts in index.html."""
    index_path = STATIC_DIR / "index.html"
    content = index_path.read_text(encoding="utf-8")

    inline_events = re.findall(r"\bon\w+\s*=", content, re.IGNORECASE)
    assert not inline_events, f"Found inline event handlers: {inline_events}"

    script_tags = re.findall(r"<script\b[^>]*>(.*?)</script>", content, re.IGNORECASE | re.DOTALL)
    for script_content in script_tags:
        assert not script_content.strip(), "Found prohibited inline JavaScript code"
