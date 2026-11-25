"""Docker token endpoint and the organization's registry settings."""

from __future__ import annotations

import base64
import hmac
import uuid
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy.orm import Session

from app.adapters import rate_limiter as build_rate_limiter
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.exceptions import AppError, UnauthorizedError
from app.core.metrics import RATE_LIMITED
from app.devices.rate_limit import retry_after_seconds
from app.devices.schemas import Page
from app.identity.dependencies import CurrentUser
from app.ports.cache import RateLimiter
from app.ports.registry import RegistryUnavailableError
from app.registry.access import events_secret
from app.registry.catalog import CatalogService
from app.registry.schemas import (
    ImageRepositoryResponse,
    ImageTagResponse,
    RegistryAccessTokenResponse,
    RegistryEvents,
    RegistryInfoResponse,
    RegistryTokenCreateRequest,
    RegistryTokenCreateResponse,
    RegistryTokenResponse,
)
from app.registry.service import RegistryService

token_router = APIRouter(prefix="/api/v1/registry", tags=["registry"])
router = APIRouter(prefix="/api/v1/organizations/{organization_id}/registry", tags=["registry"])
internal_router = APIRouter(prefix="/internal/registry", tags=["internal-registry"], include_in_schema=False)


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


def basic_token(authorization: Annotated[str | None, Header()] = None) -> str | None:
    """The token in an HTTP Basic header. Docker sends it as the password; the user name is not checked."""
    scheme, _, encoded = (authorization or "").partition(" ")
    if scheme.lower() != "basic":
        return None
    try:
        decoded = base64.b64decode(encoded.strip(), validate=True).decode()
    except ValueError:  # also not UTF-8
        return None
    return decoded.partition(":")[2] or None


RegistrySvc = Annotated[RegistryService, Depends(get_registry_service)]


@token_router.get(
    "/token", response_model=RegistryAccessTokenResponse, dependencies=[Depends(enforce_registry_rate_limit)]
)
def issue_registry_token(
    response: Response,
    presented: Annotated[str | None, Depends(basic_token)],
    registry: RegistrySvc,
    service: str = "",
    scope: Annotated[list[str] | None, Query()] = None,
) -> RegistryAccessTokenResponse:
    """Docker token auth. The password is a registry token; the user name is not checked."""
    response.headers["Cache-Control"] = "no-store"
    return registry.issue_access_token(presented=presented, service=service, scopes=scope or [])


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


def get_catalog_service(
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> CatalogService:
    return CatalogService(session, settings=settings)


CatalogSvc = Annotated[CatalogService, Depends(get_catalog_service)]


@router.get("/images", response_model=Page[ImageRepositoryResponse])
def list_images(
    organization_id: uuid.UUID,
    user: CurrentUser,
    catalog: CatalogSvc,
    search: Annotated[str | None, Query(max_length=255)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[ImageRepositoryResponse]:
    return catalog.list_repositories(
        actor=user, organization_id=organization_id, search=search, page=page, page_size=page_size
    )


@router.get("/images/tags", response_model=Page[ImageTagResponse])
def list_image_tags(
    organization_id: uuid.UUID,
    user: CurrentUser,
    catalog: CatalogSvc,
    repository: Annotated[str, Query(min_length=1, max_length=255)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[ImageTagResponse]:
    return catalog.list_tags(
        actor=user, organization_id=organization_id, repository=repository, page=page, page_size=page_size
    )


@router.delete("/images", status_code=204)
def delete_image(
    organization_id: uuid.UUID,
    user: CurrentUser,
    catalog: CatalogSvc,
    repository: Annotated[str, Query(min_length=1, max_length=255)],
    tag: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
) -> None:
    """Without ``tag``, every tag of the repository."""
    catalog.delete(actor=user, organization_id=organization_id, repository=repository, tag=tag)


@internal_router.post("/events", status_code=204)
def receive_registry_events(
    payload: RegistryEvents,
    catalog: CatalogSvc,
    settings: Annotated[Settings, Depends(get_settings)],
    x_registry_events_token: Annotated[str | None, Header()] = None,
) -> None:
    """Notifications from the registry. Not reachable through the public proxy."""
    expected = events_secret(settings)
    if settings.registry_provider == "none" or not hmac.compare_digest(
        (x_registry_events_token or "").encode(), expected.encode()
    ):
        raise UnauthorizedError("registry_events_forbidden", "Invalid registry events token.")
    try:
        catalog.handle_events(payload.events)
    except RegistryUnavailableError as exc:
        # The registry sends it again.
        raise AppError("registry_unavailable", str(exc), status_code=503) from exc
