"""Teams: named groups of organization members.

Teams organize people only. Role and device-group scope stay on each member,
so team changes never change what anyone can do.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.audit.service import AuditRecorder
from app.authorization.service import AuthzService
from app.core.exceptions import ConflictError, NotFoundError
from app.identity.models import User
from app.tenancy.models import OrganizationMembership, Team, TeamMember
from app.tenancy.repository import MembershipRepository, TeamRepository
from app.tenancy.schemas import (
    TeamCreateRequest,
    TeamMemberResponse,
    TeamResponse,
    TeamSummaryResponse,
    TeamUpdateRequest,
)


class TeamService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.teams = TeamRepository(session)
        self.memberships = MembershipRepository(session)
        self.authz = AuthzService(session)
        self.audit = AuditRecorder(session)

    def list_teams(self, *, actor: User, organization_id: uuid.UUID) -> list[TeamSummaryResponse]:
        self._require(actor, organization_id, "team.read")
        return [self._to_summary(team) for team in self.teams.list(organization_id)]

    def get_team(self, *, actor: User, organization_id: uuid.UUID, team_id: uuid.UUID) -> TeamResponse:
        self._require(actor, organization_id, "team.read")
        return self._to_response(self._require_team(organization_id, team_id))

    def create_team(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        payload: TeamCreateRequest,
    ) -> TeamResponse:
        self._require(actor, organization_id, "team.create")
        self._ensure_name_free(organization_id, payload.name)
        members = self._require_members(organization_id, payload.membership_ids)

        team = Team(organization_id=organization_id, name=payload.name, description=payload.description)
        self.teams.create(team)
        for membership in members:
            team.members.append(TeamMember(membership_id=membership.id))
        self.audit.record(
            actor=actor,
            organization_id=organization_id,
            action="team.create",
            resource_type="team",
            resource_id=team.id,
            metadata={"name": team.name, "membership_ids": [str(item.id) for item in members]},
        )
        self.session.commit()
        self.session.refresh(team)
        return self._to_response(team)

    def update_team(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        team_id: uuid.UUID,
        payload: TeamUpdateRequest,
    ) -> TeamResponse:
        self._require(actor, organization_id, "team.update")
        team = self._require_team(organization_id, team_id)
        if payload.name is not None and payload.name.lower() != team.name.lower():
            self._ensure_name_free(organization_id, payload.name)
        if payload.name is not None:
            team.name = payload.name
        if "description" in payload.model_fields_set:
            team.description = payload.description
        self.audit.record(
            actor=actor,
            organization_id=organization_id,
            action="team.update",
            resource_type="team",
            resource_id=team.id,
            metadata={"name": team.name},
        )
        self.session.commit()
        self.session.refresh(team)
        return self._to_response(team)

    def delete_team(self, *, actor: User, organization_id: uuid.UUID, team_id: uuid.UUID) -> None:
        self._require(actor, organization_id, "team.delete")
        team = self._require_team(organization_id, team_id)
        self.audit.record(
            actor=actor,
            organization_id=organization_id,
            action="team.delete",
            resource_type="team",
            resource_id=team.id,
            metadata={"name": team.name},
        )
        self.teams.delete(team)
        self.session.commit()

    def add_member(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        team_id: uuid.UUID,
        membership_id: uuid.UUID,
    ) -> TeamResponse:
        self._require(actor, organization_id, "team.update")
        team = self._require_team(organization_id, team_id)
        (membership,) = self._require_members(organization_id, [membership_id])
        if any(item.membership_id == membership.id for item in team.members):
            raise ConflictError("team_member_exists", "This member is already on the team.")
        team.members.append(TeamMember(membership_id=membership.id))
        self.audit.record(
            actor=actor,
            organization_id=organization_id,
            action="team.member_add",
            resource_type="team",
            resource_id=team.id,
            metadata={"membership_id": str(membership.id)},
        )
        self.session.commit()
        self.session.refresh(team)
        return self._to_response(team)

    def remove_member(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        team_id: uuid.UUID,
        membership_id: uuid.UUID,
    ) -> TeamResponse:
        self._require(actor, organization_id, "team.update")
        team = self._require_team(organization_id, team_id)
        link = next((item for item in team.members if item.membership_id == membership_id), None)
        if link is None:
            raise NotFoundError("team_member_not_found", "This member is not on the team.")
        team.members.remove(link)
        self.audit.record(
            actor=actor,
            organization_id=organization_id,
            action="team.member_remove",
            resource_type="team",
            resource_id=team.id,
            metadata={"membership_id": str(membership_id)},
        )
        self.session.commit()
        self.session.refresh(team)
        return self._to_response(team)

    def _require(self, actor: User, organization_id: uuid.UUID, permission: str) -> OrganizationMembership:
        membership = self.authz.get_active_membership(user_id=actor.id, organization_id=organization_id)
        if membership is None:
            raise NotFoundError("organization_not_found", "Organization was not found.")
        self.authz.require(membership, permission)
        return membership

    def _require_team(self, organization_id: uuid.UUID, team_id: uuid.UUID) -> Team:
        team = self.teams.get(organization_id=organization_id, team_id=team_id)
        if team is None:
            raise NotFoundError("team_not_found", "Team was not found.")
        return team

    def _require_members(
        self,
        organization_id: uuid.UUID,
        membership_ids: list[uuid.UUID],
    ) -> list[OrganizationMembership]:
        unique_ids = list(dict.fromkeys(membership_ids))
        found = self.memberships.list_by_ids(organization_id=organization_id, membership_ids=unique_ids)
        if len(found) != len(unique_ids):
            raise NotFoundError("member_not_found", "One or more members were not found in this organization.")
        return found

    def _ensure_name_free(self, organization_id: uuid.UUID, name: str) -> None:
        if self.teams.get_by_name(organization_id=organization_id, name=name):
            raise ConflictError("team_exists", "A team with this name already exists.")

    def _to_summary(self, team: Team) -> TeamSummaryResponse:
        return TeamSummaryResponse(
            id=team.id,
            organization_id=team.organization_id,
            name=team.name,
            description=team.description,
            member_count=len(team.members),
            created_at=team.created_at,
            updated_at=team.updated_at,
        )

    def _to_response(self, team: Team) -> TeamResponse:
        members = [
            TeamMemberResponse(
                membership_id=membership.id,
                email=user.email,
                full_name=user.full_name,
                role=membership.role,
                role_name=membership.role_ref.name,
            )
            for membership, user in self.teams.list_members(team.id)
        ]
        return TeamResponse(**self._to_summary(team).model_dump(), members=members)
