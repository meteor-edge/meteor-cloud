"""FastAPI dependencies for authenticated users."""

from __future__ import annotations

import uuid
from functools import lru_cache
from typing import Annotated

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.audit_context import set_request_actor
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.exceptions import AppError, ForbiddenError, UnauthorizedError
from app.core.metrics import RATE_LIMITED
from app.identity.models import User
from app.identity.schemas import UserLoginRequest, normalize_email
from app.identity.service import AuthenticationService
from app.ports.cache import RateLimiter

bearer_scheme = HTTPBearer(auto_error=False)


def get_authentication_service(
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthenticationService:
    return AuthenticationService(session, settings=settings)


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    settings: Annotated[Settings, Depends(get_settings)],
    service: Annotated[AuthenticationService, Depends(get_authentication_service)],
) -> User:
    """Resolve the authenticated active user from a bearer token."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise UnauthorizedError("invalid_credentials", "Not authenticated.")

    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.PyJWTError as exc:
        raise UnauthorizedError(
            "invalid_credentials",
            "Could not validate credentials.",
        ) from exc

    subject = payload.get("sub")
    if subject is None:
        raise UnauthorizedError("invalid_credentials", "Could not validate credentials.")

    try:
        user_id = uuid.UUID(str(subject))
    except ValueError as exc:
        raise UnauthorizedError(
            "invalid_credentials",
            "Could not validate credentials.",
        ) from exc

    user = service.require_active_user(user_id)
    # Tokens from before "log out everywhere" (or issued before versions existed: no "ver",
    # which counts as 0) stop working once the user's version moves on.
    if payload.get("ver", 0) != (user.token_version or 0):
        raise UnauthorizedError("invalid_credentials", "Your session has ended. Please sign in again.")
    set_request_actor(actor_type="user", actor_id=user.id, user_id=user.id)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
AuthService = Annotated[AuthenticationService, Depends(get_authentication_service)]


class LoginRateLimitError(AppError):
    def __init__(self, retry_after: int | None = None) -> None:
        super().__init__(
            "rate_limited",
            "Too many sign-in attempts. Please wait a few minutes and try again.",
            status_code=429,
            headers={"Retry-After": str(retry_after)} if retry_after else None,
        )


def _login_limiter(limit: int, prefix: str) -> RateLimiter:
    from app.adapters import rate_limiter as build_rate_limiter

    settings = get_settings()
    return build_rate_limiter(
        settings,
        limit=limit,
        window_seconds=settings.login_rate_limit_window_seconds,
        prefix=prefix,
        fail_open=False,
    )


@lru_cache
def _build_login_email_rate_limiter() -> RateLimiter:
    return _login_limiter(get_settings().login_rate_limit_requests, "login_email_rl")


@lru_cache
def _build_login_ip_rate_limiter() -> RateLimiter:
    return _login_limiter(get_settings().login_ip_rate_limit_requests, "login_ip_rl")


def get_login_email_rate_limiter() -> RateLimiter:
    return _build_login_email_rate_limiter()


def get_login_ip_rate_limiter() -> RateLimiter:
    return _build_login_ip_rate_limiter()


def enforce_login_rate_limit(
    request: Request,
    payload: UserLoginRequest,
    email_limiter: Annotated[RateLimiter, Depends(get_login_email_rate_limiter)],
    ip_limiter: Annotated[RateLimiter, Depends(get_login_ip_rate_limiter)],
) -> None:
    """Slow password guessing: a fixed window per source IP and per email address."""
    from app.devices.rate_limit import retry_after_seconds

    client_host = request.client.host if request.client else "unknown"
    if not ip_limiter.allow(client_host):
        RATE_LIMITED.labels(limiter="login_ip").inc()
        raise LoginRateLimitError(retry_after_seconds(ip_limiter))
    if not email_limiter.allow(normalize_email(payload.email)):
        RATE_LIMITED.labels(limiter="login_email").inc()
        raise LoginRateLimitError(retry_after_seconds(email_limiter))


def require_signup_allowed(settings: Annotated[Settings, Depends(get_settings)]) -> None:
    if not settings.allow_signup:
        raise ForbiddenError(
            "signup_disabled",
            "Sign-up is turned off on this platform. Ask an administrator to add you to an organization.",
        )
