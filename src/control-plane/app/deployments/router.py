"""Organization-scoped Deploy HTTP routes."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.deployments.apps import AppRedeployRequest, AppResponse, AppRunRequest, AppRunResponse, AppService
from app.deployments.dependencies import DeploymentSvc
from app.deployments.schemas import DeploymentCreate, DeploymentResponse, RollbackResponse
from app.devices.dependencies import CurrentDevice
from app.devices.schemas import Page
from app.identity.dependencies import CurrentUser
from app.releases.secrets import ReleaseSecrets
from app.releases.service import ReleaseService

router = APIRouter(
    prefix="/api/v1/organizations/{organization_id}/deployments",
    tags=["deployments"],
)

apps_router = APIRouter(prefix="/api/v1/organizations/{organization_id}/devices/{device_id}/apps", tags=["apps"])

agent_secrets_router = APIRouter(prefix="/api/v1/agent", tags=["agent"])


class AgentSecretsRequest(BaseModel):
    workload_key: str = Field(min_length=1, max_length=255)
    release_id: uuid.UUID


class AgentSecretsResponse(BaseModel):
    # Secret values.
    env: dict[str, str]


@apps_router.post("/run", response_model=AppRunResponse, status_code=201)
def run_app(
    organization_id: uuid.UUID,
    device_id: uuid.UUID,
    current_user: CurrentUser,
    service: DeploymentSvc,
    payload: AppRunRequest,
) -> AppRunResponse:
    """Create a release from an image and deploy it to this device, with a public URL if asked."""
    return AppService(service, ReleaseService(service.session)).run(
        actor=current_user, organization_id=organization_id, device_id=device_id, payload=payload
    )


@apps_router.get("/deployed", response_model=list[AppResponse])
def list_apps(
    organization_id: uuid.UUID, device_id: uuid.UUID, current_user: CurrentUser, service: DeploymentSvc
) -> list[AppResponse]:
    """Single-container apps of the device, and whether a newer image is in the registry."""
    return AppService(service, ReleaseService(service.session)).list_apps(
        actor=current_user, organization_id=organization_id, device_id=device_id
    )


@apps_router.post("/{workload_key}/redeploy", response_model=AppRunResponse, status_code=201)
def redeploy_app(
    organization_id: uuid.UUID,
    device_id: uuid.UUID,
    workload_key: str,
    current_user: CurrentUser,
    service: DeploymentSvc,
    payload: AppRedeployRequest,
) -> AppRunResponse:
    return AppService(service, ReleaseService(service.session)).redeploy(
        actor=current_user,
        organization_id=organization_id,
        device_id=device_id,
        workload_key=workload_key,
        payload=payload,
    )


@agent_secrets_router.post("/software/secrets", response_model=AgentSecretsResponse)
def agent_release_secrets(
    payload: AgentSecretsRequest,
    device: CurrentDevice,
    response: Response,
    service: DeploymentSvc,
    settings: Annotated[Settings, Depends(get_settings)],
) -> AgentSecretsResponse:
    """Values of a release's secret_env, for the agent that is about to start it."""
    response.headers["Cache-Control"] = "no-store"
    return AgentSecretsResponse(
        env=ReleaseSecrets(service.session, settings).for_device(
            device=device, workload_key=payload.workload_key, release_id=payload.release_id
        )
    )


@router.get("", response_model=Page[DeploymentResponse])
def list_deployments(
    organization_id: uuid.UUID,
    current_user: CurrentUser,
    service: DeploymentSvc,
    status: Annotated[str | None, Query(max_length=32)] = None,
    release_id: Annotated[uuid.UUID | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[DeploymentResponse]:
    return service.list_deployments(
        actor=current_user,
        organization_id=organization_id,
        status=status,
        release_id=release_id,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=DeploymentResponse, status_code=201)
def create_deployment(
    organization_id: uuid.UUID,
    current_user: CurrentUser,
    service: DeploymentSvc,
    payload: DeploymentCreate,
) -> DeploymentResponse:
    return service.create_deployment(
        actor=current_user,
        organization_id=organization_id,
        payload=payload,
    )


@router.get("/{deployment_id}", response_model=DeploymentResponse)
def get_deployment(
    organization_id: uuid.UUID,
    deployment_id: uuid.UUID,
    current_user: CurrentUser,
    service: DeploymentSvc,
) -> DeploymentResponse:
    return service.get_deployment(
        actor=current_user,
        organization_id=organization_id,
        deployment_id=deployment_id,
    )


@router.post("/{deployment_id}/cancel", response_model=DeploymentResponse)
def cancel_deployment(
    organization_id: uuid.UUID,
    deployment_id: uuid.UUID,
    current_user: CurrentUser,
    service: DeploymentSvc,
) -> DeploymentResponse:
    return service.cancel_deployment(
        actor=current_user,
        organization_id=organization_id,
        deployment_id=deployment_id,
    )


@router.post("/{deployment_id}/pause", response_model=DeploymentResponse)
def pause_deployment(
    organization_id: uuid.UUID,
    deployment_id: uuid.UUID,
    current_user: CurrentUser,
    service: DeploymentSvc,
) -> DeploymentResponse:
    return service.pause_deployment(
        actor=current_user,
        organization_id=organization_id,
        deployment_id=deployment_id,
    )


@router.post("/{deployment_id}/resume", response_model=DeploymentResponse)
def resume_deployment(
    organization_id: uuid.UUID,
    deployment_id: uuid.UUID,
    current_user: CurrentUser,
    service: DeploymentSvc,
) -> DeploymentResponse:
    return service.resume_deployment(
        actor=current_user,
        organization_id=organization_id,
        deployment_id=deployment_id,
    )


@router.post("/{deployment_id}/retry-failed", response_model=DeploymentResponse)
def retry_failed_deployment(
    organization_id: uuid.UUID,
    deployment_id: uuid.UUID,
    current_user: CurrentUser,
    service: DeploymentSvc,
) -> DeploymentResponse:
    return service.retry_failed(
        actor=current_user,
        organization_id=organization_id,
        deployment_id=deployment_id,
    )


@router.post("/{deployment_id}/rollback", response_model=RollbackResponse, status_code=201)
def rollback_deployment(
    organization_id: uuid.UUID,
    deployment_id: uuid.UUID,
    current_user: CurrentUser,
    service: DeploymentSvc,
) -> RollbackResponse:
    return service.rollback_deployment(
        actor=current_user,
        organization_id=organization_id,
        deployment_id=deployment_id,
    )
