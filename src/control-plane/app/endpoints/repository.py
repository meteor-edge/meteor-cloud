"""Persistence for application endpoints."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.endpoints.models import ApplicationEndpoint


class EndpointRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, *, organization_id: uuid.UUID, endpoint_id: uuid.UUID) -> ApplicationEndpoint | None:
        return self.session.scalar(
            select(ApplicationEndpoint).where(
                ApplicationEndpoint.id == endpoint_id,
                ApplicationEndpoint.organization_id == organization_id,
            )
        )

    def get_by_hostname(self, hostname: str) -> ApplicationEndpoint | None:
        return self.session.scalar(
            select(ApplicationEndpoint).where(ApplicationEndpoint.hostname == hostname)
        )

    def list_for_organization(
        self, *, organization_id: uuid.UUID, device_id: uuid.UUID | None = None
    ) -> list[ApplicationEndpoint]:
        statement = select(ApplicationEndpoint).where(
            ApplicationEndpoint.organization_id == organization_id
        )
        if device_id is not None:
            statement = statement.where(ApplicationEndpoint.device_id == device_id)
        statement = statement.order_by(ApplicationEndpoint.hostname.asc())
        return list(self.session.scalars(statement).all())

    def list_enabled_ready(self) -> list[ApplicationEndpoint]:
        return list(
            self.session.scalars(
                select(ApplicationEndpoint).where(
                    ApplicationEndpoint.enabled.is_(True),
                    ApplicationEndpoint.availability == "ready",
                )
            ).all()
        )

    def list_for_device(self, *, device_id: uuid.UUID) -> list[ApplicationEndpoint]:
        return list(
            self.session.scalars(
                select(ApplicationEndpoint).where(ApplicationEndpoint.device_id == device_id)
            ).all()
        )

    def enabled_on_port(self, *, device_id: uuid.UUID, port: int) -> ApplicationEndpoint | None:
        return self.session.scalar(
            select(ApplicationEndpoint).where(
                ApplicationEndpoint.device_id == device_id,
                ApplicationEndpoint.port == port,
                ApplicationEndpoint.enabled.is_(True),
            )
        )

    def add(self, row: ApplicationEndpoint) -> ApplicationEndpoint:
        self.session.add(row)
        self.session.flush()
        return row

    def delete(self, row: ApplicationEndpoint) -> None:
        self.session.delete(row)
        self.session.flush()
