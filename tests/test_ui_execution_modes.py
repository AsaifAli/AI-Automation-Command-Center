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


def test_streamlit_entrypoint_can_import_its_execution_mode_helper():
    """The import used by app.py must resolve from Streamlit's script directory."""
    import ast
    import os
    import subprocess
    import sys
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[1]
    ui_dir = project_root / "ui"
    app_tree = ast.parse((ui_dir / "app.py").read_text(encoding="utf-8"))
    import_node = next(
        node
        for node in ast.walk(app_tree)
        if isinstance(node, ast.ImportFrom)
        and any(alias.name == "execution_mode_label" for alias in node.names)
    )

    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "-c", ast.unparse(import_node)],
        cwd=ui_dir,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
