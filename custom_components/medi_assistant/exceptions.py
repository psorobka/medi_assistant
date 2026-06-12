from __future__ import annotations


class AuthError(Exception):
    """Authentication failed."""


class MfaRequired(Exception):
    """MFA is required — holds interim context for async_submit_mfa."""

    def __init__(self, mfa_code_id: str, csrf: str, return_url: str) -> None:
        self.mfa_code_id = mfa_code_id
        self.csrf = csrf
        self.return_url = return_url


class InvalidGrant(Exception):
    """Refresh token is invalid or expired."""


class ApiError(Exception):
    """Generic Medicover API error."""
