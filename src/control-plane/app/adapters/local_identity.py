"""Local identity adapter: accounts in the PostgreSQL ``users`` table."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.identity.models import User
from app.identity.repository import UserRepository
from app.ports.identity import IdentityAccount, IdentityAccountInactiveError


def _to_account(user: User) -> IdentityAccount:
    return IdentityAccount(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
    )


class LocalIdentityDirectory:
    def __init__(self, session: Session) -> None:
        self.users = UserRepository(session)

    def get_by_id(self, account_id: uuid.UUID) -> IdentityAccount | None:
        user = self.users.get_by_id(account_id)
        return _to_account(user) if user else None

    def get_by_email(self, email: str) -> IdentityAccount | None:
        user = self.users.get_by_email(email)
        return _to_account(user) if user else None

    def ensure_account(self, *, email: str, full_name: str, password: str) -> IdentityAccount:
        user = self.users.get_by_email(email)
        if user is None:
            user = self.users.create(
                email=email,
                full_name=full_name,
                password_hash=hash_password(password),
            )
        elif not user.is_active:
            raise IdentityAccountInactiveError(email)
        return _to_account(user)
