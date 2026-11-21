from ui.api_access import api_timeout_for


def test_workflow_create_uses_longer_inline_timeout():
    assert api_timeout_for("/api/v1/runs", run_timeout_seconds=150) == 150


def test_other_api_posts_keep_short_timeout():
    assert api_timeout_for("/api/v1/runs/run-1/approval") == 15
    assert api_timeout_for("/api/v1/runs/history") == 15


def test_workflow_timeout_reads_environment(monkeypatch):
    monkeypatch.setenv("RUN_REQUEST_TIMEOUT_SECONDS", "82")
    assert api_timeout_for("/api/v1/runs") == 82


def test_workflow_timeout_is_bounded_and_invalid_values_fall_back(monkeypatch):
    assert api_timeout_for("/api/v1/runs", run_timeout_seconds=5) == 30
    assert api_timeout_for("/api/v1/runs", run_timeout_seconds=999) == 180
    monkeypatch.setenv("RUN_REQUEST_TIMEOUT_SECONDS", "not-a-number")
    assert api_timeout_for("/api/v1/runs") == 150
