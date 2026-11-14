"""Internal policy snapshot for the data plane. Not a user API."""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response

from app.applications.dependencies import ApplicationSvc
from app.applications.schemas import PolicySnapshot
from app.core.config import Settings, get_settings
from app.core.exceptions import UnauthorizedError

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
