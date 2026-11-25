import logging

from app.core.logging import JsonFormatter, RedactingFilter, redact_sensitive_text


def test_redacts_common_secrets_from_log_text():
    raw = (
        "authorization: Bearer super-secret-token "
        "api_key=provider-secret password=hunter2 "
        "sk-abcdefghijklmnopqrstuvwxyz123456"
    )
    cleaned = redact_sensitive_text(raw)
    assert "super-secret-token" not in cleaned
    assert "provider-secret" not in cleaned
    assert "hunter2" not in cleaned
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in cleaned
    assert "[REDACTED]" in cleaned


def test_redacts_log_record_message_before_json_formatting():
    record = logging.LogRecord(
        "test", logging.ERROR, __file__, 1,
        "request failed api_key=%s", ("private-api-token",), None
    )
    RedactingFilter().filter(record)
    rendered = JsonFormatter().format(record)
    assert "private-api-token" not in rendered
    assert "[REDACTED]" in rendered
