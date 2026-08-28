"""Shared secret redaction primitives for logs, ledgers, and provider output."""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"
SECRET_KEY = re.compile(r"(?i)(secret|password|token|api[_-]?key|cookie|authorization)")
SECRET_VALUE = re.compile(
    r"(?i)(bearer\s+[a-z0-9._~+/=-]+|(?:sk|key|token)[-_][a-z0-9_-]{8,})"
)


def redact_text(value: str) -> str:
    return SECRET_VALUE.sub(REDACTED, value)


def redact(value: Any, *, key: str | None = None) -> Any:
    if key is not None and SECRET_KEY.search(key):
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {
            str(item_key): redact(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
    return value
