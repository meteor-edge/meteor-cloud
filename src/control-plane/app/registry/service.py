"""Registry access: Docker token auth (scope -> RBAC), namespaces and push tokens."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.audit.service import OUTCOME_DENIED, OUTCOME_FAILURE, AuditRecorder
from app.authorization.service import AuthzService
from app.core.audit_context import set_request_actor
from app.core.config import Settings, get_settings
from app.core.exceptions import AppError, ConflictError, ForbiddenError, NotFoundError
from app.devices.tokens import generate_registry_token, hash_token
from app.identity.models import User
from app.registry.access import sign_access_token
from app.registry.models import ContainerImage, RegistryNamespace, RegistryToken
from app.registry.schemas import (
    COMPONENT,
    RegistryAccessTokenResponse,
    RegistryAccountResponse,
    RegistryInfoResponse,
    RegistryTokenCreateRequest,
    RegistryTokenCreateResponse,
    RegistryTokenResponse,
)
from app.tenancy.models import Organization

# More scopes than any client asks for at once (a push with cross-repository mounts asks for a few).
_MAX_SCOPES = 20
_REPOSITORY_RE = re.compile(rf"({COMPONENT})/({COMPONENT}(?:/{COMPONENT})*)")
_ACTION_PERMISSIONS = {"pull": "image.read", "push": "image.push", "delete": "image.delete"}


class RegistryAuthError(AppError):
    def __init__(self) -> None:
        super().__init__(
            "invalid_registry_credentials",
            "Sign in with a registry token as the password.",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="MeteorCloud registry"'},
        )


@dataclass(frozen=True, slots=True)
class RequestedScope:
    repository: str
    actions: tuple[str, ...]


def parse_scopes(values: list[str]) -> list[RequestedScope]:
    """Repository scopes out of ``type:name:actions`` strings; anything else is never granted."""
    scopes: list[RequestedScope] = []
    for item in " ".join(values).split()[:_MAX_SCOPES]:
        kind, _, rest = item.partition(":")
        name, _, actions = rest.rpartition(":")
        if kind == "repository" and name:
            scopes.append(RequestedScope(name, tuple(dict.fromkeys(a for a in actions.split(",") if a))))
    return scopes


# generate_registry_token: the prefix and 32 random bytes.
_TOKEN_RE = re.compile(r"rgt_[A-Za-z0-9_-]{43}")


def namespace_for(session: Session, organization: Organization) -> str:
    """The organization's namespace, claimed on first use. The caller commits."""
    statement = select(RegistryNamespace.name).where(RegistryNamespace.organization_id == organization.id)
    name = session.scalar(statement)
    if name is not None:
        return name
    session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended("registry-namespaces", 0))))
    name = session.scalar(statement)
    if name is not None:
        return name
    name, suffix = organization.slug, 1
    while session.get(RegistryNamespace, name) is not None:
        suffix += 1
        name = f"{organization.slug}-{suffix}"
    session.add(RegistryNamespace(name=name, organization_id=organization.id))
    session.flush()
    return name


def used_bytes(session: Session, organization_id: uuid.UUID) -> int:
    """Size of the tagged images; layers that images share are counted for each."""
    images = (
        select(ContainerImage.digest, func.max(ContainerImage.size_bytes).label("size"))
        .where(ContainerImage.organization_id == organization_id)
        .group_by(ContainerImage.digest)
        .subquery()
    )
    return int(session.scalar(select(func.coalesce(func.sum(images.c.size), 0))) or 0)


