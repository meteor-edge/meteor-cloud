"""Organization and membership business rules."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.identity.models import User
from app.identity.repository import UserRepository
from app.tenancy.models import OrganizationRole
from app.tenancy.permissions import (
    can_assign_role,
    can_delete_organization,
    can_manage_members,
    can_modify_member,
    can_update_organization,
)
from app.tenancy.repository import MembershipRepository, OrganizationRepository
from app.tenancy.schemas import (
    MemberAddRequest,
    MemberResponse,
    MemberRoleUpdateRequest,
    OrganizationCreateRequest,
    OrganizationResponse,
    OrganizationUpdateRequest,
    slugify,
)


class OrganizationService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.organizations = OrganizationRepository(session)
        self.memberships = MembershipRepository(session)
        self.users = UserRepository(session)

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
        self.memberships.create(
            organization_id=organization.id,
            user_id=actor.id,
            role=OrganizationRole.OWNER,
        )
        self.session.commit()
        self.session.refresh(organization)
        return self._to_org_response(organization, OrganizationRole.OWNER, member_count=1)

    def list_organizations(self, actor: User) -> list[OrganizationResponse]:
        rows = self.organizations.list_for_user(actor.id)
        return [
            self._to_org_response(
                organization,
                membership.role,
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
        return self._to_org_response(
            organization,
            membership.role,
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
        if not can_update_organization(membership.role):
            raise ForbiddenError(
                "insufficient_permission",
                "You do not have permission to update this organization.",
            )

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
            membership.role,
            member_count=self.organizations.count_members(organization.id),
        )

    def delete_organization(self, *, actor: User, organization_id: uuid.UUID) -> None:
        organization, membership = self._require_membership(organization_id, actor.id)
        if not can_delete_organization(membership.role):
            raise ForbiddenError(
                "insufficient_permission",
                "Only an organization owner can delete the organization.",
            )
        self.organizations.delete(organization)
        self.session.commit()

    def list_members(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
    ) -> list[MemberResponse]:
        _, _membership = self._require_membership(organization_id, actor.id)
        rows = self.memberships.list_for_organization(organization_id)
        return [self._to_member_response(membership, user) for membership, user in rows]

    def add_member(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        payload: MemberAddRequest,
    ) -> MemberResponse:
        _, actor_membership = self._require_membership(organization_id, actor.id)
        if not can_manage_members(actor_membership.role):
            raise ForbiddenError(
                "insufficient_permission",
                "You do not have permission to add members.",
            )
        if not can_assign_role(actor_membership.role, payload.role):
            raise ForbiddenError(
                "insufficient_permission",
                "You do not have permission to assign this role.",
            )

        user = self.users.get_by_email(payload.email)
        if user is None or not user.is_active:
            raise NotFoundError(
                "member_not_found",
                "No active user was found with that email address.",
            )

        existing = self.memberships.get_user_membership(
            organization_id=organization_id,
            user_id=user.id,
        )
        if existing is not None:
            raise ConflictError(
                "member_already_exists",
                "This user is already a member of the organization.",
            )

        membership = self.memberships.create(
            organization_id=organization_id,
            user_id=user.id,
            role=payload.role,
        )
        self.session.commit()
        self.session.refresh(membership)
        return self._to_member_response(membership, user)

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

        if not can_manage_members(actor_membership.role):
            raise ForbiddenError(
                "insufficient_permission",
                "You do not have permission to change member roles.",
            )
        if not can_modify_member(actor_membership.role, target.role):
            raise ForbiddenError(
                "insufficient_permission",
                "You do not have permission to modify this member.",
            )
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

        if target.role == payload.role:
            user = self.users.get_by_id(target.user_id)
            assert user is not None
            return self._to_member_response(target, user)

        # Guard invalid role transitions that admins should not perform.
        if actor_membership.role is OrganizationRole.ADMIN and target.role in {
            OrganizationRole.OWNER,
            OrganizationRole.ADMIN,
        }:
            raise ForbiddenError(
                "invalid_role_change",
                "Admins cannot modify owners or other admins.",
            )

        self.memberships.update_role(target, payload.role)
        self.session.commit()
        self.session.refresh(target)
        user = self.users.get_by_id(target.user_id)
        assert user is not None
        return self._to_member_response(target, user)

    def remove_member(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        membership_id: uuid.UUID,
    ) -> None:
        _, actor_membership = self._require_membership(organization_id, actor.id)
        target = self._require_org_membership(organization_id, membership_id)

        if not can_manage_members(actor_membership.role):
            raise ForbiddenError(
                "insufficient_permission",
                "You do not have permission to remove members.",
            )
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
        return result

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

    def _to_org_response(
        self,
        organization,
        role: OrganizationRole,
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
            current_user_role=role,
            member_count=member_count,
        )

    def _to_member_response(self, membership, user: User) -> MemberResponse:
        return MemberResponse(
            id=membership.id,
            organization_id=membership.organization_id,
            user_id=membership.user_id,
            email=user.email,
            full_name=user.full_name,
            role=membership.role,
            created_at=membership.created_at,
            updated_at=membership.updated_at,
        )
