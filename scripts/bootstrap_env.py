"""Create private local FlowPilot secrets without overwriting existing configuration."""
from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = ROOT / ".env.example"
ENV_FILE = ROOT / ".env"
PROMETHEUS_DIR = ROOT / "secrets"
PROMETHEUS_TOKEN = PROMETHEUS_DIR / "prometheus-token"


def _token(prefix: str = "") -> str:
    return prefix + secrets.token_urlsafe(36)


def _set_env_value(lines: list[str], key: str, value: str) -> None:
    prefix = f"{key}="
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            lines[index] = prefix + value
            return
    lines.append(prefix + value)


def main() -> int:
    if ENV_FILE.exists():
        print("Refusing to overwrite the existing .env file.", file=sys.stderr)
        print("Keep your current credentials. Review .env.example and add only missing settings manually.", file=sys.stderr)
        return 2
    if not ENV_EXAMPLE.is_file():
        print("Missing .env.example; run this script from a complete FlowPilot checkout.", file=sys.stderr)
        return 2
    if PROMETHEUS_TOKEN.exists():
        print("Refusing to overwrite the existing Prometheus token file.", file=sys.stderr)
        print("Move it to a secure backup location first, then rerun this script.", file=sys.stderr)
        return 2

    values = {
        "POSTGRES_PASSWORD": _token(),
        "GRAFANA_PASSWORD": _token(),
        "LITELLM_MASTER_KEY": _token("sk-flowpilot-"),
        "API_VIEWER_KEYS": _token("fp-viewer-"),
        "API_OPERATOR_KEYS": _token("fp-operator-"),
        "API_REVIEWER_KEYS": _token("fp-reviewer-"),
        "API_ADMIN_KEYS": _token("fp-admin-"),
        "FLOWPILOT_GATEWAY_CREDENTIAL_KEY": Fernet.generate_key().decode("ascii"),
    }
    # The local Streamlit service receives only the operator role by default.
    values["API_OPERATOR_KEY"] = values["API_OPERATOR_KEYS"]

    lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
    for key, value in values.items():
        _set_env_value(lines, key, value)

    PROMETHEUS_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(ENV_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines).rstrip() + "\n")

    # The parent directory is owner-only; the read-only bind mount needs the
    # file readable by Prometheus' non-root container user.
    token_fd = os.open(PROMETHEUS_TOKEN, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(token_fd, "w", encoding="utf-8") as handle:
        handle.write(values["API_VIEWER_KEYS"] + "\n")

    print("Created .env with owner-only permissions and a viewer-scoped Prometheus token inside an owner-only secrets/ directory.")
    print("Provider API keys and shared Gateway URLs remain blank; configure them privately when needed.")
    print("Never commit .env or the secrets/ directory.")
    print("Start the local stack with: docker compose up --build -d")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
