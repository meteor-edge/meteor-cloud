"""Organization-scoped rule, alert, silence and notification-integration routes."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query

from app.identity.dependencies import CurrentUser
from app.rules.dependencies import RuleSvc
from app.rules.models import Rule
from app.rules.schemas import (
    AlertDetail,
    AlertEventResponse,
    AlertResponse,
    DevicePreview,
    IntegrationCreate,
    IntegrationResponse,
    IntegrationUpdate,
    RuleResponse,
    RuleTestRequest,
    RuleVersionResponse,
    RuleWrite,
    SilenceCreate,
    SilenceResponse,
    ValidationResult,
)
from app.rules.service import RuleService

PREFIX = "/api/v1/organizations/{organization_id}"
router = APIRouter(prefix=PREFIX, tags=["rules"])

Limit = Annotated[int, Query(ge=1, le=200)]
AlertStatus = Literal["firing", "acknowledged", "resolved"]
Severity = Literal["info", "warning", "critical"]


def _view(service: RuleService, rule: Rule) -> RuleResponse:
    return RuleResponse(
        id=rule.id,
        organization_id=rule.organization_id,
        name=rule.name,
        enabled=rule.enabled,
        current_version=rule.current_version,
        scope_type=rule.scope_type,
        severity=rule.severity,
        definition=service.definition_of(rule),
        created_by_user_id=rule.created_by_user_id,
        created_at=rule.created_at,
        updated_at=rule.updated_at,
    )


# ---- rules ---------------------------------------------------------------


@router.get("/rules", response_model=list[RuleResponse])
def list_rules(
    organization_id: uuid.UUID,
    current_user: CurrentUser,
    service: RuleSvc,
    enabled: bool | None = None,
    limit: Limit = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    rows = service.list_rules(
        actor=current_user, organization_id=organization_id, enabled=enabled, limit=limit, offset=offset
    )
    return [_view(service, row) for row in rows]


@router.post("/rules", response_model=RuleResponse, status_code=201)
def create_rule(organization_id: uuid.UUID, current_user: CurrentUser, service: RuleSvc, payload: RuleWrite):
    rule = service.create_rule(actor=current_user, organization_id=organization_id, definition=payload.definition)
    return _view(service, rule)


@router.post("/rules/validate", response_model=ValidationResult)
def validate_rule(organization_id: uuid.UUID, current_user: CurrentUser, service: RuleSvc, payload: RuleWrite):
    """Check a definition without saving it. Always 200; ``valid`` and ``problems`` say why."""
    return service.validate(actor=current_user, organization_id=organization_id, definition=payload.definition)


@router.post("/rules/preview-devices", response_model=DevicePreview)
def preview_devices(organization_id: uuid.UUID, current_user: CurrentUser, service: RuleSvc, payload: RuleWrite):
    """Devices the definition's scope selects now (first 200, with the total)."""
    return service.preview_devices(actor=current_user, organization_id=organization_id, definition=payload.definition)


@router.post("/rules/test")
def test_rule(
    organization_id: uuid.UUID, current_user: CurrentUser, service: RuleSvc, payload: RuleTestRequest
) -> dict[str, Any]:
    """Run a definition over sample events in the engine with a virtual clock; returns every step."""
    return service.test_rule(actor=current_user, organization_id=organization_id, payload=payload)


