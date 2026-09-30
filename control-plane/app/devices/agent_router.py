"""Device-facing agent HTTP routes: registration and heartbeat."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.core.config import Settings, get_settings
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
