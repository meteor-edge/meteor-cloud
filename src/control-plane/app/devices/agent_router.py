"""Device-facing agent HTTP routes: registration and heartbeat."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.artifacts.dependencies import ArtifactSvc
from app.artifacts.router import stream_artifact
from app.core.config import Settings, get_settings
from app.deployments.software_dependencies import DeviceSoftwareSvc
from app.deployments.software_schemas import DesiredSoftwareItem
from app.devices.dependencies import (
    CurrentApiKey,
    CurrentDevice,
    EnrollmentSvc,
    RegistrationSvc,
    enforce_enroll_poll_rate_limit,
    enforce_enroll_request_rate_limit,
    enforce_registration_rate_limit,
)
from app.devices.schemas import (
    AgentEnrollCheckResponse,
    AgentEnrollPollRequest,
    AgentEnrollPollResponse,
    AgentEnrollRequest,
    AgentEnrollResponse,
    AgentHeartbeatRequest,
    AgentHeartbeatResponse,
    AgentRegisterRequest,
    AgentRegisterResponse,
)
from app.mqtt.schemas import AgentMqttCredentialsResponse, AgentMqttStatusResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])


@router.post(
    "/register",
    response_model=AgentRegisterResponse,
    status_code=201,
    dependencies=[Depends(enforce_registration_rate_limit)],
)
def register_device(
    payload: AgentRegisterRequest,
    request: Request,
    service: RegistrationSvc,
    settings: Annotated[Settings, Depends(get_settings)],
) -> AgentRegisterResponse:
    # HTTP registration remains allowed for now but is flagged as a known
    # limitation. A setting is available to require HTTPS later.
    if request.url.scheme != "https":
        if settings.registration_require_https:
            from app.core.exceptions import ForbiddenError

            raise ForbiddenError(
                "https_required",
                "Device registration requires a secure (HTTPS) connection.",
            )
        logger.warning(
            "Device registration received over insecure transport (%s). "
            "Enable REGISTRATION_REQUIRE_HTTPS in production.",
            request.url.scheme,
        )
    return service.register(payload)


@router.post("/heartbeat", response_model=AgentHeartbeatResponse)
def device_heartbeat(
    payload: AgentHeartbeatRequest,
    device: CurrentDevice,
    service: RegistrationSvc,
) -> AgentHeartbeatResponse:
    return service.heartbeat(device=device, payload=payload)


@router.get("/mqtt", response_model=AgentMqttStatusResponse)
def agent_mqtt_status(device: CurrentDevice, service: RegistrationSvc) -> AgentMqttStatusResponse:
    """Whether MQTT is on, and a version the agent compares with its stored config."""
    return service.mqtt_status()


@router.post("/mqtt/credentials", response_model=AgentMqttCredentialsResponse)
def agent_mqtt_credentials(device: CurrentDevice, service: RegistrationSvc) -> AgentMqttCredentialsResponse:
    return service.reissue_mqtt_credentials(device=device)


@router.get("/software", response_model=list[DesiredSoftwareItem])
def agent_list_software(
    device: CurrentDevice,
    software: DeviceSoftwareSvc,
) -> list[DesiredSoftwareItem]:
    """Return current desired software for this device (MQTT wake-up / poll)."""
    return software.list_desired_for_agent(device_id=device.id)


@router.get("/artifacts/{artifact_id}/download")
def agent_download_artifact(
    artifact_id: uuid.UUID,
    device: CurrentDevice,
    software: DeviceSoftwareSvc,
    artifacts: ArtifactSvc,
) -> StreamingResponse:
    """Stream an artifact only if it is on this device's current desired software."""
    software.require_desired_artifact(
        organization_id=device.organization_id,
        device_id=device.id,
        artifact_id=artifact_id,
    )
    artifact, stored = artifacts.open_content(
        organization_id=device.organization_id,
        artifact_id=artifact_id,
    )
    return stream_artifact(artifact, stored)


def _warn_insecure_transport(request: Request, settings: Settings) -> None:
    if request.url.scheme == "https":
        return
    if settings.registration_require_https:
        from app.core.exceptions import ForbiddenError

        raise ForbiddenError(
            "https_required",
            "Device registration requires a secure (HTTPS) connection.",
        )
    logger.warning(
        "Device enrollment received over insecure transport (%s). Enable REGISTRATION_REQUIRE_HTTPS in production.",
        request.url.scheme,
    )


@router.get("/enroll/check", response_model=AgentEnrollCheckResponse)
@router.post("/enroll/check", response_model=AgentEnrollCheckResponse)
def enroll_check(
    api_key: CurrentApiKey,
    service: EnrollmentSvc,
) -> AgentEnrollCheckResponse:
    return service.check(api_key=api_key)


@router.post(
    "/enroll/request",
    response_model=AgentEnrollResponse,
    status_code=201,
    dependencies=[Depends(enforce_enroll_request_rate_limit)],
)
def enroll_request(
    payload: AgentEnrollRequest,
    request: Request,
    api_key: CurrentApiKey,
    service: EnrollmentSvc,
    settings: Annotated[Settings, Depends(get_settings)],
) -> AgentEnrollResponse:
    _warn_insecure_transport(request, settings)
    return service.submit_request(api_key=api_key, payload=payload)


@router.post(
    "/enroll/poll",
    response_model=AgentEnrollPollResponse,
    dependencies=[Depends(enforce_enroll_poll_rate_limit)],
)
def enroll_poll(
    payload: AgentEnrollPollRequest,
    service: EnrollmentSvc,
) -> AgentEnrollPollResponse:
    return service.poll(payload)
