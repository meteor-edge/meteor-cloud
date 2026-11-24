"""Organization signing keys."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, LargeBinary, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin, UUIDPrimaryKeyMixin


class OrganizationSigningKey(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """An Ed25519 key pair. One active key per organization; retired keys stay listed.

    ``endorsement`` is the previous active key's signature over this key, so a device
    that trusts the old key can trust the new one after a rotation.
    """

    __tablename__ = "organization_signing_keys"
    __table_args__ = (
        Index(
            "uq_organization_signing_keys_active",
            "organization_id",
            unique=True,
            postgresql_where=text("retired_at IS NULL"),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    key_id: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    public_key: Mapped[str] = mapped_column(String(64), nullable=False)  # base64url, no padding
    sealed_private_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    endorsed_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    endorsement: Mapped[str | None] = mapped_column(String(128), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
