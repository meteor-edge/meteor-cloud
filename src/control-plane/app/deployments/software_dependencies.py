"""FastAPI dependencies for device software."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.deployments.software_service import DeviceSoftwareService


def get_device_software_service(
    session: Annotated[Session, Depends(get_db)],
) -> DeviceSoftwareService:
    return DeviceSoftwareService(session)


DeviceSoftwareSvc = Annotated[DeviceSoftwareService, Depends(get_device_software_service)]
