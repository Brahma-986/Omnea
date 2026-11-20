"""Slack Integration: Protocol, Real Client, Manifest, and OAuth Package."""

from procure_core.slack.client import ISlackClient, RealSlackClient, SlackMessageResult
from procure_core.slack.manifest import (
    create_slack_oauth_url,
    generate_slack_manifest,
    verify_slack_oauth_state,
)

__all__ = [
    "ISlackClient",
    "RealSlackClient",
    "SlackMessageResult",
    "generate_slack_manifest",
    "create_slack_oauth_url",
    "verify_slack_oauth_state",
]
