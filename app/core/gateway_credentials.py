"""Application-encrypted, one-time storage for queued Gateway sessions.

Only an opaque credential UUID is placed in RQ/Redis. The short-lived bearer
is encrypted before it reaches PostgreSQL and atomically consumed by one
worker before inference.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class GatewayCredentialError(RuntimeError):
    """Raised when a queued Gateway credential cannot be stored or used."""


class GatewayCredentialVault:
    def __init__(self, encryption_key: str, ttl_seconds: int = 900) -> None:
        key = (encryption_key or "").strip()
        if not key:
            raise GatewayCredentialError(
                "Queued BYOK requires FLOWPILOT_GATEWAY_CREDENTIAL_KEY."
            )
        if not 60 <= int(ttl_seconds) <= 1800:
            raise GatewayCredentialError(
                "Gateway credential TTL must be between 60 and 1800 seconds."
            )
        try:
            self._fernet = Fernet(key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise GatewayCredentialError(
                "FLOWPILOT_GATEWAY_CREDENTIAL_KEY is invalid."
            ) from exc
        self.ttl_seconds = int(ttl_seconds)

    def encrypt(self, bearer_token: str) -> bytes:
        token = (bearer_token or "").strip()
        if not token or len(token) > 8192 or any(char.isspace() for char in token):
            raise GatewayCredentialError("The Gateway session token is invalid.")
        return self._fernet.encrypt(token.encode("utf-8"))

    def decrypt(self, encrypted_token: bytes) -> str:
        try:
            token = self._fernet.decrypt(
                bytes(encrypted_token),
                ttl=self.ttl_seconds,
            ).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError, TypeError, ValueError) as exc:
            raise GatewayCredentialError(
                "The queued Gateway session is expired or cannot be decrypted. Reopen the portfolio demo and queue the workflow again."
            ) from exc
        if not token or len(token) > 8192:
            raise GatewayCredentialError("The queued Gateway session is invalid.")
        return token
