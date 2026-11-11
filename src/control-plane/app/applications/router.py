"""Organization-scoped application identity routes (operators) and the agent mint route."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter

from app.applications.dependencies import ApplicationSvc
from app.applications.schemas import (
    AgentAppCredentialRequest,
    AgentAppCredentialResponse,
    ApplicationCreate,
    ApplicationResponse,
    ApplicationUpdate,
    CommandCreate,
    CredentialCreate,
    CredentialResponse,
    CredentialSecretResponse,
)
from app.devices.dependencies import CurrentDevice
from app.identity.dependencies import CurrentUser

router = APIRouter(
    prefix="/api/v1/organizations/{organization_id}/applications",
    tags=["applications"],
)


@router.get("", response_model=list[ApplicationResponse])
def list_applications(organization_id: uuid.UUID, current_user: CurrentUser, service: ApplicationSvc):
    return service.list_applications(actor=current_user, organization_id=organization_id)


@router.post("", response_model=ApplicationResponse, status_code=201)
def create_application(
    organization_id: uuid.UUID, current_user: CurrentUser, service: ApplicationSvc, payload: ApplicationCreate
):
    return service.create_application(actor=current_user, organization_id=organization_id, payload=payload)


@router.get("/{application_id}", response_model=ApplicationResponse)
def get_application(
    organization_id: uuid.UUID, application_id: uuid.UUID, current_user: CurrentUser, service: ApplicationSvc
):
    return service.get_application(
        actor=current_user, organization_id=organization_id, application_id=application_id
    )


@router.patch("/{application_id}", response_model=ApplicationResponse)
def update_application(
    organization_id: uuid.UUID,
    application_id: uuid.UUID,
    current_user: CurrentUser,
    service: ApplicationSvc,
    payload: ApplicationUpdate,
):
    return service.update_application(
        actor=current_user, organization_id=organization_id, application_id=application_id, payload=payload
    )


@router.delete("/{application_id}", status_code=204)
def delete_application(
    organization_id: uuid.UUID, application_id: uuid.UUID, current_user: CurrentUser, service: ApplicationSvc
) -> None:
    service.delete_application(actor=current_user, organization_id=organization_id, application_id=application_id)


@router.get("/{application_id}/credentials", response_model=list[CredentialResponse])
def list_credentials(
    organization_id: uuid.UUID, application_id: uuid.UUID, current_user: CurrentUser, service: ApplicationSvc
):
    return service.list_credentials(
        actor=current_user, organization_id=organization_id, application_id=application_id
    )


@router.post("/{application_id}/credentials", response_model=CredentialSecretResponse, status_code=201)
def create_credential(
    organization_id: uuid.UUID,
    application_id: uuid.UUID,
    current_user: CurrentUser,
    service: ApplicationSvc,
    payload: CredentialCreate,
):
    return service.create_credential(
        actor=current_user, organization_id=organization_id, application_id=application_id, payload=payload
    )


@router.post("/{application_id}/credentials/{credential_id}/revoke", response_model=CredentialResponse)
def revoke_credential(
    organization_id: uuid.UUID,
    application_id: uuid.UUID,
    credential_id: uuid.UUID,
    current_user: CurrentUser,
    service: ApplicationSvc,
):
    return service.revoke_credential(
        actor=current_user,
        organization_id=organization_id,
        application_id=application_id,
        credential_id=credential_id,
    )


@router.post("/{application_id}/credentials/{credential_id}/rotate", response_model=CredentialSecretResponse)
def rotate_credential(
    organization_id: uuid.UUID,
    application_id: uuid.UUID,
    credential_id: uuid.UUID,
    current_user: CurrentUser,
    service: ApplicationSvc,
):
    return service.rotate_credential(
        actor=current_user,
        organization_id=organization_id,
        application_id=application_id,
        credential_id=credential_id,
    )


@router.post("/{application_id}/commands", status_code=202)
def send_command(
    organization_id: uuid.UUID,
    application_id: uuid.UUID,
    current_user: CurrentUser,
    service: ApplicationSvc,
    payload: CommandCreate,
) -> dict[str, Any]:
    return service.send_command(
        actor=current_user, organization_id=organization_id, application_id=application_id, payload=payload
    )


@router.get("/{application_id}/commands/{command_id}")
def get_command(
    organization_id: uuid.UUID,
    application_id: uuid.UUID,
    command_id: uuid.UUID,
    current_user: CurrentUser,
    service: ApplicationSvc,
) -> dict[str, Any]:
    return service.get_command(
        actor=current_user,
        organization_id=organization_id,
        application_id=application_id,
        command_id=command_id,
    )


agent_router = APIRouter(prefix="/api/v1/agent", tags=["agent"])


@agent_router.post("/applications/credentials", response_model=AgentAppCredentialResponse, status_code=201)
def mint_workload_credential(
    payload: AgentAppCredentialRequest, device: CurrentDevice, service: ApplicationSvc
) -> AgentAppCredentialResponse:
    """The agent calls this before starting (and to rotate the token of) a bound workload."""
    return service.mint_for_workload(device=device, workload_key=payload.workload_key)
