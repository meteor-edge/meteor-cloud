"""Seed the permission catalog and immutable system roles."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.authorization.catalog import PERMISSIONS, SYSTEM_ROLES
from app.authorization.models import Permission, Role, RolePermission


def seed_authorization_catalog(session: Session) -> dict[str, uuid.UUID]:
    """Upsert permissions and system roles. Return role key → role id."""
    permission_ids: dict[str, uuid.UUID] = {}
    for item in PERMISSIONS:
        existing = session.scalar(
            select(Permission).where(Permission.resource == item.resource, Permission.action == item.action)
        )
        if existing is None:
            existing = Permission(
                resource=item.resource,
                action=item.action,
                label=item.label,
                description=item.description,
            )
            session.add(existing)
            session.flush()
        else:
            existing.label = item.label
            existing.description = item.description
        permission_ids[item.id] = existing.id

    role_ids: dict[str, uuid.UUID] = {}
    for role_def in SYSTEM_ROLES:
        role = session.scalar(
            select(Role).where(Role.organization_id.is_(None), Role.key == role_def.key)
        )
        if role is None:
            role = Role(
                organization_id=None,
                key=role_def.key,
                name=role_def.name,
                description=role_def.description,
                is_system=True,
            )
            session.add(role)
            session.flush()
        else:
            role.name = role_def.name
            role.description = role_def.description
            role.is_system = True
        role_ids[role_def.key] = role.id

        wanted = {permission_ids[key] for key in role_def.permissions}
        current = {
            row.permission_id
            for row in session.scalars(select(RolePermission).where(RolePermission.role_id == role.id)).all()
        }
        for permission_id in wanted - current:
            session.add(RolePermission(role_id=role.id, permission_id=permission_id))
        for permission_id in current - wanted:
            link = session.get(RolePermission, (role.id, permission_id))
            if link is not None:
                session.delete(link)

    session.flush()
    return role_ids
