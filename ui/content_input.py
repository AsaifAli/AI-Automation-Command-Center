"""Input parsing helpers shared by the Streamlit content form and tests."""

from __future__ import annotations


def parse_topic_briefs(raw_text: str) -> list[str]:
    """Treat each non-empty line as one complete brief; preserve punctuation."""
    return [line.strip() for line in (raw_text or "").splitlines() if line.strip()]
