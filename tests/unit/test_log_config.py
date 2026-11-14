"""Unit tests for structured JSON logging."""

import json
import logging

import pytest

from procure_core.log_config import (
    JSONLogFormatter,
    request_id_ctx,
    tenant_id_ctx,
)


@pytest.mark.unit
def test_json_log_formatter_basic() -> None:
    formatter = JSONLogFormatter()
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="Test message %s",
        args=("arg1",),
        exc_info=None,
    )

    formatted = formatter.format(record)
    data = json.loads(formatted)

    assert data["level"] == "INFO"
    assert data["logger"] == "test_logger"
    assert data["message"] == "Test message arg1"
    assert "timestamp" in data


@pytest.mark.unit
def test_json_log_formatter_with_context() -> None:
    formatter = JSONLogFormatter()
    token_req = request_id_ctx.set("req-12345")
    token_tenant = tenant_id_ctx.set("tenant-abc")

    try:
        record = logging.LogRecord(
            name="test_logger",
            level=logging.WARNING,
            pathname=__file__,
            lineno=25,
            msg="Contextual warning",
            args=(),
            exc_info=None,
        )
        formatted = formatter.format(record)
        data = json.loads(formatted)

        assert data["request_id"] == "req-12345"
        assert data["tenant_id"] == "tenant-abc"
    finally:
        request_id_ctx.reset(token_req)
        tenant_id_ctx.reset(token_tenant)


@pytest.mark.unit
def test_json_log_formatter_with_exception() -> None:
    formatter = JSONLogFormatter()
    try:
        raise ValueError("Simulated fault")
    except ValueError:
        import sys

        exc_info = sys.exc_info()
        record = logging.LogRecord(
            name="test_logger",
            level=logging.ERROR,
            pathname=__file__,
            lineno=45,
            msg="Error occurred",
            args=(),
            exc_info=exc_info,
        )
        formatted = formatter.format(record)
        data = json.loads(formatted)

        assert "exception" in data
        assert "ValueError: Simulated fault" in data["exception"]
