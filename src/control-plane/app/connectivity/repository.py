"""Persistence for device WireGuard peers."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectivity.models import ConnectivityStatus, DeviceConnectivity
from app.devices.models import Device


class ConnectivityRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, device_id: uuid.UUID) -> DeviceConnectivity | None:
        return self.session.get(DeviceConnectivity, device_id)

    def get_by_public_key(self, public_key: str) -> DeviceConnectivity | None:
        return self.session.scalar(
            select(DeviceConnectivity).where(DeviceConnectivity.wg_public_key == public_key)
        )

    def list_for_organization(self, organization_id: uuid.UUID) -> list[DeviceConnectivity]:
        return list(
            self.session.scalars(
                select(DeviceConnectivity)
                .where(DeviceConnectivity.organization_id == organization_id)
                .order_by(DeviceConnectivity.created_at.asc())
            ).all()
        )

    def list_active_peers(self) -> list[DeviceConnectivity]:
        """Peers the gateway should have: active rows of enabled devices that still hold a credential."""
        return list(
            self.session.scalars(
                select(DeviceConnectivity)
                .join(Device, Device.id == DeviceConnectivity.device_id)
                .where(
                    DeviceConnectivity.status == ConnectivityStatus.ACTIVE.value,
                    DeviceConnectivity.wg_public_key.is_not(None),
                    DeviceConnectivity.wg_address.is_not(None),
                    Device.is_enabled.is_(True),
                    Device.credential_hash.is_not(None),
                )
            ).all()
        )

    def allocated_addresses(self) -> set[str]:
        rows = self.session.scalars(
            select(DeviceConnectivity.wg_address).where(
                DeviceConnectivity.wg_address.is_not(None),
                DeviceConnectivity.status != ConnectivityStatus.REVOKED.value,
            )
        )
        return {str(value) for value in rows.all() if value is not None}

    def add(self, row: DeviceConnectivity) -> DeviceConnectivity:
        self.session.add(row)
        self.session.flush()
        return row
