"""Structured JSON logging configuration."""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

# Context variables for request tracing
request_id_ctx: ContextVar[str | None] = ContextVar("request_id_ctx", default=None)
tenant_id_ctx: ContextVar[str | None] = ContextVar("tenant_id_ctx", default=None)


class JSONLogFormatter(logging.Formatter):
    """Custom logging formatter that serializes records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        log_payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add trace context if available
        req_id = request_id_ctx.get()
        if req_id:
            log_payload["request_id"] = req_id

        tenant_id = tenant_id_ctx.get()
        if tenant_id:
            log_payload["tenant_id"] = tenant_id

        # Attach standard extras if present
        extra = getattr(record, "extra", None)
        if isinstance(extra, dict):
            log_payload["extra"] = extra

        # Attach exception details
        if record.exc_info:
            log_payload["exception"] = self.formatException(record.exc_info)
        elif record.exc_text:
            log_payload["exception"] = record.exc_text

        return json.dumps(log_payload, default=str)


def setup_logging(level: str = "INFO") -> None:
    """Configure the root logger with the structured JSON formatter."""
    root_logger = logging.getLogger()
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    root_logger.setLevel(numeric_level)

    # Clear existing handlers
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(JSONLogFormatter())
    root_logger.addHandler(console_handler)


def get_logger(name: str) -> logging.Logger:
    """Return a logger instance with the given name."""
    return logging.getLogger(name)
