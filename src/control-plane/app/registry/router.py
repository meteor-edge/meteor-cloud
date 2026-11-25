"""Docker token endpoint and the organization's registry settings."""

from __future__ import annotations

import uuid
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.orm import Session

from app.adapters import rate_limiter as build_rate_limiter
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.exceptions import AppError
from app.core.metrics import RATE_LIMITED
from app.devices.rate_limit import retry_after_seconds
from app.identity.dependencies import CurrentUser
from app.ports.cache import RateLimiter
from app.registry.schemas import (
    RegistryAccessTokenResponse,
    RegistryInfoResponse,
    RegistryTokenCreateRequest,
    RegistryTokenCreateResponse,
    RegistryTokenResponse,
)
from app.registry.service import RegistryService

token_router = APIRouter(prefix="/api/v1/registry", tags=["registry"])
router = APIRouter(prefix="/api/v1/organizations/{organization_id}/registry", tags=["registry"])
basic_scheme = HTTPBasic(auto_error=False)


def get_registry_service(
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RegistryService:
    return RegistryService(session, settings=settings)


@lru_cache
def _build_registry_rate_limiter() -> RateLimiter:
    settings = get_settings()
    return build_rate_limiter(
        settings,
        limit=settings.registry_token_rate_limit_requests,
        window_seconds=settings.registry_token_rate_limit_window_seconds,
        prefix="registry_token_rl",
    )


def get_registry_rate_limiter() -> RateLimiter:
    return _build_registry_rate_limiter()


def enforce_registry_rate_limit(
    request: Request, limiter: Annotated[RateLimiter, Depends(get_registry_rate_limiter)]
) -> None:
    if not limiter.allow(request.client.host if request.client else "unknown"):
        RATE_LIMITED.labels(limiter="registry_token").inc()
        raise AppError(
            "rate_limited",
            "Too many registry sign-ins. Please try again later.",
            status_code=429,
            headers={"Retry-After": str(retry_after_seconds(limiter))},
        )


RegistrySvc = Annotated[RegistryService, Depends(get_registry_service)]


@token_router.get(
    "/token", response_model=RegistryAccessTokenResponse, dependencies=[Depends(enforce_registry_rate_limit)]
)
def issue_registry_token(
    response: Response,
    credentials: Annotated[HTTPBasicCredentials | None, Depends(basic_scheme)],
    registry: RegistrySvc,
    service: str = "",
    scope: Annotated[list[str] | None, Query()] = None,
) -> RegistryAccessTokenResponse:
    """Docker token auth. The password is a registry token; the user name is not checked."""
    response.headers["Cache-Control"] = "no-store"
    return registry.issue_access_token(
        password=credentials.password if credentials else None, service=service, scopes=scope or []
    )


@router.get("", response_model=RegistryInfoResponse)
def get_registry(organization_id: uuid.UUID, user: CurrentUser, registry: RegistrySvc) -> RegistryInfoResponse:
    return registry.info(actor=user, organization_id=organization_id)


@router.get("/tokens", response_model=list[RegistryTokenResponse])
def list_registry_tokens(
    organization_id: uuid.UUID, user: CurrentUser, registry: RegistrySvc
) -> list[RegistryTokenResponse]:
    return registry.list_tokens(actor=user, organization_id=organization_id)


@router.post("/tokens", response_model=RegistryTokenCreateResponse, status_code=201)
def create_registry_token(
    organization_id: uuid.UUID, payload: RegistryTokenCreateRequest, user: CurrentUser, registry: RegistrySvc
) -> RegistryTokenCreateResponse:
    """The token itself is only in this answer."""
    return registry.create_token(actor=user, organization_id=organization_id, payload=payload)


@router.post("/tokens/{token_id}/revoke", response_model=RegistryTokenResponse)
def revoke_registry_token(
    organization_id: uuid.UUID, token_id: uuid.UUID, user: CurrentUser, registry: RegistrySvc
) -> RegistryTokenResponse:
    return registry.revoke_token(actor=user, organization_id=organization_id, token_id=token_id)
