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


def test_streamlit_entrypoint_is_a_retirement_notice_not_a_product_ui():
    """The old public UI must not initiate workflow or model API calls."""
    import ast
    from pathlib import Path

    app_path = Path(__file__).resolve().parents[1] / "ui" / "app.py"
    app_tree = ast.parse(app_path.read_text(encoding="utf-8"))

    imported_modules = {
        node.module
        for node in ast.walk(app_tree)
        if isinstance(node, ast.ImportFrom)
    } | {
        alias.name
        for node in ast.walk(app_tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }

    assert "streamlit" in imported_modules
    assert "ui.legacy_notice" in imported_modules
    assert "requests" not in imported_modules
    assert "execution_modes" not in imported_modules
    assert "api_access" not in imported_modules

    call_names = {
        node.func.attr
        for node in ast.walk(app_tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "link_button" in call_names
    assert "set_page_config" in call_names
