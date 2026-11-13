"""Applications, their scoped data-plane credentials, and the policy snapshot."""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.applications.models import Application, ApplicationCredential
from app.applications.schemas import (
    AgentAppCredentialResponse,
    ApplicationCreate,
    ApplicationPermissions,
    ApplicationUpdate,
    CommandCreate,
    CredentialCreate,
    CredentialSecretResponse,
    PolicyApplication,
    PolicyCredential,
    PolicySnapshot,
)
from app.audit.service import AuditRecorder
from app.authorization.service import AuthzService
from app.core.config import Settings, get_settings
from app.core.exceptions import AppError, ConflictError, NotFoundError, ValidationAppError
from app.deployments.software_models import DeviceSoftware
from app.devices.models import Device
from app.devices.tokens import hash_token
from app.identity.models import User
from app.ports.data_plane import DataPlaneApi

APP_TOKEN_PREFIX = "mca_"
# An old agent-minted token stays valid this long after a rotation, so a running
# container can pick up the new token file without an authentication gap.
ROTATION_GRACE = timedelta(minutes=10)


def generate_app_token() -> tuple[str, str, str]:
    """``(plaintext, sha256 hex, display prefix)``; the plaintext is never stored."""
    plaintext = f"{APP_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
    return plaintext, hash_token(plaintext), plaintext[:12]


def application_slug_from_spec(spec: dict[str, Any] | None) -> tuple[str, str] | None:
    """``(application slug, environment)`` a release spec binds its workload to, if any."""
    binding = (spec or {}).get("meteorcloud")
    if not isinstance(binding, dict) or not binding.get("application"):
        return None
    return str(binding["application"]), str(binding.get("environment") or "production")


