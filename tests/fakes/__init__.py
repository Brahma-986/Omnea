"""Test doubles and fakes."""

from tests.fakes.fake_email_sender import CapturedEmail, FakeEmailSender
from tests.fakes.fake_stream_ticket_store import FakeStreamTicketStore

__all__ = ["FakeEmailSender", "CapturedEmail", "FakeStreamTicketStore"]
