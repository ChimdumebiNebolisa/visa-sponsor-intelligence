"""Structured application logging with defensive secret redaction."""

from __future__ import annotations

import json
import logging as stdlib_logging
from datetime import UTC, datetime
from typing import Any

from sponsor_intel.config import LogLevel

_STANDARD_RECORD_FIELDS = frozenset(stdlib_logging.LogRecord("", 0, "", 0, "", (), None).__dict__)
_SENSITIVE_KEY_FRAGMENTS = ("authorization", "credential", "key", "password", "secret", "token")


def _is_sensitive_key(key: str) -> bool:
    normalized = key.casefold()
    return any(fragment in normalized for fragment in _SENSITIVE_KEY_FRAGMENTS)


class JsonFormatter(stdlib_logging.Formatter):
    """Emit one compact JSON object per log record."""

    def format(self, record: stdlib_logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_FIELDS or key in payload:
                continue
            payload[key] = "[REDACTED]" if _is_sensitive_key(key) else value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_logging(level: LogLevel | str = LogLevel.INFO) -> stdlib_logging.Logger:
    """Configure and return the application logger."""

    logger = stdlib_logging.getLogger("sponsor_intel")
    handler = stdlib_logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(str(level))
    logger.propagate = False
    return logger


def get_logger(name: str) -> stdlib_logging.Logger:
    """Return a child logger within the application namespace."""

    return stdlib_logging.getLogger(f"sponsor_intel.{name}")
