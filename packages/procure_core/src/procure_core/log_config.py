"""Structured JSON logging configuration with sensitive data redaction."""

from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

# Context variables for request tracing and verified tenant scope
request_id_ctx: ContextVar[str | None] = ContextVar("request_id_ctx", default=None)
tenant_id_ctx: ContextVar[str | None] = ContextVar("tenant_id_ctx", default=None)

# Regular expressions for scrubbing sensitive tokens, keys, and authorization values
REDACTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # JWT tokens (starts with eyJ..., 3 base64url segments separated by dots)
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+"), "[REDACTED]"),
    # Sensitive URL query parameters (token=..., ticket=..., key=..., password=..., secret=...)
    (
        re.compile(
            r"([?&](?:token|ticket|api_key|apiKey|key|secret|password|authorization)=)[^&\s\"'>]+",
            re.IGNORECASE,
        ),
        r"\1[REDACTED]",
    ),
    # Authorization headers with Bearer, Basic, or raw credential value
    (
        re.compile(
            r"((?:Authorization|authorization)\s*[:=]\s*(?:Bearer\s+|Basic\s+)?)[^\s,]+",
            re.IGNORECASE,
        ),
        r"\1[REDACTED]",
    ),
    (
        re.compile(
            r"((?:x-api-key|X-API-Key)\s*[:=]\s*)[^\s,]+",
            re.IGNORECASE,
        ),
        r"\1[REDACTED]",
    ),
    # Standalone Bearer and Basic authentication tokens
    (re.compile(r"\b(Bearer\s+)[A-Za-z0-9._~+/-]+=*", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"\b(Basic\s+)[A-Za-z0-9._~+/-]+=*", re.IGNORECASE), r"\1[REDACTED]"),
    # Omnea API keys (omnea_...)
    (re.compile(r"omnea_[A-Za-z0-9_]{16,}"), "[REDACTED]"),
]


def redact_sensitive_data(text: str) -> str:
    """Scrub JWT tokens, API keys, credentials, and authorization query values."""
    if not text or not isinstance(text, str):
        return text
    for pattern, replacement in REDACTION_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _sanitize_obj(obj: Any) -> Any:
    """Recursively redact sensitive strings in dicts, lists, or tuples."""
    if isinstance(obj, str):
        return redact_sensitive_data(obj)
    if isinstance(obj, dict):
        return {k: _sanitize_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_obj(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_sanitize_obj(v) for v in obj)
    return obj


class RedactingFilter(logging.Filter):
    """Logging filter that redacts sensitive tokens and secrets from log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(
                    redact_sensitive_data(a) if isinstance(a, str) else a for a in record.args
                )
            elif isinstance(record.args, dict):
                record.args = {
                    k: (redact_sensitive_data(v) if isinstance(v, str) else v)
                    for k, v in record.args.items()
                }
        elif isinstance(record.msg, str):
            record.msg = redact_sensitive_data(record.msg)

        if record.exc_text:
            record.exc_text = redact_sensitive_data(record.exc_text)
        return True


class JSONLogFormatter(logging.Formatter):
    """Custom logging formatter that serializes records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        raw_message = record.getMessage()
        safe_message = redact_sensitive_data(raw_message)

        log_payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": safe_message,
        }

        # Add trace context if available
        req_id = request_id_ctx.get()
        if req_id:
            log_payload["request_id"] = req_id

        tenant_id = tenant_id_ctx.get()
        if tenant_id:
            log_payload["tenant_id"] = tenant_id

        # Attach standard extras if present, sanitized
        extra = getattr(record, "extra", None)
        if isinstance(extra, dict):
            log_payload["extra"] = _sanitize_obj(extra)

        # Attach exception details, sanitized
        if record.exc_info:
            log_payload["exception"] = redact_sensitive_data(self.formatException(record.exc_info))
        elif record.exc_text:
            log_payload["exception"] = redact_sensitive_data(record.exc_text)

        return json.dumps(log_payload, default=str)


def setup_logging(level: str = "INFO") -> None:
    """Configure the root logger and uvicorn loggers with JSON formatting and redaction."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Clear existing handlers
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(JSONLogFormatter())
    console_handler.addFilter(RedactingFilter())
    root_logger.addHandler(console_handler)
    root_logger.addFilter(RedactingFilter())

    # Configure uvicorn loggers to redact tokens from access lines
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error", "api"):
        u_logger = logging.getLogger(name)
        u_logger.addFilter(RedactingFilter())
        for h in u_logger.handlers:
            h.addFilter(RedactingFilter())


def get_logger(name: str) -> logging.Logger:
    """Return a logger instance with redaction filter installed."""
    lg = logging.getLogger(name)
    lg.addFilter(RedactingFilter())
    return lg