class RegistryService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.authz = AuthzService(session)
        self.audit = AuditRecorder(session)

    @property
    def enabled(self) -> bool:
        return self.settings.registry_provider != "none"

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise NotFoundError("registry_disabled", "The container registry is not enabled on this platform.")

    # --- Docker token endpoint ----------------------------------------------

    def issue_access_token(
        self, *, presented: str | None, service: str, scopes: list[str]
    ) -> RegistryAccessTokenResponse:
        self._require_enabled()
        if service != self.settings.registry_public_host:
            raise AppError("invalid_registry_service", "Unknown registry service.", status_code=400)
        now = datetime.now(UTC)
        if presented and "." in presented:  # a device's pull password; registry tokens have no dots
            subject, access = self._device_access(presented, parse_scopes(scopes))
        else:
            token = self._authenticate(presented)
            access = self._grant(token, parse_scopes(scopes))
            self._touch(token, now)
            subject = str(token.id)
        # The registry reports the subject in push events; the token id maps to a person or robot.
        signed = sign_access_token(self.session, self.settings, subject=subject, access=access, now=now)
        ttl = self.settings.registry_token_ttl_seconds
        self.session.commit()
        return RegistryAccessTokenResponse(token=signed, access_token=signed, expires_in=ttl, issued_at=now)

    def account(self, *, presented: str | None) -> RegistryAccountResponse:
        self._require_enabled()
        token = self._authenticate(presented)
        namespace = namespace_for(self.session, self.session.get(Organization, token.organization_id))
        response = RegistryAccountResponse(
            host=self.settings.registry_public_host,
            namespace=namespace,
            token_name=token.name,
            access="push" if "push" in self._allowed_actions(token) else "pull",
        )
        self.session.commit()
        return response

    def _device_access(self, presented: str, scopes: list[RequestedScope]) -> tuple[str, list[dict[str, Any]]]:
        # Imported here: it names images with this module's namespace_for.
        from app.registry.images import RegistryImages

        found = RegistryImages(self.session, self.settings).pullable_repository(presented)
        if found is None:
            raise RegistryAuthError()
        device, repository = found
        set_request_actor(actor_type="device", actor_id=device.id)
        access = [
            {"type": "repository", "name": repository, "actions": ["pull"]}
            for scope in scopes
            if scope.repository == repository and "pull" in scope.actions
        ]
        return f"device:{device.id}", access

    def _authenticate(self, presented: str | None) -> RegistryToken:
        token = None
        # Only our own random tokens are looked up, never a password someone chose.
        if presented and _TOKEN_RE.fullmatch(presented):
            token = self.session.scalar(select(RegistryToken).where(RegistryToken.token_hash == hash_token(presented)))
        if token is None:
            raise RegistryAuthError()
        set_request_actor(actor_type="registry_token", actor_id=token.id)
        reason = None
        if token.revoked_at is not None:
            reason = "revoked"
        elif token.expires_at is not None and token.expires_at <= datetime.now(UTC):
            reason = "expired"
        elif token.kind == "personal":
            user = self.session.get(User, token.user_id)
            if user is None or not user.is_active:
                reason = "inactive"
            elif self._membership(token) is None:
                reason = "not_a_member"
        if reason is not None:
            self.audit.record(
                organization_id=token.organization_id,
                action="registry_token.use",
                resource_type="registry_token",
                resource_id=token.id,
                outcome=OUTCOME_FAILURE,
                metadata={"reason": reason, "token_prefix": token.token_prefix},
            )
            self.session.commit()
            raise RegistryAuthError()
        return token

    def _membership(self, token: RegistryToken):
        return self.authz.get_active_membership(user_id=token.user_id, organization_id=token.organization_id)

    def _allowed_actions(self, token: RegistryToken) -> set[str]:
        ceiling = {"pull"} if token.access == "pull" else {"pull", "push"}
        if token.kind == "robot":
            return ceiling
        if token.access == "push":
            ceiling = ceiling | {"delete"}
        membership = self._membership(token)
        granted = self.authz.permission_ids_for_role(membership.role_id) if membership is not None else frozenset()
        return {action for action in ceiling if _ACTION_PERMISSIONS[action] in granted}

    def _grant(self, token: RegistryToken, scopes: list[RequestedScope]) -> list[dict[str, Any]]:
        """What the token may do, out of what was asked. Docker expects less access, not an error."""
        if not scopes:
            return []
        organization = self.session.get(Organization, token.organization_id)
        namespace = namespace_for(self.session, organization)
        allowed = self._allowed_actions(token)
        quota = self.settings.registry_org_quota_bytes
        if quota and "push" in allowed and any("push" in scope.actions for scope in scopes):
            if used_bytes(self.session, token.organization_id) >= quota:
                allowed = allowed - {"push"}
        access: list[dict[str, Any]] = []
        for scope in scopes:
            match = _REPOSITORY_RE.fullmatch(scope.repository)
            granted: list[str] = []
            if match is not None and match[1] == namespace and self._within_prefix(token, match[2]):
                granted = [action for action in scope.actions if action in allowed]
            if granted:
                access.append({"type": "repository", "name": scope.repository, "actions": granted})
            refused = [a for a in ("push", "delete") if a in scope.actions and a not in granted]
            if refused:
                self.audit.record(
                    organization_id=token.organization_id,
                    action="image.access",
                    resource_type="image",
                    resource_id=None,
                    outcome=OUTCOME_DENIED,
                    metadata={
                        "repository": scope.repository[:256],
                        "actions": refused,
                        "token_prefix": token.token_prefix,
                    },
                )
        return access

    @staticmethod
    def _within_prefix(token: RegistryToken, path: str) -> bool:
        prefix = token.repository_prefix
        return prefix is None or path == prefix or path.startswith(f"{prefix}/")

    def _touch(self, token: RegistryToken, now: datetime) -> None:
        # Docker asks many times per push; one write a minute is enough.
        self.session.execute(
            update(RegistryToken)
            .where(
                RegistryToken.id == token.id,
                or_(RegistryToken.last_used_at.is_(None), RegistryToken.last_used_at < now - timedelta(minutes=1)),
            )
            .values(last_used_at=now)
            .execution_options(synchronize_session=False)
        )

    # --- organization API ---------------------------------------------------

    def info(self, *, actor: User, organization_id: uuid.UUID) -> RegistryInfoResponse:
        membership = self.authz.require_membership(user_id=actor.id, organization_id=organization_id)
        self.authz.require(membership, "image.read")
        if not self.enabled:
            return RegistryInfoResponse(enabled=False)
        namespace = namespace_for(self.session, self.session.get(Organization, organization_id))
        self.session.commit()
        return RegistryInfoResponse(
            enabled=True,
            host=self.settings.registry_public_host,
            namespace=namespace,
            used_bytes=used_bytes(self.session, organization_id),
            quota_bytes=self.settings.registry_org_quota_bytes or None,
        )

    def list_tokens(self, *, actor: User, organization_id: uuid.UUID) -> list[RegistryTokenResponse]:
        """Your own tokens; people who manage the organization see all of them."""
        membership = self.authz.require_membership(user_id=actor.id, organization_id=organization_id)
        self.authz.require(membership, "image.read")
        statement = select(RegistryToken).where(RegistryToken.organization_id == organization_id)
        if not self.authz.has_permission(membership, "organization.update"):
            statement = statement.where(RegistryToken.user_id == actor.id)
        rows = self.session.scalars(statement.order_by(RegistryToken.created_at.desc()))
        return [RegistryTokenResponse.model_validate(row) for row in rows]

    def create_token(
        self, *, actor: User, organization_id: uuid.UUID, payload: RegistryTokenCreateRequest
    ) -> RegistryTokenCreateResponse:
        self._require_enabled()
        membership = self.authz.require_membership(user_id=actor.id, organization_id=organization_id)
        self.authz.require(membership, "image.push" if payload.access == "push" else "image.read")
        if payload.kind == "robot":
            # A robot keeps working after its creator leaves, so it is an organization setting.
            self.authz.require(membership, "organization.update")
        if payload.expires_at is not None and payload.expires_at <= datetime.now(UTC):
            raise ConflictError("invalid_expiry", "Expiry must be in the future.")
        generated = generate_registry_token()
        row = RegistryToken(
            organization_id=organization_id,
            kind=payload.kind,
            access=payload.access,
            user_id=actor.id if payload.kind == "personal" else None,
            name=payload.name,
            token_hash=generated.token_hash,
            token_prefix=generated.display_prefix,
            repository_prefix=payload.repository_prefix,
            expires_at=payload.expires_at,
            created_by_user_id=actor.id,
        )
        self.session.add(row)
        self.session.flush()
        self.audit.record(
            actor=actor,
            organization_id=organization_id,
            action="registry_token.create",
            resource_type="registry_token",
            resource_id=row.id,
            metadata={
                "name": row.name,
                "kind": row.kind,
                "access": row.access,
                "repository_prefix": row.repository_prefix,
                "token_prefix": row.token_prefix,
            },
        )
        self.session.commit()
        self.session.refresh(row)
        base = RegistryTokenResponse.model_validate(row)
        return RegistryTokenCreateResponse(**base.model_dump(), token=generated.plaintext)

    def revoke_token(self, *, actor: User, organization_id: uuid.UUID, token_id: uuid.UUID) -> RegistryTokenResponse:
        membership = self.authz.require_membership(user_id=actor.id, organization_id=organization_id)
        manages = self.authz.has_permission(membership, "organization.update")
        row = self.session.scalar(
            select(RegistryToken)
            .where(RegistryToken.id == token_id, RegistryToken.organization_id == organization_id)
            .with_for_update()  # two revocations at once are one audit event
        )
        if row is None:
            raise NotFoundError("registry_token_not_found", "Registry token was not found.")
        if row.user_id != actor.id and not manages:
            raise ForbiddenError("insufficient_permission", "You do not have permission to perform this action.")
        if row.revoked_at is None:
            row.revoked_at = datetime.now(UTC)
            self.audit.record(
                actor=actor,
                organization_id=organization_id,
                action="registry_token.revoke",
                resource_type="registry_token",
                resource_id=row.id,
                metadata={"name": row.name, "kind": row.kind, "token_prefix": row.token_prefix},
            )
            self.session.commit()
            self.session.refresh(row)
        return RegistryTokenResponse.model_validate(row)
