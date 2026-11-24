"""Rules (versioned definitions), alerts and their history, silences and notification integrations.

The engine in the data plane evaluates; this service decides who may change a rule, keeps
every version, owns the alert records people act on, and serves the engine its snapshot.
"""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import array
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.audit.service import AuditRecorder
from app.authorization.service import AuthzService
from app.core.config import Settings, get_settings
from app.core.exceptions import AppError, ConflictError, ForbiddenError, NotFoundError, ValidationAppError
from app.core.secretbox import SecretBoxError, seal, unseal
from app.device_actions.service import DeviceActionService
from app.devices.models import Device, DeviceGroup, DeviceType
from app.devices.status import offline_cutoff
from app.identity.models import User
from app.rules.definition import (
    FUNCTIONS,
    Problem,
    RuleDefinitionError,
    condition_hash,
    severity_of,
    validate_definition,
)
from app.rules.engine_client import RulesEngineClient
from app.rules.metrics import KNOWN_METRICS, compare, numeric_metrics, spec_for
from app.rules.models import (
    ACTIVE_ALERT_STATUSES,
    ALERT_ACKNOWLEDGED,
    ALERT_FIRING,
    ALERT_RESOLVED,
    Alert,
    AlertEvent,
    AlertSilence,
    NotificationIntegration,
    Rule,
    RuleVersion,
)
from app.rules.schemas import (
    AlertSummary,
    AlertTransition,
    AlertTransitionResult,
    ChannelCapabilities,
    CommandResult,
    DeliveryRequest,
    DeliveryResponse,
    DeliveryTestResult,
    DevicePreview,
    EngineAction,
    IntegrationCreate,
    IntegrationResponse,
    IntegrationUpdate,
    LiveAlert,
    LiveDevice,
    LiveQuery,
    LiveResult,
    MatchingDevice,
    MetricResponse,
    ProblemResponse,
    RulesSnapshot,
    RuleTestRequest,
    SilenceCreate,
    SnapshotDevice,
    SnapshotRule,
    ValidationResult,
    check_integration,
)
from app.tenancy.models import Organization

# Device commands a rule may send, and the permission its author must hold. The
# control plane re-checks the author's current access when the command is delivered.
COMMAND_PERMISSIONS = {"reboot": "device.reboot", "shutdown": "device.reboot"}
PREVIEW_LIMIT = 200


def _as_uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def scope_matches(scope: dict[str, Any], organization_id: uuid.UUID | None, device: Device) -> bool:
    """Python twin of engine.Rule.Matches, for previews and the API (the engine re-checks)."""
    kind = scope.get("type")
    if kind != "platform" and device.organization_id != organization_id:
        return False
    if kind == "device" and str(device.id) not in {str(d) for d in scope.get("device_ids") or []}:
        return False
    if kind == "group" and str(device.device_group_id) != str(scope.get("group_id")):
        return False
    if kind == "device_type" and str(device.device_type_id) != str(scope.get("device_type_id")):
        return False
    labels = device.labels or {}
    return all(str(labels.get(k)) == str(v) and k in labels for k, v in (scope.get("labels") or {}).items())


