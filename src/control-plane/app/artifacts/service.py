"""Artifact business rules: upload, list, download, delete.

The database row is written only after the content is fully stored, so a
listed artifact always has content. If the row cannot be committed, the stored
object is removed again.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import BinaryIO

from jose import JWTError, jwt
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.artifacts.models import Artifact, ArtifactType
from app.artifacts.repository import ArtifactRepository
from app.artifacts.schemas import ArtifactCreate, ArtifactDownloadLinkResponse, ArtifactResponse
from app.audit.service import AuditRecorder
from app.core.config import Settings, get_settings
from app.core.exceptions import AppError, ConflictError, NotFoundError, UnauthorizedError
from app.authorization.service import AuthzService
from app.devices.repository import DeviceTypeRepository
from app.devices.schemas import Page
from app.identity.models import User
from app.ports.storage import ObjectStorage, StoredObject, StoredObjectNotFoundError
from app.tenancy.models import OrganizationMembership
from app.tenancy.repository import OrganizationRepository

logger = logging.getLogger(__name__)

DOWNLOAD_TICKET_TYPE = "artifact_download"
_UNSAFE_FILE_NAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


class ArtifactTooLargeError(AppError):
    def __init__(self, max_bytes: int) -> None:
        """Report an exceeded upload limit in bytes with HTTP status 413."""
        super().__init__(
            "artifact_too_large",
            f"The file exceeds the maximum artifact size of {max_bytes} bytes.",
            status_code=413,
        )


class _HashingReader:
    """File-like wrapper that hashes and counts bytes as storage reads them."""

    def __init__(self, source: BinaryIO, max_bytes: int) -> None:
        """Wrap the source at its current position with an inclusive byte limit."""
        self._source = source
        self._max_bytes = max_bytes
        self._digest = hashlib.sha256()
        self.size = 0

    def read(self, size: int = -1) -> bytes:
        """Read and hash bytes, raising ArtifactTooLargeError above the byte limit.

        Size counts all bytes read, including the chunk that exceeds the limit;
        that chunk is not hashed or returned. Source read errors propagate.
        """
        chunk = self._source.read(size)
        self.size += len(chunk)
        if self.size > self._max_bytes:
            raise ArtifactTooLargeError(self._max_bytes)
        self._digest.update(chunk)
        return chunk

    def hexdigest(self) -> str:
        """Return the SHA-256 hex digest of bytes accepted by read so far."""
        return self._digest.hexdigest()


def safe_file_name(file_name: str | None) -> str:
    """Reduce a client-supplied file name to a safe object-key segment."""
    base = (file_name or "").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _UNSAFE_FILE_NAME_CHARS.sub("-", base).strip(".-")
    return cleaned[:200] or "artifact.bin"


def build_storage_key(organization_id: uuid.UUID, artifact_id: uuid.UUID, file_name: str) -> str:
    # IDs, not slugs, so renaming an organization or device type never orphans content.
    """Build an organization/artifact ID path using an already sanitized file name."""
    return f"organizations/{organization_id}/artifacts/{artifact_id}/{file_name}"


class ArtifactService:
    def __init__(self, session: Session, storage: ObjectStorage, settings: Settings | None = None) -> None:
        self.session = session
        self.storage = storage
        self.settings = settings or get_settings()
        self.organizations = OrganizationRepository(session)
        self.artifacts = ArtifactRepository(session)
        self.device_types = DeviceTypeRepository(session)
        self.audit = AuditRecorder(session)
        self.authz = AuthzService(session)

    # ---------------------------------------------------------------- helpers
    def _require_membership(self, organization_id: uuid.UUID, user_id: uuid.UUID) -> OrganizationMembership:
        """Return membership or raise NotFoundError for a missing organization or membership."""
        result = self.organizations.get_for_user(organization_id=organization_id, user_id=user_id)
        if result is None:
            raise NotFoundError("organization_not_found", "Organization was not found.")
        return result[1]

    def _require_artifact(self, organization_id: uuid.UUID, artifact_id: uuid.UUID) -> Artifact:
        """Return an artifact in the organization or raise NotFoundError."""
        artifact = self.artifacts.get(organization_id=organization_id, artifact_id=artifact_id)
        if artifact is None:
            raise NotFoundError("artifact_not_found", "Artifact was not found.")
        return artifact

    # ------------------------------------------------------------------ reads
    def list_artifacts(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        type: ArtifactType | None = None,
        device_type_id: uuid.UUID | None = None,
        version: str | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Page[ArtifactResponse]:
        """Return a filtered metadata page after checking organization membership.

        Page numbers are one-based. Version and name/file-name search use
        case-insensitive SQL LIKE matching. Raise NotFoundError if the actor
        cannot access the organization; database errors propagate.
        """
        membership = self._require_membership(organization_id, actor.id)
        self.authz.require(membership, "artifact.read")
        items, total = self.artifacts.list_paginated(
            organization_id=organization_id,
            type=type,
            device_type_id=device_type_id,
            version=version,
            search=search,
            page=page,
            page_size=page_size,
        )
        return Page[ArtifactResponse](
            items=[ArtifactResponse.model_validate(item) for item in items],
            total=total,
            page=page,
            page_size=page_size,
        )

    def get_artifact(self, *, actor: User, organization_id: uuid.UUID, artifact_id: uuid.UUID) -> ArtifactResponse:
        """Return metadata for an organization member.

        Raise NotFoundError if membership or the artifact is absent;
        database and response validation errors propagate.
        """
        membership = self._require_membership(organization_id, actor.id)
        self.authz.require(membership, "artifact.read")
        return ArtifactResponse.model_validate(self._require_artifact(organization_id, artifact_id))

    # ----------------------------------------------------------------- upload
    def upload_artifact(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        payload: ArtifactCreate,
        data: BinaryIO,
        file_name: str | None,
        content_type: str | None,
        declared_size: int | None = None,
    ) -> ArtifactResponse:
        """Store content, commit metadata and an audit event, and return metadata.

        Read data from its current position while computing byte size and
        SHA-256. declared_size is only an early size check; the configured
        inclusive byte limit is also enforced during reads. Sanitize file_name.

        Raise NotFoundError for missing membership or a foreign/missing device
        type, ForbiddenError for a non-manager, ArtifactTooLargeError above the
        limit, ConflictError for a duplicate or database integrity violation,
        and AppError for empty content (422) or storage/read failure (503).
        Other errors creating metadata or committing propagate after rollback
        and attempted object cleanup. Errors refreshing or validating committed
        metadata also propagate. Cleanup failures are suppressed, so objects may
        remain.
        """
        membership = self._require_membership(organization_id, actor.id)
        self.authz.require(membership, "artifact.create")

        max_bytes = self.settings.artifact_max_upload_bytes
        if declared_size is not None and declared_size > max_bytes:
            raise ArtifactTooLargeError(max_bytes)
        if payload.device_type_id is not None and (
            self.device_types.get(organization_id=organization_id, type_id=payload.device_type_id) is None
        ):
            raise NotFoundError("device_type_not_found", "Device type was not found.")
        if self.artifacts.find_duplicate(
            organization_id=organization_id,
            device_type_id=payload.device_type_id,
            type=payload.type,
            name=payload.name,
            version=payload.version,
        ):
            raise ConflictError(
                "artifact_exists",
                "An artifact with this name, type, and version already exists.",
            )

        artifact_id = uuid.uuid4()
        stored_name = safe_file_name(file_name)
        key = build_storage_key(organization_id, artifact_id, stored_name)
        reader = _HashingReader(data, max_bytes)
        try:
            self.storage.put(key, reader, content_type=content_type)  # type: ignore[arg-type]
        except ArtifactTooLargeError:
            self._discard_object(key)
            raise
        except Exception as exc:
            self._discard_object(key)
            if reader.size > max_bytes:
                raise ArtifactTooLargeError(max_bytes) from exc
            logger.exception("Artifact upload to object storage failed", extra={"storage_key": key})
            raise AppError(
                "storage_unavailable",
                "The artifact could not be stored. Please try again later.",
                status_code=503,
            ) from exc

        if reader.size == 0:
            self._discard_object(key)
            raise AppError("artifact_empty", "The uploaded file is empty.", status_code=422)

        artifact = Artifact(
            id=artifact_id,
            organization_id=organization_id,
            device_type_id=payload.device_type_id,
            name=payload.name,
            version=payload.version,
            type=payload.type,
            description=payload.description,
            file_name=stored_name,
            content_type=content_type,
            storage_key=key,
            size_bytes=reader.size,
            checksum_sha256=reader.hexdigest(),
            metadata_=payload.metadata,
            created_by_user_id=actor.id,
        )
        try:
            self.artifacts.create(artifact)
            self.audit.record(
                actor=actor,
                organization_id=organization_id,
                action="artifact.upload",
                resource_type="artifact",
                resource_id=artifact.id,
                metadata={"name": artifact.name, "version": artifact.version, "type": artifact.type.value},
            )
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            self._discard_object(key)
            raise ConflictError(
                "artifact_exists",
                "An artifact with this name, type, and version already exists.",
            ) from exc
        except Exception:
            self.session.rollback()
            self._discard_object(key)
            raise
        self.session.refresh(artifact)
        return ArtifactResponse.model_validate(artifact)

    # ----------------------------------------------------------------- delete
    def delete_artifact(self, *, actor: User, organization_id: uuid.UUID, artifact_id: uuid.UUID) -> None:
        """Commit metadata deletion and an audit event, then attempt object deletion.

        Raise NotFoundError for missing membership or artifact and ForbiddenError
        for a non-manager. Database errors propagate; object deletion errors
        are suppressed and may leave orphaned content.
        """
        membership = self._require_membership(organization_id, actor.id)
        self.authz.require(membership, "artifact.delete")
        artifact = self._require_artifact(organization_id, artifact_id)
        key = artifact.storage_key
        self.audit.record(
            actor=actor,
            organization_id=organization_id,
            action="artifact.delete",
            resource_type="artifact",
            resource_id=artifact.id,
            metadata={"name": artifact.name, "version": artifact.version},
        )
        self.artifacts.delete(artifact)
        self.session.commit()
        # Metadata is gone first so the artifact can never be listed without content.
        self._discard_object(key)

    def _discard_object(self, key: str) -> None:
        """Attempt object deletion, suppressing storage errors that may leave an orphan."""
        try:
            self.storage.delete(key)
        except Exception:
            logger.warning("Could not delete artifact object; it is orphaned", extra={"storage_key": key})

    # --------------------------------------------------------------- download
    def open_download(
        self, *, actor: User, organization_id: uuid.UUID, artifact_id: uuid.UUID
    ) -> tuple[Artifact, StoredObject]:
        """Return metadata and an open content stream for an organization member.

        Raise NotFoundError for missing membership, artifact, or stored content,
        and AppError (503) for other errors opening storage. Database errors and
        later stream-read errors propagate.
        """
        membership = self._require_membership(organization_id, actor.id)
        self.authz.require(membership, "artifact.read")
        artifact = self._require_artifact(organization_id, artifact_id)
        return artifact, self._open(artifact)

    def create_download_link(
        self, *, actor: User, organization_id: uuid.UUID, artifact_id: uuid.UUID
    ) -> ArtifactDownloadLinkResponse:
        """Return a relative download URL and its UTC expiry for a member.

        The ticket is bound to the actor, organization, and artifact; its lifetime
        is configured in seconds. This does not check stored content. Raise
        NotFoundError for absent membership or artifact; database and signing
        errors propagate.
        """
        membership = self._require_membership(organization_id, actor.id)
        self.authz.require(membership, "artifact.read")
        artifact = self._require_artifact(organization_id, artifact_id)
        expires_at = datetime.now(UTC) + timedelta(seconds=self.settings.artifact_download_link_ttl_seconds)
        # No "sub" claim: the ticket must never be accepted as a user access token.
        ticket = jwt.encode(
            {
                "typ": DOWNLOAD_TICKET_TYPE,
                "uid": str(actor.id),
                "oid": str(organization_id),
                "aid": str(artifact.id),
                "exp": expires_at,
            },
            self.settings.jwt_secret_key,
            algorithm=self.settings.jwt_algorithm,
        )
        return ArtifactDownloadLinkResponse(
            url=f"/api/v1/organizations/{organization_id}/artifacts/{artifact.id}/download",
            ticket=ticket,
            expires_at=expires_at,
        )

    def open_download_with_ticket(
        self, *, organization_id: uuid.UUID, artifact_id: uuid.UUID, ticket: str
    ) -> tuple[Artifact, StoredObject]:
        """Validate a download ticket and return metadata and an open content stream.

        Recheck the ticket user's membership. Raise UnauthorizedError for an
        invalid, expired, mismatched ticket or removed membership, NotFoundError
        for missing metadata/content, and AppError (503) for other storage-open
        failures. Database and later stream-read errors propagate.
        """
        invalid = UnauthorizedError("invalid_download_link", "The download link is invalid or has expired.")
        try:
            claims = jwt.decode(ticket, self.settings.jwt_secret_key, algorithms=[self.settings.jwt_algorithm])
            user_id = uuid.UUID(str(claims["uid"]))
        except (JWTError, KeyError, ValueError) as exc:
            raise invalid from exc
        if (
            claims.get("typ") != DOWNLOAD_TICKET_TYPE
            or claims.get("oid") != str(organization_id)
            or claims.get("aid") != str(artifact_id)
        ):
            raise invalid
        # Re-check membership so a removed member cannot use a link issued earlier.
        if self.organizations.get_for_user(organization_id=organization_id, user_id=user_id) is None:
            raise invalid
        artifact = self._require_artifact(organization_id, artifact_id)
        return artifact, self._open(artifact)

    def _open(self, artifact: Artifact) -> StoredObject:
        """Open content, mapping missing objects to NotFoundError and other failures to AppError (503).

        Errors raised later by the chunk iterator are not converted.
        """
        try:
            return self.storage.get(artifact.storage_key)
        except StoredObjectNotFoundError as exc:
            raise NotFoundError(
                "artifact_content_missing",
                "The artifact content is missing from storage.",
            ) from exc
        except Exception as exc:
            logger.exception("Artifact download from object storage failed")
            raise AppError(
                "storage_unavailable",
                "The artifact could not be read. Please try again later.",
                status_code=503,
            ) from exc
