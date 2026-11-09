"""Public OS image routes for Meteor Imager: list and download without login.

Only OS images an owner or admin published (``published``) are served. The imager asks
for the enrollment key, Wi-Fi and domain after the image is chosen, and writes them to
the card's boot partition; none of that is sent here.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.artifacts.dependencies import ArtifactSvc, enforce_public_os_image_rate_limit
from app.artifacts.router import stream_artifact
from app.artifacts.schemas import PublicOsImage

router = APIRouter(
    prefix="/api/v1/os-images",
    tags=["os-images"],
    dependencies=[Depends(enforce_public_os_image_rate_limit)],
)


@router.get("", response_model=list[PublicOsImage])
def list_os_images(service: ArtifactSvc) -> list[PublicOsImage]:
    """Published OS images, newest first."""
    return service.list_public_os_images()


@router.get("/{artifact_id}/download")
def download_os_image(artifact_id: uuid.UUID, service: ArtifactSvc) -> StreamingResponse:
    """Stream a published OS image with its size and SHA-256 headers."""
    artifact, stored = service.open_public_os_image(artifact_id)
    return stream_artifact(artifact, stored)
