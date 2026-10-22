"""Persistence for Deploy history and per-device progress."""

from __future__ import annotations

import uuid

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.deployments.models import (
    Deployment,
    DeploymentDevice,
    DeploymentDeviceStatus,
    DeploymentStatus,
)


class DeploymentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, deployment: Deployment) -> Deployment:
        self.session.add(deployment)
        self.session.flush()
        return deployment

    def create_device(self, row: DeploymentDevice) -> DeploymentDevice:
        self.session.add(row)
        self.session.flush()
        return row

    def get(self, *, organization_id: uuid.UUID, deployment_id: uuid.UUID) -> Deployment | None:
        return self.session.scalar(
            select(Deployment).where(
                Deployment.id == deployment_id,
                Deployment.organization_id == organization_id,
            )
        )

    def list_paginated(
        self,
        *,
        organization_id: uuid.UUID,
        status: str | None = None,
        release_id: uuid.UUID | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Deployment], int]:
        statement: Select[tuple[Deployment]] = select(Deployment).where(
            Deployment.organization_id == organization_id
        )
        if status is not None:
            statement = statement.where(Deployment.status == status)
        if release_id is not None:
            statement = statement.where(Deployment.release_id == release_id)

        total = int(
            self.session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        )
        statement = (
            statement.order_by(Deployment.created_at.desc(), Deployment.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(self.session.scalars(statement).all()), total

    def list_devices(self, *, deployment_id: uuid.UUID) -> list[DeploymentDevice]:
        return list(
            self.session.scalars(
                select(DeploymentDevice)
                .where(DeploymentDevice.deployment_id == deployment_id)
                .order_by(DeploymentDevice.created_at.asc(), DeploymentDevice.id.asc())
            ).all()
        )

    def count_by_status(self, *, deployment_id: uuid.UUID) -> dict[str, int]:
        rows = self.session.execute(
            select(DeploymentDevice.status, func.count())
            .where(DeploymentDevice.deployment_id == deployment_id)
            .group_by(DeploymentDevice.status)
        ).all()
        return {str(status): int(count) for status, count in rows}

    def list_active_for_device(
        self,
        *,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
        release_id: uuid.UUID,
    ) -> list[DeploymentDevice]:
        """Open progress rows for this device on deployments of ``release_id``."""
        open_statuses = (
            DeploymentDeviceStatus.PENDING.value,
            DeploymentDeviceStatus.UPDATING.value,
        )
        return list(
            self.session.scalars(
                select(DeploymentDevice)
                .join(Deployment, Deployment.id == DeploymentDevice.deployment_id)
                .where(
                    Deployment.organization_id == organization_id,
                    Deployment.release_id == release_id,
                    DeploymentDevice.device_id == device_id,
                    DeploymentDevice.status.in_(open_statuses),
                )
            ).all()
        )

    def list_in_progress_for_device(
        self,
        *,
        organization_id: uuid.UUID,
        device_id: uuid.UUID,
    ) -> list[Deployment]:
        """In-progress deployments that include this device (any row status)."""
        return list(
            self.session.scalars(
                select(Deployment)
                .join(DeploymentDevice, DeploymentDevice.deployment_id == Deployment.id)
                .where(
                    Deployment.organization_id == organization_id,
                    Deployment.status == DeploymentStatus.IN_PROGRESS.value,
                    DeploymentDevice.device_id == device_id,
                )
                .distinct()
            ).all()
        )
