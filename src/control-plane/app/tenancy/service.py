"""Organization and membership business rules."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import AuditRecorder
from app.authorization.service import AuthzService
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationAppError
from app.devices.models import DeviceGroup
from app.identity.models import User
from app.ports.identity import IdentityAccountInactiveError, IdentityDirectory
from app.tenancy.models import OrganizationRole
from app.tenancy.permissions import can_assign_role, can_modify_member
from app.tenancy.repository import MembershipRepository, OrganizationRepository, TeamRepository
from app.tenancy.schemas import (
    MemberAddRequest,
    MemberResponse,
    MemberRoleUpdateRequest,
    MemberScopeResponse,
    OrganizationCreateRequest,
    OrganizationResponse,
    OrganizationUpdateRequest,
    TeamRef,
    slugify,
)


class OrganizationService:
    def __init__(self, session: Session, identity: IdentityDirectory) -> None:
        self.session = session
        self.organizations = OrganizationRepository(session)
        self.memberships = MembershipRepository(session)
        self.teams = TeamRepository(session)
        self.identity = identity
        self.authz = AuthzService(session)
        self.audit = AuditRecorder(session)

    def create_organization(
        self,
        *,
        actor: User,
        payload: OrganizationCreateRequest,
    ) -> OrganizationResponse:
        slug = payload.slug or slugify(payload.name)
        if not slug:
            raise ConflictError(
                "organization_slug_exists",
                "A valid slug could not be derived from the organization name.",
            )
        if self.organizations.get_by_slug(slug) is not None:
            raise ConflictError(
                "organization_slug_exists",
                "An organization with this slug already exists.",
            )

        organization = self.organizations.create(
            name=payload.name,
            slug=slug,
            description=payload.description,
            created_by_user_id=actor.id,
        )
        membership = self.memberships.create(
            organization_id=organization.id,
            user_id=actor.id,
            role=OrganizationRole.OWNER,
        )
        self.session.commit()
        self.session.refresh(organization)
        self.session.refresh(membership)
        return self._to_org_response(organization, membership, member_count=1)

    def list_organizations(self, actor: User) -> list[OrganizationResponse]:
        rows = self.organizations.list_for_user(actor.id)
        return [
            self._to_org_response(
                organization,
                membership,
                member_count=self.organizations.count_members(organization.id),
            )
            for organization, membership in rows
        ]

    def get_organization(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
    ) -> OrganizationResponse:
        organization, membership = self._require_membership(organization_id, actor.id)
        self.authz.require(membership, "organization.read")
        return self._to_org_response(
            organization,
            membership,
            member_count=self.organizations.count_members(organization.id),
        )

    def update_organization(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        payload: OrganizationUpdateRequest,
    ) -> OrganizationResponse:
        organization, membership = self._require_membership(organization_id, actor.id)
        self.authz.require(membership, "organization.update")

        if payload.name is not None:
            organization.name = payload.name
        if payload.description is not None:
            organization.description = payload.description
        if payload.slug is not None and payload.slug != organization.slug:
            existing = self.organizations.get_by_slug(payload.slug)
            if existing is not None and existing.id != organization.id:
                raise ConflictError(
                    "organization_slug_exists",
                    "An organization with this slug already exists.",
                )
            organization.slug = payload.slug

        self.organizations.update(organization)
        self.session.commit()
        self.session.refresh(organization)
        return self._to_org_response(
            organization,
            membership,
            member_count=self.organizations.count_members(organization.id),
        )

    def delete_organization(self, *, actor: User, organization_id: uuid.UUID) -> None:
        organization, membership = self._require_membership(organization_id, actor.id)
        self.authz.require(membership, "organization.delete")
        self.organizations.delete(organization)
        self.session.commit()

    def list_members(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
    ) -> list[MemberResponse]:
        _, membership = self._require_membership(organization_id, actor.id)
        self.authz.require(membership, "member.read")
        rows = self.memberships.list_for_organization(organization_id)
        teams = self._team_refs(organization_id)
        return [
            self._to_member_response(
                item,
                email=user.email,
                full_name=user.full_name,
                teams=teams.get(item.id, []),
            )
            for item, user in rows
        ]

    def add_member(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        payload: MemberAddRequest,
    ) -> MemberResponse:
        _, actor_membership = self._require_membership(organization_id, actor.id)
        self.authz.require(actor_membership, "member.invite")
        if not can_assign_role(actor_membership.role, payload.role):
            raise ForbiddenError(
                "insufficient_permission",
                "You do not have permission to assign this role.",
            )

        group_ids = list(payload.scope.device_group_ids) if payload.scope else []
        self._validate_device_groups(organization_id, group_ids)

        account = self.identity.get_by_email(payload.email)
        created_account = account is None
        if account is None:
            if not payload.full_name or not payload.password:
                raise ValidationAppError(
                    "member_account_details_required",
                    "This person is new. Enter a full name and temporary password to add them.",
                )
            try:
                account = self.identity.ensure_account(
                    email=payload.email,
                    full_name=payload.full_name,
                    password=payload.password,
                )
            except IdentityAccountInactiveError as exc:
                raise self._inactive_member_error() from exc
        elif not account.is_active:
            raise self._inactive_member_error()
        elif self.memberships.get_user_membership(
            organization_id=organization_id,
            user_id=account.id,
        ):
            raise ConflictError(
                "member_already_exists",
                "This person is already a member of the organization.",
            )

        membership = self.memberships.create(
            organization_id=organization_id,
            user_id=account.id,
            role=payload.role,
        )
        self.authz.replace_device_group_scope(membership, group_ids or None)
        self.audit.record(
            actor=actor,
            organization_id=organization_id,
            action="member.invite",
            resource_type="membership",
            resource_id=membership.id,
            metadata={
                "role_key": payload.role.value,
                "device_group_ids": [str(item) for item in group_ids],
                "created_account": created_account,
            },
        )
        self.session.commit()
        self.session.refresh(membership)
        return self._to_member_response(membership, email=account.email, full_name=account.full_name)

    def change_role(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        membership_id: uuid.UUID,
        payload: MemberRoleUpdateRequest,
    ) -> MemberResponse:
        _, actor_membership = self._require_membership(organization_id, actor.id)
        target = self._require_org_membership(organization_id, membership_id)
        self.authz.require(actor_membership, "member.update")

        if not can_modify_member(actor_membership.role, target.role):
            raise ForbiddenError(
                "insufficient_permission",
                "You do not have permission to modify this member.",
            )

        if payload.role is not None:
            if not can_assign_role(actor_membership.role, payload.role):
                raise ForbiddenError(
                    "insufficient_permission",
                    "You do not have permission to assign this role.",
                )
            if target.role is OrganizationRole.OWNER and payload.role is not OrganizationRole.OWNER:
                if self.memberships.count_owners(organization_id) <= 1:
                    raise ForbiddenError(
                        "last_owner_required",
                        "The organization must always have at least one owner.",
                    )
            if actor_membership.role is OrganizationRole.ADMIN and target.role in {
                OrganizationRole.OWNER,
                OrganizationRole.ADMIN,
            }:
                raise ForbiddenError(
                    "invalid_role_change",
                    "Admins cannot modify owners or other admins.",
                )
            if target.role != payload.role:
                old_role = target.role.value
                self.memberships.update_role(target, payload.role)
                self.audit.record(
                    actor=actor,
                    organization_id=organization_id,
                    action="member.role_change",
                    resource_type="membership",
                    resource_id=target.id,
                    metadata={"old_role_key": old_role, "new_role_key": payload.role.value},
                )

        if payload.scope is not None:
            group_ids = list(payload.scope.device_group_ids)
            self._validate_device_groups(organization_id, group_ids)
            old_scope = self.authz.scope_for(target)
            self.authz.replace_device_group_scope(target, group_ids or None)
            self.audit.record(
                actor=actor,
                organization_id=organization_id,
                action="member.scope_change",
                resource_type="membership",
                resource_id=target.id,
                metadata={
                    "old_scope": {
                        "mode": old_scope.mode,
                        "device_group_ids": [str(item) for item in old_scope.device_group_ids],
                    },
                    "new_scope": {
                        "mode": "device_groups" if group_ids else "organization",
                        "device_group_ids": [str(item) for item in group_ids],
                    },
                },
            )

        self.session.commit()
        self.session.refresh(target)
        account = self.identity.get_by_id(target.user_id)
        assert account is not None
        return self._to_member_response(target, email=account.email, full_name=account.full_name)

    def remove_member(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        membership_id: uuid.UUID,
    ) -> None:
        _, actor_membership = self._require_membership(organization_id, actor.id)
        target = self._require_org_membership(organization_id, membership_id)
        self.authz.require(actor_membership, "member.remove")
        if not can_modify_member(actor_membership.role, target.role):
            raise ForbiddenError(
                "insufficient_permission",
                "You do not have permission to remove this member.",
            )
        if target.role is OrganizationRole.OWNER and self.memberships.count_owners(organization_id) <= 1:
            raise ForbiddenError(
                "last_owner_required",
                "The last owner cannot be removed from the organization.",
            )

        self.audit.record(
            actor=actor,
            organization_id=organization_id,
            action="member.remove",
            resource_type="membership",
            resource_id=target.id,
            metadata={"role_key": target.role.value},
        )
        self.memberships.delete(target)
        self.session.commit()

    def leave_organization(self, *, actor: User, organization_id: uuid.UUID) -> None:
        _, membership = self._require_membership(organization_id, actor.id)
        if membership.role is OrganizationRole.OWNER and self.memberships.count_owners(organization_id) <= 1:
            raise ForbiddenError(
                "last_owner_required",
                "The last owner cannot leave the organization.",
            )
        self.memberships.delete(membership)
        self.session.commit()

    def _require_membership(
        self,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ):
        result = self.organizations.get_for_user(
            organization_id=organization_id,
            user_id=user_id,
        )
        if result is None:
            raise NotFoundError(
                "organization_not_found",
                "Organization was not found.",
            )
        organization, membership = result
        if membership.status != "active":
            raise NotFoundError(
                "organization_not_found",
                "Organization was not found.",
            )
        return organization, membership

    def _require_org_membership(
        self,
        organization_id: uuid.UUID,
        membership_id: uuid.UUID,
    ):
        membership = self.memberships.get(membership_id)
        if membership is None or membership.organization_id != organization_id:
            raise NotFoundError(
                "member_not_found",
                "Organization member was not found.",
            )
        return membership

    def _validate_device_groups(self, organization_id: uuid.UUID, group_ids: list[uuid.UUID]) -> None:
        if not group_ids:
            return
        found = {
            row
            for row in self.session.scalars(
                select(DeviceGroup.id).where(
                    DeviceGroup.organization_id == organization_id,
                    DeviceGroup.id.in_(group_ids),
                )
            ).all()
        }
        missing = [str(item) for item in group_ids if item not in found]
        if missing:
            raise NotFoundError(
                "device_group_not_found",
                "One or more device groups were not found in this organization.",
            )

    def _group_names(self, group_ids: tuple[uuid.UUID, ...]) -> list[str]:
        if not group_ids:
            return []
        rows = self.session.execute(
            select(DeviceGroup.id, DeviceGroup.name).where(DeviceGroup.id.in_(group_ids))
        ).all()
        by_id = {row[0]: row[1] for row in rows}
        return [by_id[item] for item in group_ids if item in by_id]

    def _to_org_response(
        self,
        organization,
        membership,
        *,
        member_count: int | None = None,
    ) -> OrganizationResponse:
        return OrganizationResponse(
            id=organization.id,
            name=organization.name,
            slug=organization.slug,
            description=organization.description,
            created_by_user_id=organization.created_by_user_id,
            created_at=organization.created_at,
            updated_at=organization.updated_at,
            current_user_role=membership.role,
            member_count=member_count,
        )

    def _team_refs(self, organization_id: uuid.UUID) -> dict[uuid.UUID, list[TeamRef]]:
        return {
            membership_id: [TeamRef(id=team_id, name=name) for team_id, name in pairs]
            for membership_id, pairs in self.teams.refs_by_membership(organization_id).items()
        }

    @staticmethod
    def _inactive_member_error() -> NotFoundError:
        return NotFoundError(
            "member_not_found",
            "No active account was found with that email address.",
        )

    def _to_member_response(
        self,
        membership,
        *,
        email: str,
        full_name: str,
        teams: list[TeamRef] | None = None,
    ) -> MemberResponse:
        scope = self.authz.scope_for(membership)
        if teams is None:
            teams = self._team_refs(membership.organization_id).get(membership.id, [])
        return MemberResponse(
            id=membership.id,
            organization_id=membership.organization_id,
            user_id=membership.user_id,
            email=email,
            full_name=full_name,
            role=membership.role,
            role_name=membership.role_ref.name,
            status=membership.status,
            scope=MemberScopeResponse(
                mode=scope.mode,
                device_group_ids=list(scope.device_group_ids),
                device_group_names=self._group_names(scope.device_group_ids),
            ),
            teams=teams,
            created_at=membership.created_at,
            updated_at=membership.updated_at,
        )
