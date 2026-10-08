"""Helpers to resolve system roles by key."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.authorization.catalog import LEGACY_ROLE_MAP, SYSTEM_ROLE_BY_KEY
from app.authorization.models import Role
from app.core.exceptions import AppError
from app.tenancy.models import OrganizationRole


def normalize_role_key(value: str) -> str:
    key = LEGACY_ROLE_MAP.get(value, value)
    if key not in SYSTEM_ROLE_BY_KEY:
        raise AppError("invalid_role", f"Unknown role '{value}'.", status_code=422)
    return key


def role_key_to_enum(key: str) -> OrganizationRole:
    return OrganizationRole(normalize_role_key(key))


def get_system_role(session: Session, key: str) -> Role:
    normalized = normalize_role_key(key)
    role = session.scalar(select(Role).where(Role.organization_id.is_(None), Role.key == normalized))
    if role is None:
        raise RuntimeError(f"System role '{normalized}' is not seeded")
    return role


def get_system_role_id(session: Session, key: str) -> uuid.UUID:
    return get_system_role(session, key).id
