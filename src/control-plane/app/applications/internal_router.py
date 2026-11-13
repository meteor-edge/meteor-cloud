"""Internal policy snapshot for the data plane. Not a user API."""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.applications.dependencies import ApplicationSvc
from app.applications.schemas import DeviceDirectory, DeviceDirectoryEntry, PolicySnapshot
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.exceptions import UnauthorizedError
from app.devices.models import Device

router = APIRouter(prefix="/internal/data-plane", tags=["internal-data-plane"], include_in_schema=False)


def _require_internal_token(
    settings: Annotated[Settings, Depends(get_settings)],
    x_data_plane_internal_token: Annotated[str | None, Header()] = None,
) -> None:
    expected = settings.data_plane_internal_token
    if not expected or not hmac.compare_digest(x_data_plane_internal_token or "", expected):
        raise UnauthorizedError("data_plane_internal_forbidden", "Invalid data-plane internal token.")


@router.get("/policy", response_model=PolicySnapshot)
def policy_snapshot(
    service: ApplicationSvc,
    response: Response,
    _: Annotated[None, Depends(_require_internal_token)],
    if_none_match: Annotated[str | None, Header()] = None,
):
    """Applications, permissions and credential hashes. ``If-None-Match`` on the version gives 304."""
    snapshot = service.policy_snapshot()
    etag = f'"{snapshot.version}"'
    if if_none_match == etag:
        return Response(status_code=304, headers={"ETag": etag})
    response.headers["ETag"] = etag
    return snapshot


@router.get("/devices", response_model=DeviceDirectory)
def device_directory(
    session: Annotated[Session, Depends(get_db)],
    response: Response,
    _: Annotated[None, Depends(_require_internal_token)],
    if_none_match: Annotated[str | None, Header()] = None,
):
    """Enrolled, enabled devices and their organization.

    Device ingestion uses it to bind the device ID in an MQTT topic to an
    organization without trusting the payload. ``If-None-Match`` on the version gives 304.
    """
    rows = session.execute(
        select(Device.id, Device.organization_id)
        .where(Device.is_enabled.is_(True), Device.credential_hash.is_not(None))
        .order_by(Device.id)
    ).all()
    digest = hashlib.sha256()
    for device_id, organization_id in rows:
        digest.update(f"{device_id}:{organization_id};".encode())
    version = digest.hexdigest()[:32]
    etag = f'"{version}"'
    if if_none_match == etag:
        return Response(status_code=304, headers={"ETag": etag})
    response.headers["ETag"] = etag
    return DeviceDirectory(
        version=version,
        generated_at=datetime.now(UTC),
        devices=[DeviceDirectoryEntry(id=device_id, organization_id=org_id) for device_id, org_id in rows],
    )