@router.get("/rules/{rule_id}/status")
def rule_status(
    organization_id: uuid.UUID,
    rule_id: uuid.UUID,
    current_user: CurrentUser,
    service: RuleSvc,
    after: Annotated[str, Query(max_length=64, description="Page: device_id of the last row already shown.")] = "",
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    """The engine's evaluation state of this rule per device (phase, episode, last values, timers)."""
    return service.evaluation_status(
        actor=current_user, organization_id=organization_id, rule_id=rule_id, after=after, limit=limit
    )


@router.get("/rules/{rule_id}", response_model=RuleResponse)
def get_rule(organization_id: uuid.UUID, rule_id: uuid.UUID, current_user: CurrentUser, service: RuleSvc):
    return _view(service, service.get_rule(actor=current_user, organization_id=organization_id, rule_id=rule_id))


@router.put("/rules/{rule_id}", response_model=RuleResponse)
def update_rule(
    organization_id: uuid.UUID, rule_id: uuid.UUID, current_user: CurrentUser, service: RuleSvc, payload: RuleWrite
):
    """Save a new version. If the condition, scope, sources or mode changed, engine state resets."""
    rule = service.update_rule(
        actor=current_user, organization_id=organization_id, rule_id=rule_id, definition=payload.definition
    )
    return _view(service, rule)


@router.post("/rules/{rule_id}/enable", response_model=RuleResponse)
def enable_rule(organization_id: uuid.UUID, rule_id: uuid.UUID, current_user: CurrentUser, service: RuleSvc):
    rule = service.set_enabled(actor=current_user, organization_id=organization_id, rule_id=rule_id, enabled=True)
    return _view(service, rule)


@router.post("/rules/{rule_id}/disable", response_model=RuleResponse)
def disable_rule(organization_id: uuid.UUID, rule_id: uuid.UUID, current_user: CurrentUser, service: RuleSvc):
    rule = service.set_enabled(actor=current_user, organization_id=organization_id, rule_id=rule_id, enabled=False)
    return _view(service, rule)


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(organization_id: uuid.UUID, rule_id: uuid.UUID, current_user: CurrentUser, service: RuleSvc) -> None:
    service.delete_rule(actor=current_user, organization_id=organization_id, rule_id=rule_id)


@router.get("/rules/{rule_id}/versions", response_model=list[RuleVersionResponse])
def list_rule_versions(organization_id: uuid.UUID, rule_id: uuid.UUID, current_user: CurrentUser, service: RuleSvc):
    return service.list_versions(actor=current_user, organization_id=organization_id, rule_id=rule_id)


@router.get("/rules/{rule_id}/alerts", response_model=list[AlertResponse])
def list_rule_alerts(
    organization_id: uuid.UUID,
    rule_id: uuid.UUID,
    current_user: CurrentUser,
    service: RuleSvc,
    active: bool | None = None,
    before: datetime | None = None,
    before_id: uuid.UUID | None = None,
    limit: Limit = 50,
):
    service.get_rule(actor=current_user, organization_id=organization_id, rule_id=rule_id)
    return service.list_alerts(
        actor=current_user,
        organization_id=organization_id,
        rule_id=rule_id,
        active=active,
        before=before,
        before_id=before_id,
        limit=limit,
    )


# ---- alerts --------------------------------------------------------------


@router.get("/alerts", response_model=list[AlertResponse])
def list_alerts(
    organization_id: uuid.UUID,
    current_user: CurrentUser,
    service: RuleSvc,
    status: AlertStatus | None = None,
    active: bool | None = None,
    rule_id: uuid.UUID | None = None,
    device_id: uuid.UUID | None = None,
    severity: Severity | None = None,
    before: Annotated[datetime | None, Query(description="Page: fired_at of the last row already shown.")] = None,
    before_id: Annotated[uuid.UUID | None, Query(description="Page: id of the last row already shown.")] = None,
    limit: Limit = 50,
):
    return service.list_alerts(
        actor=current_user,
        organization_id=organization_id,
        status=status,
        active=active,
        rule_id=rule_id,
        device_id=device_id,
        severity=severity,
        before=before,
        before_id=before_id,
        limit=limit,
    )


@router.get("/alerts/{alert_id}", response_model=AlertDetail)
def get_alert(organization_id: uuid.UUID, alert_id: uuid.UUID, current_user: CurrentUser, service: RuleSvc):
    alert, events = service.get_alert(actor=current_user, organization_id=organization_id, alert_id=alert_id)
    return AlertDetail(
        **AlertResponse.model_validate(alert).model_dump(),
        events=[AlertEventResponse.model_validate(e) for e in events],
    )


@router.post("/alerts/{alert_id}/acknowledge", response_model=AlertResponse)
def acknowledge_alert(organization_id: uuid.UUID, alert_id: uuid.UUID, current_user: CurrentUser, service: RuleSvc):
    return service.acknowledge(actor=current_user, organization_id=organization_id, alert_id=alert_id)


# ---- silences ------------------------------------------------------------


@router.get("/alert-silences", response_model=list[SilenceResponse])
def list_silences(
    organization_id: uuid.UUID, current_user: CurrentUser, service: RuleSvc, include_expired: bool = False
):
    return service.list_silences(actor=current_user, organization_id=organization_id, include_expired=include_expired)


@router.post("/alert-silences", response_model=SilenceResponse, status_code=201)
def create_silence(organization_id: uuid.UUID, current_user: CurrentUser, service: RuleSvc, payload: SilenceCreate):
    return service.create_silence(actor=current_user, organization_id=organization_id, payload=payload)


@router.delete("/alert-silences/{silence_id}", status_code=204)
def delete_silence(
    organization_id: uuid.UUID, silence_id: uuid.UUID, current_user: CurrentUser, service: RuleSvc
) -> None:
    service.delete_silence(actor=current_user, organization_id=organization_id, silence_id=silence_id)


# ---- notification integrations -------------------------------------------


@router.get("/notification-integrations", response_model=list[IntegrationResponse])
def list_integrations(organization_id: uuid.UUID, current_user: CurrentUser, service: RuleSvc):
    return service.list_integrations(actor=current_user, organization_id=organization_id)


@router.post("/notification-integrations", response_model=IntegrationResponse, status_code=201)
def create_integration(
    organization_id: uuid.UUID, current_user: CurrentUser, service: RuleSvc, payload: IntegrationCreate
):
    return service.create_integration(actor=current_user, organization_id=organization_id, payload=payload)


@router.patch("/notification-integrations/{integration_id}", response_model=IntegrationResponse)
def update_integration(
    organization_id: uuid.UUID,
    integration_id: uuid.UUID,
    current_user: CurrentUser,
    service: RuleSvc,
    payload: IntegrationUpdate,
):
    return service.update_integration(
        actor=current_user, organization_id=organization_id, integration_id=integration_id, payload=payload
    )


@router.delete("/notification-integrations/{integration_id}", status_code=204)
def delete_integration(
    organization_id: uuid.UUID, integration_id: uuid.UUID, current_user: CurrentUser, service: RuleSvc
) -> None:
    service.delete_integration(actor=current_user, organization_id=organization_id, integration_id=integration_id)
