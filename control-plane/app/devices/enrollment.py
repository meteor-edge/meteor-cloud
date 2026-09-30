"""Device-initiated enrollment: request submission and claim/polling.

Flow: a device authenticates with an organization enrollment API key and submits
an enrollment request (status ``pending``). An admin approves or rejects it. The
device then polls with its one-time claim secret; on first poll after approval a
device credential is issued (and the device row created), returned exactly once,
and the request is marked claimed. Everything is atomic and secrets are never
logged.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import ConflictError, UnauthorizedError
from app.devices.identity import DeviceIdentity, match_existing_device
from app.devices.models import (
    Device,
    DeviceEnrollmentRequest,
    EnrollmentApiKey,
)
from app.devices.repository import (
    DeviceEnrollmentRequestRepository,
    DeviceRepository,
    EnrollmentApiKeyRepository,
)
from app.devices.schemas import (
    AgentEnrollCheckResponse,
    AgentEnrollPollRequest,
    AgentEnrollPollResponse,
    AgentEnrollRequest,
    AgentEnrollResponse,
)
from app.devices.tokens import generate_claim_secret, generate_device_token, hash_token
from app.tenancy.models import Organization
from data_plane.connectivity.mqtt.credentials import issue_mqtt_credentials


class EnrollmentService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.api_keys = EnrollmentApiKeyRepository(session)
        self.requests = DeviceEnrollmentRequestRepository(session)
        self.devices = DeviceRepository(session)

    # ------------------------------------------------------------- submit
    def submit_request(
        self,
        *,
        api_key: EnrollmentApiKey,
        payload: AgentEnrollRequest,
    ) -> AgentEnrollResponse:
        try:
            return self._submit_request(api_key, payload)
        except Exception:
            self.session.rollback()
            raise

    def _submit_request(
        self,
        api_key: EnrollmentApiKey,
        payload: AgentEnrollRequest,
    ) -> AgentEnrollResponse:
        identity = DeviceIdentity.from_inventory(
            serial_number=payload.serial_number,
            mac_addresses=payload.mac_addresses,
        )
        claim = generate_claim_secret()
        now = datetime.now(UTC)
        ttl = self.settings.enrollment_request_ttl_seconds
        expires_at = now + timedelta(seconds=ttl) if ttl > 0 else None

        request = DeviceEnrollmentRequest(
            organization_id=api_key.organization_id,
            api_key_id=api_key.id,
            status="pending",
            claim_secret_hash=claim.token_hash,
            claim_secret_prefix=claim.display_prefix,
            requested_name=(payload.name.strip()[:255] if payload.name else None),
            serial_number=identity.serial_number,
            mac_addresses=identity.mac_addresses,
            hostname=payload.hostname,
            os_name=payload.os_name,
            os_version=payload.os_version,
            kernel_version=payload.kernel_version,
            architecture=payload.architecture,
            cpu_model=payload.cpu_model,
            cpu_cores=payload.cpu_cores,
            memory_mb=payload.memory_mb,
            labels=payload.labels or {},
            metadata_=payload.metadata or {},
            expires_at=expires_at,
        )
        self.requests.create(request)

        api_key.last_used_at = now
        self.api_keys.update(api_key)

        self.session.commit()
        self.session.refresh(request)
        return AgentEnrollResponse(
            request_id=request.id,
            claim_secret=claim.plaintext,
            status="pending",
            poll_interval_seconds=self.settings.enrollment_poll_interval_seconds,
            expires_at=request.expires_at,
        )

    def check(self, *, api_key: EnrollmentApiKey) -> AgentEnrollCheckResponse:
        organization = self.session.get(Organization, api_key.organization_id)
        organization_name = organization.name if organization is not None else ""
        api_key.last_used_at = datetime.now(UTC)
        self.api_keys.update(api_key)
        self.session.commit()
        return AgentEnrollCheckResponse(
            ok=True,
            organization_id=api_key.organization_id,
            organization_name=organization_name,
            key_name=api_key.name,
            key_prefix=api_key.key_prefix,
            expires_at=api_key.expires_at,
        )

    # -------------------------------------------------------------- poll
    def poll(self, payload: AgentEnrollPollRequest) -> AgentEnrollPollResponse:
        try:
            return self._poll(payload)
        except Exception:
            self.session.rollback()
            raise

    def _poll(self, payload: AgentEnrollPollRequest) -> AgentEnrollPollResponse:
        request = self.requests.get_by_id(payload.request_id)
        if request is None or request.claim_secret_hash != hash_token(payload.claim_secret):
            raise UnauthorizedError(
                "invalid_enrollment_request",
                "The enrollment request is invalid.",
            )

        now = datetime.now(UTC)
        poll_interval = self.settings.enrollment_poll_interval_seconds

        if request.status == "pending" and self._is_expired(request, now):
            request.status = "expired"
            self.requests.update(request)
            self.session.commit()

        if request.status == "pending":
            return AgentEnrollPollResponse(status="pending", poll_interval_seconds=poll_interval)
        if request.status == "expired":
            return AgentEnrollPollResponse(status="expired", poll_interval_seconds=poll_interval)
        if request.status == "rejected":
            return AgentEnrollPollResponse(
                status="rejected",
                poll_interval_seconds=poll_interval,
                rejection_reason=request.rejection_reason,
            )

        # status == "approved"
        if request.claimed_at is not None:
            # Already claimed; the device holds its credential. Do not re-issue.
            return AgentEnrollPollResponse(
                status="approved",
                poll_interval_seconds=poll_interval,
                device_id=request.device_id,
            )

        device, plaintext = self._issue_device(request, now)
        mqtt = issue_mqtt_credentials(self.session, device, self.settings)
        request.claimed_at = now
        request.device_id = device.id
        self.requests.update(request)
        self.session.commit()
        self.session.refresh(device)
        return AgentEnrollPollResponse(
            status="approved",
            poll_interval_seconds=poll_interval,
            device_id=device.id,
            device_token=plaintext,
            organization_id=device.organization_id,
            name=device.name,
            heartbeat_interval_seconds=self.settings.device_heartbeat_interval_seconds,
            mqtt=mqtt,
        )

    # ------------------------------------------------------------- helpers
    def _is_expired(self, request: DeviceEnrollmentRequest, now: datetime) -> bool:
        if request.expires_at is None:
            return False
        expires_at = request.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return expires_at <= now

    def _issue_device(self, request: DeviceEnrollmentRequest, now: datetime) -> tuple[Device, str]:
        identity = DeviceIdentity.from_inventory(
            serial_number=request.serial_number,
            mac_addresses=request.mac_addresses,
        )
        candidates = self.devices.list_candidates_for_identity(
            serial_number=identity.serial_number,
            mac_addresses=identity.mac_addresses,
        )
        match = match_existing_device(
            identity,
            organization_id=request.organization_id,
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
        if match.device is not None:
            device = self._update_existing(match.device, request, identity, generated, now)
        else:
            device = self._create_new(request, identity, generated, now)
        return device, generated.plaintext

    def _resolve_name(self, request: DeviceEnrollmentRequest, identity: DeviceIdentity) -> str:
        candidate = (
            request.assigned_name
            or request.requested_name
            or request.hostname
            or identity.serial_number
            or (identity.mac_addresses[0] if identity.mac_addresses else None)
        )
        if candidate:
            return candidate.strip()[:255]
        return "unnamed-device"

    def _apply_inventory(self, device: Device, request: DeviceEnrollmentRequest, identity: DeviceIdentity) -> None:
        device.serial_number = identity.serial_number
        device.mac_addresses = identity.mac_addresses
        device.hostname = request.hostname
        device.os_name = request.os_name
        device.os_version = request.os_version
        device.kernel_version = request.kernel_version
        device.architecture = request.architecture
        device.cpu_model = request.cpu_model
        device.cpu_cores = request.cpu_cores
        device.memory_mb = request.memory_mb
        if request.labels:
            device.labels = request.labels
        if request.metadata_:
            device.metadata_ = request.metadata_

    def _create_new(
        self,
        request: DeviceEnrollmentRequest,
        identity: DeviceIdentity,
        generated,
        now: datetime,
    ) -> Device:
        device = Device(
            organization_id=request.organization_id,
            name=self._resolve_name(request, identity),
            device_type_id=request.device_type_id,
            device_group_id=request.device_group_id,
            is_enabled=True,
            labels=request.labels or {},
            metadata_=request.metadata_ or {},
            credential_hash=generated.token_hash,
            credential_prefix=generated.display_prefix,
            last_seen_at=now,
            registered_at=now,
        )
        self._apply_inventory(device, request, identity)
        return self.devices.create(device)

    def _update_existing(
        self,
        device: Device,
        request: DeviceEnrollmentRequest,
        identity: DeviceIdentity,
        generated,
        now: datetime,
    ) -> Device:
        if request.assigned_name:
            device.name = request.assigned_name.strip()[:255]
        if request.device_type_id is not None:
            device.device_type_id = request.device_type_id
        if request.device_group_id is not None:
            device.device_group_id = request.device_group_id
        self._apply_inventory(device, request, identity)
        device.credential_hash = generated.token_hash
        device.credential_prefix = generated.display_prefix
        device.last_seen_at = now
        device.registered_at = now
        device.is_enabled = True
        return self.devices.update(device)
