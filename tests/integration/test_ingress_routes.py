"""Integration tests for FastAPI ingress endpoints (Slack Events, Commands, Teams, Email)."""

import json
from urllib.parse import urlencode

import pytest
from httpx import AsyncClient

from procure_core.config import get_settings
from tests.unit.test_slack_adapter import generate_slack_headers


@pytest.mark.integration
async def test_slack_url_verification_challenge(async_client: AsyncClient) -> None:
    """Slack URL verification challenge returns the challenge string."""
    settings = get_settings()
    payload = {
        "type": "url_verification",
        "token": "verification_token",
        "challenge": "3eZbrAqImPVSmqlOKJwEDAnG",
    }
    body = json.dumps(payload).encode("utf-8")
    headers = generate_slack_headers(body, settings.SLACK_SIGNING_SECRET)

    response = await async_client.post(
        "/api/v1/ingress/slack/events",
        content=body,
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json() == {"challenge": "3eZbrAqImPVSmqlOKJwEDAnG"}


@pytest.mark.integration
async def test_slack_event_acceptance_and_deduplication(async_client: AsyncClient) -> None:
    """Slack message is enqueued within SLA and duplicates are acknowledged safely."""
    settings = get_settings()
    event_id = "Ev_acceptance_999"
    payload = {
        "type": "event_callback",
        "event_id": event_id,
        "team_id": "T_TEST_WORKSPACE",
        "event": {
            "type": "app_mention",
            "user": "U_BOB",
            "text": "<@U_BOT> Need approval for AWS Cloud subscription renewal",
            "ts": "1710000000.999999",
            "channel": "C_PROCUREMENT",
        },
    }
    body = json.dumps(payload).encode("utf-8")
    headers = generate_slack_headers(body, settings.SLACK_SIGNING_SECRET)

    # 1. First event
    resp1 = await async_client.post(
        "/api/v1/ingress/slack/events",
        content=body,
        headers=headers,
    )
    assert resp1.status_code == 200
    assert resp1.json() == {"ok": True}

    # 2. Duplicate event with identical signature/event_id is ignored
    resp2 = await async_client.post(
        "/api/v1/ingress/slack/events",
        content=body,
        headers=headers,
    )
    assert resp2.status_code == 200
    assert resp2.json() == {"ok": True}


@pytest.mark.integration
async def test_slack_slash_command(async_client: AsyncClient) -> None:
    """Slack slash command endpoint enqueues and returns ephemeral ack."""
    settings = get_settings()
    form_data = {
        "command": "/procure",
        "text": "new vendor onboarding request for Datadog",
        "user_id": "U_CHARLIE",
        "channel_id": "C_DEVOPS",
        "team_id": "T_TEST_WORKSPACE",
        "trigger_id": "trig_slash_12345",
        "response_url": "https://hooks.slack.com/commands/1234/5678",
    }
    body = urlencode(form_data).encode("utf-8")
    headers = generate_slack_headers(body, settings.SLACK_SIGNING_SECRET)
    headers["content-type"] = "application/x-www-form-urlencoded"

    response = await async_client.post(
        "/api/v1/ingress/slack/commands",
        content=body,
        headers=headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["response_type"] == "ephemeral"
    assert "received" in data["text"]


@pytest.mark.integration
async def test_teams_and_email_disabled_by_default(async_client: AsyncClient) -> None:
    """Teams and Email ingress endpoints return 404 when disabled by default."""
    teams_resp = await async_client.post(
        "/api/v1/ingress/teams",
        json={"type": "message", "text": "hello from teams"},
    )
    assert teams_resp.status_code == 404

    email_resp = await async_client.post(
        "/api/v1/ingress/email",
        json={"from": "supplier@acme.com", "text": "invoice attached"},
    )
    assert email_resp.status_code == 404