class ApplicationService:
    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        data_plane: DataPlaneApi | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.authz = AuthzService(session)
        self.audit = AuditRecorder(session)
        self.data_plane = data_plane

    # ---- operator API -------------------------------------------------

    def _authorize(self, actor: User, organization_id: uuid.UUID, permission: str) -> None:
        membership = self.authz.require_membership(user_id=actor.id, organization_id=organization_id)
        self.authz.require(membership, permission)

    def _get(self, organization_id: uuid.UUID, application_id: uuid.UUID) -> Application:
        row = self.session.get(Application, application_id)
        if row is None or row.organization_id != organization_id:
            raise NotFoundError("application_not_found", "Application was not found.")
        return row

    def list_applications(self, *, actor: User, organization_id: uuid.UUID) -> list[Application]:
        self._authorize(actor, organization_id, "application.read")
        return list(
            self.session.scalars(
                select(Application)
                .where(Application.organization_id == organization_id)
                .order_by(Application.slug.asc())
            )
        )

    def get_application(self, *, actor: User, organization_id: uuid.UUID, application_id: uuid.UUID) -> Application:
        self._authorize(actor, organization_id, "application.read")
        return self._get(organization_id, application_id)

    def _check_command_targets(self, organization_id: uuid.UUID, permissions: ApplicationPermissions) -> None:
        if not permissions.send_commands:
            return
        known = set(
            self.session.scalars(
                select(Application.slug).where(
                    Application.organization_id == organization_id,
                    Application.slug.in_(permissions.send_commands),
                )
            )
        )
        missing = sorted(set(permissions.send_commands) - known)
        if missing:
            raise ValidationAppError(
                "unknown_command_target",
                f"send_commands names applications that do not exist: {', '.join(missing)}",
            )

    def create_application(
        self, *, actor: User, organization_id: uuid.UUID, payload: ApplicationCreate
    ) -> Application:
        self._authorize(actor, organization_id, "application.create")
        self._check_command_targets(organization_id, payload.permissions)
        row = Application(
            organization_id=organization_id,
            slug=payload.slug,
            name=payload.name.strip(),
            description=payload.description,
            permissions=payload.permissions.model_dump(),
            created_by_user_id=actor.id,
        )
        self.session.add(row)
        try:
            self.session.flush()
        except IntegrityError as exc:
            self.session.rollback()
            raise ConflictError("application_exists", "An application with this slug already exists.") from exc
        self.audit.record(
            action="application.create",
            resource_type="application",
            actor=actor,
            organization_id=organization_id,
            resource_id=row.id,
            metadata={"slug": row.slug},
        )
        self.session.commit()
        self.session.refresh(row)
        return row

    def update_application(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        application_id: uuid.UUID,
        payload: ApplicationUpdate,
    ) -> Application:
        self._authorize(actor, organization_id, "application.update")
        row = self._get(organization_id, application_id)
        if payload.name is not None:
            row.name = payload.name.strip()
        if "description" in payload.model_fields_set:
            row.description = payload.description
        if payload.enabled is not None:
            row.enabled = payload.enabled
        if payload.permissions is not None:
            self._check_command_targets(organization_id, payload.permissions)
            row.permissions = payload.permissions.model_dump()
        self.audit.record(
            action="application.update",
            resource_type="application",
            actor=actor,
            organization_id=organization_id,
            resource_id=row.id,
            metadata={"fields": sorted(payload.model_fields_set)},
        )
        self.session.commit()
        self.session.refresh(row)
        return row

    def delete_application(self, *, actor: User, organization_id: uuid.UUID, application_id: uuid.UUID) -> None:
        self._authorize(actor, organization_id, "application.delete")
        row = self._get(organization_id, application_id)
        self.audit.record(
            action="application.delete",
            resource_type="application",
            actor=actor,
            organization_id=organization_id,
            resource_id=row.id,
            metadata={"slug": row.slug},
        )
        self.session.delete(row)
        self.session.commit()

    # ---- credentials --------------------------------------------------

    def list_credentials(
        self, *, actor: User, organization_id: uuid.UUID, application_id: uuid.UUID
    ) -> list[ApplicationCredential]:
        self._authorize(actor, organization_id, "application.read")
        self._get(organization_id, application_id)
        return list(
            self.session.scalars(
                select(ApplicationCredential)
                .where(ApplicationCredential.application_id == application_id)
                .order_by(ApplicationCredential.created_at.desc())
            )
        )

    def _secret_response(self, row: ApplicationCredential, token: str) -> CredentialSecretResponse:
        return CredentialSecretResponse(
            id=row.id,
            application_id=row.application_id,
            device_id=row.device_id,
            environment=row.environment,
            name=row.name,
            token_prefix=row.token_prefix,
            expires_at=row.expires_at,
            revoked_at=row.revoked_at,
            workload_key=row.workload_key,
            created_at=row.created_at,
            token=token,
            endpoint=self.settings.data_plane_public_url or None,
        )

    def create_credential(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        application_id: uuid.UUID,
        payload: CredentialCreate,
    ) -> CredentialSecretResponse:
        self._authorize(actor, organization_id, "application.credential")
        application = self._get(organization_id, application_id)
        if payload.device_id is not None:
            device = self.session.get(Device, payload.device_id)
            if device is None or device.organization_id != organization_id:
                raise NotFoundError("device_not_found", "Device was not found.")
        token, token_hash, prefix = generate_app_token()
        row = ApplicationCredential(
            organization_id=organization_id,
            application_id=application.id,
            device_id=payload.device_id,
            environment=payload.environment,
            name=payload.name.strip(),
            token_hash=token_hash,
            token_prefix=prefix,
            expires_at=datetime.now(UTC) + timedelta(days=payload.expires_in_days),
            created_by_user_id=actor.id,
        )
        self.session.add(row)
        self.session.flush()
        self.audit.record(
            action="application.credential.create",
            resource_type="application_credential",
            actor=actor,
            organization_id=organization_id,
            resource_id=row.id,
            metadata={"application": application.slug, "device_id": str(payload.device_id or "")},
        )
        self.session.commit()
        self.session.refresh(row)
        return self._secret_response(row, token)

    def _credential(
        self, organization_id: uuid.UUID, application_id: uuid.UUID, credential_id: uuid.UUID
    ) -> ApplicationCredential:
        row = self.session.get(ApplicationCredential, credential_id)
        if row is None or row.organization_id != organization_id or row.application_id != application_id:
            raise NotFoundError("credential_not_found", "Credential was not found.")
        return row

    def revoke_credential(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        application_id: uuid.UUID,
        credential_id: uuid.UUID,
    ) -> ApplicationCredential:
        self._authorize(actor, organization_id, "application.credential")
        row = self._credential(organization_id, application_id, credential_id)
        if row.revoked_at is None:
            row.revoked_at = datetime.now(UTC)
        self.audit.record(
            action="application.credential.revoke",
            resource_type="application_credential",
            actor=actor,
            organization_id=organization_id,
            resource_id=row.id,
        )
        self.session.commit()
        self.session.refresh(row)
        return row

    def rotate_credential(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        application_id: uuid.UUID,
        credential_id: uuid.UUID,
    ) -> CredentialSecretResponse:
        """New secret, same scope and lifetime; the old secret stops working immediately."""
        self._authorize(actor, organization_id, "application.credential")
        row = self._credential(organization_id, application_id, credential_id)
        if row.revoked_at is not None:
            raise ConflictError("credential_revoked", "A revoked credential cannot be rotated.")
        lifetime = row.expires_at - row.created_at
        token, token_hash, prefix = generate_app_token()
        row.token_hash = token_hash
        row.token_prefix = prefix
        row.expires_at = datetime.now(UTC) + max(lifetime, timedelta(days=1))
        self.audit.record(
            action="application.credential.rotate",
            resource_type="application_credential",
            actor=actor,
            organization_id=organization_id,
            resource_id=row.id,
        )
        self.session.commit()
        self.session.refresh(row)
        return self._secret_response(row, token)

    # ---- device agent -------------------------------------------------

    def mint_for_workload(self, *, device: Device, workload_key: str) -> AgentAppCredentialResponse:
        """Device-scoped token for a workload whose desired release names an application.

        A device can only obtain tokens for applications an operator deployed to it.
        """
        software = self.session.scalar(
            select(DeviceSoftware).where(
                DeviceSoftware.device_id == device.id,
                DeviceSoftware.workload_key == workload_key,
            )
        )
        binding = application_slug_from_spec(software.desired_spec if software is not None else None)
        if binding is None:
            raise NotFoundError(
                "application_binding_not_found",
                "This workload is not bound to a MeteorCloud application.",
            )
        slug, environment = binding
        application = self.session.scalar(
            select(Application).where(
                Application.organization_id == device.organization_id,
                Application.slug == slug,
            )
        )
        if application is None or not application.enabled:
            raise NotFoundError("application_not_found", "Application was not found or is disabled.")

        now = datetime.now(UTC)
        previous = self.session.scalars(
            select(ApplicationCredential).where(
                ApplicationCredential.application_id == application.id,
                ApplicationCredential.device_id == device.id,
                ApplicationCredential.workload_key == workload_key,
                ApplicationCredential.revoked_at.is_(None),
                ApplicationCredential.expires_at > now,
            )
        )
        for old in previous:
            old.expires_at = min(old.expires_at, now + ROTATION_GRACE)
        # The workload was bound to another application before: that access ends now.
        for stale in self.session.scalars(
            select(ApplicationCredential).where(
                ApplicationCredential.device_id == device.id,
                ApplicationCredential.workload_key == workload_key,
                ApplicationCredential.application_id != application.id,
                ApplicationCredential.revoked_at.is_(None),
            )
        ):
            stale.revoked_at = now

        ttl = timedelta(hours=self.settings.app_credential_ttl_hours)
        token, token_hash, prefix = generate_app_token()
        row = ApplicationCredential(
            organization_id=device.organization_id,
            application_id=application.id,
            device_id=device.id,
            environment=environment,
            name=f"{workload_key} on {device.name}"[:120],
            token_hash=token_hash,
            token_prefix=prefix,
            expires_at=now + ttl,
            workload_key=workload_key,
        )
        self.session.add(row)
        self.session.flush()
        self.audit.record(
            action="application.credential.mint",
            resource_type="application_credential",
            organization_id=device.organization_id,
            resource_id=row.id,
            metadata={"application": slug, "device_id": str(device.id), "workload_key": workload_key},
        )
        self.session.commit()
        return AgentAppCredentialResponse(
            token=token,
            credential_id=row.id,
            expires_at=row.expires_at,
            endpoint=self.settings.data_plane_public_url or None,
            application=slug,
            application_id=application.id,
            organization_id=device.organization_id,
            device_id=device.id,
            environment=environment,
            refresh_after=now + ttl / 2,
        )

    # ---- data plane ---------------------------------------------------

    def _still_bound(
        self, credentials: list[ApplicationCredential], slugs: dict[uuid.UUID, str]
    ) -> list[ApplicationCredential]:
        """Drop tokens the agent obtained for a workload that is no longer bound to that application.

        A token issued for a release stops working when the release is removed from the
        device or bound to another application or environment. Credentials an operator
        created by hand have no workload and are kept.
        """
        device_ids = {row.device_id for row in credentials if row.workload_key and row.device_id}
        if not device_ids:
            return credentials
        bound = {
            (device_id, workload_key): application_slug_from_spec(spec)
            for device_id, workload_key, spec in self.session.execute(
                select(DeviceSoftware.device_id, DeviceSoftware.workload_key, DeviceSoftware.desired_spec).where(
                    DeviceSoftware.device_id.in_(device_ids)
                )
            )
        }
        return [
            row
            for row in credentials
            if not row.workload_key
            or bound.get((row.device_id, row.workload_key)) == (slugs[row.application_id], row.environment)
        ]

    def policy_snapshot(self) -> PolicySnapshot:
        """Everything the data plane needs to authenticate and authorize applications offline."""
        now = datetime.now(UTC)
        applications = list(
            self.session.scalars(select(Application).where(Application.enabled.is_(True)).order_by(Application.id))
        )
        app_ids = {row.id for row in applications}
        credentials = [
            row
            for row in self.session.execute(
                select(ApplicationCredential)
                .outerjoin(Device, Device.id == ApplicationCredential.device_id)
                .where(
                    ApplicationCredential.revoked_at.is_(None),
                    ApplicationCredential.expires_at > now,
                    # A disabled device takes its applications' credentials with it.
                    (ApplicationCredential.device_id.is_(None)) | (Device.is_enabled.is_(True)),
                )
                .order_by(ApplicationCredential.id)
            ).scalars()
            if row.application_id in app_ids
        ]
        credentials = self._still_bound(credentials, {row.id: row.slug for row in applications})
        apps = [
            PolicyApplication(
                id=row.id,
                organization_id=row.organization_id,
                slug=row.slug,
                permissions=ApplicationPermissions.model_validate(row.permissions or {}).model_dump(),
            )
            for row in applications
        ]
        creds = [
            PolicyCredential(
                id=row.id,
                organization_id=row.organization_id,
                application_id=row.application_id,
                device_id=row.device_id,
                environment=row.environment,
                token_hash=row.token_hash,
                expires_at=row.expires_at,
            )
            for row in credentials
        ]
        material = json.dumps(
            {
                "applications": [item.model_dump(mode="json") for item in apps],
                "credentials": [item.model_dump(mode="json") for item in creds],
            },
            sort_keys=True,
        )
        return PolicySnapshot(
            version=hashlib.sha256(material.encode("utf-8")).hexdigest()[:32],
            generated_at=now,
            applications=apps,
            credentials=creds,
        )

    def _require_data_plane(self) -> DataPlaneApi:
        if self.data_plane is None:
            raise AppError(
                "data_plane_not_configured",
                "The data plane is not configured (DATA_PLANE_API_URL).",
                status_code=503,
            )
        return self.data_plane

    def send_command(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        application_id: uuid.UUID,
        payload: CommandCreate,
    ) -> dict[str, Any]:
        """The control plane authorizes; the data plane stores and delivers."""
        self._authorize(actor, organization_id, "application.command")
        application = self._get(organization_id, application_id)
        if not application.enabled:
            raise ConflictError("application_disabled", "This application is disabled.")
        if payload.device_id is not None:
            device = self.session.get(Device, payload.device_id)
            if device is None or device.organization_id != organization_id:
                raise NotFoundError("device_not_found", "Device was not found.")
        data_plane = self._require_data_plane()
        # The audit record is committed before the command leaves: a command is never
        # sent without one. The key makes a retry of this request the same command.
        idempotency_key = payload.idempotency_key or uuid.uuid4().hex
        self.audit.record(
            action="application.command.send",
            resource_type="application",
            actor=actor,
            organization_id=organization_id,
            resource_id=application.id,
            metadata={
                "name": payload.name,
                "idempotency_key": idempotency_key,
                "device_id": str(payload.device_id) if payload.device_id else None,
            },
        )
        self.session.commit()
        return data_plane.create_command(
            {
                "organization_id": str(organization_id),
                "application_id": str(application.id),
                "device_id": str(payload.device_id) if payload.device_id else None,
                "name": payload.name,
                "payload": payload.payload,
                "timeout_seconds": payload.timeout_seconds,
                "expires_in_seconds": payload.expires_in_seconds,
                "max_attempts": payload.max_attempts,
                "idempotency_key": idempotency_key,
                "issued_by": f"user:{actor.id}",
            }
        )

    def get_command(
        self, *, actor: User, organization_id: uuid.UUID, application_id: uuid.UUID, command_id: uuid.UUID
    ) -> dict[str, Any]:
        self._authorize(actor, organization_id, "application.read")
        self._get(organization_id, application_id)
        result = self._require_data_plane().get_command(organization_id=organization_id, command_id=command_id)
        if result is None or result.get("application_id") != str(application_id):
            raise NotFoundError("command_not_found", "Command was not found.")
        return result
