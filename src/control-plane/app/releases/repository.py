"""Organization-scoped release persistence."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.releases.kinds import ReleaseKind
from app.releases.models import Release


class ReleaseRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, *, organization_id: uuid.UUID, release_id: uuid.UUID) -> Release | None:
        statement = select(Release).where(
            Release.id == release_id,
            Release.organization_id == organization_id,
        )
        return self.session.scalar(statement)

    def find_duplicate(
        self,
        *,
        organization_id: uuid.UUID,
        kind: ReleaseKind,
        name: str,
        version: str,
    ) -> Release | None:
        statement = select(Release).where(
            Release.organization_id == organization_id,
            Release.kind == kind,
            func.lower(Release.name) == name.lower(),
            func.lower(Release.version) == version.lower(),
        )
        return self.session.scalar(statement)

    def list_paginated(
        self,
        *,
        organization_id: uuid.UUID,
        kind: ReleaseKind | None = None,
        version: str | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Release], int]:
        statement = select(Release).where(Release.organization_id == organization_id)
        if kind is not None:
            statement = statement.where(Release.kind == kind)
        if version:
            statement = statement.where(func.lower(Release.version).like(f"%{version.lower()}%"))
        if search:
            pattern = f"%{search.lower()}%"
            statement = statement.where(func.lower(Release.name).like(pattern))

        total = int(self.session.scalar(select(func.count()).select_from(statement.subquery())) or 0)
        statement = (
            statement.order_by(Release.created_at.desc(), Release.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(self.session.scalars(statement).all()), total

    def create(self, release: Release) -> Release:
        self.session.add(release)
        self.session.flush()
        return release

    def delete(self, release: Release) -> None:
        self.session.delete(release)
        self.session.flush()
