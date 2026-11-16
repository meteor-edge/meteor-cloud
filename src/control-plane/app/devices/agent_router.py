"""Device-facing agent HTTP routes: registration and heartbeat."""

from __future__ import annotations

import ipaddress
import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.artifacts.dependencies import ArtifactSvc
from app.artifacts.router import stream_artifact
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.metrics import ARTIFACT_DOWNLOADS
from app.deployments.software_dependencies import DeviceSoftwareSvc
from app.deployments.software_schemas import DesiredSoftwareItem
from app.devices.dependencies import (
    CurrentApiKey,
    CurrentDevice,
    EnrollmentSvc,
    RegistrationSvc,
    enforce_enroll_ip_rate_limit,
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
from app.signing.schemas import SigningKeyResponse
from app.signing.service import SigningService, signing_key_response

logger = logging.getLogger(__name__)

# Sent by agents that can follow a download redirect to object storage.
AGENT_REDIRECT_HEADER = "X-Meteorcli-Redirect"

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])


def _observed_lan_ip(request: Request) -> str | None:
    """Private address the request arrived from, when the agent sent no LAN address.

    A public or proxied address is not the device's Ethernet or Wi-Fi address, so it is ignored.
    """
    host = request.client.host if request.client else None
    if not host:
        return None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return None
    if (
        not address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_unspecified
        or address.is_multicast
    ):
        return None
    return str(address)


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
    request: Request,
    device: CurrentDevice,
    service: RegistrationSvc,
) -> AgentHeartbeatResponse:
    if payload.local_ip is None:
        observed = _observed_lan_ip(request)
        if observed is not None:
            payload = payload.model_copy(update={"local_ip": observed})
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


@router.get("/signing-keys", response_model=list[SigningKeyResponse])
def agent_signing_keys(
    device: CurrentDevice,
    session: Annotated[Session, Depends(get_db)],
) -> list[SigningKeyResponse]:
    """The organization's signing keys. The agent pins them on first use and afterwards
    accepts a new key only when a key it already trusts endorsed it."""
    rows = SigningService(session).list_keys(device.organization_id)
    session.commit()
    return [signing_key_response(row) for row in rows]


@router.get("/artifacts/{artifact_id}/download", response_model=None)
def agent_download_artifact(
    artifact_id: uuid.UUID,
    request: Request,
    device: CurrentDevice,
    software: DeviceSoftwareSvc,
    artifacts: ArtifactSvc,
    settings: Annotated[Settings, Depends(get_settings)],
) -> StreamingResponse | RedirectResponse:
    """Send an artifact only if it is on this device's current desired software.

    Agents that send ``X-Meteorcli-Redirect: 1`` get a ``302`` to a short-lived
    object-storage URL when storage supports it, so the bytes do not pass through the
    API. The redirect carries ``X-Checksum-SHA256``; the agent checks the download
    against it. Older agents, and filesystem storage, get the content streamed.
    """
    software.require_desired_artifact(
        organization_id=device.organization_id,
        device_id=device.id,
        artifact_id=artifact_id,
    )
    if settings.artifact_download_redirect and request.headers.get(AGENT_REDIRECT_HEADER) == "1":
        artifact, url = artifacts.presigned_download(organization_id=device.organization_id, artifact_id=artifact_id)
        if url:
            ARTIFACT_DOWNLOADS.labels(mode="redirect").inc()
            return RedirectResponse(
                url,
                status_code=302,
                headers={"X-Checksum-SHA256": artifact.checksum_sha256, "Cache-Control": "no-store"},
            )
    artifact, stored = artifacts.open_content(
        organization_id=device.organization_id,
        artifact_id=artifact_id,
    )
    ARTIFACT_DOWNLOADS.labels(mode="stream").inc()
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


@router.get(
    "/enroll/check",
    response_model=AgentEnrollCheckResponse,
    dependencies=[Depends(enforce_enroll_ip_rate_limit)],
)
@router.post(
    "/enroll/check",
    response_model=AgentEnrollCheckResponse,
    dependencies=[Depends(enforce_enroll_ip_rate_limit)],
)
def enroll_check(
    api_key: CurrentApiKey,
    service: EnrollmentSvc,
) -> AgentEnrollCheckResponse:
    return service.check(api_key=api_key)


@router.post(
    "/enroll/request",
    response_model=AgentEnrollResponse,
    status_code=201,
    # IP limit first: it runs before the key lookup in the second dependency.
    dependencies=[Depends(enforce_enroll_ip_rate_limit), Depends(enforce_enroll_request_rate_limit)],
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
