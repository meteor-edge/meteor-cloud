"""Operator and agent connectivity HTTP routes."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.connectivity.dependencies import ConnectivitySvc
from app.connectivity.schemas import (
    AgentWireGuardRegisterRequest,
    AgentWireGuardRegisterResponse,
    DeviceConnectivityResponse,
)
from app.core.config import Settings, get_settings
from app.core.exceptions import AppError
from app.devices.dependencies import CurrentDevice
from app.identity.dependencies import CurrentUser

router = APIRouter(tags=["connectivity"])


class ConnectivityRateLimitError(AppError):
    def __init__(self) -> None:
        super().__init__(
            "rate_limited",
            "Too many connectivity registration attempts. Please try again later.",
            status_code=429,
        )


def enforce_connectivity_rate_limit(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    from app.adapters import rate_limiter as build_limiter

    limiter = build_limiter(
        settings,
        limit=settings.connectivity_rate_limit_requests,
        window_seconds=settings.connectivity_rate_limit_window_seconds,
        prefix="conn_rl",
    )
    client = request.client.host if request.client else "unknown"
    if not limiter.allow(client):
        raise ConnectivityRateLimitError()


@router.post(
    "/api/v1/agent/connectivity/wireguard",
    response_model=AgentWireGuardRegisterResponse,
    dependencies=[Depends(enforce_connectivity_rate_limit)],
)
def register_wireguard(
    payload: AgentWireGuardRegisterRequest,
    device: CurrentDevice,
    service: ConnectivitySvc,
) -> AgentWireGuardRegisterResponse:
    return service.register_agent(device=device, payload=payload)


@router.get(
    "/api/v1/organizations/{organization_id}/devices/{device_id}/connectivity",
    response_model=DeviceConnectivityResponse,
)
def get_device_connectivity(
    organization_id: uuid.UUID,
    device_id: uuid.UUID,
    current_user: CurrentUser,
    service: ConnectivitySvc,
) -> DeviceConnectivityResponse:
    return service.get_for_operator(
        actor=current_user,
        organization_id=organization_id,
        device_id=device_id,
    )


@router.post(
    "/api/v1/organizations/{organization_id}/devices/{device_id}/connectivity/revoke",
    response_model=DeviceConnectivityResponse,
)
def revoke_device_connectivity(
    organization_id: uuid.UUID,
    device_id: uuid.UUID,
    current_user: CurrentUser,
    service: ConnectivitySvc,
) -> DeviceConnectivityResponse:
    return service.revoke(
        actor=current_user,
        organization_id=organization_id,
        device_id=device_id,
    )


@router.post(
    "/api/v1/organizations/{organization_id}/devices/{device_id}/connectivity/reenroll",
    response_model=DeviceConnectivityResponse,
)
def reenroll_device_connectivity(
    organization_id: uuid.UUID,
    device_id: uuid.UUID,
    current_user: CurrentUser,
    service: ConnectivitySvc,
) -> DeviceConnectivityResponse:
    """Forget the device's WireGuard key, address and SSH host key so it can register again."""
    return service.reenroll(
        actor=current_user,
        organization_id=organization_id,
        device_id=device_id,
    )
