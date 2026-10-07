"""FastAPI dependencies for the artifact API."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.adapters import object_storage as build_object_storage
from app.artifacts.service import ArtifactService
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.ports.storage import ObjectStorage


@lru_cache
def _build_object_storage() -> ObjectStorage:
    return build_object_storage(get_settings())


def get_object_storage() -> ObjectStorage:
    return _build_object_storage()


def get_artifact_service(
    session: Annotated[Session, Depends(get_db)],
    storage: Annotated[ObjectStorage, Depends(get_object_storage)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ArtifactService:
    return ArtifactService(session, storage, settings=settings)


ArtifactSvc = Annotated[ArtifactService, Depends(get_artifact_service)]
