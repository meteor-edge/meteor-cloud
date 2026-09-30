"""Organization-scoped fleet management HTTP routes."""

from __future__ import annotations

import uuid
from queue import Empty
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.devices.dependencies import FleetSvc
from app.devices.schemas import (
    DeviceCredentialResponse,
    DeviceEnrollmentRequestResponse,
    DeviceGroupCreateRequest,
    DeviceGroupResponse,
    DeviceGroupUpdateRequest,
    DeviceResponse,
    DeviceTypeCreateRequest,
    DeviceTypeResponse,
    DeviceTypeUpdateRequest,
    DeviceUpdateRequest,
    EnrollmentApiKeyCreateRequest,
    EnrollmentApiKeyCreateResponse,
    EnrollmentApiKeyResponse,
    EnrollmentApproveRequest,
    EnrollmentRejectRequest,
    Page,
    RegistrationTokenCreateRequest,
    RegistrationTokenCreateResponse,
    RegistrationTokenResponse,
)
from app.identity.dependencies import CurrentUser
from app.mqtt.hub import get_mqtt_event_hub
from app.mqtt.runtime import get_mqtt_publisher, get_mqtt_topic_watcher
from app.mqtt.schemas import DevicePingResponse, MqttTestPublishRequest, MqttTestPublishResponse
from app.mqtt.service import MqttPublisher

router = APIRouter(prefix="/api/v1/organizations/{organization_id}", tags=["fleet"])


# ------------------------------------------------------------------ device types
@router.get("/device-types", response_model=list[DeviceTypeResponse])
def list_device_types(
    organization_id: uuid.UUID,
    current_user: CurrentUser,
    service: FleetSvc,
) -> list[DeviceTypeResponse]:
    return service.list_device_types(actor=current_user, organization_id=organization_id)


@router.post("/device-types", response_model=DeviceTypeResponse, status_code=201)
def create_device_type(
    organization_id: uuid.UUID,
    payload: DeviceTypeCreateRequest,
    current_user: CurrentUser,
    service: FleetSvc,
) -> DeviceTypeResponse:
    return service.create_device_type(actor=current_user, organization_id=organization_id, payload=payload)


@router.get("/device-types/{type_id}", response_model=DeviceTypeResponse)
def get_device_type(
    organization_id: uuid.UUID,
    type_id: uuid.UUID,
    current_user: CurrentUser,
    service: FleetSvc,
) -> DeviceTypeResponse:
    return service.get_device_type(actor=current_user, organization_id=organization_id, type_id=type_id)


@router.patch("/device-types/{type_id}", response_model=DeviceTypeResponse)
def update_device_type(
    organization_id: uuid.UUID,
    type_id: uuid.UUID,
    payload: DeviceTypeUpdateRequest,
    current_user: CurrentUser,
    service: FleetSvc,
) -> DeviceTypeResponse:
    return service.update_device_type(
        actor=current_user,
        organization_id=organization_id,
        type_id=type_id,
        payload=payload,
    )


@router.delete("/device-types/{type_id}", status_code=204)
def delete_device_type(
    organization_id: uuid.UUID,
    type_id: uuid.UUID,
    current_user: CurrentUser,
    service: FleetSvc,
) -> None:
    service.delete_device_type(actor=current_user, organization_id=organization_id, type_id=type_id)


# ----------------------------------------------------------------- device groups
@router.get("/device-groups", response_model=list[DeviceGroupResponse])
def list_device_groups(
    organization_id: uuid.UUID,
    current_user: CurrentUser,
    service: FleetSvc,
) -> list[DeviceGroupResponse]:
    return service.list_device_groups(actor=current_user, organization_id=organization_id)


@router.post("/device-groups", response_model=DeviceGroupResponse, status_code=201)
def create_device_group(
    organization_id: uuid.UUID,
    payload: DeviceGroupCreateRequest,
    current_user: CurrentUser,
    service: FleetSvc,
) -> DeviceGroupResponse:
    return service.create_device_group(actor=current_user, organization_id=organization_id, payload=payload)


