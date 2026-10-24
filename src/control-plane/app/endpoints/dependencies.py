"""FastAPI dependencies for endpoint APIs."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.endpoints.service import EndpointService


def get_endpoint_service(
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> EndpointService:
    return EndpointService(session, settings=settings)


EndpointSvc = Annotated[EndpointService, Depends(get_endpoint_service)]
