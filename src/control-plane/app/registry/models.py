"""Registry signing keys, organization namespaces and push/pull tokens."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin


class RegistrySigningKey(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A P-256 key that signs registry access tokens. The newest unretired key signs."""

    __tablename__ = "registry_signing_keys"
    __table_args__ = (UniqueConstraint("key_id", name="uq_registry_signing_keys_key_id"),)

    key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    public_jwk: Mapped[str] = mapped_column(Text, nullable=False)
    sealed_private_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RegistryNamespace(Base, TimestampMixin):
    """First path component of an organization's images.

    Set on first use and never changed, so renaming the organization does not move its
    images. The row outlives the organization (``organization_id`` becomes NULL): a later
    organization with the same slug must not be able to read the old one's images.
    """

    __tablename__ = "registry_namespaces"
    __table_args__ = (UniqueConstraint("organization_id", name="uq_registry_namespaces_organization_id"),)

    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True
    )


class RegistryToken(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A password for ``docker login``: personal (acts as its user) or robot (CI, owned by the organization).

    Only the SHA-256 lookup hash is stored; the plaintext is returned once.
    """

    __tablename__ = "registry_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_registry_tokens_token_hash"),
        CheckConstraint("(kind = 'personal') = (user_id IS NOT NULL)", name="ck_registry_tokens_kind_user"),
        Index("ix_registry_tokens_organization_id", "organization_id"),
        Index("ix_registry_tokens_user_id", "user_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # personal | robot
    access: Mapped[str] = mapped_column(String(16), nullable=False)  # pull | push (push includes pull)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    # Robots only: limit to repositories under this path, e.g. "ci" allows ci and ci/*.
    repository_prefix: Mapped[str | None] = mapped_column(String(200), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class ContainerImage(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A tag in the registry. The registry is the source of truth; this copy is for listing."""

    __tablename__ = "container_images"
    __table_args__ = (
        UniqueConstraint("organization_id", "repository", "tag", name="uq_container_images_repository_tag"),
        Index("ix_container_images_organization_id_digest", "organization_id", "digest"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    # Without the organization's namespace.
    repository: Mapped[str] = mapped_column(String(255), nullable=False)
    tag: Mapped[str] = mapped_column(String(128), nullable=False)
    digest: Mapped[str] = mapped_column(String(71), nullable=False)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # [{os, architecture, variant, size_bytes}]
    platforms: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    pushed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    pushed_by_token_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("registry_tokens.id", ondelete="SET NULL"), nullable=True
    )
