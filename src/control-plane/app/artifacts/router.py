"""Organization-scoped artifact HTTP routes."""

from __future__ import annotations

import json
import uuid
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Query, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from app.artifacts.dependencies import ArtifactSvc
from app.artifacts.models import Artifact, ArtifactType
from app.artifacts.schemas import ArtifactCreate, ArtifactDownloadLinkResponse, ArtifactResponse
from app.devices.schemas import Page
from app.identity.dependencies import CurrentUser
from app.ports.storage import StoredObject

router = APIRouter(prefix="/api/v1/organizations/{organization_id}/artifacts", tags=["artifacts"])


@router.get("", response_model=Page[ArtifactResponse])
def list_artifacts(
    organization_id: uuid.UUID,
    current_user: CurrentUser,
    service: ArtifactSvc,
    type: Annotated[ArtifactType | None, Query()] = None,
    device_type_id: Annotated[uuid.UUID | None, Query()] = None,
    version: Annotated[str | None, Query(max_length=64)] = None,
    search: Annotated[str | None, Query(max_length=120)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[ArtifactResponse]:
    """Return a filtered artifact page for an organization member."""
    return service.list_artifacts(
        actor=current_user,
        organization_id=organization_id,
        type=type,
        device_type_id=device_type_id,
        version=version,
        search=search,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=ArtifactResponse, status_code=201)
def upload_artifact(
    organization_id: uuid.UUID,
    current_user: CurrentUser,
    service: ArtifactSvc,
    file: Annotated[UploadFile, File()],
    name: Annotated[str, Form()],
    version: Annotated[str, Form()],
    type: Annotated[str, Form()] = ArtifactType.OS_IMAGE.value,
    device_type_id: Annotated[str | None, Form()] = None,
    description: Annotated[str | None, Form()] = None,
    metadata: Annotated[str | None, Form(description="JSON object")] = None,
) -> ArtifactResponse:
    """Validate multipart metadata and upload the file for an owner or admin.

    Metadata is a JSON object; an empty device type means no assignment.
    Raise RequestValidationError for invalid JSON or metadata fields;
    upload and authorization errors propagate from the service.
    """
    try:
        parsed_metadata = json.loads(metadata) if metadata else {}
        payload = ArtifactCreate(
            name=name,
            version=version,
            type=type,
            # HTML forms send "" for an unselected option.
            device_type_id=device_type_id or None,
            description=description,
            metadata=parsed_metadata,
        )
    except json.JSONDecodeError as exc:
        raise RequestValidationError(
            [{"loc": ("body", "metadata"), "msg": "Metadata must be a JSON object", "type": "json_invalid"}]
        ) from exc
    except ValidationError as exc:
        raise RequestValidationError(
            [{**error, "loc": ("body", *error["loc"])} for error in exc.errors(include_url=False)]
        ) from exc

    return service.upload_artifact(
        actor=current_user,
        organization_id=organization_id,
        payload=payload,
        data=file.file,
        file_name=file.filename,
        content_type=file.content_type,
        declared_size=file.size,
    )


@router.get("/{artifact_id}", response_model=ArtifactResponse)
def get_artifact(
    organization_id: uuid.UUID,
    artifact_id: uuid.UUID,
    current_user: CurrentUser,
    service: ArtifactSvc,
) -> ArtifactResponse:
    """Return artifact metadata after checking organization membership."""
    return service.get_artifact(actor=current_user, organization_id=organization_id, artifact_id=artifact_id)


@router.delete("/{artifact_id}", status_code=204)
def delete_artifact(
    organization_id: uuid.UUID,
    artifact_id: uuid.UUID,
    current_user: CurrentUser,
    service: ArtifactSvc,
) -> None:
    """Delete an artifact for an owner or admin, returning no response body."""
    service.delete_artifact(actor=current_user, organization_id=organization_id, artifact_id=artifact_id)


@router.get("/{artifact_id}/download")
def download_artifact(
    organization_id: uuid.UUID,
    artifact_id: uuid.UUID,
    current_user: CurrentUser,
    service: ArtifactSvc,
) -> StreamingResponse:
    """Stream an attachment after checking the user's organization membership."""
    artifact, stored = service.open_download(
        actor=current_user, organization_id=organization_id, artifact_id=artifact_id
    )
    return _stream(artifact, stored)


@router.post("/{artifact_id}/download-link", response_model=ArtifactDownloadLinkResponse)
def create_download_link(
    organization_id: uuid.UUID,
    artifact_id: uuid.UUID,
    current_user: CurrentUser,
    service: ArtifactSvc,
) -> ArtifactDownloadLinkResponse:
    """Issue a temporary download URL bound to the current user and artifact."""
    return service.create_download_link(actor=current_user, organization_id=organization_id, artifact_id=artifact_id)


@router.get("/{artifact_id}/download/{ticket}")
def download_artifact_with_link(
    organization_id: uuid.UUID,
    artifact_id: uuid.UUID,
    ticket: str,
    service: ArtifactSvc,
) -> StreamingResponse:
    """Download via a short-lived link so browsers can stream without a bearer header."""
    artifact, stored = service.open_download_with_ticket(
        organization_id=organization_id, artifact_id=artifact_id, ticket=ticket
    )
    return _stream(artifact, stored)


def _stream(artifact: Artifact, stored: StoredObject) -> StreamingResponse:
    """Return an attachment stream with byte length and SHA-256 headers.

    Use the artifact's content type before the stored type, disable caching,
    and pass through errors raised while consuming the content iterator.
    """
    return StreamingResponse(
        stored.chunks,
        media_type=artifact.content_type or stored.content_type or "application/octet-stream",
        headers={
            "Content-Length": str(stored.size),
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(artifact.file_name)}",
            "X-Checksum-SHA256": artifact.checksum_sha256,
            "Cache-Control": "no-store",
        },
    )
