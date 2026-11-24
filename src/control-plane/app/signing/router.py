"""Organization signing keys: list and rotate."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.identity.dependencies import CurrentUser
from app.signing.schemas import SigningKeyResponse
from app.signing.service import SigningKeyAdminService

router = APIRouter(prefix="/api/v1/organizations/{organization_id}/signing-keys", tags=["signing"])


def get_signing_admin(session: Annotated[Session, Depends(get_db)]) -> SigningKeyAdminService:
    return SigningKeyAdminService(session)


SigningAdmin = Annotated[SigningKeyAdminService, Depends(get_signing_admin)]


@router.get("", response_model=list[SigningKeyResponse])
def list_signing_keys(organization_id: uuid.UUID, user: CurrentUser, service: SigningAdmin) -> list[SigningKeyResponse]:
    """The active key and retired keys. Devices pin these to verify desired software."""
    return service.list_keys(actor=user, organization_id=organization_id)


@router.post("/rotate", response_model=SigningKeyResponse, status_code=201)
def rotate_signing_key(organization_id: uuid.UUID, user: CurrentUser, service: SigningAdmin) -> SigningKeyResponse:
    """Retire the active key and create a new one, endorsed by the old key so devices follow."""
    return service.rotate(actor=user, organization_id=organization_id)
