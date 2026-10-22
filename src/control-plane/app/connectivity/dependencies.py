"""FastAPI dependencies for connectivity APIs."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.adapters import connectivity_provider
from app.connectivity.service import ConnectivityService
from app.core.config import Settings, get_settings
from app.core.database import get_db


def get_connectivity_service(
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ConnectivityService:
    return ConnectivityService(
        session,
        provider=connectivity_provider(settings),
        settings=settings,
    )


ConnectivitySvc = Annotated[ConnectivityService, Depends(get_connectivity_service)]
