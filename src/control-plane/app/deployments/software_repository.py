"""Persistence for device_software rows."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deployments.software_models import DeviceSoftware


class DeviceSoftwareRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_workload(
        self, *, device_id: uuid.UUID, workload_key: str
    ) -> DeviceSoftware | None:
        return self.session.scalar(
            select(DeviceSoftware).where(
                DeviceSoftware.device_id == device_id,
                DeviceSoftware.workload_key == workload_key,
            )
        )

    def by_device_for_workload(
        self, *, device_ids: list[uuid.UUID], workload_key: str
    ) -> dict[uuid.UUID, DeviceSoftware]:
        """One query for a workload's rows across many devices (Deploy detail)."""
        if not device_ids:
            return {}
        rows = self.session.scalars(
            select(DeviceSoftware).where(
                DeviceSoftware.device_id.in_(device_ids),
                DeviceSoftware.workload_key == workload_key,
            )
        )
        return {row.device_id: row for row in rows}

    def list_for_device(self, *, device_id: uuid.UUID) -> list[DeviceSoftware]:
        statement = (
            select(DeviceSoftware)
            .where(DeviceSoftware.device_id == device_id)
            .order_by(DeviceSoftware.workload_key.asc())
        )
        return list(self.session.scalars(statement).all())

    def create(self, row: DeviceSoftware) -> DeviceSoftware:
        self.session.add(row)
        self.session.flush()
        return row
