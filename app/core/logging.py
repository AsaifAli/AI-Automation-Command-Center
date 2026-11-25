import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any


_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\\s*[:=]\\s*bearer\\s+)[^\\s,;]+"),
    re.compile(r"(?i)(\\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)\\b\\s*[:=]\\s*)[^\\s,;]+"),
    re.compile(r"(?i)\\bsk-[A-Za-z0-9_-]{16,}\\b"),
)


def redact_sensitive_text(value: str) -> str:
    """Redact common credential formats before they reach stdout/APM logs."""
    redacted = value
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(r"\\1[REDACTED]" if pattern.groups else "[REDACTED]", redacted)
    return redacted


class RedactingFilter(logging.Filter):
    """Sanitize messages and exception text before formatters serialize records."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            message = "[unformattable log message]"
        record.msg = redact_sensitive_text(message)
        record.args = ()
        if record.exc_info:
            exc_type, exc_value, _ = record.exc_info
            if exc_value is not None:
                record.exc_text = redact_sensitive_text(
                    f"{exc_type.__name__}: {exc_value}"
                )
            record.exc_info = None
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_sensitive_text(record.getMessage()),
        }
        if record.exc_text:
            payload["exception"] = redact_sensitive_text(record.exc_text)
        return json.dumps(payload)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RedactingFilter())
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
