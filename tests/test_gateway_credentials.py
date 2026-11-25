import pytest
from cryptography.fernet import Fernet

from app.core.gateway_credentials import GatewayCredentialError, GatewayCredentialVault


def test_gateway_credential_is_encrypted_and_round_trips():
    key = Fernet.generate_key().decode()
    vault = GatewayCredentialVault(key, ttl_seconds=900)

    encrypted = vault.encrypt("short-lived-gateway-bearer")

    assert b"short-lived-gateway-bearer" not in encrypted
    assert vault.decrypt(encrypted) == "short-lived-gateway-bearer"


def test_gateway_credential_cannot_be_decrypted_with_another_key():
    first = GatewayCredentialVault(Fernet.generate_key().decode())
    second = GatewayCredentialVault(Fernet.generate_key().decode())

    with pytest.raises(GatewayCredentialError, match="expired or cannot be decrypted"):
        second.decrypt(first.encrypt("one-time-session"))


def test_gateway_credential_rejects_invalid_key_and_token():
    with pytest.raises(GatewayCredentialError, match="is invalid"):
        GatewayCredentialVault("not-a-fernet-key")

    vault = GatewayCredentialVault(Fernet.generate_key().decode())
    with pytest.raises(GatewayCredentialError, match="token is invalid"):
        vault.encrypt("token with whitespace")


def test_gateway_credential_ttl_is_bounded():
    with pytest.raises(GatewayCredentialError, match="TTL must be between"):
        GatewayCredentialVault(Fernet.generate_key().decode(), ttl_seconds=3600)



def test_qstash_workflow_requires_token_signing_keys_and_public_url():
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError, match="QSTASH_WORKFLOW_ENABLED requires"):
        Settings(_env_file=None, app_env="development", qstash_workflow_enabled=True)


def test_qstash_workflow_accepts_a_complete_verified_configuration():
    from app.core.config import Settings

    configured = Settings(
        _env_file=None,
        app_env="production",
        api_auth_enabled=True,
        api_operator_keys="operator-secret",
        qstash_workflow_enabled=True,
        qstash_token="qstash-secret",
        qstash_current_signing_key="sig_current",
        qstash_next_signing_key="sig_next",
        qstash_url="https://qstash-eu-central-1.upstash.io",
        upstash_workflow_url="https://flowpilot.example.com",
    )

    assert configured.qstash_workflow_enabled is True
    assert configured.qstash_workflow_retries == 2


def test_qstash_workflow_rejects_untrusted_trigger_hosts():
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError, match="QSTASH_URL must be an approved HTTPS"):
        Settings(
            _env_file=None,
            app_env="development",
            qstash_workflow_enabled=True,
            qstash_token="qstash-secret",
            qstash_current_signing_key="sig_current",
            qstash_next_signing_key="sig_next",
            qstash_url="https://attacker.example.com",
            upstash_workflow_url="https://flowpilot.example.com",
        )


def test_repository_consumes_gateway_ciphertext_by_run_and_reference(monkeypatch):
    import sys
    import types

    if "app.storage.repository" not in sys.modules and "psycopg" not in sys.modules:
        psycopg_module = types.ModuleType("psycopg")
        psycopg_rows_module = types.ModuleType("psycopg.rows")
        psycopg_rows_module.dict_row = object()
        monkeypatch.setitem(sys.modules, "psycopg", psycopg_module)
        monkeypatch.setitem(sys.modules, "psycopg.rows", psycopg_rows_module)

    from app.core.config import Settings
    from app.storage.repository import Repository

    executions = []

    class FakeCursor:
        def __init__(self, result=None):
            self.result = result

        def fetchone(self):
            return self.result

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def execute(self, query, params=None):
            executions.append((query, params))
            if "RETURNING encrypted_token" in query:
                return FakeCursor({"encrypted_token": memoryview(b"ciphertext")})
            return FakeCursor()

    repository = Repository(Settings(database_url="postgresql://test/db"))
    monkeypatch.setattr(repository, "_connect", lambda: FakeConnection())

    credential_id = repository.store_gateway_job_credential(
        "11111111-1111-1111-1111-111111111111",
        b"ciphertext",
        900,
    )
    consumed = repository.consume_gateway_job_credential(
        "11111111-1111-1111-1111-111111111111",
        credential_id,
    )

    assert consumed == b"ciphertext"
    insert = next(item for item in executions if "INSERT INTO gateway_job_credentials" in item[0])
    assert insert[1][2] == b"ciphertext"
    consume = next(item for item in executions if "RETURNING encrypted_token" in item[0])
    assert "DELETE FROM gateway_job_credentials" in consume[0]
    assert "credential_id=%s AND run_id=%s AND expires_at > %s" in consume[0]


def test_repository_workflow_run_restoration_is_idempotent_and_owner_scoped():
    import uuid

    from app.core.config import Settings
    from app.storage.repository import Repository

    repository = Repository(Settings(_env_file=None, database_url=""))
    run_id = str(uuid.uuid4())
    repository.ensure_pending_run(run_id, "competitor", "owner-a")
    repository.mark_running(run_id)
    repository.ensure_pending_run(run_id, "competitor", "owner-a")

    run = repository.get_run(run_id, owner_id="owner-a")
    assert run["status"] == "running"

    with pytest.raises(ValueError, match="scope does not match"):
        repository.ensure_pending_run(run_id, "competitor", "owner-b")

    with Repository._memory_lock:
        Repository._memory_runs.pop(run_id, None)


def test_settings_loads_compose_gateway_key_alias(monkeypatch):
    from app.core.config import Settings

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("FLOWPILOT_GATEWAY_CREDENTIAL_KEY", key)
    monkeypatch.delenv("GATEWAY_JOB_CREDENTIAL_ENCRYPTION_KEY", raising=False)

    settings = Settings(_env_file=None, app_env="development")

    assert settings.gateway_job_credential_encryption_key == key
