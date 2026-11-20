"""Slack App Manifest Generator and OAuth flow helpers."""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

from procure_core.config import get_settings


def generate_slack_manifest(base_url: str, app_name: str = "Omnea Procurement") -> dict[str, Any]:
    """Generate Slack App Manifest compliant with modern Slack API standards."""
    clean_url = base_url.rstrip("/")
    return {
        "_metadata": {
            "major_version": 1,
            "minor_version": 1,
        },
        "display_information": {
            "name": app_name,
            "description": "AI-Native Procurement Orchestration with Parallel Multi-Agent Approvals",
            "background_color": "#2563eb",
        },
        "features": {
            "app_home": {
                "home_tab_enabled": True,
                "messages_tab_enabled": True,
                "messages_tab_read_only_enabled": False,
            },
            "bot_user": {
                "display_name": app_name,
                "always_online": True,
            },
            "slash_commands": [
                {
                    "command": "/procure",
                    "url": f"{clean_url}/api/v1/ingress/slack/command",
                    "description": "Initiate a new procurement request or check approval status",
                    "usage_hint": "<vendor, amount, justification>",
                    "should_escape": False,
                }
            ],
        },
        "oauth_config": {
            "redirect_urls": [
                f"{clean_url}/api/v1/slack/oauth/callback",
            ],
            "scopes": {
                "bot": [
                    "chat:write",
                    "commands",
                    "app_mentions:read",
                    "users:read",
                    "users:read.email",
                    "channels:history",
                    "groups:history",
                ]
            },
        },
        "settings": {
            "interactivity": {
                "is_enabled": True,
                "request_url": f"{clean_url}/api/v1/slack/interactive",
            },
            "event_subscriptions": {
                "request_url": f"{clean_url}/api/v1/ingress/slack/events",
                "bot_events": ["app_mention"],
            },
            "org_deploy_enabled": False,
            "socket_mode_enabled": False,
            "token_rotation_enabled": False,
        },
    }


def create_slack_oauth_url(tenant_id: str, client_id: str, redirect_uri: str) -> tuple[str, str]:
    """Generate Slack OAuth authorization URL with signed state token to prevent CSRF."""
    settings = get_settings()
    ts = str(int(time.time()))
    state_basestring = f"{tenant_id}:{ts}"
    signature = hmac.new(
        settings.JWT_SECRET_KEY.encode("utf-8"),
        state_basestring.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    state_token = f"{tenant_id}:{ts}:{signature}"

    scopes = "chat:write,commands,app_mentions:read,users:read,users:read.email,channels:history,groups:history"
    oauth_url = (
        f"https://slack.com/oauth/v2/authorize"
        f"?client_id={client_id}"
        f"&scope={scopes}"
        f"&redirect_uri={redirect_uri}"
        f"&state={state_token}"
    )
    return oauth_url, state_token


def verify_slack_oauth_state(state_token: str, expected_tenant_id: str) -> bool:
    """Validate OAuth state parameter freshness and HMAC signature."""
    settings = get_settings()
    parts = state_token.split(":")
    if len(parts) != 3:
        return False
    tenant_id, ts_str, signature = parts
    if tenant_id != expected_tenant_id:
        return False

    try:
        ts = int(ts_str)
    except ValueError:
        return False

    # 10 minute expiration
    if abs(time.time() - ts) > 600:
        return False

    expected_basestring = f"{tenant_id}:{ts_str}".encode()
    expected_sig = hmac.new(
        settings.JWT_SECRET_KEY.encode("utf-8"),
        expected_basestring,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected_sig, signature)
