"""Channel ingress adapter re-exports."""

from procure_ingress.adapters.base import BaseIngressAdapter
from procure_ingress.adapters.email import EmailIngressAdapter
from procure_ingress.adapters.slack import SlackIngressAdapter, verify_slack_signature
from procure_ingress.adapters.teams import TeamsIngressAdapter

__all__ = [
    "BaseIngressAdapter",
    "SlackIngressAdapter",
    "TeamsIngressAdapter",
    "EmailIngressAdapter",
    "verify_slack_signature",
]
