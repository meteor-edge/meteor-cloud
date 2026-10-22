"""Organization-scoped release HTTP routes."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.devices.schemas import Page
from app.identity.dependencies import CurrentUser
from app.releases.dependencies import ReleaseSvc
from app.releases.kinds import ReleaseKind
from app.releases.schemas import ReleaseCreate, ReleaseResponse, ReleaseUpdate

router = APIRouter(prefix="/api/v1/organizations/{organization_id}/releases", tags=["releases"])


@router.get("", response_model=Page[ReleaseResponse])
def list_releases(
    organization_id: uuid.UUID,
    current_user: CurrentUser,
    service: ReleaseSvc,
    kind: Annotated[ReleaseKind | None, Query()] = None,
    version: Annotated[str | None, Query(max_length=64)] = None,
    search: Annotated[str | None, Query(max_length=120)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[ReleaseResponse]:
    return service.list_releases(
        actor=current_user,
        organization_id=organization_id,
        kind=kind,
        version=version,
        search=search,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=ReleaseResponse, status_code=201)
def create_release(
    organization_id: uuid.UUID,
    current_user: CurrentUser,
    service: ReleaseSvc,
    payload: ReleaseCreate,
) -> ReleaseResponse:
    return service.create_release(actor=current_user, organization_id=organization_id, payload=payload)


@router.get("/{release_id}", response_model=ReleaseResponse)
def get_release(
    organization_id: uuid.UUID,
    release_id: uuid.UUID,
    current_user: CurrentUser,
    service: ReleaseSvc,
) -> ReleaseResponse:
    return service.get_release(actor=current_user, organization_id=organization_id, release_id=release_id)


@router.patch("/{release_id}", response_model=ReleaseResponse)
def update_release(
    organization_id: uuid.UUID,
    release_id: uuid.UUID,
    current_user: CurrentUser,
    service: ReleaseSvc,
    payload: ReleaseUpdate,
) -> ReleaseResponse:
    return service.update_release(
        actor=current_user,
        organization_id=organization_id,
        release_id=release_id,
        payload=payload,
    )


@router.delete("/{release_id}", status_code=204)
def delete_release(
    organization_id: uuid.UUID,
    release_id: uuid.UUID,
    current_user: CurrentUser,
    service: ReleaseSvc,
) -> None:
    service.delete_release(actor=current_user, organization_id=organization_id, release_id=release_id)
