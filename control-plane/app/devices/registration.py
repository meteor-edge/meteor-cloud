"""Device-facing registration and heartbeat business rules.

Registration is atomic: the token is validated, the tenant/type/group derived,
the device upserted, its credential rotated, and the token use count incremented
in a single transaction. Any failure rolls the whole thing back. Secret values
are never logged.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import ConflictError, UnauthorizedError
from app.devices.identity import DeviceIdentity, match_existing_device
from app.devices.models import Device, RegistrationToken
from app.devices.repository import DeviceRepository, RegistrationTokenRepository
from app.devices.schemas import (
    AgentHeartbeatRequest,
    AgentHeartbeatResponse,
    AgentRegisterRequest,
    AgentRegisterResponse,
)
from app.devices.status import connectivity_status
from app.devices.tokens import generate_device_token, hash_token
from data_plane.connectivity.mqtt.credentials import issue_mqtt_credentials


class RegistrationService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.tokens = RegistrationTokenRepository(session)
        self.devices = DeviceRepository(session)

    def register(self, payload: AgentRegisterRequest) -> AgentRegisterResponse:
        try:
            return self._register(payload)
        except Exception:
            self.session.rollback()
            raise

    def _register(self, payload: AgentRegisterRequest) -> AgentRegisterResponse:
        token = self._validate_token(payload.token)

        identity = DeviceIdentity.from_inventory(
            serial_number=payload.serial_number,
            mac_addresses=payload.mac_addresses,
        )

        candidates = self.devices.list_candidates_for_identity(
            serial_number=identity.serial_number,
            mac_addresses=identity.mac_addresses,
        )
        match = match_existing_device(
            identity,
            organization_id=token.organization_id,
            candidates=candidates,
        )
        if match.cross_organization:
            raise ConflictError(
                "device_registered_elsewhere",
                "This device is already registered in another organization.",
            )
        if match.ambiguous:
            raise ConflictError(
                "ambiguous_device_identity",
                "The device identity matches multiple existing devices.",
            )

        generated = generate_device_token()
        now = datetime.now(UTC)

        if match.device is not None:
            device = self._update_existing(match.device, payload, identity, token, generated, now)
        else:
            device = self._create_new(payload, identity, token, generated, now)

        token.use_count += 1
        self.tokens.update(token)

        mqtt = issue_mqtt_credentials(self.session, device, self.settings)

        self.session.commit()
        self.session.refresh(device)

        return AgentRegisterResponse(
            device_id=device.id,
            device_token=generated.plaintext,
            organization_id=device.organization_id,
            name=device.name,
            heartbeat_interval_seconds=self.settings.device_heartbeat_interval_seconds,
            mqtt=mqtt,
        )

    def _validate_token(self, plaintext: str) -> RegistrationToken:
        token = self.tokens.get_by_hash(hash_token(plaintext))
        if token is None:
            raise UnauthorizedError(
                "invalid_registration_token",
                "The registration token is invalid.",
            )
        if token.revoked_at is not None:
            raise UnauthorizedError(
                "invalid_registration_token",
                "The registration token has been revoked.",
            )
        if token.expires_at is not None:
            expires_at = token.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at <= datetime.now(UTC):
                raise UnauthorizedError(
                    "invalid_registration_token",
                    "The registration token has expired.",
                )
        if token.max_uses is not None and token.use_count >= token.max_uses:
            raise UnauthorizedError(
                "invalid_registration_token",
                "The registration token has no remaining uses.",
            )
        return token

    def _resolve_name(self, payload: AgentRegisterRequest, identity: DeviceIdentity) -> str:
        candidate = payload.name or payload.hostname or identity.serial_number
        if not candidate and identity.mac_addresses:
            candidate = identity.mac_addresses[0]
        if candidate:
            return candidate.strip()[:255]
        return "unnamed-device"

    def _apply_inventory(
        self,
        device: Device,
        payload: AgentRegisterRequest,
        identity: DeviceIdentity,
    ) -> None:
        device.serial_number = identity.serial_number
        device.mac_addresses = identity.mac_addresses
        device.hostname = payload.hostname
        device.os_name = payload.os_name
        device.os_version = payload.os_version
        device.kernel_version = payload.kernel_version
        device.architecture = payload.architecture
        device.cpu_model = payload.cpu_model
        device.cpu_cores = payload.cpu_cores
        device.memory_mb = payload.memory_mb
        if payload.labels:
            device.labels = payload.labels
        if payload.metadata:
            device.metadata_ = payload.metadata

    def _create_new(
        self,
        payload: AgentRegisterRequest,
        identity: DeviceIdentity,
        token: RegistrationToken,
        generated,
        now: datetime,
    ) -> Device:
        device = Device(
            organization_id=token.organization_id,
            name=self._resolve_name(payload, identity),
            device_type_id=token.device_type_id,
            device_group_id=token.device_group_id,
            is_enabled=True,
            labels=payload.labels or {},
            metadata_=payload.metadata or {},
            credential_hash=generated.token_hash,
            credential_prefix=generated.display_prefix,
            last_seen_at=now,
            registered_at=now,
            registration_token_id=token.id,
        )
        self._apply_inventory(device, payload, identity)
        return self.devices.create(device)

    def _update_existing(
        self,
        device: Device,
        payload: AgentRegisterRequest,
        identity: DeviceIdentity,
        token: RegistrationToken,
        generated,
        now: datetime,
    ) -> Device:
        if payload.name:
            device.name = payload.name.strip()[:255]
        # Token-bound assignments apply on re-registration when provided.
        if token.device_type_id is not None:
            device.device_type_id = token.device_type_id
        if token.device_group_id is not None:
            device.device_group_id = token.device_group_id
        self._apply_inventory(device, payload, identity)
        device.credential_hash = generated.token_hash
        device.credential_prefix = generated.display_prefix
        device.last_seen_at = now
        device.registered_at = now
        device.registration_token_id = token.id
        device.is_enabled = True
        return self.devices.update(device)

    def heartbeat(
        self,
        *,
        device: Device,
        payload: AgentHeartbeatRequest,
    ) -> AgentHeartbeatResponse:
        now = datetime.now(UTC)
        device.last_seen_at = now
        if payload.hostname is not None:
            device.hostname = payload.hostname
        if payload.os_version is not None:
            device.os_version = payload.os_version
        if payload.kernel_version is not None:
            device.kernel_version = payload.kernel_version
        self.devices.update(device)
        self.session.commit()
        self.session.refresh(device)
        return AgentHeartbeatResponse(
            device_id=device.id,
            status=connectivity_status(
                device.last_seen_at,
                offline_threshold_seconds=self.settings.device_offline_threshold_seconds,
                now=now,
            ),
            heartbeat_interval_seconds=self.settings.device_heartbeat_interval_seconds,
            server_time=now,
        )