def _integration_view(row: NotificationIntegration) -> IntegrationResponse:
    return IntegrationResponse(
        id=row.id,
        organization_id=row.organization_id,
        kind=row.kind,
        name=row.name,
        url=row.url,
        recipients=list((row.config or {}).get("recipients") or []),
        chat_id=(row.config or {}).get("chat_id"),
        enabled=row.enabled,
        has_secret=row.secret_sealed is not None,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _integration_config(kind: str, recipients: list[str] | None, chat_id: str | None) -> dict[str, Any]:
    if kind == "email":
        return {"recipients": list(dict.fromkeys(recipients or []))}
    if kind == "telegram":
        return {"chat_id": chat_id}
    return {}


class RuleService:
    def __init__(
        self, session: Session, *, settings: Settings | None = None, engine: RulesEngineClient | None = None
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.engine = engine or RulesEngineClient(self.settings)
        self.authz = AuthzService(session)
        self.audit = AuditRecorder(session)

    # ---- helpers --------------------------------------------------------

    def _authorize(self, actor: User, organization_id: uuid.UUID, permission: str):
        membership = self.authz.require_membership(user_id=actor.id, organization_id=organization_id)
        self.authz.require(membership, permission)
        return membership

    def _rule(self, organization_id: uuid.UUID, rule_id: uuid.UUID) -> Rule:
        row = self.session.get(Rule, rule_id)
        if row is None or row.organization_id != organization_id or row.deleted_at is not None:
            raise NotFoundError("rule_not_found", "Rule was not found.")
        return row

    def _validate(self, organization_id: uuid.UUID, definition: dict[str, Any]) -> list[Problem]:
        """Schema problems first; only a well-formed rule has its references checked."""
        try:
            validate_definition(definition, allow_platform=False)
        except RuleDefinitionError as exc:
            return exc.problems
        problems: list[Problem] = []
        scope = definition["scope"]
        kind = scope["type"]
        if kind == "device":
            wanted = {str(d) for d in scope["device_ids"]}
            ids = {u for u in (_as_uuid(d) for d in wanted) if u is not None}
            found = {
                str(d)
                for d in self.session.scalars(
                    select(Device.id).where(Device.organization_id == organization_id, Device.id.in_(ids))
                )
            }
            for missing in sorted(wanted - found):
                problems.append(Problem("scope.device_ids", f"device {missing} is not in this organization"))
        elif kind == "group":
            group_id = _as_uuid(scope["group_id"])
            group = self.session.get(DeviceGroup, group_id) if group_id else None
            if group is None or group.organization_id != organization_id:
                problems.append(Problem("scope.group_id", "device group is not in this organization"))
        elif kind == "device_type":
            type_id = _as_uuid(scope["device_type_id"])
            device_type = self.session.get(DeviceType, type_id) if type_id else None
            if device_type is None or device_type.organization_id != organization_id:
                problems.append(Problem("scope.device_type_id", "device type is not in this organization"))
        for index, action in enumerate(definition.get("actions") or []):
            if action.get("type") == "command" and action.get("command") not in COMMAND_PERMISSIONS:
                problems.append(
                    Problem(f"actions[{index}].command", f"must be one of: {', '.join(sorted(COMMAND_PERMISSIONS))}")
                )
            integration_id = action.get("integration_id")
            if integration_id:
                ref = _as_uuid(integration_id)
                row = self.session.get(NotificationIntegration, ref) if ref else None
                if row is None or row.organization_id != organization_id:
                    problems.append(
                        Problem(f"actions[{index}].integration_id", "integration is not in this organization")
                    )
        return problems

    def _require_valid(self, organization_id: uuid.UUID, definition: dict[str, Any]) -> None:
        problems = self._validate(organization_id, definition)
        if problems:
            raise ValidationAppError("invalid_rule", str(RuleDefinitionError(problems)))

    def _require_action_rights(self, membership, definition: dict[str, Any]) -> None:
        for action in definition.get("actions") or []:
            if action.get("type") != "command":
                continue
            permission = COMMAND_PERMISSIONS[action["command"]]
            if not self.authz.has_permission(membership, permission):
                raise ForbiddenError(
                    "insufficient_permission",
                    f"A rule that sends the {action.get('command')!r} command needs the {permission} permission.",
                )

    # ---- rules ----------------------------------------------------------

    def validate(self, *, actor: User, organization_id: uuid.UUID, definition: Any) -> ValidationResult:
        self._authorize(actor, organization_id, "rule.read")
        if not isinstance(definition, dict):
            return ValidationResult(valid=False, problems=[ProblemResponse(path="", message="must be an object")])
        problems = self._validate(organization_id, definition)
        return ValidationResult(
            valid=not problems,
            problems=[ProblemResponse(path=p.path, message=p.message) for p in problems],
            condition_hash=None if problems else condition_hash(definition),
        )

    def preview_devices(self, *, actor: User, organization_id: uuid.UUID, definition: Any) -> DevicePreview:
        self._authorize(actor, organization_id, "rule.read")
        scope = definition.get("scope") if isinstance(definition, dict) else None
        if not isinstance(scope, dict) or scope.get("type") == "platform":
            raise ValidationAppError("invalid_scope", "definition.scope must be an organization scope object.")
        devices = self.session.scalars(
            select(Device)
            .where(Device.organization_id == organization_id, Device.is_enabled.is_(True))
            .order_by(Device.name, Device.id)
        )
        matched = [d for d in devices if scope_matches(scope, organization_id, d)]
        return DevicePreview(
            total=len(matched),
            devices=[
                MatchingDevice(id=d.id, name=d.name, device_group_id=d.device_group_id, device_type_id=d.device_type_id)
                for d in matched[:PREVIEW_LIMIT]
            ],
        )

    def list_rules(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        enabled: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Rule]:
        self._authorize(actor, organization_id, "rule.read")
        statement = select(Rule).where(Rule.organization_id == organization_id, Rule.deleted_at.is_(None))
        if enabled is not None:
            statement = statement.where(Rule.enabled.is_(enabled))
        return list(self.session.scalars(statement.order_by(Rule.name, Rule.id).limit(limit).offset(offset)))

    def get_rule(self, *, actor: User, organization_id: uuid.UUID, rule_id: uuid.UUID) -> Rule:
        self._authorize(actor, organization_id, "rule.read")
        return self._rule(organization_id, rule_id)

    def definition_of(self, rule: Rule, version: int | None = None) -> dict[str, Any]:
        row = self.session.scalar(
            select(RuleVersion).where(
                RuleVersion.rule_id == rule.id, RuleVersion.version == (version or rule.current_version)
            )
        )
        return row.definition if row is not None else {}

    def list_versions(self, *, actor: User, organization_id: uuid.UUID, rule_id: uuid.UUID) -> list[RuleVersion]:
        self._authorize(actor, organization_id, "rule.read")
        rule = self._rule(organization_id, rule_id)
        return list(
            self.session.scalars(
                select(RuleVersion).where(RuleVersion.rule_id == rule.id).order_by(RuleVersion.version.desc())
            )
        )

    def create_rule(self, *, actor: User, organization_id: uuid.UUID, definition: dict[str, Any]) -> Rule:
        membership = self._authorize(actor, organization_id, "rule.create")
        self._require_valid(organization_id, definition)
        self._require_action_rights(membership, definition)
        rule = Rule(
            organization_id=organization_id,
            name=definition["name"],
            enabled=definition.get("enabled", True),
            current_version=1,
            scope_type=definition["scope"]["type"],
            severity=severity_of(definition),
            created_by_user_id=actor.id,
        )
        self.session.add(rule)
        self.session.flush()
        self.session.add(RuleVersion(rule_id=rule.id, version=1, definition=definition, created_by_user_id=actor.id))
        self.audit.record(
            action="rule.create",
            resource_type="rule",
            actor=actor,
            organization_id=organization_id,
            resource_id=rule.id,
            metadata={"name": rule.name, "version": 1, "condition_hash": condition_hash(definition)},
        )
        self.session.commit()
        return rule

    def update_rule(
        self, *, actor: User, organization_id: uuid.UUID, rule_id: uuid.UUID, definition: dict[str, Any]
    ) -> Rule:
        membership = self._authorize(actor, organization_id, "rule.update")
        rule = self._rule(organization_id, rule_id)
        self._require_valid(organization_id, definition)
        self._require_action_rights(membership, definition)
        previous = self.definition_of(rule)
        version = rule.current_version + 1
        self.session.add(
            RuleVersion(rule_id=rule.id, version=version, definition=definition, created_by_user_id=actor.id)
        )
        rule.current_version = version
        rule.name = definition["name"]
        rule.enabled = definition.get("enabled", True)
        rule.scope_type = definition["scope"]["type"]
        rule.severity = severity_of(definition)
        try:
            self.session.flush()
        except IntegrityError as exc:
            self.session.rollback()
            raise ConflictError("rule_version_conflict", "The rule was changed meanwhile; reload and retry.") from exc
        self.audit.record(
            action="rule.update",
            resource_type="rule",
            actor=actor,
            organization_id=organization_id,
            resource_id=rule.id,
            metadata={
                "version": version,
                "state_reset": condition_hash(previous) != condition_hash(definition),
            },
        )
        self.session.commit()
        return rule

    def set_enabled(self, *, actor: User, organization_id: uuid.UUID, rule_id: uuid.UUID, enabled: bool) -> Rule:
        self._authorize(actor, organization_id, "rule.update")
        rule = self._rule(organization_id, rule_id)
        if rule.enabled != enabled:
            rule.enabled = enabled
            self.audit.record(
                action="rule.enable" if enabled else "rule.disable",
                resource_type="rule",
                actor=actor,
                organization_id=organization_id,
                resource_id=rule.id,
            )
            self.session.commit()
        return rule

    def delete_rule(self, *, actor: User, organization_id: uuid.UUID, rule_id: uuid.UUID) -> None:
        """Soft delete: the rule leaves the snapshot (its alerts resolve as rule_removed); history stays."""
        self._authorize(actor, organization_id, "rule.delete")
        rule = self._rule(organization_id, rule_id)
        rule.deleted_at = datetime.now(UTC)
        rule.enabled = False
        self.audit.record(
            action="rule.delete",
            resource_type="rule",
            actor=actor,
            organization_id=organization_id,
            resource_id=rule.id,
            metadata={"name": rule.name},
        )
        self.session.commit()

    # ---- alerts ---------------------------------------------------------

    def list_alerts(
        self,
        *,
        actor: User,
        organization_id: uuid.UUID,
        status: str | None = None,
        active: bool | None = None,
        rule_id: uuid.UUID | None = None,
        device_id: uuid.UUID | None = None,
        severity: str | None = None,
        search: str = "",
        metric: str = "",
        before: datetime | None = None,
        before_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> list[tuple[Alert, str]]:
        membership = self._authorize(actor, organization_id, "alert.read")
        statement = self._visible(
            membership,
            select(Alert, Device.name)
            .join(Device, Device.id == Alert.device_id)
            .where(Alert.organization_id == organization_id),
        )
        if search.strip():
            statement = statement.where(
                or_(
                    Device.name.icontains(search.strip(), autoescape=True),
                    Alert.rule_name.icontains(search.strip(), autoescape=True),
                )
            )
        if metric:
            # Values are keyed by field, or by function(field) for aggregates.
            functions = [f"{fn}({metric})" for fn in FUNCTIONS]
            statement = statement.where(Alert.trigger_values.has_any(array([metric, *functions])))
        if status:
            statement = statement.where(Alert.status == status)
        if active is not None:
            statement = statement.where(
                Alert.status.in_(ACTIVE_ALERT_STATUSES) if active else Alert.status == ALERT_RESOLVED
            )
        if rule_id:
            statement = statement.where(Alert.rule_id == rule_id)
        if device_id:
            statement = statement.where(Alert.device_id == device_id)
        if severity:
            statement = statement.where(Alert.severity == severity)
        if before is not None:
            if before_id is not None:
                statement = statement.where(
                    or_(Alert.fired_at < before, and_(Alert.fired_at == before, Alert.id < before_id))
                )
            else:
                statement = statement.where(Alert.fired_at < before)
        return [
            (alert, name)
            for alert, name in self.session.execute(
                statement.order_by(Alert.fired_at.desc(), Alert.id.desc()).limit(limit)
            )
        ]

    def alert_summary(self, *, actor: User, organization_id: uuid.UUID) -> AlertSummary:
        membership = self._authorize(actor, organization_id, "alert.read")
        rows = self.session.execute(
            self._visible(membership, select(Alert.rule_id, Alert.device_id, Alert.severity, Alert.status))
            .join(Device, Device.id == Alert.device_id)
            .where(Alert.organization_id == organization_id, Alert.status.in_(ACTIVE_ALERT_STATUSES))
        ).all()
        return AlertSummary(
            firing=sum(r.status == ALERT_FIRING for r in rows),
            acknowledged=sum(r.status == ALERT_ACKNOWLEDGED for r in rows),
            devices=len({r.device_id for r in rows}),
            by_severity=dict(Counter(r.severity for r in rows)),
            by_rule=dict(Counter(r.rule_id for r in rows)),
        )

    def _visible(self, membership, statement):
        """Limit a statement that joins devices to the member's device groups."""
        scope = self.authz.scope_for(membership)
        if scope.mode == "device_groups":
            statement = statement.where(Device.device_group_id.in_(scope.device_group_ids))
        return statement

    def _alert(self, organization_id: uuid.UUID, alert_id: uuid.UUID) -> Alert:
        row = self.session.get(Alert, alert_id)
        if row is None or row.organization_id != organization_id:
            raise NotFoundError("alert_not_found", "Alert was not found.")
        return row

    def get_alert(
        self, *, actor: User, organization_id: uuid.UUID, alert_id: uuid.UUID
    ) -> tuple[Alert, str, list[AlertEvent]]:
        membership = self._authorize(actor, organization_id, "alert.read")
        alert = self._alert(organization_id, alert_id)
        name = self.session.scalar(self._visible(membership, select(Device.name).where(Device.id == alert.device_id)))
        if name is None:
            raise NotFoundError("alert_not_found", "Alert was not found.")
        events = list(
            self.session.scalars(
                select(AlertEvent).where(AlertEvent.alert_id == alert.id).order_by(AlertEvent.at, AlertEvent.id)
            )
        )
        return alert, name, events

    def acknowledge(self, *, actor: User, organization_id: uuid.UUID, alert_id: uuid.UUID) -> Alert:
        self._authorize(actor, organization_id, "alert.acknowledge")
        alert = self._alert(organization_id, alert_id)
        if alert.status == ALERT_ACKNOWLEDGED:
            return alert
        if alert.status == ALERT_RESOLVED:
            raise ConflictError("alert_resolved", "The alert is already resolved.")
        now = datetime.now(UTC)
        alert.status = ALERT_ACKNOWLEDGED
        alert.acknowledged_at = now
        alert.acknowledged_by_user_id = actor.id
        self.session.add(
            AlertEvent(alert_id=alert.id, transition="acknowledged", actor_type="user", actor_id=str(actor.id), at=now)
        )
        self.audit.record(
            action="alert.acknowledge",
            resource_type="alert",
            actor=actor,
            organization_id=organization_id,
            resource_id=alert.id,
            metadata={"rule_id": str(alert.rule_id), "device_id": str(alert.device_id)},
        )
        self.session.commit()
        return alert

    # ---- silences -------------------------------------------------------

    def _active_silences(self, organization_id: uuid.UUID, at: datetime) -> list[AlertSilence]:
        return list(
            self.session.scalars(
                select(AlertSilence).where(
                    AlertSilence.organization_id == organization_id,
                    AlertSilence.starts_at <= at,
                    AlertSilence.ends_at > at,
                )
            )
        )

    @staticmethod
    def _silenced(silences: list[AlertSilence], rule_id: uuid.UUID, device_id: uuid.UUID) -> bool:
        return any(
            (s.rule_id is None or s.rule_id == rule_id) and (s.device_id is None or s.device_id == device_id)
            for s in silences
        )

    def _refresh_silenced(self, organization_id: uuid.UUID) -> None:
        silences = self._active_silences(organization_id, datetime.now(UTC))
        for alert in self.session.scalars(
            select(Alert).where(Alert.organization_id == organization_id, Alert.status.in_(ACTIVE_ALERT_STATUSES))
        ):
            alert.silenced = self._silenced(silences, alert.rule_id, alert.device_id)

    def list_silences(
        self, *, actor: User, organization_id: uuid.UUID, include_expired: bool = False
    ) -> list[AlertSilence]:
        self._authorize(actor, organization_id, "alert.read")
        statement = select(AlertSilence).where(AlertSilence.organization_id == organization_id)
        if not include_expired:
            statement = statement.where(AlertSilence.ends_at > datetime.now(UTC))
        return list(self.session.scalars(statement.order_by(AlertSilence.ends_at.desc()).limit(200)))

    def create_silence(self, *, actor: User, organization_id: uuid.UUID, payload: SilenceCreate) -> AlertSilence:
        self._authorize(actor, organization_id, "alert.silence")
        if payload.rule_id is not None:
            self._rule(organization_id, payload.rule_id)
        if payload.device_id is not None:
            device = self.session.get(Device, payload.device_id)
            if device is None or device.organization_id != organization_id:
                raise NotFoundError("device_not_found", "Device was not found.")
        now = datetime.now(UTC)
        starts_at = payload.starts_at or now
        if payload.ends_at <= max(starts_at, now):
            raise ValidationAppError("invalid_silence", "ends_at must be in the future and after starts_at.")
        row = AlertSilence(
            organization_id=organization_id,
            rule_id=payload.rule_id,
            device_id=payload.device_id,
            comment=payload.comment,
            starts_at=starts_at,
            ends_at=payload.ends_at,
            created_by_user_id=actor.id,
        )
        self.session.add(row)
        self.session.flush()
        self._refresh_silenced(organization_id)
        self.audit.record(
            action="alert.silence",
            resource_type="alert_silence",
            actor=actor,
            organization_id=organization_id,
            resource_id=row.id,
            metadata={
                "rule_id": str(payload.rule_id) if payload.rule_id else None,
                "device_id": str(payload.device_id) if payload.device_id else None,
                "ends_at": payload.ends_at.isoformat(),
            },
        )
        self.session.commit()
        return row

    def delete_silence(self, *, actor: User, organization_id: uuid.UUID, silence_id: uuid.UUID) -> None:
        self._authorize(actor, organization_id, "alert.silence")
        row = self.session.get(AlertSilence, silence_id)
        if row is None or row.organization_id != organization_id:
            raise NotFoundError("silence_not_found", "Silence was not found.")
        self.session.delete(row)
        self.session.flush()
        self._refresh_silenced(organization_id)
        self.audit.record(
            action="alert.unsilence",
            resource_type="alert_silence",
            actor=actor,
            organization_id=organization_id,
            resource_id=silence_id,
        )
        self.session.commit()

    # ---- notification integrations -------------------------------------

    def list_integrations(self, *, actor: User, organization_id: uuid.UUID) -> list[IntegrationResponse]:
        self._authorize(actor, organization_id, "rule.read")
        rows = self.session.scalars(
            select(NotificationIntegration)
            .where(NotificationIntegration.organization_id == organization_id)
            .order_by(NotificationIntegration.name)
        )
        return [_integration_view(row) for row in rows]

    def _seal(self, row: NotificationIntegration, secret: str | None) -> None:
        row.secret_sealed = seal(self.settings, secret, context=f"integration:{row.id}") if secret else None

    def _unseal(self, row: NotificationIntegration) -> str | None:
        if row.secret_sealed is None:
            return None
        try:
            return unseal(self.settings, row.secret_sealed, context=f"integration:{row.id}")
        except SecretBoxError as exc:
            raise ConflictError("integration_secret_unreadable", "The integration secret cannot be read.") from exc

    def channel_capabilities(self, *, actor: User, organization_id: uuid.UUID) -> ChannelCapabilities:
        self._authorize(actor, organization_id, "rule.read")
        try:
            channels = self.engine.engine().get("channels") or {}
        except AppError:
            return ChannelCapabilities(engine=False, email=False)
        return ChannelCapabilities(engine=True, email=bool(channels.get("email")))

    def test_integration(
        self, *, actor: User, organization_id: uuid.UUID, integration_id: uuid.UUID
    ) -> DeliveryTestResult:
        """Send a sample alert through an integration now, even when it is switched off."""
        self._authorize(actor, organization_id, "integration.manage")
        row = self._integration(organization_id, integration_id)
        organization = self.session.get(Organization, organization_id)
        result = self.engine.test_delivery(
            {
                "organization_id": str(organization_id),
                "delivery": {
                    "url": row.url,
                    "kind": row.kind,
                    "secret": self._unseal(row) or "",
                    "enabled": True,
                    "config": row.config or {},
                    "device_name": "Example device",
                    "organization_name": organization.name if organization else "",
                },
            }
        )
        self.audit.record(
            action="integration.test",
            resource_type="notification_integration",
            actor=actor,
            organization_id=organization_id,
            resource_id=row.id,
            outcome="success" if result.get("delivered") else "failure",
        )
        self.session.commit()
        return DeliveryTestResult(delivered=bool(result.get("delivered")), error=result.get("error"))

    # ---- metrics and live device state ----------------------------------

    def _visible_devices(self, actor: User, organization_id: uuid.UUID):
        membership = self._authorize_devices(actor, organization_id)
        return self.session.execute(
            self._visible(
                membership,
                select(
                    Device.id,
                    Device.name,
                    Device.device_group_id,
                    Device.device_type_id,
                    Device.last_seen_at,
                    Device.mqtt_metrics,
                    Device.mqtt_metrics_at,
                ).where(Device.organization_id == organization_id, Device.is_enabled.is_(True)),
            ).order_by(Device.name, Device.id)
        ).all()

    def _authorize_devices(self, actor: User, organization_id: uuid.UUID):
        membership = self.authz.require_membership(user_id=actor.id, organization_id=organization_id)
        self.authz.require_role_permission(membership, "device.read")
        return membership

    def list_metrics(self, *, actor: User, organization_id: uuid.UUID, search: str = "") -> list[MetricResponse]:
        """Every known metric plus whatever the organization's devices report."""
        seen: dict[str, list[float]] = {}
        for device in self._visible_devices(actor, organization_id):
            for field, value in numeric_metrics(device.mqtt_metrics).items():
                seen.setdefault(field, []).append(value)
        known = [m.field for m in KNOWN_METRICS]
        others = sorted(set(seen) - set(known))
        words = search.lower().split()
        out = []
        for field in [*known, *others]:
            spec = spec_for(field)
            text = f"{field} {spec.label} {spec.group} {spec.description}".lower()
            if not all(word in text for word in words):
                continue
            values = seen.get(field, [])
            out.append(
                MetricResponse(
                    field=field,
                    label=spec.label,
                    unit=spec.unit,
                    group=spec.group,
                    description=spec.description,
                    suggested_operator=spec.operator,
                    suggested_value=spec.threshold,
                    devices=len(values),
                    min=min(values, default=None),
                    max=max(values, default=None),
                )
            )
        return out[:1000]

    def live_devices(self, *, actor: User, organization_id: uuid.UUID, query: LiveQuery) -> LiveResult:
        membership = self._authorize_devices(actor, organization_id)
        rows = self._visible_devices(actor, organization_id)
        now = datetime.now(UTC)
        online_after = offline_cutoff(offline_threshold_seconds=self.settings.device_offline_threshold_seconds)
        fresh_after = now - timedelta(minutes=query.fresh_minutes)
        alerts: dict[uuid.UUID, list[LiveAlert]] = {}
        if self.authz.has_permission(membership, "alert.read"):
            for alert in self.session.scalars(
                select(Alert)
                .where(Alert.organization_id == organization_id, Alert.status.in_(ACTIVE_ALERT_STATUSES))
                .order_by(Alert.fired_at.desc())
            ):
                alerts.setdefault(alert.device_id, []).append(
                    LiveAlert(
                        id=alert.id,
                        rule_id=alert.rule_id,
                        rule_name=alert.rule_name,
                        severity=alert.severity,
                        status=alert.status,
                    )
                )
        needle = query.search.strip().lower()
        matched: list[LiveDevice] = []
        for row in rows:
            online = row.last_seen_at is not None and row.last_seen_at >= online_after
            fresh = row.mqtt_metrics_at is not None and row.mqtt_metrics_at >= fresh_after
            active = alerts.get(row.id, [])
            if (
                (needle and needle not in row.name.lower())
                or (query.state != "any" and online != (query.state == "online"))
                or (query.alerts != "any" and bool(active) != (query.alerts == "active"))
                or (query.group_id and row.device_group_id != query.group_id)
                or (query.device_type_id and row.device_type_id != query.device_type_id)
            ):
                continue
            metrics = numeric_metrics(row.mqtt_metrics)
            if query.conditions:
                results = [
                    fresh and c.field in metrics and compare(metrics[c.field], c.operator, c.value, c.max)
                    for c in query.conditions
                ]
                if not (all(results) if query.match == "all" else any(results)):
                    continue
            matched.append(
                LiveDevice(
                    id=row.id,
                    name=row.name,
                    device_group_id=row.device_group_id,
                    device_type_id=row.device_type_id,
                    online=online,
                    metrics_at=row.mqtt_metrics_at,
                    fresh=fresh,
                    metrics=metrics,
                    alerts=active,
                )
            )
        # Devices with alerts first, then by name.
        matched.sort(key=lambda d: (not d.alerts, d.name.lower()))
        return LiveResult(
            total=len(rows),
            matching=len(matched),
            devices=matched[query.offset : query.offset + query.limit],
        )

    def create_integration(
        self, *, actor: User, organization_id: uuid.UUID, payload: IntegrationCreate
    ) -> IntegrationResponse:
        self._authorize(actor, organization_id, "integration.manage")
        row = NotificationIntegration(
            id=uuid.uuid4(),
            organization_id=organization_id,
            kind=payload.kind,
            name=payload.name.strip(),
            url=str(payload.url) if payload.url else "",
            config=_integration_config(payload.kind, payload.recipients, payload.chat_id),
            enabled=payload.enabled,
            created_by_user_id=actor.id,
        )
        self._seal(row, payload.secret)
        self.session.add(row)
        try:
            self.session.flush()
        except IntegrityError as exc:
            self.session.rollback()
            raise ConflictError("integration_exists", "An integration with this name already exists.") from exc
        self.audit.record(
            action="integration.create",
            resource_type="notification_integration",
            actor=actor,
            organization_id=organization_id,
            resource_id=row.id,
            metadata={"kind": row.kind, "name": row.name},
        )
        self.session.commit()
        return _integration_view(row)

    def _integration(self, organization_id: uuid.UUID, integration_id: uuid.UUID) -> NotificationIntegration:
        row = self.session.get(NotificationIntegration, integration_id)
        if row is None or row.organization_id != organization_id:
            raise NotFoundError("integration_not_found", "Integration was not found.")
        return row

    def update_integration(
        self, *, actor: User, organization_id: uuid.UUID, integration_id: uuid.UUID, payload: IntegrationUpdate
    ) -> IntegrationResponse:
        self._authorize(actor, organization_id, "integration.manage")
        row = self._integration(organization_id, integration_id)
        changed = sorted(payload.model_fields_set - {"secret"})
        if payload.name is not None:
            row.name = payload.name.strip()
        sent = payload.model_fields_set
        config = row.config or {}
        url = str(payload.url) if payload.url is not None else row.url
        recipients = payload.recipients if payload.recipients is not None else config.get("recipients")
        chat_id = payload.chat_id if payload.chat_id is not None else config.get("chat_id")
        if "secret" in sent:
            secret = payload.secret or None
        else:
            secret = self._unseal(row) if sent & {"url", "recipients", "chat_id"} else None
        if sent & {"url", "recipients", "chat_id", "secret"}:
            try:
                check_integration(row.kind, url=url, recipients=recipients, chat_id=chat_id, secret=secret)
            except ValueError as exc:
                raise ValidationAppError("invalid_integration", f"{exc}.") from exc
            row.url = url
            row.config = _integration_config(row.kind, recipients, chat_id)
        if payload.enabled is not None:
            row.enabled = payload.enabled
        if "secret" in sent:
            self._seal(row, payload.secret or None)
            changed.append("secret")
        try:
            self.session.flush()
        except IntegrityError as exc:
            self.session.rollback()
            raise ConflictError("integration_exists", "An integration with this name already exists.") from exc
        self.audit.record(
            action="integration.update",
            resource_type="notification_integration",
            actor=actor,
            organization_id=organization_id,
            resource_id=row.id,
            metadata={"fields": changed},
        )
        self.session.commit()
        return _integration_view(row)

    def delete_integration(self, *, actor: User, organization_id: uuid.UUID, integration_id: uuid.UUID) -> None:
        self._authorize(actor, organization_id, "integration.manage")
        row = self._integration(organization_id, integration_id)
        key = str(row.id)
        for rule in self.session.scalars(
            select(Rule).where(Rule.organization_id == organization_id, Rule.deleted_at.is_(None))
        ):
            if any(a.get("integration_id") == key for a in self.definition_of(rule).get("actions") or []):
                raise ConflictError("integration_in_use", f"Rule {rule.name!r} still uses this integration.")
        self.session.delete(row)
        self.audit.record(
            action="integration.delete",
            resource_type="notification_integration",
            actor=actor,
            organization_id=organization_id,
            resource_id=key,
        )
        self.session.commit()

    # ---- engine-facing --------------------------------------------------

    def snapshot(self) -> RulesSnapshot:
        """Every enabled rule at its current version, and the device index. Version is a content hash."""
        rows = self.session.execute(
            select(Rule, RuleVersion)
            .join(RuleVersion, and_(RuleVersion.rule_id == Rule.id, RuleVersion.version == Rule.current_version))
            .where(Rule.enabled.is_(True), Rule.deleted_at.is_(None))
            .order_by(Rule.id)
        ).all()
        rules = []
        for rule, version in rows:
            definition = copy.deepcopy(version.definition)
            # The rule row's switch is authoritative; the engine reads the definition's.
            definition["enabled"] = True
            rules.append(
                SnapshotRule(
                    id=rule.id, organization_id=rule.organization_id, version=version.version, definition=definition
                )
            )
        devices = [
            SnapshotDevice(
                id=d.id,
                organization_id=d.organization_id,
                group_id=d.device_group_id,
                device_type_id=d.device_type_id,
                labels={str(k): str(v) for k, v in (d.labels or {}).items()},
            )
            for d in self.session.scalars(select(Device).where(Device.is_enabled.is_(True)).order_by(Device.id))
        ]
        body = json.dumps(
            {
                "rules": [r.model_dump(mode="json") for r in rules],
                "devices": [d.model_dump(mode="json") for d in devices],
            },
            sort_keys=True,
        )
        return RulesSnapshot(version=hashlib.sha256(body.encode()).hexdigest()[:32], rules=rules, devices=devices)

    def apply_transitions(self, items: list[AlertTransition]) -> list[AlertTransitionResult]:
        """Create or resolve alert records from the engine's ``alert`` actions. Replays are no-ops."""
        results = []
        for item in items:
            savepoint = self.session.begin_nested()
            try:
                result = self._apply_one(item)
                savepoint.commit()
            except IntegrityError:
                # A concurrent delivery of the same transition won the race.
                savepoint.rollback()
                result = AlertTransitionResult(idempotency_key=item.idempotency_key, alert_id=None, status="duplicate")
            results.append(result)
        self.session.commit()
        return results

    def _apply_one(self, item: AlertTransition) -> AlertTransitionResult:
        key = item.idempotency_key
        seen = self.session.scalar(select(AlertEvent).where(AlertEvent.idempotency_key == key))
        if seen is not None:
            alert = self.session.get(Alert, seen.alert_id)
            return AlertTransitionResult(
                idempotency_key=key, alert_id=seen.alert_id, status="duplicate", silenced=bool(alert and alert.silenced)
            )
        rule = self.session.get(Rule, item.rule_id)
        device = self.session.get(Device, item.device_id)
        if (
            rule is None
            or device is None
            or device.organization_id != item.organization_id
            or (rule.organization_id is not None and rule.organization_id != item.organization_id)
        ):
            # Never let an engine message attach an alert to another tenant's rule or device.
            return AlertTransitionResult(
                idempotency_key=key, alert_id=None, status="ignored", detail="rule or device not in organization"
            )
        ev = item.event
        base = select(Alert).where(
            Alert.rule_id == item.rule_id, Alert.device_id == item.device_id, Alert.fingerprint == ev.fingerprint
        )
        if ev.transition == "firing":
            return self._fire(item, base)
        if not ev.fingerprint:
            # A resolve ends the episode: per-event alerts of it carry fingerprints of their own.
            for extra in self.session.scalars(
                select(Alert).where(
                    Alert.rule_id == item.rule_id,
                    Alert.device_id == item.device_id,
                    Alert.episode == ev.episode,
                    Alert.fingerprint != "",
                    Alert.status.in_(ACTIVE_ALERT_STATUSES),
                )
            ):
                self._resolve(
                    extra, ev.at, ev.reason or "condition_cleared", key=None, values=ev.values, event_id=ev.event_id
                )
        alert = self.session.scalar(base.where(Alert.episode == ev.episode).order_by(Alert.fired_at.desc()))
        if alert is None:
            return AlertTransitionResult(idempotency_key=key, alert_id=None, status="ignored", detail="no such alert")
        if alert.status == ALERT_RESOLVED:
            return AlertTransitionResult(
                idempotency_key=key, alert_id=alert.id, status="duplicate", silenced=alert.silenced
            )
        self._resolve(alert, ev.at, ev.reason or "condition_cleared", key=key, values=ev.values, event_id=ev.event_id)
        return AlertTransitionResult(idempotency_key=key, alert_id=alert.id, status="applied", silenced=alert.silenced)

    def _fire(self, item: AlertTransition, base) -> AlertTransitionResult:
        ev = item.event
        active = self.session.scalar(base.where(Alert.status.in_(ACTIVE_ALERT_STATUSES)))
        if active is not None:
            if active.episode == ev.episode:
                return AlertTransitionResult(
                    idempotency_key=item.idempotency_key,
                    alert_id=active.id,
                    status="duplicate",
                    silenced=active.silenced,
                )
            # The resolve of an older episode never arrived: close it before opening the new one.
            self._resolve(active, ev.at, "superseded", key=None, values={}, event_id=None)
            self.session.flush()
        silenced = self._silenced(self._active_silences(item.organization_id, ev.at), item.rule_id, item.device_id)
        alert = Alert(
            organization_id=item.organization_id,
            rule_id=item.rule_id,
            rule_version=item.rule_version,
            rule_name=item.rule_name,
            device_id=item.device_id,
            episode=ev.episode,
            fingerprint=ev.fingerprint,
            severity=item.severity,
            status=ALERT_FIRING,
            silenced=silenced,
            fired_at=ev.at,
            trigger_event_id=ev.event_id,
            trigger_values=ev.values,
        )
        self.session.add(alert)
        self.session.flush()
        self.session.add(
            AlertEvent(
                alert_id=alert.id,
                transition="firing",
                actor_type="engine",
                at=ev.at,
                event_id=ev.event_id,
                values=ev.values,
                idempotency_key=item.idempotency_key,
            )
        )
        self.session.flush()
        return AlertTransitionResult(
            idempotency_key=item.idempotency_key, alert_id=alert.id, status="applied", silenced=silenced
        )

    def _resolve(
        self,
        alert: Alert,
        at: datetime,
        reason: str,
        *,
        key: str | None,
        values: dict[str, Any],
        event_id: str | None,
    ) -> None:
        alert.status = ALERT_RESOLVED
        alert.resolved_at = at
        alert.resolve_reason = reason
        self.session.add(
            AlertEvent(
                alert_id=alert.id,
                transition="resolved",
                actor_type="engine",
                at=at,
                reason=reason,
                event_id=event_id,
                values=values,
                idempotency_key=key,
            )
        )
        self.session.flush()

    # ---- rule tests and evaluation status (proxied to the rule processor) ----

    def test_rule(self, *, actor: User, organization_id: uuid.UUID, payload: RuleTestRequest) -> dict[str, Any]:
        self._authorize(actor, organization_id, "rule.read")
        self._require_valid(organization_id, payload.definition)
        return self.engine.simulate(payload.model_dump(mode="json", exclude_none=True))

    def evaluation_status(
        self, *, actor: User, organization_id: uuid.UUID, rule_id: uuid.UUID, after: str = "", limit: int = 100
    ) -> dict[str, Any]:
        self._authorize(actor, organization_id, "rule.read")
        rule = self._rule(organization_id, rule_id)
        result = self.engine.status(str(rule.id), after=after, limit=limit)
        return {"rule_id": str(rule.id), "current_version": rule.current_version, "enabled": rule.enabled, **result}

    # ---- dispatcher-facing --------------------------------------------------

    def _engine_rule_and_device(self, item: EngineAction) -> tuple[Rule, Device]:
        rule = self.session.get(Rule, item.rule_id)
        device = self.session.get(Device, item.device_id)
        if (
            rule is None
            or device is None
            or device.organization_id != item.organization_id
            or (rule.organization_id is not None and rule.organization_id != item.organization_id)
        ):
            raise NotFoundError("rule_or_device_not_found", "Rule or device is not in this organization.")
        return rule, device

    def delivery(self, payload: DeliveryRequest) -> DeliveryResponse:
        """Where a webhook goes, only if the integration belongs to the action's organization."""
        row = self.session.get(NotificationIntegration, payload.integration_id)
        if row is None or row.organization_id != payload.organization_id:
            raise NotFoundError("integration_not_found", "Integration was not found.")
        secret = self._unseal(row) or ""
        device = self.session.get(Device, payload.device_id)
        organization = self.session.get(Organization, payload.organization_id)
        silences = self._active_silences(payload.organization_id, payload.at)
        return DeliveryResponse(
            url=row.url,
            kind=row.kind,
            secret=secret,
            enabled=row.enabled,
            silenced=self._silenced(silences, payload.rule_id, payload.device_id),
            config=row.config or {},
            device_name=device.name if device and device.organization_id == payload.organization_id else "",
            organization_name=organization.name if organization else "",
        )

    def run_command(self, item: EngineAction) -> CommandResult:
        """Send a rule's device command as its author, with the console's permission and scope checks."""
        rule, device = self._engine_rule_and_device(item)
        command = item.action.get("command")
        if command not in COMMAND_PERMISSIONS:
            raise ValidationAppError("unsupported_command", f"Rules cannot send the {command!r} command.")
        author = self.session.get(User, rule.created_by_user_id) if rule.created_by_user_id else None
        if author is None or not author.is_active:
            raise ForbiddenError("rule_author_inactive", "The rule's author can no longer send commands.")
        metadata = {
            "rule_id": str(rule.id),
            "rule_version": item.rule_version,
            "device_id": str(device.id),
            "command": command,
            "episode": item.event.episode,
            "idempotency_key": item.idempotency_key,
        }
        actions = DeviceActionService(self.session, self.settings)
        try:
            if command == "reboot":
                actions.reboot(actor=author, organization_id=device.organization_id, device_id=device.id)
            else:
                actions.shutdown(actor=author, organization_id=device.organization_id, device_id=device.id)
        except ConflictError as exc:
            # A restart or shut-down already waits (a redelivery of this very command, or another one).
            self.session.rollback()
            return CommandResult(status="skipped", detail=exc.code)
        except ForbiddenError:
            self.session.rollback()
            self.audit.record(
                action="rule.command",
                resource_type="rule",
                actor_type="rule",
                actor_id=rule.id,
                organization_id=device.organization_id,
                resource_id=rule.id,
                outcome="denied",
                metadata=metadata,
            )
            self.session.commit()
            raise
        self.audit.record(
            action="rule.command",
            resource_type="rule",
            actor_type="rule",
            actor_id=rule.id,
            organization_id=device.organization_id,
            resource_id=rule.id,
            metadata=metadata,
        )
        self.session.commit()
        return CommandResult(status="sent")

    def platform_event(self, item: EngineAction) -> None:
        """Record a rule's platform event in the audit log; replays of one key are recorded once."""
        rule, device = self._engine_rule_and_device(item)
        exists = self.session.scalar(
            select(AuditEvent.id).where(
                AuditEvent.action == "rule.event",
                AuditEvent.resource_id == str(rule.id),
                AuditEvent.metadata_["idempotency_key"].astext == item.idempotency_key,
            )
        )
        if exists is not None:
            return
        self.audit.record(
            action="rule.event",
            resource_type="rule",
            actor_type="rule",
            actor_id=rule.id,
            organization_id=device.organization_id,
            resource_id=rule.id,
            metadata={
                "idempotency_key": item.idempotency_key,
                "rule_version": item.rule_version,
                "rule_name": item.rule_name,
                "device_id": str(device.id),
                "severity": item.severity,
                "transition": item.event.transition,
                "episode": item.event.episode,
                "values": item.event.values,
                "params": item.action.get("params") or {},
            },
        )
        self.session.commit()
