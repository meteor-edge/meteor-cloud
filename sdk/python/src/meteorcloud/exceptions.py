"""Errors raised by the MeteorCloud SDK.

Every error derives from :class:`MeteorCloudError`. Errors that came from the
service carry its machine-readable ``code`` (for example ``forbidden_stream``).
"""

from __future__ import annotations


class MeteorCloudError(Exception):
    """Base class for every SDK error."""

    def __init__(self, message: str, *, code: str = "", status: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status

    def __str__(self) -> str:
        return f"{self.message} [{self.code}]" if self.code else self.message


class ConfigurationError(MeteorCloudError):
    """The client is not configured: endpoint or token is missing or unreadable."""


class AuthenticationError(MeteorCloudError):
    """The token is missing, invalid, expired or revoked."""


class PermissionDeniedError(MeteorCloudError):
    """The application is not allowed to do this (stream or command permission)."""


class ValidationError(MeteorCloudError):
    """The request was refused as invalid; retrying it unchanged will not help."""


class NotFoundError(MeteorCloudError):
    """The requested item does not exist or is not visible to this application."""


class RateLimitedError(MeteorCloudError):
    """Too many requests for this credential and the retry budget ran out."""


class UnavailableError(MeteorCloudError):
    """MeteorCloud could not be reached or is overloaded and the retry budget ran out.

    For publishes, nothing from the failed call should be assumed stored. Calling
    publish again with the same message ids is safe: stored messages are reported
    as duplicates instead of being stored twice.
    """


class PublishError(MeteorCloudError):
    """A message was rejected by the service (see ``code`` for the reason)."""


class CommandError(MeteorCloudError):
    """A command finished in a state other than SUCCEEDED."""

    def __init__(self, message: str, *, command: object = None, code: str = "") -> None:
        super().__init__(message, code=code)
        self.command = command


class CommandTimeoutError(CommandError):
    """Waiting for a command result took longer than the caller allowed."""


class ClientClosedError(MeteorCloudError):
    """The client was closed."""
