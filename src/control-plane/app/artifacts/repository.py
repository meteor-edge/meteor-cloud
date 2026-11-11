"""Organization-scoped artifact persistence (PostgreSQL adapter)."""

from __future__ import annotations

import uuid
from collections.abc import Collection

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.artifacts.models import Artifact, ArtifactType, artifact_device_types


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

    def list_published_os_images(self) -> list[Artifact]:
        """Published OS images of every organization, newest first."""
        statement = (
            select(Artifact)
            .where(Artifact.published.is_(True), Artifact.type == ArtifactType.OS_IMAGE)
            .order_by(Artifact.created_at.desc(), Artifact.id.asc())
        )
        return list(self.session.scalars(statement).all())

    def get_published_os_image(self, artifact_id: uuid.UUID) -> Artifact | None:
        """A published OS image by ID, or None (also for unpublished or other types)."""
        statement = select(Artifact).where(
            Artifact.id == artifact_id,
            Artifact.published.is_(True),
            Artifact.type == ArtifactType.OS_IMAGE,
        )
        return self.session.scalar(statement)

    def lock_names(self, *, organization_id: uuid.UUID) -> None:
        """Serialize artifact creation and device type changes in one organization until commit."""
        self.session.execute(
            select(func.pg_advisory_xact_lock(func.hashtextextended(f"artifacts:{organization_id}", 0)))
        )

    def find_duplicate(
        self,
        *,
        organization_id: uuid.UUID,
        device_type_ids: Collection[uuid.UUID],
        type: ArtifactType,
        name: str,
        version: str,
        exclude_id: uuid.UUID | None = None,
    ) -> Artifact | None:
        """Find an artifact with the same type, name and version for one of these device types.

        Name and version comparisons ignore case. No device types matches only
        other artifacts without device types, rather than all of them.
        """
        has_types = select(artifact_device_types.c.artifact_id).where(
            artifact_device_types.c.artifact_id == Artifact.id
        )
        if device_type_ids:
            overlap = has_types.where(artifact_device_types.c.device_type_id.in_(device_type_ids)).exists()
        else:
            overlap = ~has_types.exists()
        statement = select(Artifact).where(
            Artifact.organization_id == organization_id,
            overlap,
            Artifact.type == type,
            func.lower(Artifact.name) == name.lower(),
            func.lower(Artifact.version) == version.lower(),
        )
        if exclude_id is not None:
            statement = statement.where(Artifact.id != exclude_id)
        return self.session.scalars(statement.limit(1)).first()

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
        device type filters are exact (an artifact matches each of its device types); None omits either filter. Version and
        name/file-name search use case-insensitive SQL LIKE patterns, with
        surrounding wildcards; supplied percent and underscore remain wildcards.
        """
        statement = select(Artifact).where(Artifact.organization_id == organization_id)
        if type is not None:
            statement = statement.where(Artifact.type == type)
        if device_type_id is not None:
            statement = statement.where(
                select(artifact_device_types.c.artifact_id)
                .where(
                    artifact_device_types.c.artifact_id == Artifact.id,
                    artifact_device_types.c.device_type_id == device_type_id,
                )
                .exists()
            )
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
        """Count artifacts per device type they are for, omitting types with no artifacts."""
        statement = (
            select(artifact_device_types.c.device_type_id, func.count())
            .join(Artifact, Artifact.id == artifact_device_types.c.artifact_id)
            .where(Artifact.organization_id == organization_id)
            .group_by(artifact_device_types.c.device_type_id)
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
