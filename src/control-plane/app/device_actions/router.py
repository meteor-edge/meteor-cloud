"""Device actions: operators order them, the device collects and reports."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.device_actions.schemas import (
    AddUserRequest,
    AgentActionReportRequest,
    AgentActionReportResponse,
    AgentDeviceActionsResponse,
    DeviceActionsResponse,
    SetPasswordRequest,
)
from app.device_actions.service import DeviceActionService
from app.devices.dependencies import CurrentDevice
from app.identity.dependencies import CurrentUser

router = APIRouter(tags=["device-actions"])


def get_action_service(
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DeviceActionService:
    return DeviceActionService(session, settings)


ActionSvc = Annotated[DeviceActionService, Depends(get_action_service)]
_DEVICE = "/api/v1/organizations/{organization_id}/devices/{device_id}/actions"


@router.get(_DEVICE, response_model=DeviceActionsResponse)
def list_device_actions(
    organization_id: uuid.UUID, device_id: uuid.UUID, current_user: CurrentUser, service: ActionSvc
) -> DeviceActionsResponse:
    """Recent restart, shut-down and SSH-user actions. Never a password."""
    return service.list_actions(actor=current_user, organization_id=organization_id, device_id=device_id)


@router.post(f"{_DEVICE}/reboot", response_model=DeviceActionsResponse)
def reboot_device(
    organization_id: uuid.UUID, device_id: uuid.UUID, current_user: CurrentUser, service: ActionSvc
) -> DeviceActionsResponse:
    """Restart the device on its next heartbeat."""
    return service.reboot(actor=current_user, organization_id=organization_id, device_id=device_id)


@router.post(f"{_DEVICE}/shutdown", response_model=DeviceActionsResponse)
def shutdown_device(
    organization_id: uuid.UUID, device_id: uuid.UUID, current_user: CurrentUser, service: ActionSvc
) -> DeviceActionsResponse:
    """Shut the device down on its next heartbeat."""
    return service.shutdown(actor=current_user, organization_id=organization_id, device_id=device_id)


@router.post(f"{_DEVICE}/set-password", response_model=DeviceActionsResponse)
def set_device_password(
    organization_id: uuid.UUID,
    device_id: uuid.UUID,
    payload: SetPasswordRequest,
    current_user: CurrentUser,
    service: ActionSvc,
) -> DeviceActionsResponse:
    """Change the password of an existing user on the device."""
    return service.set_password(
        actor=current_user, organization_id=organization_id, device_id=device_id, payload=payload
    )


@router.post(f"{_DEVICE}/add-user", response_model=DeviceActionsResponse)
def add_device_user(
    organization_id: uuid.UUID,
    device_id: uuid.UUID,
    payload: AddUserRequest,
    current_user: CurrentUser,
    service: ActionSvc,
) -> DeviceActionsResponse:
    """Add a user who can log in over SSH."""
    return service.add_user(actor=current_user, organization_id=organization_id, device_id=device_id, payload=payload)


@router.get("/api/v1/agent/actions", response_model=AgentDeviceActionsResponse)
def agent_actions(device: CurrentDevice, service: ActionSvc) -> AgentDeviceActionsResponse:
    """Actions waiting for this device, with passwords. Each action is handed out once."""
    return service.changes_for_agent(device=device)


@router.post("/api/v1/agent/actions/report", response_model=AgentActionReportResponse)
def agent_actions_report(
    payload: AgentActionReportRequest, device: CurrentDevice, service: ActionSvc
) -> AgentActionReportResponse:
    """What happened to the actions this device collected."""
    return service.report_from_agent(device=device, payload=payload)
