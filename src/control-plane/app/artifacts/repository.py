"""Organization-scoped artifact persistence (PostgreSQL adapter)."""

from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.artifacts.models import Artifact, ArtifactType


class ArtifactRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, *, organization_id: uuid.UUID, artifact_id: uuid.UUID) -> Artifact | None:
        """Return an artifact in the organization, or None if it is absent."""
        statement = select(Artifact).where(
            Artifact.id == artifact_id,
            Artifact.organization_id == organization_id,
        )
        return self.session.scalar(statement)

    def find_duplicate(
        self,
        *,
        organization_id: uuid.UUID,
        device_type_id: uuid.UUID | None,
        type: ArtifactType,
        name: str,
        version: str,
    ) -> Artifact | None:
        """Find an artifact with matching type, device type, name, and version.

        Name and version comparisons ignore case. A None device type matches
        only organization-wide artifacts, rather than all device types.
        """
        type_condition = (
            Artifact.device_type_id.is_(None) if device_type_id is None else Artifact.device_type_id == device_type_id
        )
        statement = select(Artifact).where(
            Artifact.organization_id == organization_id,
            type_condition,
            Artifact.type == type,
            func.lower(Artifact.name) == name.lower(),
            func.lower(Artifact.version) == version.lower(),
        )
        return self.session.scalar(statement)

    def list_paginated(
        self,
        *,
        organization_id: uuid.UUID,
        type: ArtifactType | None = None,
        device_type_id: uuid.UUID | None = None,
        version: str | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Artifact], int]:
        """Return a page of artifacts and the total number of matches.

        Pages are one-based and ordered newest first, then by ID. Type and
        device type filters are exact; None omits either filter. Version and
        name/file-name search use case-insensitive SQL LIKE patterns, with
        surrounding wildcards; supplied percent and underscore remain wildcards.
        """
        statement = select(Artifact).where(Artifact.organization_id == organization_id)
        if type is not None:
            statement = statement.where(Artifact.type == type)
        if device_type_id is not None:
            statement = statement.where(Artifact.device_type_id == device_type_id)
        if version:
            statement = statement.where(func.lower(Artifact.version).like(f"%{version.lower()}%"))
        if search:
            pattern = f"%{search.lower()}%"
            statement = statement.where(
                or_(
                    func.lower(Artifact.name).like(pattern),
                    func.lower(Artifact.file_name).like(pattern),
                )
            )

        total = int(self.session.scalar(select(func.count()).select_from(statement.subquery())) or 0)
        statement = (
            statement.order_by(Artifact.created_at.desc(), Artifact.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(self.session.scalars(statement).all()), total

    def count_by_device_type(self, *, organization_id: uuid.UUID) -> dict[uuid.UUID, int]:
        """Count artifacts per assigned device type, omitting types with no artifacts."""
        statement = (
            select(Artifact.device_type_id, func.count())
            .where(Artifact.organization_id == organization_id, Artifact.device_type_id.is_not(None))
            .group_by(Artifact.device_type_id)
        )
        return {type_id: int(count) for type_id, count in self.session.execute(statement).all()}

    def create(self, artifact: Artifact) -> Artifact:
        """Add and flush metadata without committing or writing object storage.

        Database errors, including uniqueness violations, propagate.
        """
        self.session.add(artifact)
        self.session.flush()
        return artifact

    def delete(self, artifact: Artifact) -> None:
        """Delete and flush metadata without committing or deleting stored content.

        Database errors propagate.
        """
        self.session.delete(artifact)
        self.session.flush()
