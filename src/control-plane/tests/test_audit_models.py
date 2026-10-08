"""Tests for the AuditEvent ORM model."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.tenancy.models import OrganizationRole
from tests.conftest import add_member, create_org_with_owner, create_user


def test_audit_event_applies_column_defaults(db_session: Session) -> None:
    owner = create_user(db_session, email="owner-audit-defaults@example.com")
    org, _ = create_org_with_owner(db_session, owner)

    event = AuditEvent(
        organization_id=org.id,
        actor_user_id=owner.id,
        action="device.delete",
        resource_type="device",
    )
    db_session.add(event)
    db_session.commit()
    db_session.refresh(event)

    assert event.id is not None
    assert event.outcome == "success"
    assert event.metadata_ == {}
    assert event.created_at is not None
    assert event.resource_id is None
    assert event.request_id is None
    assert event.ip is None
    assert event.user_agent is None


def test_audit_event_allows_null_organization_and_actor(db_session: Session) -> None:
    event = AuditEvent(action="system.startup", resource_type="system")
    db_session.add(event)
    db_session.commit()
    db_session.refresh(event)

    assert event.organization_id is None
    assert event.actor_user_id is None


def test_audit_event_stores_arbitrary_metadata(db_session: Session) -> None:
    owner = create_user(db_session, email="owner-audit-metadata@example.com")
    org, _ = create_org_with_owner(db_session, owner)

    event = AuditEvent(
        organization_id=org.id,
        actor_user_id=owner.id,
        action="enrollment_key.create",
        resource_type="enrollment_key",
        resource_id="key-123",
        outcome="failure",
        metadata_={"name": "Field techs", "count": 3},
    )
    db_session.add(event)
    db_session.commit()
    db_session.refresh(event)

    assert event.resource_id == "key-123"
    assert event.outcome == "failure"
    assert event.metadata_ == {"name": "Field techs", "count": 3}


def test_audit_event_actor_set_null_when_user_deleted(db_session: Session) -> None:
    owner = create_user(db_session, email="owner-audit-cascade@example.com")
    member = create_user(db_session, email="member-audit-cascade@example.com")
    org, _ = create_org_with_owner(db_session, owner)
    add_member(db_session, org, member, OrganizationRole.OPERATOR)

    event = AuditEvent(
        organization_id=org.id,
        actor_user_id=member.id,
        action="auth.login",
        resource_type="user",
    )
    db_session.add(event)
    db_session.commit()
    event_id = event.id

    db_session.delete(member)
    db_session.commit()

    refreshed = db_session.get(AuditEvent, event_id)
    assert refreshed is not None
    assert refreshed.actor_user_id is None


def test_audit_event_cascade_deleted_with_organization(db_session: Session) -> None:
    owner = create_user(db_session, email="owner-audit-org-cascade@example.com")
    org, _ = create_org_with_owner(db_session, owner)

    event = AuditEvent(
        organization_id=org.id,
        actor_user_id=owner.id,
        action="registration_token.create",
        resource_type="registration_token",
    )
    db_session.add(event)
    db_session.commit()
    event_id = event.id

    db_session.delete(org)
    db_session.commit()

    assert db_session.get(AuditEvent, event_id) is None
