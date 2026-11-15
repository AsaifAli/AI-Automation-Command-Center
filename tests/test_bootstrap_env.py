from __future__ import annotations

import importlib.util
import os
from pathlib import Path

from cryptography.fernet import Fernet

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bootstrap_env.py"


def _load_bootstrap():
    spec = importlib.util.spec_from_file_location("flowpilot_bootstrap_env", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bootstrap_creates_private_credentials_without_provider_keys(tmp_path, monkeypatch, capsys):
    module = _load_bootstrap()
    root = tmp_path / "project"
    root.mkdir()
    example = root / ".env.example"
    example.write_text(
        "POSTGRES_PASSWORD=\nGRAFANA_PASSWORD=\nLITELLM_MASTER_KEY=\n"
        "API_VIEWER_KEYS=\nAPI_OPERATOR_KEYS=\nAPI_REVIEWER_KEYS=\nAPI_ADMIN_KEYS=\n"
        "API_OPERATOR_KEY=\nFLOWPILOT_GATEWAY_CREDENTIAL_KEY=\nOPENROUTER_API_KEY=\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "ROOT", root)
    monkeypatch.setattr(module, "ENV_EXAMPLE", example)
    monkeypatch.setattr(module, "ENV_FILE", root / ".env")
    monkeypatch.setattr(module, "PROMETHEUS_DIR", root / "secrets")
    monkeypatch.setattr(module, "PROMETHEUS_TOKEN", root / "secrets" / "prometheus-token")

    assert module.main() == 0

    env_file = root / ".env"
    token_file = root / "secrets" / "prometheus-token"
    assert os.stat(env_file).st_mode & 0o777 == 0o600
    assert os.stat(root / "secrets").st_mode & 0o777 == 0o700
    assert os.stat(token_file).st_mode & 0o777 == 0o644

    values = dict(
        line.split("=", 1)
        for line in env_file.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    assert values["POSTGRES_PASSWORD"]
    assert values["API_OPERATOR_KEY"] == values["API_OPERATOR_KEYS"]
    Fernet(values["FLOWPILOT_GATEWAY_CREDENTIAL_KEY"].encode("ascii"))
    assert values["OPENROUTER_API_KEY"] == ""
    assert token_file.read_text(encoding="utf-8").strip() == values["API_VIEWER_KEYS"]
    assert "Created .env" in capsys.readouterr().out


def test_bootstrap_refuses_to_overwrite_existing_env(tmp_path, monkeypatch):
    module = _load_bootstrap()
    root = tmp_path / "project"
    root.mkdir()
    example = root / ".env.example"
    example.write_text("POSTGRES_PASSWORD=\n", encoding="utf-8")
    existing = root / ".env"
    existing.write_text("POSTGRES_PASSWORD=keep-me\n", encoding="utf-8")
    monkeypatch.setattr(module, "ROOT", root)
    monkeypatch.setattr(module, "ENV_EXAMPLE", example)
    monkeypatch.setattr(module, "ENV_FILE", existing)
    monkeypatch.setattr(module, "PROMETHEUS_DIR", root / "secrets")
    monkeypatch.setattr(module, "PROMETHEUS_TOKEN", root / "secrets" / "prometheus-token")

    assert module.main() == 2
    assert existing.read_text(encoding="utf-8") == "POSTGRES_PASSWORD=keep-me\n"
    assert not (root / "secrets" / "prometheus-token").exists()
