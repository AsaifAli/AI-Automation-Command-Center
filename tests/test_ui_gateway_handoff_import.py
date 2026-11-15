from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_streamlit_entry_directory_can_import_gateway_handoff() -> None:
    """The UI-local import must not resolve ui/app.py as the root app package."""
    project_root = Path(__file__).resolve().parents[1]
    ui_dir = project_root / "ui"
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(ui_dir), str(project_root), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from gateway_handoff import GatewayHandoffError, exchange_handoff_code; "
                "assert GatewayHandoffError and callable(exchange_handoff_code)"
            ),
        ],
        cwd=ui_dir,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
