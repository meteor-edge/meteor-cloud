"""Domain and API error types with stable error codes."""

from __future__ import annotations


class AppError(Exception):
    """Base application error mapped to a structured API response."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class ConflictError(AppError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, status_code=409)


class NotFoundError(AppError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, status_code=404)


class ForbiddenError(AppError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, status_code=403)


class UnauthorizedError(AppError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, status_code=401)


class ValidationAppError(AppError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message, status_code=422)
