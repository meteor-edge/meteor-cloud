"""Fleet administration business rules (organization API).

Handles device types, device groups, registration tokens, and device lifecycle
management. Every operation resolves organization membership first and enforces
RBAC before touching data, guaranteeing tenant isolation.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.audit.service import AuditRecorder
from app.core.config import Settings, get_settings
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.devices.models import (
    Device,
    DeviceEnrollmentRequest,
    DeviceGroup,
    DeviceMqttCredential,
    DeviceType,
    EnrollmentApiKey,
    RegistrationToken,
)
from app.devices.permissions import can_manage_fleet
from app.devices.repository import (
    DeviceEnrollmentRequestRepository,
    DeviceGroupRepository,
    DeviceRepository,
    DeviceTypeRepository,
    EnrollmentApiKeyRepository,
    RegistrationTokenRepository,
)
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
from app.devices.status import connectivity_status, offline_cutoff
from app.devices.tokens import (
    generate_api_key,
    generate_device_token,
    generate_registration_token,
)
from app.identity.models import User
from app.mqtt.schemas import DevicePingResponse, MqttTestPublishResponse
from app.mqtt.service import MqttPublisher, MqttService
from app.tenancy.models import OrganizationMembership, OrganizationRole
from app.tenancy.repository import OrganizationRepository


class FleetService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.organizations = OrganizationRepository(session)
        self.device_types = DeviceTypeRepository(session)
        self.device_groups = DeviceGroupRepository(session)
        self.tokens = RegistrationTokenRepository(session)
        self.devices = DeviceRepository(session)
        self.api_keys = EnrollmentApiKeyRepository(session)
        self.enrollment_requests = DeviceEnrollmentRequestRepository(session)
        self.audit = AuditRecorder(session)

    def _record_audit(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        action: str,
        resource_type: str,
        resource_id: uuid.UUID | str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.audit.record(
            actor=actor,
            organization_id=organization_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            metadata=metadata,
        )

    # ---------------------------------------------------------------- helpers
    def _require_membership(
        self,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> OrganizationMembership:
        result = self.organizations.get_for_user(
            organization_id=organization_id,
            user_id=user_id,
        )
        if result is None:
            raise NotFoundError("organization_not_found", "Organization was not found.")
        return result[1]

    def _require_manage(self, role: OrganizationRole) -> None:
        if not can_manage_fleet(role):
            raise ForbiddenError(
                "insufficient_permission",
                "You do not have permission to manage fleet resources.",
            )

    # ------------------------------------------------------------- device types
    def list_device_types(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
    ) -> list[DeviceTypeResponse]:
        self._require_membership(organization_id, actor.id)
        return [
            DeviceTypeResponse.model_validate(item) for item in self.device_types.list(organization_id=organization_id)
        ]

    def get_device_type(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        type_id: uuid.UUID,
    ) -> DeviceTypeResponse:
        self._require_membership(organization_id, actor.id)
        device_type = self._require_device_type(organization_id, type_id)
        return DeviceTypeResponse.model_validate(device_type)

    def create_device_type(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        payload: DeviceTypeCreateRequest,
    ) -> DeviceTypeResponse:
        membership = self._require_membership(organization_id, actor.id)
        self._require_manage(membership.role)
        if self.device_types.get_by_name(organization_id=organization_id, name=payload.name):
            raise ConflictError(
                "device_type_exists",
                "A device type with this name already exists.",
            )
        device_type = DeviceType(
            organization_id=organization_id,
            name=payload.name,
            description=payload.description,
            capabilities=payload.capabilities,
        )
        self.device_types.create(device_type)
        self.session.commit()
        self.session.refresh(device_type)
        return DeviceTypeResponse.model_validate(device_type)

    def update_device_type(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        type_id: uuid.UUID,
        payload: DeviceTypeUpdateRequest,
    ) -> DeviceTypeResponse:
        membership = self._require_membership(organization_id, actor.id)
        self._require_manage(membership.role)
        device_type = self._require_device_type(organization_id, type_id)

        if payload.name is not None and payload.name.lower() != device_type.name.lower():
            existing = self.device_types.get_by_name(organization_id=organization_id, name=payload.name)
            if existing is not None and existing.id != device_type.id:
                raise ConflictError(
                    "device_type_exists",
                    "A device type with this name already exists.",
                )
        if payload.name is not None:
            device_type.name = payload.name
        if payload.description is not None:
            device_type.description = payload.description
        if payload.capabilities is not None:
            device_type.capabilities = payload.capabilities

        self.device_types.update(device_type)
        self.session.commit()
        self.session.refresh(device_type)
        return DeviceTypeResponse.model_validate(device_type)

    def delete_device_type(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        type_id: uuid.UUID,
    ) -> None:
        membership = self._require_membership(organization_id, actor.id)
        self._require_manage(membership.role)
        device_type = self._require_device_type(organization_id, type_id)
        if self.device_types.count_devices(organization_id=organization_id, type_id=type_id):
            raise ConflictError(
                "device_type_in_use",
                "This device type is still assigned to devices.",
            )
        self.device_types.delete(device_type)
        self.session.commit()

    def _require_device_type(self, organization_id: uuid.UUID, type_id: uuid.UUID) -> DeviceType:
        device_type = self.device_types.get(organization_id=organization_id, type_id=type_id)
        if device_type is None:
            raise NotFoundError("device_type_not_found", "Device type was not found.")
        return device_type

    # ------------------------------------------------------------ device groups
    def list_device_groups(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
    ) -> list[DeviceGroupResponse]:
        self._require_membership(organization_id, actor.id)
        return [
            DeviceGroupResponse.model_validate(item)
            for item in self.device_groups.list(organization_id=organization_id)
        ]

    def get_device_group(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        group_id: uuid.UUID,
    ) -> DeviceGroupResponse:
        self._require_membership(organization_id, actor.id)
        group = self._require_device_group(organization_id, group_id)
        return DeviceGroupResponse.model_validate(group)

    def create_device_group(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        payload: DeviceGroupCreateRequest,
    ) -> DeviceGroupResponse:
        membership = self._require_membership(organization_id, actor.id)
        self._require_manage(membership.role)
        if self.device_groups.get_by_name(organization_id=organization_id, name=payload.name):
            raise ConflictError(
                "device_group_exists",
                "A device group with this name already exists.",
            )
        group = DeviceGroup(
            organization_id=organization_id,
            name=payload.name,
            description=payload.description,
            labels=payload.labels,
        )
        self.device_groups.create(group)
        self.session.commit()
        self.session.refresh(group)
        return DeviceGroupResponse.model_validate(group)

    def update_device_group(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        group_id: uuid.UUID,
        payload: DeviceGroupUpdateRequest,
    ) -> DeviceGroupResponse:
        membership = self._require_membership(organization_id, actor.id)
        self._require_manage(membership.role)
        group = self._require_device_group(organization_id, group_id)

        if payload.name is not None and payload.name.lower() != group.name.lower():
            existing = self.device_groups.get_by_name(organization_id=organization_id, name=payload.name)
            if existing is not None and existing.id != group.id:
                raise ConflictError(
                    "device_group_exists",
                    "A device group with this name already exists.",
                )
        if payload.name is not None:
            group.name = payload.name
        if payload.description is not None:
            group.description = payload.description
        if payload.labels is not None:
            group.labels = payload.labels

        self.device_groups.update(group)
        self.session.commit()
        self.session.refresh(group)
        return DeviceGroupResponse.model_validate(group)

    def delete_device_group(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        group_id: uuid.UUID,
    ) -> None:
        membership = self._require_membership(organization_id, actor.id)
        self._require_manage(membership.role)
        group = self._require_device_group(organization_id, group_id)
        if self.device_groups.count_devices(organization_id=organization_id, group_id=group_id):
            raise ConflictError(
                "device_group_in_use",
                "This device group is still assigned to devices.",
            )
        self.device_groups.delete(group)
        self.session.commit()

    def _require_device_group(self, organization_id: uuid.UUID, group_id: uuid.UUID) -> DeviceGroup:
        group = self.device_groups.get(organization_id=organization_id, group_id=group_id)
        if group is None:
            raise NotFoundError("device_group_not_found", "Device group was not found.")
        return group

    # -------------------------------------------------------- registration tokens
    def list_registration_tokens(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
    ) -> list[RegistrationTokenResponse]:
        self._require_membership(organization_id, actor.id)
        return [
            RegistrationTokenResponse.model_validate(item) for item in self.tokens.list(organization_id=organization_id)
        ]

    def create_registration_token(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        payload: RegistrationTokenCreateRequest,
    ) -> RegistrationTokenCreateResponse:
        membership = self._require_membership(organization_id, actor.id)
        self._require_manage(membership.role)

        if payload.device_type_id is not None:
            self._require_device_type(organization_id, payload.device_type_id)
        if payload.device_group_id is not None:
            self._require_device_group(organization_id, payload.device_group_id)
        if payload.expires_at is not None and payload.expires_at <= datetime.now(UTC):
            raise ConflictError(
                "invalid_expiry",
                "Expiry must be in the future.",
            )

        generated = generate_registration_token()
        token = RegistrationToken(
            organization_id=organization_id,
            name=payload.name,
            token_hash=generated.token_hash,
            token_prefix=generated.display_prefix,
            device_type_id=payload.device_type_id,
            device_group_id=payload.device_group_id,
            expires_at=payload.expires_at,
            max_uses=payload.max_uses,
            created_by_user_id=actor.id,
        )
        self.tokens.create(token)
        self._record_audit(
            actor=actor,
            organization_id=organization_id,
            action="registration_token.create",
            resource_type="registration_token",
            resource_id=token.id,
            metadata={"name": token.name},
        )
        self.session.commit()
        self.session.refresh(token)

        base = RegistrationTokenResponse.model_validate(token)
        return RegistrationTokenCreateResponse(
            **base.model_dump(),
            token=generated.plaintext,
        )

    def revoke_registration_token(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        token_id: uuid.UUID,
    ) -> RegistrationTokenResponse:
        membership = self._require_membership(organization_id, actor.id)
        self._require_manage(membership.role)
        token = self.tokens.get(organization_id=organization_id, token_id=token_id)
        if token is None:
            raise NotFoundError(
                "registration_token_not_found",
                "Registration token was not found.",
            )
        if token.revoked_at is None:
            token.revoked_at = datetime.now(UTC)
            self.tokens.update(token)
            self._record_audit(
                actor=actor,
                organization_id=organization_id,
                action="registration_token.revoke",
                resource_type="registration_token",
                resource_id=token.id,
            )
            self.session.commit()
            self.session.refresh(token)
        return RegistrationTokenResponse.model_validate(token)

    # ------------------------------------------------------- enrollment api keys
    def list_enrollment_keys(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
    ) -> list[EnrollmentApiKeyResponse]:
        self._require_membership(organization_id, actor.id)
        return [
            EnrollmentApiKeyResponse.model_validate(item)
            for item in self.api_keys.list(organization_id=organization_id)
        ]

    def create_enrollment_key(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        payload: EnrollmentApiKeyCreateRequest,
    ) -> EnrollmentApiKeyCreateResponse:
        membership = self._require_membership(organization_id, actor.id)
        self._require_manage(membership.role)

        if payload.expires_at is not None and payload.expires_at <= datetime.now(UTC):
            raise ConflictError("invalid_expiry", "Expiry must be in the future.")

        generated = generate_api_key()
        key = EnrollmentApiKey(
            organization_id=organization_id,
            name=payload.name,
            key_hash=generated.token_hash,
            key_prefix=generated.display_prefix,
            expires_at=payload.expires_at,
            created_by_user_id=actor.id,
        )
        self.api_keys.create(key)
        self._record_audit(
            actor=actor,
            organization_id=organization_id,
            action="enrollment_key.create",
            resource_type="enrollment_key",
            resource_id=key.id,
            metadata={"name": key.name},
        )
        self.session.commit()
        self.session.refresh(key)

        base = EnrollmentApiKeyResponse.model_validate(key)
        return EnrollmentApiKeyCreateResponse(
            **base.model_dump(),
            api_key=generated.plaintext,
        )

    def revoke_enrollment_key(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        key_id: uuid.UUID,
    ) -> EnrollmentApiKeyResponse:
        membership = self._require_membership(organization_id, actor.id)
        self._require_manage(membership.role)
        key = self.api_keys.get(organization_id=organization_id, key_id=key_id)
        if key is None:
            raise NotFoundError(
                "enrollment_key_not_found",
                "Enrollment API key was not found.",
            )
        if key.revoked_at is None:
            key.revoked_at = datetime.now(UTC)
            self.api_keys.update(key)
            self._record_audit(
                actor=actor,
                organization_id=organization_id,
                action="enrollment_key.revoke",
                resource_type="enrollment_key",
                resource_id=key.id,
            )
            self.session.commit()
            self.session.refresh(key)
        return EnrollmentApiKeyResponse.model_validate(key)

    # --------------------------------------------------- enrollment requests
    def list_enrollment_requests(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        status: str | None = None,
    ) -> list[DeviceEnrollmentRequestResponse]:
        self._require_membership(organization_id, actor.id)
        return [
            DeviceEnrollmentRequestResponse.model_validate(item)
            for item in self.enrollment_requests.list(organization_id=organization_id, status=status)
        ]

    def approve_enrollment_request(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        request_id: uuid.UUID,
        payload: EnrollmentApproveRequest,
    ) -> DeviceEnrollmentRequestResponse:
        membership = self._require_membership(organization_id, actor.id)
        self._require_manage(membership.role)
        request = self._require_enrollment_request(organization_id, request_id)
        if request.status != "pending":
            raise ConflictError(
                "enrollment_request_not_pending",
                "Only pending requests can be approved.",
            )
        if payload.device_type_id is not None:
            self._require_device_type(organization_id, payload.device_type_id)
            request.device_type_id = payload.device_type_id
        if payload.device_group_id is not None:
            self._require_device_group(organization_id, payload.device_group_id)
            request.device_group_id = payload.device_group_id
        if payload.name is not None:
            request.assigned_name = payload.name

        request.status = "approved"
        request.reviewed_by_user_id = actor.id
        request.reviewed_at = datetime.now(UTC)
        self.enrollment_requests.update(request)
        self._record_audit(
            actor=actor,
            organization_id=organization_id,
            action="enrollment_request.approve",
            resource_type="enrollment_request",
            resource_id=request.id,
        )
        self.session.commit()
        self.session.refresh(request)
        return DeviceEnrollmentRequestResponse.model_validate(request)

    def reject_enrollment_request(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        request_id: uuid.UUID,
        payload: EnrollmentRejectRequest,
    ) -> DeviceEnrollmentRequestResponse:
        membership = self._require_membership(organization_id, actor.id)
        self._require_manage(membership.role)
        request = self._require_enrollment_request(organization_id, request_id)
        if request.status != "pending":
            raise ConflictError(
                "enrollment_request_not_pending",
                "Only pending requests can be rejected.",
            )
        request.status = "rejected"
        request.rejection_reason = payload.reason
        request.reviewed_by_user_id = actor.id
        request.reviewed_at = datetime.now(UTC)
        self.enrollment_requests.update(request)
        self._record_audit(
            actor=actor,
            organization_id=organization_id,
            action="enrollment_request.reject",
            resource_type="enrollment_request",
            resource_id=request.id,
        )
        self.session.commit()
        self.session.refresh(request)
        return DeviceEnrollmentRequestResponse.model_validate(request)

    def _require_enrollment_request(self, organization_id: uuid.UUID, request_id: uuid.UUID) -> DeviceEnrollmentRequest:
        request = self.enrollment_requests.get(organization_id=organization_id, request_id=request_id)
        if request is None:
            raise NotFoundError(
                "enrollment_request_not_found",
                "Enrollment request was not found.",
            )
        return request

    # ----------------------------------------------------------------- devices
    def list_devices(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        search: str | None = None,
        device_type_id: uuid.UUID | None = None,
        device_group_id: uuid.UUID | None = None,
        architecture: str | None = None,
        enabled: bool | None = None,
        status: str | None = None,
        sort: str = "name",
        order: str = "asc",
        page: int = 1,
        page_size: int = 20,
    ) -> Page[DeviceResponse]:
        self._require_membership(organization_id, actor.id)
        cutoff = offline_cutoff(offline_threshold_seconds=self.settings.device_offline_threshold_seconds)
        devices, total = self.devices.list_paginated(
            organization_id=organization_id,
            search=search,
            device_type_id=device_type_id,
            device_group_id=device_group_id,
            architecture=architecture,
            enabled=enabled,
            status=status,
            online_cutoff=cutoff,
            sort=sort,
            order=order,
            page=page,
            page_size=page_size,
        )
        return Page[DeviceResponse](
            items=[self._to_device_response(device) for device in devices],
            total=total,
            page=page,
            page_size=page_size,
        )

    def get_device(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
    ) -> DeviceResponse:
        self._require_membership(organization_id, actor.id)
        device = self._require_device(organization_id, device_id)
        return self._to_device_response(device)

    def update_device(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
        payload: DeviceUpdateRequest,
    ) -> DeviceResponse:
        membership = self._require_membership(organization_id, actor.id)
        self._require_manage(membership.role)
        device = self._require_device(organization_id, device_id)

        if payload.name is not None:
            device.name = payload.name
        if payload.clear_device_type:
            device.device_type_id = None
        elif payload.device_type_id is not None:
            self._require_device_type(organization_id, payload.device_type_id)
            device.device_type_id = payload.device_type_id
        if payload.clear_device_group:
            device.device_group_id = None
        elif payload.device_group_id is not None:
            self._require_device_group(organization_id, payload.device_group_id)
            device.device_group_id = payload.device_group_id
        if payload.labels is not None:
            device.labels = payload.labels
        if payload.metadata is not None:
            device.metadata_ = payload.metadata

        self.devices.update(device)
        self.session.commit()
        self.session.refresh(device)
        return self._to_device_response(device)

    def delete_device(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
    ) -> None:
        membership = self._require_membership(organization_id, actor.id)
        self._require_manage(membership.role)
        device = self._require_device(organization_id, device_id)
        device_name = device.name
        self.devices.delete(device)
        self._record_audit(
            actor=actor,
            organization_id=organization_id,
            action="device.delete",
            resource_type="device",
            resource_id=device_id,
            metadata={"name": device_name},
        )
        self.session.commit()

    def set_device_enabled(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
        enabled: bool,
    ) -> DeviceResponse:
        membership = self._require_membership(organization_id, actor.id)
        self._require_manage(membership.role)
        device = self._require_device(organization_id, device_id)
        device.is_enabled = enabled
        self.devices.update(device)
        self.session.commit()
        self.session.refresh(device)
        return self._to_device_response(device)

    def rotate_device_credential(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
    ) -> DeviceCredentialResponse:
        membership = self._require_membership(organization_id, actor.id)
        self._require_manage(membership.role)
        device = self._require_device(organization_id, device_id)
        generated = generate_device_token()
        device.credential_hash = generated.token_hash
        device.credential_prefix = generated.display_prefix
        self.devices.update(device)
        self.session.commit()
        return DeviceCredentialResponse(
            device_id=device.id,
            token=generated.plaintext,
            credential_prefix=generated.display_prefix,
        )

    def revoke_device_credential(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
    ) -> DeviceResponse:
        membership = self._require_membership(organization_id, actor.id)
        self._require_manage(membership.role)
        device = self._require_device(organization_id, device_id)
        device.credential_hash = None
        device.credential_prefix = None
        self.devices.update(device)
        self.session.commit()
        self.session.refresh(device)
        return self._to_device_response(device)

    def revoke_mqtt_credential(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
    ) -> DeviceResponse:
        membership = self._require_membership(organization_id, actor.id)
        self._require_manage(membership.role)
        device = self._require_device(organization_id, device_id)
        cred = self.session.get(DeviceMqttCredential, device_id)
        if cred is None:
            raise NotFoundError("mqtt_not_configured", "This device has no MQTT credential.")
        if cred.revoked_at is None:
            cred.revoked_at = datetime.now(UTC)
            self.session.add(cred)
            self.session.commit()
        self.session.refresh(device)
        return self._to_device_response(device)

    def ping_device(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
        publisher: MqttPublisher,
    ) -> DevicePingResponse:
        membership = self._require_membership(organization_id, actor.id)
        self._require_manage(membership.role)
        self._require_device(organization_id, device_id)
        return MqttService(self.session, settings=self.settings).send_ping(
            organization_id=organization_id,
            device_id=device_id,
            publisher=publisher,
        )

    def require_mqtt_listener(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        device_id: uuid.UUID | None,
        topic: str | None,
    ) -> str:
        from app.mqtt.topics import validate_mqtt_topic

        self._require_membership(organization_id, actor.id)
        if device_id is not None:
            self._require_device(organization_id, device_id)
            resolved = topic or f"devices/{device_id}/events"
        else:
            resolved = topic or ""
        return validate_mqtt_topic(resolved, allow_wildcards=True)

    def publish_mqtt_test_event(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        device_id: uuid.UUID | None,
        topic: str | None,
        payload: str | dict[str, Any] | None,
        publisher: MqttPublisher,
    ) -> MqttTestPublishResponse:
        from app.core.exceptions import ValidationAppError
        from app.mqtt.topics import validate_mqtt_topic

        membership = self._require_membership(organization_id, actor.id)
        self._require_manage(membership.role)
        if device_id is not None:
            self._require_device(organization_id, device_id)
            resolved = topic or f"devices/{device_id}/events"
        else:
            if not topic:
                raise ValidationAppError("mqtt_topic_required", "A topic is required.")
            resolved = topic
        resolved = validate_mqtt_topic(resolved, allow_wildcards=False)
        if isinstance(payload, dict):
            text = json.dumps(payload)
        elif payload is None:
            text = "hello from console"
        else:
            text = payload
        try:
            publisher.publish(resolved, text, qos=1, retain=False)
        except Exception as exc:
            raise ConflictError(
                "mqtt_unavailable",
                "Could not publish to the MQTT broker.",
            ) from exc
        return MqttTestPublishResponse(topic=resolved, payload=text)

    def _require_device(self, organization_id: uuid.UUID, device_id: uuid.UUID) -> Device:
        device = self.devices.get(organization_id=organization_id, device_id=device_id)
        if device is None:
            raise NotFoundError("device_not_found", "Device was not found.")
        return device

    def _to_device_response(self, device: Device) -> DeviceResponse:
        status = connectivity_status(
            device.last_seen_at,
            offline_threshold_seconds=self.settings.device_offline_threshold_seconds,
        )
        return DeviceResponse(
            id=device.id,
            organization_id=device.organization_id,
            name=device.name,
            device_type_id=device.device_type_id,
            device_group_id=device.device_group_id,
            is_enabled=device.is_enabled,
            status=status,
            serial_number=device.serial_number,
            mac_addresses=device.mac_addresses,
            hostname=device.hostname,
            os_name=device.os_name,
            os_version=device.os_version,
            kernel_version=device.kernel_version,
            architecture=device.architecture,
            cpu_model=device.cpu_model,
            cpu_cores=device.cpu_cores,
            memory_mb=device.memory_mb,
            labels=device.labels,
            metadata=device.metadata_,
            credential_prefix=device.credential_prefix,
            last_seen_at=device.last_seen_at,
            registered_at=device.registered_at,
            created_at=device.created_at,
            updated_at=device.updated_at,
            mqtt_configured=self._mqtt_configured(device.id),
            mqtt_status=device.mqtt_status,
            mqtt_status_at=device.mqtt_status_at,
            mqtt_metrics=device.mqtt_metrics,
            mqtt_metrics_at=device.mqtt_metrics_at,
        )

    def _mqtt_configured(self, device_id: uuid.UUID) -> bool:
        cred = self.session.get(DeviceMqttCredential, device_id)
        return cred is not None and cred.revoked_at is None
