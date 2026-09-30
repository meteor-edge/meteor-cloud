"""User persistence helpers."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.identity.models import User
from app.identity.schemas import normalize_email


class UserRepository:
    """Explicit database access for users."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_id(self, user_id: uuid.UUID) -> User | None:
        return self.session.get(User, user_id)

    def get_by_email(self, email: str) -> User | None:
        normalized = normalize_email(email)
        statement = select(User).where(User.email == normalized)
        return self.session.scalar(statement)

    def create(
        self,
        *,
        email: str,
        full_name: str,
        password_hash: str,
        is_active: bool = True,
        is_superuser: bool = False,
    ) -> User:
        user = User(
            email=normalize_email(email),
            full_name=full_name,
            password_hash=password_hash,
            is_active=is_active,
            is_superuser=is_superuser,
        )
        self.session.add(user)
        self.session.flush()
        return user
