"""Internal connectivity APIs for the host gateway agent.

Not a user API. The gateway must send ``X-Connectivity-Internal-Token``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header

from app.connectivity.dependencies import ConnectivitySvc
from app.connectivity.schemas import HandshakeReportRequest, InternalPeerList
from app.core.config import Settings, get_settings
from app.core.exceptions import UnauthorizedError

router = APIRouter(prefix="/internal/connectivity", tags=["internal-connectivity"], include_in_schema=False)


def _require_internal_token(
    settings: Annotated[Settings, Depends(get_settings)],
    x_connectivity_internal_token: Annotated[str | None, Header()] = None,
) -> None:
    expected = settings.connectivity_internal_token
    if not expected or x_connectivity_internal_token != expected:
        raise UnauthorizedError("connectivity_internal_forbidden", "Invalid connectivity internal token.")


@router.get("/peers", response_model=InternalPeerList)
def list_peers(
    service: ConnectivitySvc,
    _: Annotated[None, Depends(_require_internal_token)],
) -> InternalPeerList:
    return InternalPeerList(interface="wg0", peers=service.list_internal_peers())


@router.post("/handshakes")
def report_handshakes(
    payload: HandshakeReportRequest,
    service: ConnectivitySvc,
    _: Annotated[None, Depends(_require_internal_token)],
) -> dict[str, int]:
    updated = service.apply_handshakes(payload.items)
    return {"updated": updated}
