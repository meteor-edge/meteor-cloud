"""Sign desired-software manifests with the organization's Ed25519 key.

A device pins its organization's public keys the first time it fetches them, then
refuses desired software whose signature does not verify. Signatures cover the device
id, the release, the revision, the spec and the artifact's SHA-256, so a compromised
artifact store or a replay to another device is rejected. The agent also refuses a
revision older than the one it already applied. The format is shared with
``edge_agent.signing`` on the device; change both together.
"""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import AuditRecorder
from app.authorization.service import AuthzService
from app.core.config import Settings, get_settings
from app.core.secretbox import seal, unseal
from app.identity.models import User
from app.signing.models import OrganizationSigningKey
from app.signing.schemas import SigningKeyResponse

DESIRED_CONTEXT = b"meteorcloud-desired-v1\n"
ENDORSEMENT_CONTEXT = b"meteorcloud-key-endorsement-v1\n"


def canonical_json(value: Any) -> bytes:
    """The exact bytes that are signed: sorted keys, no spaces, UTF-8."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def key_id_for(public_raw: bytes) -> str:
    return hashlib.sha256(public_raw).hexdigest()[:16]


def endorsement_payload(key_id: str, public_key: str) -> bytes:
    return ENDORSEMENT_CONTEXT + canonical_json({"key_id": key_id, "public_key": public_key})


class SigningService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()

    # --- keys -------------------------------------------------------------

    def _context(self, organization_id: uuid.UUID, key_id: str) -> str:
        return f"organization_signing_keys:{organization_id}:{key_id}"

    def _private_key(self, row: OrganizationSigningKey) -> Ed25519PrivateKey:
        raw = base64.b64decode(
            unseal(self.settings, row.sealed_private_key, context=self._context(row.organization_id, row.key_id))
        )
        return Ed25519PrivateKey.from_private_bytes(raw)

    def _new_key(self, organization_id: uuid.UUID) -> tuple[OrganizationSigningKey, Ed25519PrivateKey]:
        private = Ed25519PrivateKey.generate()
        public_raw = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        private_raw = private.private_bytes(
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
        )
        key_id = key_id_for(public_raw)
        row = OrganizationSigningKey(
            organization_id=organization_id,
            key_id=key_id,
            public_key=b64url(public_raw),
            sealed_private_key=seal(
                self.settings,
                base64.b64encode(private_raw).decode(),
                context=self._context(organization_id, key_id),
            ),
        )
        return row, private

    def _active(self, organization_id: uuid.UUID) -> OrganizationSigningKey | None:
        return self.session.scalars(
            select(OrganizationSigningKey).where(
                OrganizationSigningKey.organization_id == organization_id,
                OrganizationSigningKey.retired_at.is_(None),
            )
        ).first()

    def active_key(self, organization_id: uuid.UUID) -> OrganizationSigningKey:
        """The organization's active key, created on first use."""
        row = self._active(organization_id)
        if row is not None:
            return row
        row, _ = self._new_key(organization_id)
        try:
            with self.session.begin_nested():
                self.session.add(row)
        except IntegrityError:
            # Another request created it first.
            existing = self._active(organization_id)
            if existing is None:
                raise
            return existing
        return row

    def list_keys(self, organization_id: uuid.UUID) -> list[OrganizationSigningKey]:
        self.active_key(organization_id)
        return list(
            self.session.scalars(
                select(OrganizationSigningKey)
                .where(OrganizationSigningKey.organization_id == organization_id)
                .order_by(OrganizationSigningKey.created_at)
            )
        )

    def rotate(self, organization_id: uuid.UUID) -> OrganizationSigningKey:
        """Retire the active key and create a new one endorsed by it."""
        old = self.active_key(organization_id)
        old_private = self._private_key(old)
        old.retired_at = datetime.now(UTC)
        self.session.flush()
        new, _ = self._new_key(organization_id)
        new.endorsed_by = old.key_id
        new.endorsement = b64url(old_private.sign(endorsement_payload(new.key_id, new.public_key)))
        self.session.add(new)
        self.session.flush()
        return new

    # --- signatures -------------------------------------------------------

    def sign_desired(self, organization_id: uuid.UUID, payload: dict[str, Any]) -> tuple[str, str]:
        """Return (key_id, signature) for one desired-software payload."""
        row = self.active_key(organization_id)
        signature = self._private_key(row).sign(DESIRED_CONTEXT + canonical_json(payload))
        return row.key_id, b64url(signature)


def signing_key_response(row: OrganizationSigningKey) -> SigningKeyResponse:
    return SigningKeyResponse(
        key_id=row.key_id,
        algorithm="ed25519",
        public_key=row.public_key,
        endorsed_by=row.endorsed_by,
        endorsement=row.endorsement,
        created_at=row.created_at,
        retired_at=row.retired_at,
    )


class SigningKeyAdminService:
    """Operator-facing view and rotation of the organization's signing keys."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.signing = SigningService(session)
        self.authz = AuthzService(session)
        self.audit = AuditRecorder(session)

    def list_keys(self, *, actor: User, organization_id: uuid.UUID) -> list[SigningKeyResponse]:
        membership = self.authz.require_membership(user_id=actor.id, organization_id=organization_id)
        self.authz.require(membership, "organization.read")
        rows = self.signing.list_keys(organization_id)
        self.session.commit()
        return [signing_key_response(row) for row in rows]

    def rotate(self, *, actor: User, organization_id: uuid.UUID) -> SigningKeyResponse:
        membership = self.authz.require_membership(user_id=actor.id, organization_id=organization_id)
        self.authz.require(membership, "organization.update")
        row = self.signing.rotate(organization_id)
        self.audit.record(
            actor=actor,
            organization_id=organization_id,
            action="signing_key.rotated",
            resource_type="signing_key",
            resource_id=row.key_id,
            metadata={"endorsed_by": row.endorsed_by},
        )
        self.session.commit()
        self.session.refresh(row)
        return signing_key_response(row)
