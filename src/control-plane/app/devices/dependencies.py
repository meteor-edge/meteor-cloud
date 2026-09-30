"""FastAPI dependencies for the fleet and agent APIs."""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.adapters import rate_limiter as build_redis_rate_limiter
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.exceptions import AppError, UnauthorizedError
from app.devices.enrollment import EnrollmentService
from app.devices.models import Device, EnrollmentApiKey
from app.devices.rate_limit import RateLimiter
from app.devices.registration import RegistrationService
from app.devices.repository import DeviceRepository, EnrollmentApiKeyRepository
from app.devices.service import FleetService
from app.devices.tokens import API_KEY_PREFIX, DEVICE_TOKEN_PREFIX, hash_token

device_bearer_scheme = HTTPBearer(auto_error=False)
api_key_bearer_scheme = HTTPBearer(auto_error=False)


class RegistrationRateLimitError(AppError):
    def __init__(self) -> None:
        super().__init__(
            "rate_limited",
            "Too many registration attempts. Please try again later.",
            status_code=429,
        )


def get_fleet_service(
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> FleetService:
    return FleetService(session, settings=settings)


def get_registration_service(
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RegistrationService:
    return RegistrationService(session, settings=settings)


def get_enrollment_service(
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> EnrollmentService:
    return EnrollmentService(session, settings=settings)


def _redis_rate_limiter(*, limit: int, window_seconds: int, prefix: str = "reg_rl") -> RateLimiter:
    return build_redis_rate_limiter(
        get_settings(),
        limit=limit,
        window_seconds=window_seconds,
        prefix=prefix,
    )


@lru_cache
def _build_rate_limiter() -> RateLimiter:
    settings = get_settings()
    return _redis_rate_limiter(
        limit=settings.registration_rate_limit_requests,
        window_seconds=settings.registration_rate_limit_window_seconds,
    )


def get_rate_limiter() -> RateLimiter:
    return _build_rate_limiter()


def enforce_registration_rate_limit(
    request: Request,
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
) -> None:
    client_host = request.client.host if request.client else "unknown"
    if not limiter.allow(client_host):
        raise RegistrationRateLimitError()


@lru_cache
def _build_enroll_request_rate_limiter() -> RateLimiter:
    settings = get_settings()
    return _redis_rate_limiter(
        limit=settings.enrollment_request_rate_limit_requests,
        window_seconds=settings.enrollment_request_rate_limit_window_seconds,
        prefix="enroll_req_rl",
    )


@lru_cache
def _build_enroll_poll_rate_limiter() -> RateLimiter:
    settings = get_settings()
    return _redis_rate_limiter(
        limit=settings.enrollment_poll_rate_limit_requests,
        window_seconds=settings.enrollment_poll_rate_limit_window_seconds,
        prefix="enroll_poll_rl",
    )


def get_enroll_request_rate_limiter() -> RateLimiter:
    return _build_enroll_request_rate_limiter()


def get_enroll_poll_rate_limiter() -> RateLimiter:
    return _build_enroll_poll_rate_limiter()


def get_current_api_key(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(api_key_bearer_scheme)],
    session: Annotated[Session, Depends(get_db)],
) -> EnrollmentApiKey:
    """Resolve the organization enrollment API key from its ``key_`` bearer.

    Rejects non-``key_`` credentials, unknown/revoked/expired keys.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise UnauthorizedError("invalid_api_key", "Not authenticated.")

    token = credentials.credentials
    if not token.startswith(API_KEY_PREFIX):
        raise UnauthorizedError(
            "invalid_api_key",
            "An enrollment API key is required.",
        )

    keys = EnrollmentApiKeyRepository(session)
    api_key = keys.get_by_hash(hash_token(token))
    if api_key is None:
        raise UnauthorizedError("invalid_api_key", "The enrollment API key is invalid.")
    if api_key.revoked_at is not None:
        raise UnauthorizedError(
            "invalid_api_key",
            "The enrollment API key has been revoked.",
        )
    if api_key.expires_at is not None:
        expires_at = api_key.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= datetime.now(UTC):
            raise UnauthorizedError(
                "invalid_api_key",
                "The enrollment API key has expired.",
            )
    return api_key


def enforce_enroll_request_rate_limit(
    request: Request,
    api_key: Annotated[EnrollmentApiKey, Depends(get_current_api_key)],
    limiter: Annotated[RateLimiter, Depends(get_enroll_request_rate_limiter)],
) -> None:
    client_host = request.client.host if request.client else "unknown"
    if not limiter.allow(f"{client_host}:{api_key.id}"):
        raise RegistrationRateLimitError()


def enforce_enroll_poll_rate_limit(
    request: Request,
    limiter: Annotated[RateLimiter, Depends(get_enroll_poll_rate_limiter)],
) -> None:
    client_host = request.client.host if request.client else "unknown"
    if not limiter.allow(client_host):
        raise RegistrationRateLimitError()


def get_current_device(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(device_bearer_scheme)],
    session: Annotated[Session, Depends(get_db)],
) -> Device:
    """Resolve the authenticated device from its ``dev_`` bearer credential.

    User JWTs are rejected because only ``dev_``-prefixed credentials are
    accepted, and disabled or credential-revoked devices are rejected.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise UnauthorizedError("invalid_device_credentials", "Not authenticated.")

    token = credentials.credentials
    if not token.startswith(DEVICE_TOKEN_PREFIX):
        raise UnauthorizedError(
            "invalid_device_credentials",
            "A device credential is required.",
        )

    devices = DeviceRepository(session)
    device = devices.get_by_credential_hash(hash_token(token))
    if device is None or device.credential_hash is None:
        raise UnauthorizedError(
            "invalid_device_credentials",
            "The device credential is invalid.",
        )
    if not device.is_enabled:
        raise UnauthorizedError(
            "device_disabled",
            "This device has been disabled.",
        )
    return device


FleetSvc = Annotated[FleetService, Depends(get_fleet_service)]
RegistrationSvc = Annotated[RegistrationService, Depends(get_registration_service)]
EnrollmentSvc = Annotated[EnrollmentService, Depends(get_enrollment_service)]
CurrentDevice = Annotated[Device, Depends(get_current_device)]
CurrentApiKey = Annotated[EnrollmentApiKey, Depends(get_current_api_key)]
