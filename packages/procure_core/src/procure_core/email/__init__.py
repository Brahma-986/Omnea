"""Email package re-exports."""

from procure_core.email.sender import (
    EmailSenderProtocol,
    PostmarkEmailSender,
    ResendEmailSender,
    SesEmailSender,
    get_email_sender,
    set_email_sender,
)

__all__ = [
    "EmailSenderProtocol",
    "ResendEmailSender",
    "PostmarkEmailSender",
    "SesEmailSender",
    "get_email_sender",
    "set_email_sender",
]
