"""Wi-Fi management routes: operators change networks, the device collects and reports."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.devices.dependencies import CurrentDevice
from app.identity.dependencies import CurrentUser
from app.network.schemas import (
    AgentWifiChangesResponse,
    AgentWifiReportRequest,
    AgentWifiReportResponse,
    DeviceWifiResponse,
    WifiNetworkRemoveRequest,
    WifiNetworkSetRequest,
)
from app.network.service import DeviceWifiService

router = APIRouter(tags=["network"])


def get_wifi_service(
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DeviceWifiService:
    return DeviceWifiService(session, settings)


WifiSvc = Annotated[DeviceWifiService, Depends(get_wifi_service)]
_DEVICE = "/api/v1/organizations/{organization_id}/devices/{device_id}/wifi"


@router.get(_DEVICE, response_model=DeviceWifiResponse)
def get_device_wifi(
    organization_id: uuid.UUID, device_id: uuid.UUID, current_user: CurrentUser, service: WifiSvc
) -> DeviceWifiResponse:
    """The device's Wi-Fi as it last reported it, and the changes made here. Never a password."""
    return service.get(actor=current_user, organization_id=organization_id, device_id=device_id)


@router.put(f"{_DEVICE}/networks", response_model=DeviceWifiResponse)
def set_device_wifi_network(
    organization_id: uuid.UUID,
    device_id: uuid.UUID,
    payload: WifiNetworkSetRequest,
    current_user: CurrentUser,
    service: WifiSvc,
) -> DeviceWifiResponse:
    """Add a Wi-Fi network to the device, or change the password of one it has."""
    return service.set_network(
        actor=current_user, organization_id=organization_id, device_id=device_id, payload=payload
    )


@router.post(f"{_DEVICE}/networks/remove", response_model=DeviceWifiResponse)
def remove_device_wifi_network(
    organization_id: uuid.UUID,
    device_id: uuid.UUID,
    payload: WifiNetworkRemoveRequest,
    current_user: CurrentUser,
    service: WifiSvc,
) -> DeviceWifiResponse:
    """Make the device forget a Wi-Fi network. Refused for the network it depends on."""
    return service.remove_network(
        actor=current_user, organization_id=organization_id, device_id=device_id, payload=payload
    )


@router.get("/api/v1/agent/network/wifi", response_model=AgentWifiChangesResponse)
def agent_wifi_changes(device: CurrentDevice, service: WifiSvc) -> AgentWifiChangesResponse:
    """Wi-Fi changes waiting for this device, with their passwords."""
    return service.changes_for_agent(device=device)


@router.post("/api/v1/agent/network/wifi/report", response_model=AgentWifiReportResponse)
def agent_wifi_report(
    payload: AgentWifiReportRequest, device: CurrentDevice, service: WifiSvc
) -> AgentWifiReportResponse:
    """What happened to the changes, and the device's current Wi-Fi."""
    return service.report_from_agent(device=device, payload=payload)
