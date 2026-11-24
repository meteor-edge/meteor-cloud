"""Identity HTTP routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.identity.dependencies import (
    AuthService,
    CurrentUser,
    enforce_login_rate_limit,
    require_signup_allowed,
)
from app.identity.schemas import (
    AuthConfigResponse,
    TokenResponse,
    UserLoginRequest,
    UserPublic,
    UserRegisterRequest,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.get("/config", response_model=AuthConfigResponse)
def auth_config(settings: Annotated[Settings, Depends(get_settings)]) -> AuthConfigResponse:
    """Public: what the sign-in page may offer (no secrets)."""
    return AuthConfigResponse(signup_enabled=settings.allow_signup)


@router.post(
    "/register",
    response_model=UserPublic,
    status_code=201,
    dependencies=[Depends(require_signup_allowed)],
)
def register_user(payload: UserRegisterRequest, service: AuthService) -> UserPublic:
    return service.register_user(payload)


@router.post("/login", response_model=TokenResponse, dependencies=[Depends(enforce_login_rate_limit)])
def login(payload: UserLoginRequest, service: AuthService) -> TokenResponse:
    return service.login(payload)


@router.get("/me", response_model=UserPublic)
def get_me(current_user: CurrentUser) -> UserPublic:
    return UserPublic.model_validate(current_user)


@router.post("/logout-all", status_code=204)
def logout_all(current_user: CurrentUser, service: AuthService) -> None:
    """Sign this user out of every session, this one included."""
    service.revoke_all_sessions(current_user)