@router.get("/device-groups/{group_id}", response_model=DeviceGroupResponse)
def get_device_group(
    organization_id: uuid.UUID,
    group_id: uuid.UUID,
    current_user: CurrentUser,
    service: FleetSvc,
) -> DeviceGroupResponse:
    return service.get_device_group(actor=current_user, organization_id=organization_id, group_id=group_id)


@router.patch("/device-groups/{group_id}", response_model=DeviceGroupResponse)
def update_device_group(
    organization_id: uuid.UUID,
    group_id: uuid.UUID,
    payload: DeviceGroupUpdateRequest,
    current_user: CurrentUser,
    service: FleetSvc,
) -> DeviceGroupResponse:
    return service.update_device_group(
        actor=current_user,
        organization_id=organization_id,
        group_id=group_id,
        payload=payload,
    )


@router.delete("/device-groups/{group_id}", status_code=204)
def delete_device_group(
    organization_id: uuid.UUID,
    group_id: uuid.UUID,
    current_user: CurrentUser,
    service: FleetSvc,
) -> None:
    service.delete_device_group(actor=current_user, organization_id=organization_id, group_id=group_id)


# ----------------------------------------------------------- registration tokens
@router.get("/registration-tokens", response_model=list[RegistrationTokenResponse])
def list_registration_tokens(
    organization_id: uuid.UUID,
    current_user: CurrentUser,
    service: FleetSvc,
) -> list[RegistrationTokenResponse]:
    return service.list_registration_tokens(actor=current_user, organization_id=organization_id)


@router.post(
    "/registration-tokens",
    response_model=RegistrationTokenCreateResponse,
    status_code=201,
)
def create_registration_token(
    organization_id: uuid.UUID,
    payload: RegistrationTokenCreateRequest,
    current_user: CurrentUser,
    service: FleetSvc,
) -> RegistrationTokenCreateResponse:
    return service.create_registration_token(actor=current_user, organization_id=organization_id, payload=payload)


@router.post(
    "/registration-tokens/{token_id}/revoke",
    response_model=RegistrationTokenResponse,
)
def revoke_registration_token(
    organization_id: uuid.UUID,
    token_id: uuid.UUID,
    current_user: CurrentUser,
    service: FleetSvc,
) -> RegistrationTokenResponse:
    return service.revoke_registration_token(actor=current_user, organization_id=organization_id, token_id=token_id)


# ------------------------------------------------------------ enrollment api keys
@router.get("/enrollment-keys", response_model=list[EnrollmentApiKeyResponse])
def list_enrollment_keys(
    organization_id: uuid.UUID,
    current_user: CurrentUser,
    service: FleetSvc,
) -> list[EnrollmentApiKeyResponse]:
    return service.list_enrollment_keys(actor=current_user, organization_id=organization_id)


@router.post(
    "/enrollment-keys",
    response_model=EnrollmentApiKeyCreateResponse,
    status_code=201,
)
def create_enrollment_key(
    organization_id: uuid.UUID,
    payload: EnrollmentApiKeyCreateRequest,
    current_user: CurrentUser,
    service: FleetSvc,
) -> EnrollmentApiKeyCreateResponse:
    return service.create_enrollment_key(actor=current_user, organization_id=organization_id, payload=payload)


@router.post(
    "/enrollment-keys/{key_id}/revoke",
    response_model=EnrollmentApiKeyResponse,
)
def revoke_enrollment_key(
    organization_id: uuid.UUID,
    key_id: uuid.UUID,
    current_user: CurrentUser,
    service: FleetSvc,
) -> EnrollmentApiKeyResponse:
    return service.revoke_enrollment_key(actor=current_user, organization_id=organization_id, key_id=key_id)


