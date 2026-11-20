"""Unit tests for Stage 6: Slack App Manifest, OAuth Flow, In-Place Updates, and Multichannel Notifications."""

from __future__ import annotations

import uuid

import pytest

from procure_core.db import get_tenant_session
from procure_core.email.sender import EmailSenderProtocol
from procure_core.models.notification import UserNotificationPreferenceModel
from procure_core.notifications.service import NotificationService
from procure_core.slack.manifest import (
    create_slack_oauth_url,
    generate_slack_manifest,
    verify_slack_oauth_state,
)
from tests.fakes.fake_slack_client import FakeSlackClient


class FakeEmailSender(EmailSenderProtocol):
    """Test fake capturing sent email messages."""

    def __init__(self) -> None:
        self.sent_emails: list[dict[str, str]] = []

    async def send_email(
        self,
        to: str,
        subject: str,
        html_content: str,
        text_content: str | None = None,
    ) -> bool:
        self.sent_emails.append(
            {
                "to": to,
                "subject": subject,
                "html": html_content,
                "text": text_content or "",
            }
        )
        return True


def test_slack_manifest_generation() -> None:
    """Verify Slack App Manifest structure, scopes, and interactive URLs."""
    manifest = generate_slack_manifest("https://omnea.corp.internal")

    # Metadata & display
    assert manifest["display_information"]["name"] == "Omnea Procurement"
    assert manifest["features"]["bot_user"]["always_online"] is True

    # Slash commands
    cmds = manifest["features"]["slash_commands"]
    assert len(cmds) == 1
    assert cmds[0]["command"] == "/procure"
    assert "https://omnea.corp.internal/api/v1/ingress/slack/command" == cmds[0]["url"]

    # OAuth scopes & redirect URLs
    scopes = manifest["oauth_config"]["scopes"]["bot"]
    assert "chat:write" in scopes
    assert "commands" in scopes
    assert "channels:history" in scopes
    assert "users:read.email" in scopes

    redirects = manifest["oauth_config"]["redirect_urls"]
    assert "https://omnea.corp.internal/api/v1/slack/oauth/callback" in redirects

    # Interactivity & event subscriptions
    assert manifest["settings"]["interactivity"]["is_enabled"] is True
    assert (
        manifest["settings"]["interactivity"]["request_url"]
        == "https://omnea.corp.internal/api/v1/slack/interactive"
    )


def test_slack_oauth_url_and_state_verification() -> None:
    """Verify HMAC-SHA256 signed OAuth URL generation and tamper detection."""
    tenant_id = "tenant_acme_corp"
    client_id = "slack_client_xyz"
    redirect_uri = "https://omnea.corp.internal/api/v1/slack/oauth/callback"

    oauth_url, state_token = create_slack_oauth_url(
        tenant_id=tenant_id,
        client_id=client_id,
        redirect_uri=redirect_uri,
    )

    assert "https://slack.com/oauth/v2/authorize" in oauth_url
    assert f"client_id={client_id}" in oauth_url
    assert f"state={state_token}" in oauth_url

    # Valid token verification
    assert verify_slack_oauth_state(state_token, expected_tenant_id=tenant_id) is True

    # Tampered token verification
    tampered_token = state_token[:-6] + "badbad"
    assert verify_slack_oauth_state(tampered_token, expected_tenant_id=tenant_id) is False

    # Mismatched tenant
    assert verify_slack_oauth_state(state_token, expected_tenant_id="wrong_tenant") is False

    # Invalid format token
    assert verify_slack_oauth_state("invalid:token", expected_tenant_id=tenant_id) is False


