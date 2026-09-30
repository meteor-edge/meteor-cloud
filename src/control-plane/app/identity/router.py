"""Identity HTTP routes."""

from __future__ import annotations

from fastapi import APIRouter

from app.identity.dependencies import AuthService, CurrentUser
from app.identity.schemas import (
    TokenResponse,
    UserLoginRequest,
    UserPublic,
    UserRegisterRequest,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/register", response_model=UserPublic, status_code=201)
def register_user(payload: UserRegisterRequest, service: AuthService) -> UserPublic:
    return service.register_user(payload)


@router.post("/login", response_model=TokenResponse)
def login(payload: UserLoginRequest, service: AuthService) -> TokenResponse:
    return service.login(payload)


@router.get("/me", response_model=UserPublic)
def get_me(current_user: CurrentUser) -> UserPublic:
    return UserPublic.model_validate(current_user)
