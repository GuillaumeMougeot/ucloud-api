"""Exception hierarchy for ucloud-api."""

from __future__ import annotations


class UCloudError(Exception):
    """Base class for every error raised by this package."""


class ConfigError(UCloudError):
    """Raised when credentials/configuration are missing or malformed."""


class AuthError(UCloudError):
    """Raised when authentication (token refresh) fails."""


class APIError(UCloudError):
    """Raised when the UCloud HTTP API returns an error response."""

    def __init__(self, message: str, *, status_code: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class JobFailedError(UCloudError):
    """Raised when a job reaches a terminal state other than the one we waited for."""

    def __init__(self, message: str, *, state: str | None = None):
        super().__init__(message)
        self.state = state


class JobTimeoutError(UCloudError):
    """Raised when a job does not reach the expected state within the timeout."""
