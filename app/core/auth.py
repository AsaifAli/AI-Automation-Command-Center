
import hmac
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Header, HTTPException

from app.core.config import get_settings

ROLE_PERMISSIONS = {
    "viewer": frozenset(
        {
            "system:read",
            "workflows:read",
            "runs:read",
            "metrics:read",
        }
    ),
    "operator": frozenset(
        {
            "system:read",
            "workflows:read",
            "runs:read",
            "metrics:read",
            "runs:execute",
        }
    ),
    "reviewer": frozenset(
        {
            "system:read",
            "workflows:read",
            "runs:read",
            "metrics:read",
            "approvals:decide",
        }
    ),
    "admin": frozenset({"*"}),
    # A Gateway-authenticated workspace owner can operate and review only
    # their own project-scoped runs. They cannot administer the shared queue.
    "workspace_owner": frozenset(
        {
            "system:read",
            "workflows:read",
            "runs:read",
            "metrics:read",
            "runs:execute",
            "approvals:decide",
        }
    ),
}


@dataclass(frozen=True)
class Principal:
    subject: str
    role: str
    permissions: frozenset[str]
    owner_id: str = ""

    def can(self, permission: str) -> bool:
        return (
            "*" in self.permissions
            or permission in self.permissions
        )


def _split_keys(raw_value: str) -> list[str]:
    return [
        value.strip()
        for value in raw_value.split(",")
        if value.strip()
    ]


def authenticate_api_key(
    api_key: str | None,
    settings,
) -> Principal:
    """Resolve a configured API key to its server-assigned role."""

    if not settings.api_auth_enabled:
        return Principal(
            subject="Local development",
            role="admin",
            permissions=ROLE_PERMISSIONS["admin"],
            owner_id="local-development",
        )

    if not api_key:
        raise HTTPException(
            status_code=401,
            detail="Missing API key",
        )

    role_entries = (
        (
            "admin",
            _split_keys(settings.api_admin_keys)
            + _split_keys(settings.api_key),
            settings.api_admin_name.strip()
            or "API administrator",
        ),
        (
            "reviewer",
            _split_keys(settings.api_reviewer_keys),
            settings.api_reviewer_name.strip()
            or "Configured Reviewer",
        ),
        (
            "operator",
            _split_keys(settings.api_operator_keys),
            "API operator",
        ),
        (
            "viewer",
            _split_keys(settings.api_viewer_keys),
            "API viewer",
        ),
    )

    supplied = api_key.encode("utf-8")

    for role, credentials, subject in role_entries:
        for credential in credentials:
            if hmac.compare_digest(
                supplied,
                credential.encode("utf-8"),
            ):
                return Principal(
                    subject=subject,
                    role=role,
                    permissions=ROLE_PERMISSIONS[role],
                    # API role keys belong to the same private control plane.
                    # BYOK sessions override this with the verified Gateway owner scope.
                    owner_id="flowpilot-control-plane",
                )

    raise HTTPException(
        status_code=401,
        detail="Invalid API key",
    )


def require_permission(permission: str) -> Callable:
    """Create a FastAPI dependency requiring a specific permission."""

    def dependency(
        x_api_key: str | None = Header(
            default=None,
            alias="X-API-Key",
        ),
    ) -> Principal:

        principal = authenticate_api_key(
            x_api_key,
            get_settings(),
        )

        if not principal.can(permission):
            raise HTTPException(
                status_code=403,
                detail="Insufficient permissions",
            )

        return principal

    return dependency
