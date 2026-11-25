"""The key that signs registry access tokens, created on first use and kept encrypted."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.algorithms import ECAlgorithm
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.secretbox import seal, unseal
from app.devices.tokens import hash_token
from app.registry.models import RegistrySigningKey


def _context(key_id: str) -> str:
    return f"registry_signing_keys:{key_id}"


class RegistryKeys:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    def _active(self) -> RegistrySigningKey | None:
        return self.session.scalars(
            select(RegistrySigningKey)
            .where(RegistrySigningKey.retired_at.is_(None))
            .order_by(RegistrySigningKey.created_at.desc())
            .limit(1)
        ).first()

    def _lock(self) -> None:
        self.session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended("registry-signing-key", 0))))

    def _create(self) -> RegistrySigningKey:
        private = ec.generate_private_key(ec.SECP256R1())
        jwk = json.loads(ECAlgorithm.to_jwk(private.public_key()))
        public = {name: jwk[name] for name in ("crv", "kty", "x", "y")}
        key_id = hash_token(json.dumps(public, sort_keys=True, separators=(",", ":")))[:32]
        pem = private.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ).decode()
        row = RegistrySigningKey(
            key_id=key_id,
            public_jwk=json.dumps({**public, "kid": key_id, "use": "sig", "alg": "ES256"}),
            sealed_private_key=seal(self.settings, pem, context=_context(key_id)),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def active(self) -> RegistrySigningKey:
        row = self._active()
        if row is None:
            self._lock()
            row = self._active() or self._create()
        return row

    def rotate(self) -> RegistrySigningKey:
        """Retire every key and create a new one. The registry must reload its key set afterwards."""
        self._lock()
        for row in self.session.scalars(select(RegistrySigningKey).where(RegistrySigningKey.retired_at.is_(None))):
            row.retired_at = datetime.now(UTC)
        self.session.flush()
        return self._create()

    def signing_key(self) -> tuple[str, str]:
        """(key id, private key PEM)."""
        row = self.active()
        return row.key_id, unseal(self.settings, row.sealed_private_key, context=_context(row.key_id))

    def jwks(self) -> dict[str, Any]:
        return {"keys": [json.loads(self.active().public_jwk)]}
