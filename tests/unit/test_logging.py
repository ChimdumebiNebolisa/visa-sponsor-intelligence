"""Tests for structured logging and secret redaction."""

import json
import logging

from sponsor_intel.logging import JsonFormatter


def test_json_formatter_redacts_sensitive_extra_fields() -> None:
    record = logging.LogRecord("sponsor_intel", logging.INFO, __file__, 1, "ready", (), None)
    record.api_key = "secret-value"
    record.build_id = "test-build"

    payload = json.loads(JsonFormatter().format(record))

    assert payload["message"] == "ready"
    assert payload["api_key"] == "[REDACTED]"
    assert payload["build_id"] == "test-build"
