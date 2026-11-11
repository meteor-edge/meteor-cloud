"""FastAPI dependencies for application APIs."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.adapters.data_plane_api import DataPlaneHttpApi
from app.applications.service import ApplicationService
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.ports.data_plane import DataPlaneApi


def get_data_plane_api(settings: Annotated[Settings, Depends(get_settings)]) -> DataPlaneApi | None:
    if not settings.data_plane_api_url:
        return None
    return DataPlaneHttpApi(settings)


def get_application_service(
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    data_plane: Annotated[DataPlaneApi | None, Depends(get_data_plane_api)],
) -> ApplicationService:
    return ApplicationService(session, settings=settings, data_plane=data_plane)


ApplicationSvc = Annotated[ApplicationService, Depends(get_application_service)]