@pytest.mark.asyncio
async def test_slack_client_inplace_update_vs_repost() -> None:
    """Verify Slack client performs in-place chat.update rather than posting duplicate messages."""
    fake_slack = FakeSlackClient()
    channel = "C_PROCUREMENT_ALERTS"
    req_id = "req-987654"

    # 1. Post initial message
    post_res = await fake_slack.post_message(
        channel=channel,
        text=f"New procurement request pending approval: {req_id} for Snowflake $48,000",
        blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": f"Request {req_id}"}}],
    )
    assert post_res.ok is True
    initial_ts = post_res.ts

    # Verify channel has 1 message
    history1 = await fake_slack.get_conversations_history(channel=channel)
    assert len(history1) == 1
    assert req_id in history1[0]["text"]

    # 2. Lookup existing message by request_id
    found_ts = await fake_slack.find_message_by_request_id(channel=channel, request_id=req_id)
    assert found_ts == initial_ts

    # 3. Update existing message in-place
    update_res = await fake_slack.update_message(
        channel=channel,
        ts=found_ts,
        text=f"Request {req_id} APPROVED by Finance Admin. PO #PO-SNOW-001 created.",
        blocks=[
            {"type": "section", "text": {"type": "mrkdwn", "text": f"Request {req_id} Approved"}}
        ],
    )
    assert update_res.ok is True
    assert update_res.ts == initial_ts

    # Channel MUST STILL HAVE EXACTLY 1 MESSAGE (not reposted!)
    history2 = await fake_slack.get_conversations_history(channel=channel)
    assert len(history2) == 1
    assert "APPROVED by Finance Admin" in history2[0]["text"]

    # Verify call log recorded updateMessage rather than postMessage
    assert fake_slack.call_log[-1]["action"] == "updateMessage"


@pytest.mark.asyncio
async def test_notification_service_preferences_enforcement() -> None:
    """Verify NotificationService honors per-user channel preferences and skips disabled channels."""
    tenant_id = f"test_notif_{uuid.uuid4().hex[:8]}"
    user_id = str(uuid.uuid4())
    user_uuid = uuid.UUID(user_id)
    email = "lead_buyer@omnea.ai"

    fake_email = FakeEmailSender()
    fake_slack = FakeSlackClient()

    notif_service = NotificationService(
        email_sender=fake_email,
        slack_client=fake_slack,
    )

    # 1. Dispatch with default preferences -> delivers to all 3 channels
    res_default = await notif_service.notify_user(
        tenant_id=tenant_id,
        user_id=user_id,
        email=email,
        event_type="approval_requested",
        title="Approval Requested: Datadog APM",
        message="Please review Datadog APM subscription for $48,000",
        slack_channel="C_PROCUREMENT",
    )
    assert "in_app" in res_default.channels_delivered
    assert "email" in res_default.channels_delivered
    assert "slack" in res_default.channels_delivered
    assert len(fake_email.sent_emails) == 1
    assert len(fake_slack.channels.get("C_PROCUREMENT", [])) == 1

    # 2. Update user preferences: disable email and slack
    async with get_tenant_session(tenant_id) as session:
        pref = UserNotificationPreferenceModel(
            tenant_id=tenant_id,
            user_id=user_uuid,
            channels={"in_app": True, "email": False, "slack": False},
            event_subscriptions={"approval_requested": True, "request_rejected": False},
        )
        session.add(pref)
        await session.commit()

    # 3. Dispatch again -> only delivers to in_app, skips email and slack
    res_custom = await notif_service.notify_user(
        tenant_id=tenant_id,
        user_id=user_id,
        email=email,
        event_type="approval_requested",
        title="Approval Requested: Snowflake",
        message="Please review Snowflake expansion for $120,000",
        slack_channel="C_PROCUREMENT",
    )
    assert res_custom.channels_delivered == ["in_app"]
    assert "email" in res_custom.skipped_by_preferences
    assert "slack" in res_custom.skipped_by_preferences
    # Email and Slack counts should remain unchanged
    assert len(fake_email.sent_emails) == 1
    assert len(fake_slack.channels.get("C_PROCUREMENT", [])) == 1

    # 4. Dispatch unsubscribed event (request_rejected) -> completely skipped
    res_unsub = await notif_service.notify_user(
        tenant_id=tenant_id,
        user_id=user_id,
        email=email,
        event_type="request_rejected",
        title="Request Rejected",
        message="Request was rejected",
        slack_channel="C_PROCUREMENT",
    )
    assert res_unsub.channels_delivered == []
    assert "all_events" in res_unsub.skipped_by_preferences
