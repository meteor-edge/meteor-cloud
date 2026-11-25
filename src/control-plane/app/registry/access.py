"""Access tokens the registry accepts (Docker token auth, docs/registry.md)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.registry.keys import RegistryKeys

TOKEN_ISSUER = "meteorcloud"


def sign_access_token(
    session: Session, settings: Settings, *, subject: str, access: list[dict[str, Any]], now: datetime | None = None
) -> str:
    """The caller commits: the signing key may have been created just now."""
    now = now or datetime.now(UTC)
    key_id, private_key = RegistryKeys(session, settings).signing_key()
    return jwt.encode(
        {
            "iss": TOKEN_ISSUER,
            "aud": settings.registry_public_host,
            "sub": subject,
            "jti": str(uuid.uuid4()),
            "iat": now,
            "nbf": now - timedelta(seconds=10),
            "exp": now + timedelta(seconds=settings.registry_token_ttl_seconds),
            "access": access,
        },
        private_key,
        algorithm="ES256",
        headers={"kid": key_id},
    )


def events_secret(settings: Settings) -> str:
    """What the registry sends with its notifications; derived, so there is nothing to configure."""
    return (
        HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"meteorcloud registry events v1")
        .derive(settings.app_secret_key.encode())
        .hex()
    )


# Not the registry's audience: a pull password is no access token, and an access token no password.
_PULL_AUDIENCE = "meteorcloud-registry-pull"


def sign_pull_password(
    session: Session, settings: Settings, *, device_id: uuid.UUID, workload_key: str, ttl: int
) -> tuple[str, datetime]:
    """What a device signs in with. Not stored; its reach is checked again on every use."""
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=ttl)
    key_id, private_key = RegistryKeys(session, settings).signing_key()
    password = jwt.encode(
        {
            "iss": TOKEN_ISSUER,
            "aud": _PULL_AUDIENCE,
            "sub": str(device_id),
            "workload_key": workload_key,
            "iat": now,
            "exp": expires_at,
        },
        private_key,
        algorithm="ES256",
        headers={"kid": key_id},
    )
    return password, expires_at


def verify_pull_password(session: Session, settings: Settings, password: str) -> dict[str, Any]:
    """Raises ``jwt.PyJWTError`` for anything but a current pull password."""
    jwk = RegistryKeys(session, settings).jwks()["keys"][0]
    return jwt.decode(
        password,
        jwt.PyJWK(jwk).key,
        algorithms=["ES256"],
        audience=_PULL_AUDIENCE,
        issuer=TOKEN_ISSUER,
        options={"require": ["exp", "sub", "aud", "iss"]},
    )
