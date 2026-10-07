"""Organization-scoped persistence for fleet resources (PostgreSQL adapter).

Every read/write requires ``organization_id`` so cross-tenant access is
impossible by construction. The only exceptions are the registration-token and
device-credential lookups used by the device-facing agent API, which resolve the
organization from the secret itself.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Select, String, asc, cast, desc, func, or_, select
from sqlalchemy.orm import Session

from app.devices.models import (
    Device,
    DeviceEnrollmentRequest,
    DeviceGroup,
    DeviceType,
    EnrollmentApiKey,
    RegistrationToken,
)


def _device_counts(session: Session, organization_id: uuid.UUID, column) -> dict[uuid.UUID, int]:
    """Count assigned devices per value of a Device classification column.

    Restrict counts to the organization and omit null assignments and
    classifications with no devices.
    """
    statement = (
        select(column, func.count())
        .where(Device.organization_id == organization_id, column.is_not(None))
        .group_by(column)
    )
    return {key: int(count) for key, count in session.execute(statement).all()}


class DeviceTypeRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, *, organization_id: uuid.UUID, type_id: uuid.UUID) -> DeviceType | None:
        statement = select(DeviceType).where(
            DeviceType.id == type_id,
            DeviceType.organization_id == organization_id,
        )
        return self.session.scalar(statement)

    def get_by_name(self, *, organization_id: uuid.UUID, name: str) -> DeviceType | None:
        statement = select(DeviceType).where(
            DeviceType.organization_id == organization_id,
            func.lower(DeviceType.name) == name.lower(),
        )
        return self.session.scalar(statement)

    def get_by_slug(self, *, organization_id: uuid.UUID, slug: str) -> DeviceType | None:
        """Return the type matching the exact slug in the organization, or None."""
        statement = select(DeviceType).where(
            DeviceType.organization_id == organization_id,
            DeviceType.slug == slug,
        )
        return self.session.scalar(statement)

    def list(self, *, organization_id: uuid.UUID) -> list[DeviceType]:
        statement = (
            select(DeviceType).where(DeviceType.organization_id == organization_id).order_by(DeviceType.name.asc())
        )
        return list(self.session.scalars(statement).all())

    def device_counts(self, *, organization_id: uuid.UUID) -> dict[uuid.UUID, int]:
        """Return assigned device counts by type, omitting types with no devices."""
        return _device_counts(self.session, organization_id, Device.device_type_id)

    def create(self, device_type: DeviceType) -> DeviceType:
        self.session.add(device_type)
        self.session.flush()
        return device_type

    def update(self, device_type: DeviceType) -> DeviceType:
        self.session.add(device_type)
        self.session.flush()
        return device_type

    def delete(self, device_type: DeviceType) -> None:
        self.session.delete(device_type)
        self.session.flush()

    def count_devices(self, *, organization_id: uuid.UUID, type_id: uuid.UUID) -> int:
        statement = (
            select(func.count())
            .select_from(Device)
            .where(
                Device.organization_id == organization_id,
                Device.device_type_id == type_id,
            )
        )
        return int(self.session.scalar(statement) or 0)


class DeviceGroupRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, *, organization_id: uuid.UUID, group_id: uuid.UUID) -> DeviceGroup | None:
        statement = select(DeviceGroup).where(
            DeviceGroup.id == group_id,
            DeviceGroup.organization_id == organization_id,
        )
        return self.session.scalar(statement)

    def get_by_name(self, *, organization_id: uuid.UUID, name: str) -> DeviceGroup | None:
        statement = select(DeviceGroup).where(
            DeviceGroup.organization_id == organization_id,
            func.lower(DeviceGroup.name) == name.lower(),
        )
        return self.session.scalar(statement)

    def get_by_slug(self, *, organization_id: uuid.UUID, slug: str) -> DeviceGroup | None:
        """Return the group matching the exact slug in the organization, or None."""
        statement = select(DeviceGroup).where(
            DeviceGroup.organization_id == organization_id,
            DeviceGroup.slug == slug,
        )
        return self.session.scalar(statement)

    def list(self, *, organization_id: uuid.UUID) -> list[DeviceGroup]:
        statement = (
            select(DeviceGroup).where(DeviceGroup.organization_id == organization_id).order_by(DeviceGroup.name.asc())
        )
        return list(self.session.scalars(statement).all())

    def device_counts(self, *, organization_id: uuid.UUID) -> dict[uuid.UUID, int]:
        """Return assigned device counts by group, omitting groups with no devices."""
        return _device_counts(self.session, organization_id, Device.device_group_id)

    def create(self, group: DeviceGroup) -> DeviceGroup:
        self.session.add(group)
        self.session.flush()
        return group

    def update(self, group: DeviceGroup) -> DeviceGroup:
        self.session.add(group)
        self.session.flush()
        return group

    def delete(self, group: DeviceGroup) -> None:
        self.session.delete(group)
        self.session.flush()

    def count_devices(self, *, organization_id: uuid.UUID, group_id: uuid.UUID) -> int:
        statement = (
            select(func.count())
            .select_from(Device)
            .where(
                Device.organization_id == organization_id,
                Device.device_group_id == group_id,
            )
        )
        return int(self.session.scalar(statement) or 0)


class RegistrationTokenRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, *, organization_id: uuid.UUID, token_id: uuid.UUID) -> RegistrationToken | None:
        statement = select(RegistrationToken).where(
            RegistrationToken.id == token_id,
            RegistrationToken.organization_id == organization_id,
        )
        return self.session.scalar(statement)

    def get_by_hash(self, token_hash: str) -> RegistrationToken | None:
        """Resolve a token by its lookup hash across all organizations."""
        statement = select(RegistrationToken).where(RegistrationToken.token_hash == token_hash)
        return self.session.scalar(statement)

    def list(self, *, organization_id: uuid.UUID) -> list[RegistrationToken]:
        statement = (
            select(RegistrationToken)
            .where(RegistrationToken.organization_id == organization_id)
            .order_by(RegistrationToken.created_at.desc())
        )
        return list(self.session.scalars(statement).all())

    def create(self, token: RegistrationToken) -> RegistrationToken:
        self.session.add(token)
        self.session.flush()
        return token

    def update(self, token: RegistrationToken) -> RegistrationToken:
        self.session.add(token)
        self.session.flush()
        return token


class DeviceRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, *, organization_id: uuid.UUID, device_id: uuid.UUID) -> Device | None:
        statement = select(Device).where(
            Device.id == device_id,
            Device.organization_id == organization_id,
        )
        return self.session.scalar(statement)

    def get_by_credential_hash(self, credential_hash: str) -> Device | None:
        """Resolve a device by its credential hash across all organizations."""
        statement = select(Device).where(Device.credential_hash == credential_hash)
        return self.session.scalar(statement)

    def list_candidates_for_identity(
        self,
        *,
        serial_number: str | None,
        mac_addresses: Sequence[str],
    ) -> list[Device]:
        """Return devices that could match an incoming identity (any org).

        Cross-organization candidates are included so the caller can reject
        registrations that collide with another tenant.
        """
        conditions = []
        if serial_number:
            conditions.append(Device.serial_number == serial_number)
        for mac in mac_addresses:
            conditions.append(Device.mac_addresses.contains([mac]))
        if not conditions:
            return []
        statement = select(Device).where(or_(*conditions))
        return list(self.session.scalars(statement).all())

    def create(self, device: Device) -> Device:
        self.session.add(device)
        self.session.flush()
        return device

    def update(self, device: Device) -> Device:
        self.session.add(device)
        self.session.flush()
        return device

    def delete(self, device: Device) -> None:
        self.session.delete(device)
        self.session.flush()

    def _base_query(
        self,
        *,
        organization_id: uuid.UUID,
        search: str | None,
        device_type_id: uuid.UUID | None,
        device_group_id: uuid.UUID | None,
        architecture: str | None,
        enabled: bool | None,
        online_cutoff: datetime | None,
        status: str | None,
    ) -> Select:
        statement = select(Device).where(Device.organization_id == organization_id)
        if search:
            pattern = f"%{search.lower()}%"
            statement = statement.where(
                or_(
                    func.lower(Device.name).like(pattern),
                    func.lower(func.coalesce(Device.hostname, "")).like(pattern),
                    func.lower(func.coalesce(Device.serial_number, "")).like(pattern),
                    func.lower(cast(Device.mac_addresses, String)).like(pattern),
                )
            )
        if device_type_id is not None:
            statement = statement.where(Device.device_type_id == device_type_id)
        if device_group_id is not None:
            statement = statement.where(Device.device_group_id == device_group_id)
        if architecture:
            statement = statement.where(func.lower(Device.architecture) == architecture.lower())
        if enabled is not None:
            statement = statement.where(Device.is_enabled.is_(enabled))
        if status == "online" and online_cutoff is not None:
            statement = statement.where(Device.last_seen_at >= online_cutoff)
        elif status == "offline" and online_cutoff is not None:
            statement = statement.where(
                Device.last_seen_at.is_not(None),
                Device.last_seen_at < online_cutoff,
            )
        elif status == "never_seen":
            statement = statement.where(Device.last_seen_at.is_(None))
        return statement

    def list_paginated(
        self,
        *,
        organization_id: uuid.UUID,
        search: str | None = None,
        device_type_id: uuid.UUID | None = None,
        device_group_id: uuid.UUID | None = None,
        architecture: str | None = None,
        enabled: bool | None = None,
        status: str | None = None,
        online_cutoff: datetime | None = None,
        sort: str = "name",
        order: str = "asc",
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Device], int]:
        statement = self._base_query(
            organization_id=organization_id,
            search=search,
            device_type_id=device_type_id,
            device_group_id=device_group_id,
            architecture=architecture,
            enabled=enabled,
            online_cutoff=online_cutoff,
            status=status,
        )

        count_statement = select(func.count()).select_from(statement.subquery())
        total = int(self.session.scalar(count_statement) or 0)

        sort_column = {
            "name": Device.name,
            "last_seen_at": Device.last_seen_at,
            "created_at": Device.created_at,
            "registered_at": Device.registered_at,
        }.get(sort, Device.name)
        direction = desc if order == "desc" else asc
        statement = statement.order_by(direction(sort_column), Device.id.asc())
        statement = statement.offset((page - 1) * page_size).limit(page_size)

        devices = list(self.session.scalars(statement).all())
        return devices, total


class EnrollmentApiKeyRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, *, organization_id: uuid.UUID, key_id: uuid.UUID) -> EnrollmentApiKey | None:
        statement = select(EnrollmentApiKey).where(
            EnrollmentApiKey.id == key_id,
            EnrollmentApiKey.organization_id == organization_id,
        )
        return self.session.scalar(statement)

    def get_by_hash(self, key_hash: str) -> EnrollmentApiKey | None:
        """Resolve an API key by its lookup hash across all organizations."""
        statement = select(EnrollmentApiKey).where(EnrollmentApiKey.key_hash == key_hash)
        return self.session.scalar(statement)

    def list(self, *, organization_id: uuid.UUID) -> list[EnrollmentApiKey]:
        statement = (
            select(EnrollmentApiKey)
            .where(EnrollmentApiKey.organization_id == organization_id)
            .order_by(EnrollmentApiKey.created_at.desc())
        )
        return list(self.session.scalars(statement).all())

    def create(self, key: EnrollmentApiKey) -> EnrollmentApiKey:
        self.session.add(key)
        self.session.flush()
        return key

    def update(self, key: EnrollmentApiKey) -> EnrollmentApiKey:
        self.session.add(key)
        self.session.flush()
        return key


class DeviceEnrollmentRequestRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, *, organization_id: uuid.UUID, request_id: uuid.UUID) -> DeviceEnrollmentRequest | None:
        statement = select(DeviceEnrollmentRequest).where(
            DeviceEnrollmentRequest.id == request_id,
            DeviceEnrollmentRequest.organization_id == organization_id,
        )
        return self.session.scalar(statement)

    def get_by_id(self, request_id: uuid.UUID) -> DeviceEnrollmentRequest | None:
        """Resolve a request by id across organizations (device-facing poll)."""
        statement = select(DeviceEnrollmentRequest).where(DeviceEnrollmentRequest.id == request_id)
        return self.session.scalar(statement)

    def list(
        self,
        *,
        organization_id: uuid.UUID,
        status: str | None = None,
    ) -> list[DeviceEnrollmentRequest]:
        statement = select(DeviceEnrollmentRequest).where(DeviceEnrollmentRequest.organization_id == organization_id)
        if status is not None:
            statement = statement.where(DeviceEnrollmentRequest.status == status)
        statement = statement.order_by(DeviceEnrollmentRequest.created_at.desc())
        return list(self.session.scalars(statement).all())

    def create(self, request: DeviceEnrollmentRequest) -> DeviceEnrollmentRequest:
        self.session.add(request)
        self.session.flush()
        return request

    def update(self, request: DeviceEnrollmentRequest) -> DeviceEnrollmentRequest:
        self.session.add(request)
        self.session.flush()
        return request
