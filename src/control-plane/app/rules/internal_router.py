"""Internal routes for the data plane's rule engine and dispatcher. Not a user API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response

from app.applications.internal_router import _require_internal_token
from app.rules.dependencies import RuleSvc
from app.rules.schemas import AlertTransitionBatch, AlertTransitionResponse, RulesSnapshot

router = APIRouter(
    prefix="/internal/rules",
    tags=["internal-rules"],
    include_in_schema=False,
    dependencies=[Depends(_require_internal_token)],
)


@router.get("/snapshot", response_model=RulesSnapshot)
def rules_snapshot(
    service: RuleSvc,
    response: Response,
    if_none_match: Annotated[str | None, Header()] = None,
):
    """Enabled rules at their current version plus the device index. ``If-None-Match`` gives 304."""
    snapshot = service.snapshot()
    etag = f'"{snapshot.version}"'
    if if_none_match == etag:
        return Response(status_code=304, headers={"ETag": etag})
    response.headers["ETag"] = etag
    return snapshot


@router.post("/alert-transitions", response_model=AlertTransitionResponse)
def alert_transitions(service: RuleSvc, payload: AlertTransitionBatch):
    """Apply ``alert`` actions from the engine outbox. Keyed by idempotency key, so retries are safe."""
    return AlertTransitionResponse(results=service.apply_transitions(payload.items))
