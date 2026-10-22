"""FastAPI dependencies for the release API."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.releases.service import ReleaseService


def get_release_service(session: Annotated[Session, Depends(get_db)]) -> ReleaseService:
    return ReleaseService(session)


ReleaseSvc = Annotated[ReleaseService, Depends(get_release_service)]
