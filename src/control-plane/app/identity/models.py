"""Identity domain models."""

from __future__ import annotations

from sqlalchemy import Boolean, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Registered platform user."""

    __tablename__ = "users"
    # As created by migration 0002: a unique constraint plus a plain index.
    __table_args__ = (
        UniqueConstraint("email", name="users_email_key"),
        Index("ix_users_email", "email"),
    )

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    is_superuser: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
