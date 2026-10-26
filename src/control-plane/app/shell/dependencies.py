"""FastAPI dependencies for browser SSH."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.adapters import connectivity_provider, shell_provider
from app.connectivity.service import ConnectivityService
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.shell.service import ShellService


def get_shell_service(
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ShellService:
    connectivity = ConnectivityService(
        session,
        provider=connectivity_provider(settings),
        settings=settings,
    )
    return ShellService(
        session,
        connectivity=connectivity,
        provider=shell_provider(settings),
        settings=settings,
    )


ShellSvc = Annotated[ShellService, Depends(get_shell_service)]
