from ui.execution_modes import execution_mode_label, is_queue_backed_execution


def test_run_controls_are_enabled_for_every_queue_backed_execution_mode():
    assert is_queue_backed_execution("qstash_http_queue") is True
    assert is_queue_backed_execution("redis_worker") is True
    assert is_queue_backed_execution("embedded_rq_worker") is True


def test_run_controls_are_not_enabled_for_inline_or_unknown_execution_modes():
    assert is_queue_backed_execution("inline_background") is False
    assert is_queue_backed_execution(None) is False
    assert is_queue_backed_execution("unknown") is False


def test_execution_mode_labels_name_the_embedded_worker_explicitly():
    assert execution_mode_label("embedded_rq_worker") == "Embedded RQ worker"
    assert execution_mode_label("redis_worker") == "Redis + worker"
    assert execution_mode_label("qstash_http_queue") == "QStash HTTP message queue"
    assert execution_mode_label("inline_background") == "API background execution"
    assert execution_mode_label(None) == "Unknown"
