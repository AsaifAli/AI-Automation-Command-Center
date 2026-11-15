
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.core.auth import (
    ROLE_PERMISSIONS,
    authenticate_api_key,
)
from app.core.config import Settings


def _settings(**overrides):
    """Create deterministic settings without reading a developer .env file."""
    return Settings(_env_file=None, app_env="test", **overrides)


@pytest.fixture
def rbac_settings():
    return _settings(
        api_auth_enabled=True,
        api_viewer_keys="viewer-test-key",
        api_operator_keys="operator-test-key",
        api_reviewer_keys="reviewer-test-key",
        api_admin_keys="admin-test-key",
        api_reviewer_name="Configured Test Reviewer",
    )


def test_viewer_permissions(rbac_settings):
    principal = authenticate_api_key(
        "viewer-test-key",
        rbac_settings,
    )

    assert principal.role == "viewer"
    assert principal.can("runs:read")
    assert principal.can("metrics:read")

    assert not principal.can("runs:execute")
    assert not principal.can("approvals:decide")
    assert not principal.can("runs:delete")


def test_operator_permissions(rbac_settings):
    principal = authenticate_api_key(
        "operator-test-key",
        rbac_settings,
    )

    assert principal.role == "operator"
    assert principal.can("runs:read")
    assert principal.can("runs:execute")

    assert not principal.can("approvals:decide")
    assert not principal.can("runs:delete")


def test_reviewer_permissions(rbac_settings):
    principal = authenticate_api_key(
        "reviewer-test-key",
        rbac_settings,
    )

    assert principal.role == "reviewer"
    assert principal.subject == "Configured Test Reviewer"
    assert principal.can("approvals:decide")
    assert principal.can("runs:read")

    assert not principal.can("runs:execute")
    assert not principal.can("runs:delete")


def test_admin_permissions(rbac_settings):
    principal = authenticate_api_key(
        "admin-test-key",
        rbac_settings,
    )

    assert principal.role == "admin"
    assert principal.can("runs:read")
    assert principal.can("runs:execute")
    assert principal.can("approvals:decide")
    assert principal.can("runs:delete")
    assert principal.can("metrics:read")


def test_missing_key_is_rejected(rbac_settings):
    with pytest.raises(HTTPException) as exc_info:
        authenticate_api_key(None, rbac_settings)

    assert exc_info.value.status_code == 401


def test_invalid_key_is_rejected(rbac_settings):
    with pytest.raises(HTTPException) as exc_info:
        authenticate_api_key(
            "wrong-key",
            rbac_settings,
        )

    assert exc_info.value.status_code == 401


def test_legacy_api_key_remains_admin_for_dashboard():
    settings = _settings(
        api_auth_enabled=True,
        api_key="legacy-dashboard-key",
        api_viewer_keys="viewer-key",
        api_operator_keys="operator-key",
        api_reviewer_keys="reviewer-key",
    )

    principal = authenticate_api_key(
        "legacy-dashboard-key",
        settings,
    )

    assert principal.role == "admin"
    assert principal.subject == "API administrator"
    assert principal.can("runs:delete")


def test_auth_disabled_allows_local_development():
    settings = _settings(
        api_auth_enabled=False,
    )

    principal = authenticate_api_key(
        None,
        settings,
    )

    assert principal.role == "admin"
    assert principal.subject == "Local development"
    assert principal.can("runs:delete")


def test_same_credential_cannot_be_reused_across_roles():
    with pytest.raises(ValidationError):
        _settings(
            api_auth_enabled=True,
            api_viewer_keys="shared-key",
            api_operator_keys="shared-key",
            api_reviewer_keys="reviewer-key",
            api_admin_keys="admin-key",
        )


def test_auth_enabled_requires_at_least_one_key():
    with pytest.raises(ValidationError):
        _settings(
            api_auth_enabled=True,
        )


def test_client_cannot_choose_its_own_role():
    principal = authenticate_api_key(
        "viewer-test-key",
        _settings(
            api_auth_enabled=True,
            api_viewer_keys="viewer-test-key",
            api_admin_keys="admin-test-key",
        ),
    )

    assert principal.role == "viewer"
    assert principal.permissions == ROLE_PERMISSIONS["viewer"]

def test_internal_production_service_can_start_without_api_keys():
    """Workers/scheduler do not serve HTTP routes and need no API keys."""
    settings = Settings(
        _env_file=None,
        app_env="production",
        api_auth_enabled=True,
        api_auth_require_keys=False,
    )

    assert settings.api_auth_enabled is True
    assert settings.api_auth_require_keys is False


def test_production_api_still_requires_api_authentication():
    """Disabling key validation must not disable production API authentication."""
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_env="production",
            api_auth_enabled=False,
            api_auth_require_keys=False,
        )


def test_production_api_requires_at_least_one_key_by_default():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_env="production",
            api_auth_enabled=True,
        )
