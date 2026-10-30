"""Structured JSON logging with redaction of secret-looking fields."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

_SECRET_KEY = re.compile(r"(key|token|secret|password|passwd|authorization|database_url)", re.I)
_URL_PASSWORD = re.compile(r"(\w+://[^:/@\s]+:)([^@\s]+)(@)")
REDACTED = "***REDACTED***"


def redact(value):
    """Recursively mask secret-looking keys and passwords embedded in URLs."""
    if isinstance(value, dict):
        return {k: (REDACTED if _SECRET_KEY.search(str(k)) else redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return _URL_PASSWORD.sub(r"\1" + REDACTED + r"\3", value)
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        extra = getattr(record, "fields", None)
        if extra:
            entry["fields"] = redact(extra)
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def configure(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
