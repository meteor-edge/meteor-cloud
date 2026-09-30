"""Organization and membership persistence helpers."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.identity.models import User
from app.tenancy.models import Organization, OrganizationMembership, OrganizationRole


class OrganizationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        name: str,
        slug: str,
        description: str | None,
        created_by_user_id: uuid.UUID,
    ) -> Organization:
        organization = Organization(
            name=name,
            slug=slug,
            description=description,
            created_by_user_id=created_by_user_id,
        )
        self.session.add(organization)
        self.session.flush()
        return organization

    def get_by_slug(self, slug: str) -> Organization | None:
        statement = select(Organization).where(Organization.slug == slug)
        return self.session.scalar(statement)

    def get_for_user(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> tuple[Organization, OrganizationMembership] | None:
        statement = (
            select(Organization, OrganizationMembership)
            .join(
                OrganizationMembership,
                OrganizationMembership.organization_id == Organization.id,
            )
            .where(
                Organization.id == organization_id,
                OrganizationMembership.user_id == user_id,
            )
        )
        row = self.session.execute(statement).one_or_none()
        if row is None:
            return None
        return row[0], row[1]

    def list_for_user(self, user_id: uuid.UUID) -> list[tuple[Organization, OrganizationMembership]]:
        statement = (
            select(Organization, OrganizationMembership)
            .join(
                OrganizationMembership,
                OrganizationMembership.organization_id == Organization.id,
            )
            .where(OrganizationMembership.user_id == user_id)
            .order_by(Organization.name.asc())
        )
        return list(self.session.execute(statement).all())

    def update(self, organization: Organization) -> Organization:
        self.session.add(organization)
        self.session.flush()
        return organization

    def delete(self, organization: Organization) -> None:
        self.session.delete(organization)
        self.session.flush()

    def count_members(self, organization_id: uuid.UUID) -> int:
        statement = (
            select(func.count())
            .select_from(OrganizationMembership)
            .where(OrganizationMembership.organization_id == organization_id)
        )
        return int(self.session.scalar(statement) or 0)


class MembershipRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, membership_id: uuid.UUID) -> OrganizationMembership | None:
        return self.session.get(OrganizationMembership, membership_id)

    def get_user_membership(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> OrganizationMembership | None:
        statement = select(OrganizationMembership).where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.user_id == user_id,
        )
        return self.session.scalar(statement)

    def list_for_organization(
        self,
        organization_id: uuid.UUID,
    ) -> list[tuple[OrganizationMembership, User]]:
        statement = (
            select(OrganizationMembership, User)
            .join(User, User.id == OrganizationMembership.user_id)
            .where(OrganizationMembership.organization_id == organization_id)
            .order_by(User.full_name.asc())
        )
        return list(self.session.execute(statement).all())

    def create(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        role: OrganizationRole,
    ) -> OrganizationMembership:
        membership = OrganizationMembership(
            organization_id=organization_id,
            user_id=user_id,
            role=role,
        )
        self.session.add(membership)
        self.session.flush()
        return membership

    def update_role(
        self,
        membership: OrganizationMembership,
        role: OrganizationRole,
    ) -> OrganizationMembership:
        membership.role = role
        self.session.add(membership)
        self.session.flush()
        return membership

    def delete(self, membership: OrganizationMembership) -> None:
        self.session.delete(membership)
        self.session.flush()

    def count_owners(self, organization_id: uuid.UUID) -> int:
        statement = (
            select(func.count())
            .select_from(OrganizationMembership)
            .where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.role == OrganizationRole.OWNER,
            )
        )
        return int(self.session.scalar(statement) or 0)
