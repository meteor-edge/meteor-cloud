"""Release catalog business rules."""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.artifacts.repository import ArtifactRepository
from app.audit.service import AuditRecorder
from app.authorization.service import AuthzService
from app.core.exceptions import ConflictError, NotFoundError
from app.devices.schemas import Page
from app.identity.models import User
from app.releases.kinds import (
    ReleaseKind,
    validate_artifact_for_kind,
    validate_release_spec,
)
from app.releases.models import Release
from app.releases.repository import ReleaseRepository
from app.releases.schemas import ReleaseCreate, ReleaseResponse, ReleaseUpdate
from app.tenancy.models import OrganizationMembership


class ReleaseService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.releases = ReleaseRepository(session)
        self.artifacts = ArtifactRepository(session)
        self.audit = AuditRecorder(session)
        self.authz = AuthzService(session)

    def _require_membership(self, organization_id: uuid.UUID, user_id: uuid.UUID) -> OrganizationMembership:
        return self.authz.require_membership(user_id=user_id, organization_id=organization_id)

    def _require_release(self, organization_id: uuid.UUID, release_id: uuid.UUID) -> Release:
        release = self.releases.get(organization_id=organization_id, release_id=release_id)
        if release is None:
            raise NotFoundError("release_not_found", "Release was not found.")
        return release

    def list_releases(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        kind: ReleaseKind | None = None,
        version: str | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[ReleaseResponse]:
        membership = self._require_membership(organization_id, actor.id)
        self.authz.require(membership, "artifact.read")
        items, total = self.releases.list_paginated(
            organization_id=organization_id,
            kind=kind,
            version=version,
            search=search,
            page=page,
            page_size=page_size,
        )
        return Page[ReleaseResponse](
            items=[ReleaseResponse.model_validate(item) for item in items],
            total=total,
            page=page,
            page_size=page_size,
        )

    def get_release(self, *, actor: User, organization_id: uuid.UUID, release_id: uuid.UUID) -> ReleaseResponse:
        membership = self._require_membership(organization_id, actor.id)
        self.authz.require(membership, "artifact.read")
        return ReleaseResponse.model_validate(self._require_release(organization_id, release_id))

    def create_release(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        payload: ReleaseCreate,
    ) -> ReleaseResponse:
        membership = self._require_membership(organization_id, actor.id)
        self.authz.require(membership, "artifact.create")

        normalized_spec = validate_release_spec(payload.kind, payload.spec)

        artifact = None
        artifact_type = None
        if payload.artifact_id is not None:
            artifact = self.artifacts.get(organization_id=organization_id, artifact_id=payload.artifact_id)
            if artifact is None:
                raise NotFoundError("artifact_not_found", "Artifact was not found.")
            artifact_type = artifact.type

        validate_artifact_for_kind(
            kind=payload.kind,
            artifact_type=artifact_type,
            artifact_id=payload.artifact_id,
            spec=normalized_spec,
        )

        if self.releases.find_duplicate(
            organization_id=organization_id,
            kind=payload.kind,
            name=payload.name,
            version=payload.version,
        ):
            raise ConflictError(
                "release_exists",
                "A release with this kind, name, and version already exists.",
            )

        release = Release(
            organization_id=organization_id,
            name=payload.name,
            version=payload.version,
            kind=payload.kind,
            artifact_id=payload.artifact_id,
            description=payload.description,
            spec=normalized_spec,
            created_by_user_id=actor.id,
        )
        try:
            self.releases.create(release)
            self.audit.record(
                actor=actor,
                organization_id=organization_id,
                action="release.create",
                resource_type="release",
                resource_id=release.id,
                metadata={
                    "name": release.name,
                    "version": release.version,
                    "kind": release.kind.value,
                },
            )
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise ConflictError(
                "release_exists",
                "A release with this kind, name, and version already exists.",
            ) from exc
        self.session.refresh(release)
        return ReleaseResponse.model_validate(release)

    def update_release(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        release_id: uuid.UUID,
        payload: ReleaseUpdate,
    ) -> ReleaseResponse:
        membership = self._require_membership(organization_id, actor.id)
        self.authz.require(membership, "artifact.update")
        release = self._require_release(organization_id, release_id)

        if payload.description is not None:
            release.description = payload.description
        if payload.spec is not None:
            normalized = validate_release_spec(release.kind, payload.spec)
            artifact = None
            artifact_type = None
            if release.artifact_id is not None:
                artifact = self.artifacts.get(organization_id=organization_id, artifact_id=release.artifact_id)
                if artifact is None:
                    raise NotFoundError("artifact_not_found", "Artifact was not found.")
                artifact_type = artifact.type
            validate_artifact_for_kind(
                kind=release.kind,
                artifact_type=artifact_type,
                artifact_id=release.artifact_id,
                spec=normalized,
            )
            release.spec = normalized

        self.audit.record(
            actor=actor,
            organization_id=organization_id,
            action="release.update",
            resource_type="release",
            resource_id=release.id,
            metadata={"name": release.name, "version": release.version},
        )
        self.session.commit()
        self.session.refresh(release)
        return ReleaseResponse.model_validate(release)

    def delete_release(self, *, actor: User, organization_id: uuid.UUID, release_id: uuid.UUID) -> None:
        membership = self._require_membership(organization_id, actor.id)
        self.authz.require(membership, "artifact.delete")
        release = self._require_release(organization_id, release_id)
        self.audit.record(
            actor=actor,
            organization_id=organization_id,
            action="release.delete",
            resource_type="release",
            resource_id=release.id,
            metadata={"name": release.name, "version": release.version, "kind": release.kind.value},
        )
        try:
            self.releases.delete(release)
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise ConflictError(
                "release_in_use",
                "This release cannot be deleted while deployments or software still reference it.",
            ) from exc
