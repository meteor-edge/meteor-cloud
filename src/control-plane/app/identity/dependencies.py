"""FastAPI dependencies for authenticated users."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.exceptions import UnauthorizedError
from app.identity.models import User
from app.identity.service import AuthenticationService

bearer_scheme = HTTPBearer(auto_error=False)


def get_authentication_service(
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthenticationService:
    return AuthenticationService(session, settings=settings)


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    settings: Annotated[Settings, Depends(get_settings)],
    service: Annotated[AuthenticationService, Depends(get_authentication_service)],
) -> User:
    """Resolve the authenticated active user from a bearer token."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise UnauthorizedError("invalid_credentials", "Not authenticated.")

    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError as exc:
        raise UnauthorizedError(
            "invalid_credentials",
            "Could not validate credentials.",
        ) from exc

    subject = payload.get("sub")
    if subject is None:
        raise UnauthorizedError("invalid_credentials", "Could not validate credentials.")

    try:
        user_id = uuid.UUID(str(subject))
    except ValueError as exc:
        raise UnauthorizedError(
            "invalid_credentials",
            "Could not validate credentials.",
        ) from exc

    return service.require_active_user(user_id)


CurrentUser = Annotated[User, Depends(get_current_user)]
AuthService = Annotated[AuthenticationService, Depends(get_authentication_service)]
