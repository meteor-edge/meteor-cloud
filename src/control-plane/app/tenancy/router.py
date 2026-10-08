"""Organization HTTP routes."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.adapters import identity_directory as build_identity_directory
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.identity.dependencies import CurrentUser
from app.ports.identity import IdentityDirectory
from app.tenancy.schemas import (
    MemberAddRequest,
    MemberResponse,
    MemberRoleUpdateRequest,
    OrganizationCreateRequest,
    OrganizationResponse,
    OrganizationUpdateRequest,
    TeamCreateRequest,
    TeamMemberAddRequest,
    TeamResponse,
    TeamSummaryResponse,
    TeamUpdateRequest,
)
from app.tenancy.service import OrganizationService
from app.tenancy.teams import TeamService

router = APIRouter(prefix="/api/v1/organizations", tags=["organizations"])


def get_identity_directory(
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> IdentityDirectory:
    return build_identity_directory(settings, session)


def get_organization_service(
    session: Annotated[Session, Depends(get_db)],
    identity: Annotated[IdentityDirectory, Depends(get_identity_directory)],
) -> OrganizationService:
    return OrganizationService(session, identity)


OrgService = Annotated[OrganizationService, Depends(get_organization_service)]


def get_team_service(session: Annotated[Session, Depends(get_db)]) -> TeamService:
    return TeamService(session)


TeamSvc = Annotated[TeamService, Depends(get_team_service)]


@router.get("", response_model=list[OrganizationResponse])
def list_organizations(
    current_user: CurrentUser,
    service: OrgService,
) -> list[OrganizationResponse]:
    return service.list_organizations(current_user)


@router.post("", response_model=OrganizationResponse, status_code=201)
def create_organization(
    payload: OrganizationCreateRequest,
    current_user: CurrentUser,
    service: OrgService,
) -> OrganizationResponse:
    return service.create_organization(actor=current_user, payload=payload)


@router.get("/{organization_id}", response_model=OrganizationResponse)
def get_organization(
    organization_id: uuid.UUID,
    current_user: CurrentUser,
    service: OrgService,
) -> OrganizationResponse:
    return service.get_organization(actor=current_user, organization_id=organization_id)


@router.patch("/{organization_id}", response_model=OrganizationResponse)
def update_organization(
    organization_id: uuid.UUID,
    payload: OrganizationUpdateRequest,
    current_user: CurrentUser,
    service: OrgService,
) -> OrganizationResponse:
    return service.update_organization(
        actor=current_user,
        organization_id=organization_id,
        payload=payload,
    )


@router.delete("/{organization_id}", status_code=204)
def delete_organization(
    organization_id: uuid.UUID,
    current_user: CurrentUser,
    service: OrgService,
) -> None:
    service.delete_organization(actor=current_user, organization_id=organization_id)


@router.get("/{organization_id}/members", response_model=list[MemberResponse])
def list_members(
    organization_id: uuid.UUID,
    current_user: CurrentUser,
    service: OrgService,
) -> list[MemberResponse]:
    return service.list_members(actor=current_user, organization_id=organization_id)


@router.post("/{organization_id}/members", response_model=MemberResponse, status_code=201)
def add_member(
    organization_id: uuid.UUID,
    payload: MemberAddRequest,
    current_user: CurrentUser,
    service: OrgService,
) -> MemberResponse:
    return service.add_member(
        actor=current_user,
        organization_id=organization_id,
        payload=payload,
    )


@router.patch(
    "/{organization_id}/members/{membership_id}",
    response_model=MemberResponse,
)
def update_member_role(
    organization_id: uuid.UUID,
    membership_id: uuid.UUID,
    payload: MemberRoleUpdateRequest,
    current_user: CurrentUser,
    service: OrgService,
) -> MemberResponse:
    return service.change_role(
        actor=current_user,
        organization_id=organization_id,
        membership_id=membership_id,
        payload=payload,
    )


@router.delete("/{organization_id}/members/{membership_id}", status_code=204)
def remove_member(
    organization_id: uuid.UUID,
    membership_id: uuid.UUID,
    current_user: CurrentUser,
    service: OrgService,
) -> None:
    service.remove_member(
        actor=current_user,
        organization_id=organization_id,
        membership_id=membership_id,
    )


@router.get("/{organization_id}/teams", response_model=list[TeamSummaryResponse])
def list_teams(
    organization_id: uuid.UUID,
    current_user: CurrentUser,
    service: TeamSvc,
) -> list[TeamSummaryResponse]:
    return service.list_teams(actor=current_user, organization_id=organization_id)


@router.post("/{organization_id}/teams", response_model=TeamResponse, status_code=201)
def create_team(
    organization_id: uuid.UUID,
    payload: TeamCreateRequest,
    current_user: CurrentUser,
    service: TeamSvc,
) -> TeamResponse:
    return service.create_team(actor=current_user, organization_id=organization_id, payload=payload)


@router.get("/{organization_id}/teams/{team_id}", response_model=TeamResponse)
def get_team(
    organization_id: uuid.UUID,
    team_id: uuid.UUID,
    current_user: CurrentUser,
    service: TeamSvc,
) -> TeamResponse:
    return service.get_team(actor=current_user, organization_id=organization_id, team_id=team_id)


@router.patch("/{organization_id}/teams/{team_id}", response_model=TeamResponse)
def update_team(
    organization_id: uuid.UUID,
    team_id: uuid.UUID,
    payload: TeamUpdateRequest,
    current_user: CurrentUser,
    service: TeamSvc,
) -> TeamResponse:
    return service.update_team(
        actor=current_user,
        organization_id=organization_id,
        team_id=team_id,
        payload=payload,
    )


@router.delete("/{organization_id}/teams/{team_id}", status_code=204)
def delete_team(
    organization_id: uuid.UUID,
    team_id: uuid.UUID,
    current_user: CurrentUser,
    service: TeamSvc,
) -> None:
    service.delete_team(actor=current_user, organization_id=organization_id, team_id=team_id)


@router.post("/{organization_id}/teams/{team_id}/members", response_model=TeamResponse)
def add_team_member(
    organization_id: uuid.UUID,
    team_id: uuid.UUID,
    payload: TeamMemberAddRequest,
    current_user: CurrentUser,
    service: TeamSvc,
) -> TeamResponse:
    return service.add_member(
        actor=current_user,
        organization_id=organization_id,
        team_id=team_id,
        membership_id=payload.membership_id,
    )


@router.delete("/{organization_id}/teams/{team_id}/members/{membership_id}", response_model=TeamResponse)
def remove_team_member(
    organization_id: uuid.UUID,
    team_id: uuid.UUID,
    membership_id: uuid.UUID,
    current_user: CurrentUser,
    service: TeamSvc,
) -> TeamResponse:
    return service.remove_member(
        actor=current_user,
        organization_id=organization_id,
        team_id=team_id,
        membership_id=membership_id,
    )


@router.post("/{organization_id}/leave", status_code=204)
def leave_organization(
    organization_id: uuid.UUID,
    current_user: CurrentUser,
    service: OrgService,
) -> None:
    service.leave_organization(actor=current_user, organization_id=organization_id)