# ------------------------------------------------------- enrollment requests
@router.get(
    "/enrollment-requests",
    response_model=list[DeviceEnrollmentRequestResponse],
)
def list_enrollment_requests(
    organization_id: uuid.UUID,
    current_user: CurrentUser,
    service: FleetSvc,
    status: Annotated[
        str | None,
        Query(pattern="^(pending|approved|rejected|expired)$"),
    ] = None,
) -> list[DeviceEnrollmentRequestResponse]:
    return service.list_enrollment_requests(actor=current_user, organization_id=organization_id, status=status)


@router.post(
    "/enrollment-requests/{request_id}/approve",
    response_model=DeviceEnrollmentRequestResponse,
)
def approve_enrollment_request(
    organization_id: uuid.UUID,
    request_id: uuid.UUID,
    payload: EnrollmentApproveRequest,
    current_user: CurrentUser,
    service: FleetSvc,
) -> DeviceEnrollmentRequestResponse:
    return service.approve_enrollment_request(
        actor=current_user,
        organization_id=organization_id,
        request_id=request_id,
        payload=payload,
    )


@router.post(
    "/enrollment-requests/{request_id}/reject",
    response_model=DeviceEnrollmentRequestResponse,
)
def reject_enrollment_request(
    organization_id: uuid.UUID,
    request_id: uuid.UUID,
    payload: EnrollmentRejectRequest,
    current_user: CurrentUser,
    service: FleetSvc,
) -> DeviceEnrollmentRequestResponse:
    return service.reject_enrollment_request(
        actor=current_user,
        organization_id=organization_id,
        request_id=request_id,
        payload=payload,
    )


