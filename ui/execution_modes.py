"""Shared UI behavior for API execution-mode reporting and run controls."""

from __future__ import annotations

QUEUE_BACKED_EXECUTION_MODES = frozenset(
    {
        "qstash_http_queue",
        "redis_worker",
        "embedded_rq_worker",
    }
)

_EXECUTION_MODE_LABELS = {
    "qstash_http_queue": "QStash HTTP message queue",
    "redis_worker": "Redis + worker",
    "embedded_rq_worker": "Embedded RQ worker",
    "inline_background": "API background execution",
}


def is_queue_backed_execution(execution_mode: str | None) -> bool:
    """Return whether Run History can safely offer Pause, Resume, and Stop."""
    return execution_mode in QUEUE_BACKED_EXECUTION_MODES


def execution_mode_label(execution_mode: str | None) -> str:
    """Return a user-facing label for the configured execution mode."""
    return _EXECUTION_MODE_LABELS.get(execution_mode, "Unknown")
