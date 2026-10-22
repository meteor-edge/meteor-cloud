"""Organization-scoped application endpoint HTTP routes."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.endpoints.dependencies import EndpointSvc
from app.endpoints.schemas import EndpointCreate, EndpointResponse, EndpointUpdate
from app.identity.dependencies import CurrentUser

router = APIRouter(
    prefix="/api/v1/organizations/{organization_id}/endpoints",
    tags=["endpoints"],
)


@router.get("", response_model=list[EndpointResponse])
def list_endpoints(
    organization_id: uuid.UUID,
    current_user: CurrentUser,
    service: EndpointSvc,
    device_id: Annotated[uuid.UUID | None, Query()] = None,
) -> list[EndpointResponse]:
    return service.list_endpoints(
        actor=current_user,
        organization_id=organization_id,
        device_id=device_id,
    )


@router.post("", response_model=EndpointResponse, status_code=201)
def create_endpoint(
    organization_id: uuid.UUID,
    current_user: CurrentUser,
    service: EndpointSvc,
    payload: EndpointCreate,
) -> EndpointResponse:
    return service.create_endpoint(
        actor=current_user,
        organization_id=organization_id,
        payload=payload,
    )


@router.get("/{endpoint_id}", response_model=EndpointResponse)
def get_endpoint(
    organization_id: uuid.UUID,
    endpoint_id: uuid.UUID,
    current_user: CurrentUser,
    service: EndpointSvc,
) -> EndpointResponse:
    return service.get_endpoint(
        actor=current_user,
        organization_id=organization_id,
        endpoint_id=endpoint_id,
    )


@router.patch("/{endpoint_id}", response_model=EndpointResponse)
def update_endpoint(
    organization_id: uuid.UUID,
    endpoint_id: uuid.UUID,
    current_user: CurrentUser,
    service: EndpointSvc,
    payload: EndpointUpdate,
) -> EndpointResponse:
    return service.update_endpoint(
        actor=current_user,
        organization_id=organization_id,
        endpoint_id=endpoint_id,
        payload=payload,
    )


@router.delete("/{endpoint_id}", status_code=204)
def delete_endpoint(
    organization_id: uuid.UUID,
    endpoint_id: uuid.UUID,
    current_user: CurrentUser,
    service: EndpointSvc,
) -> None:
    service.delete_endpoint(
        actor=current_user,
        organization_id=organization_id,
        endpoint_id=endpoint_id,
    )
