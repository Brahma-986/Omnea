"""Unit tests for Slack signature verification, event filtering, and deduplication keys."""

import hashlib
import hmac
import time
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from procure_ingress.adapters.slack import SlackIngressAdapter, verify_slack_signature


def generate_slack_headers(
    body: bytes, secret: str, timestamp: float | None = None
) -> dict[str, str]:
    """Helper to generate valid Slack HMAC headers."""
    ts = str(int(timestamp if timestamp is not None else time.time()))
    sig_basestring = b"v0:" + ts.encode("utf-8") + b":" + body
    computed = hmac.new(secret.encode("utf-8"), sig_basestring, hashlib.sha256).hexdigest()
    return {
        "x-slack-request-timestamp": ts,
        "x-slack-signature": f"v0={computed}",
    }


def make_mock_request(headers: dict[str, str], body: bytes) -> Request:
    """Create a mock Request object with raw bytes and headers."""
    req = AsyncMock(spec=Request)
    req.headers = headers
    req.body = AsyncMock(return_value=body)
    return req


@pytest.mark.unit
async def test_verify_slack_signature_valid() -> None:
    secret = "test_secret_123"
    body = b'{"type": "event_callback", "event_id": "Ev12345"}'
    headers = generate_slack_headers(body, secret)
    req = make_mock_request(headers, body)

    result = await verify_slack_signature(req, secret)
    assert result == body


@pytest.mark.unit
async def test_verify_slack_signature_invalid_secret() -> None:
    secret = "correct_secret"
    body = b'{"type": "event_callback"}'
    headers = generate_slack_headers(body, "wrong_secret")
    req = make_mock_request(headers, body)

    with pytest.raises(HTTPException) as exc_info:
        await verify_slack_signature(req, secret)
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Unauthorized"


@pytest.mark.unit
async def test_verify_slack_signature_tampered_body() -> None:
    secret = "test_secret"
    original_body = b'{"amount": 100}'
    headers = generate_slack_headers(original_body, secret)
    tampered_body = b'{"amount": 999999}'
    req = make_mock_request(headers, tampered_body)

    with pytest.raises(HTTPException) as exc_info:
        await verify_slack_signature(req, secret)
    assert exc_info.value.status_code == 401


@pytest.mark.unit
async def test_verify_slack_signature_stale_timestamp() -> None:
    secret = "test_secret"
    body = b'{"type": "event_callback"}'
    stale_time = time.time() - 301  # > 300 seconds
    headers = generate_slack_headers(body, secret, timestamp=stale_time)
    req = make_mock_request(headers, body)

    with pytest.raises(HTTPException) as exc_info:
        await verify_slack_signature(req, secret)
    assert exc_info.value.status_code == 401


@pytest.mark.unit
async def test_verify_slack_signature_missing_headers() -> None:
    req = make_mock_request({}, b"{}")
    with pytest.raises(HTTPException) as exc_info:
        await verify_slack_signature(req, "secret")
    assert exc_info.value.status_code == 401


@pytest.mark.unit
def test_slack_adapter_filters_bot_echoes() -> None:
    adapter = SlackIngressAdapter()

    # Bot with bot_id
    payload_bot_id = {"event": {"type": "message", "bot_id": "B12345", "text": "hello"}}
    assert adapter.should_ignore(payload_bot_id) is True

    # Bot with subtype bot_message
    payload_bot_subtype = {"event": {"type": "message", "subtype": "bot_message", "text": "hello"}}
    assert adapter.should_ignore(payload_bot_subtype) is True

    # Subtypes: message_changed, message_deleted
    payload_changed = {"event": {"type": "message", "subtype": "message_changed"}}
    assert adapter.should_ignore(payload_changed) is True

    payload_deleted = {"event": {"type": "message", "subtype": "message_deleted"}}
    assert adapter.should_ignore(payload_deleted) is True

    # file_shared event ignored in favor of message with files
    payload_file_shared = {"event": {"type": "file_shared", "file_id": "F123"}}
    assert adapter.should_ignore(payload_file_shared) is True


@pytest.mark.unit
def test_slack_adapter_dedup_keys() -> None:
    adapter = SlackIngressAdapter()

    cmd_payload = {"command": "/procure", "trigger_id": "trig_987"}
    assert adapter.extract_dedup_key(cmd_payload) == "slack:cmd:trig_987"

    event_payload = {"event_id": "Ev_555", "event": {"type": "message"}}
    assert adapter.extract_dedup_key(event_payload) == "slack:event:Ev_555"