# ----------------------------------------------------------------------- devices
@router.get("/devices", response_model=Page[DeviceResponse])
def list_devices(
    organization_id: uuid.UUID,
    current_user: CurrentUser,
    service: FleetSvc,
    search: Annotated[str | None, Query()] = None,
    device_type_id: Annotated[uuid.UUID | None, Query()] = None,
    device_group_id: Annotated[uuid.UUID | None, Query()] = None,
    architecture: Annotated[str | None, Query()] = None,
    enabled: Annotated[bool | None, Query()] = None,
    status: Annotated[str | None, Query(pattern="^(online|offline|never_seen)$")] = None,
    sort: Annotated[str, Query(pattern="^(name|last_seen_at|created_at|registered_at)$")] = "name",
    order: Annotated[str, Query(pattern="^(asc|desc)$")] = "asc",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[DeviceResponse]:
    return service.list_devices(
        actor=current_user,
        organization_id=organization_id,
        search=search,
        device_type_id=device_type_id,
        device_group_id=device_group_id,
        architecture=architecture,
        enabled=enabled,
        status=status,
        sort=sort,
        order=order,
        page=page,
        page_size=page_size,
    )


@router.get("/devices/{device_id}", response_model=DeviceResponse)
def get_device(
    organization_id: uuid.UUID,
    device_id: uuid.UUID,
    current_user: CurrentUser,
    service: FleetSvc,
) -> DeviceResponse:
    return service.get_device(actor=current_user, organization_id=organization_id, device_id=device_id)


@router.patch("/devices/{device_id}", response_model=DeviceResponse)
def update_device(
    organization_id: uuid.UUID,
    device_id: uuid.UUID,
    payload: DeviceUpdateRequest,
    current_user: CurrentUser,
    service: FleetSvc,
) -> DeviceResponse:
    return service.update_device(
        actor=current_user,
        organization_id=organization_id,
        device_id=device_id,
        payload=payload,
    )


@router.delete("/devices/{device_id}", status_code=204)
def delete_device(
    organization_id: uuid.UUID,
    device_id: uuid.UUID,
    current_user: CurrentUser,
    service: FleetSvc,
) -> None:
    service.delete_device(actor=current_user, organization_id=organization_id, device_id=device_id)


@router.post("/devices/{device_id}/enable", response_model=DeviceResponse)
def enable_device(
    organization_id: uuid.UUID,
    device_id: uuid.UUID,
    current_user: CurrentUser,
    service: FleetSvc,
) -> DeviceResponse:
    return service.set_device_enabled(
        actor=current_user,
        organization_id=organization_id,
        device_id=device_id,
        enabled=True,
    )


@router.post("/devices/{device_id}/disable", response_model=DeviceResponse)
def disable_device(
    organization_id: uuid.UUID,
    device_id: uuid.UUID,
    current_user: CurrentUser,
    service: FleetSvc,
) -> DeviceResponse:
    return service.set_device_enabled(
        actor=current_user,
        organization_id=organization_id,
        device_id=device_id,
        enabled=False,
    )


@router.post(
    "/devices/{device_id}/rotate-credential",
    response_model=DeviceCredentialResponse,
)
def rotate_device_credential(
    organization_id: uuid.UUID,
    device_id: uuid.UUID,
    current_user: CurrentUser,
    service: FleetSvc,
) -> DeviceCredentialResponse:
    return service.rotate_device_credential(actor=current_user, organization_id=organization_id, device_id=device_id)


@router.post(
    "/devices/{device_id}/revoke-credential",
    response_model=DeviceResponse,
)
def revoke_device_credential(
    organization_id: uuid.UUID,
    device_id: uuid.UUID,
    current_user: CurrentUser,
    service: FleetSvc,
) -> DeviceResponse:
    return service.revoke_device_credential(actor=current_user, organization_id=organization_id, device_id=device_id)


@router.post(
    "/devices/{device_id}/mqtt/revoke",
    response_model=DeviceResponse,
)
def revoke_mqtt_credential(
    organization_id: uuid.UUID,
    device_id: uuid.UUID,
    current_user: CurrentUser,
    service: FleetSvc,
) -> DeviceResponse:
    return service.revoke_mqtt_credential(actor=current_user, organization_id=organization_id, device_id=device_id)


@router.post(
    "/devices/{device_id}/commands/ping",
    response_model=DevicePingResponse,
)
def ping_device(
    organization_id: uuid.UUID,
    device_id: uuid.UUID,
    current_user: CurrentUser,
    service: FleetSvc,
    publisher: Annotated[MqttPublisher, Depends(get_mqtt_publisher)],
) -> DevicePingResponse:
    return service.ping_device(
        actor=current_user,
        organization_id=organization_id,
        device_id=device_id,
        publisher=publisher,
    )


@router.get("/mqtt/events")
def stream_mqtt_events(
    organization_id: uuid.UUID,
    current_user: CurrentUser,
    service: FleetSvc,
    device_id: Annotated[uuid.UUID | None, Query()] = None,
    topic: Annotated[str | None, Query()] = None,
) -> StreamingResponse:
    topic_filter = service.require_mqtt_listener(
        actor=current_user,
        organization_id=organization_id,
        device_id=device_id,
        topic=topic,
    )
    watcher = get_mqtt_topic_watcher()
    if watcher is not None:
        watcher.watch_topic(topic_filter)
    hub = get_mqtt_event_hub()
    queue = hub.subscribe(organization_id, topic_filter)

    def _stream():
        try:
            while True:
                try:
                    event = queue.get(timeout=15)
                    yield f"data: {event.sse_data()}\n\n"
                except Empty:
                    yield ": keepalive\n\n"
        finally:
            hub.unsubscribe(organization_id, topic_filter, queue)
            if watcher is not None:
                watcher.unwatch_topic(topic_filter)

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/mqtt/publish", response_model=MqttTestPublishResponse)
def publish_mqtt_test_event(
    organization_id: uuid.UUID,
    payload: MqttTestPublishRequest,
    current_user: CurrentUser,
    service: FleetSvc,
    publisher: Annotated[MqttPublisher, Depends(get_mqtt_publisher)],
) -> MqttTestPublishResponse:
    return service.publish_mqtt_test_event(
        actor=current_user,
        organization_id=organization_id,
        device_id=payload.device_id,
        topic=payload.topic,
        payload=payload.payload,
        publisher=publisher,
    )
