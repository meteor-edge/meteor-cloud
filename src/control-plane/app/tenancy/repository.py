"""Organization and membership persistence helpers."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.authorization.models import Role
from app.authorization.roles import get_system_role_id
from app.identity.models import User
from app.tenancy.models import Organization, OrganizationMembership, OrganizationRole, Team, TeamMember


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
            role_id=get_system_role_id(self.session, role.value),
        )
        self.session.add(membership)
        self.session.flush()
        return membership

    def update_role(
        self,
        membership: OrganizationMembership,
        role: OrganizationRole,
    ) -> OrganizationMembership:
        membership.role_id = get_system_role_id(self.session, role.value)
        self.session.add(membership)
        self.session.flush()
        return membership

    def delete(self, membership: OrganizationMembership) -> None:
        self.session.delete(membership)
        self.session.flush()

    def list_by_ids(
        self,
        *,
        organization_id: uuid.UUID,
        membership_ids: list[uuid.UUID],
    ) -> list[OrganizationMembership]:
        if not membership_ids:
            return []
        statement = select(OrganizationMembership).where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.id.in_(membership_ids),
        )
        return list(self.session.scalars(statement).all())

    def count_owners(self, organization_id: uuid.UUID) -> int:
        statement = (
            select(func.count())
            .select_from(OrganizationMembership)
            .join(Role, Role.id == OrganizationMembership.role_id)
            .where(
                OrganizationMembership.organization_id == organization_id,
                Role.key == OrganizationRole.OWNER.value,
            )
        )
        return int(self.session.scalar(statement) or 0)


class TeamRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, *, organization_id: uuid.UUID, team_id: uuid.UUID) -> Team | None:
        team = self.session.get(Team, team_id)
        if team is None or team.organization_id != organization_id:
            return None
        return team

    def get_by_name(self, *, organization_id: uuid.UUID, name: str) -> Team | None:
        statement = select(Team).where(
            Team.organization_id == organization_id,
            func.lower(Team.name) == name.lower(),
        )
        return self.session.scalar(statement)

    def list(self, organization_id: uuid.UUID) -> list[Team]:
        statement = select(Team).where(Team.organization_id == organization_id).order_by(func.lower(Team.name))
        return list(self.session.scalars(statement).all())

    def create(self, team: Team) -> Team:
        self.session.add(team)
        self.session.flush()
        return team

    def delete(self, team: Team) -> None:
        self.session.delete(team)
        self.session.flush()

    def list_members(self, team_id: uuid.UUID) -> list[tuple[OrganizationMembership, User]]:
        statement = (
            select(OrganizationMembership, User)
            .join(TeamMember, TeamMember.membership_id == OrganizationMembership.id)
            .join(User, User.id == OrganizationMembership.user_id)
            .where(TeamMember.team_id == team_id)
            .order_by(User.full_name.asc())
        )
        return list(self.session.execute(statement).all())

    def refs_by_membership(self, organization_id: uuid.UUID) -> dict[uuid.UUID, list[tuple[uuid.UUID, str]]]:
        """Map membership id to the (team id, team name) pairs it belongs to, sorted by name."""
        statement = (
            select(TeamMember.membership_id, Team.id, Team.name)
            .join(Team, Team.id == TeamMember.team_id)
            .where(Team.organization_id == organization_id)
            .order_by(func.lower(Team.name))
        )
        result: dict[uuid.UUID, list[tuple[uuid.UUID, str]]] = {}
        for membership_id, team_id, name in self.session.execute(statement).all():
            result.setdefault(membership_id, []).append((team_id, name))
        return result
