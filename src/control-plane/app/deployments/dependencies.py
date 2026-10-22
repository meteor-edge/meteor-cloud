"""FastAPI dependencies for Deploy APIs."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.adapters import deployment_provider
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.deployments.service import DeploymentService


def get_deployment_service(
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DeploymentService:
    return DeploymentService(
        session,
        provider=deployment_provider(settings, session),
        provider_name=settings.deployment_provider,
        resolve_provider=False,
    )


DeploymentSvc = Annotated[DeploymentService, Depends(get_deployment_service)]
