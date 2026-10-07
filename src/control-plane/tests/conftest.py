"""Shared pytest fixtures for backend tests."""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator

os.environ["MQTT_ENABLED"] = "false"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

# Ensure metadata includes domain models.
from app.artifacts import models as _artifact_models  # noqa: F401
from app.artifacts.dependencies import get_object_storage
from app.audit import models as _audit_models  # noqa: F401
from app.core.config import get_settings
from app.core.database import get_db
from app.core.models import Base
from app.core.security import hash_password
from app.devices import models as _fleet_models  # noqa: F401
from app.devices.dependencies import (
    get_enroll_poll_rate_limiter,
    get_enroll_request_rate_limiter,
    get_rate_limiter,
)
from app.devices.rate_limit import InMemoryRateLimiter
from app.identity import models as _identity_models  # noqa: F401
from app.identity.models import User
from app.main import create_app
from app.tenancy import models as _organization_models  # noqa: F401
from app.tenancy.models import Organization, OrganizationMembership, OrganizationRole
from tests.db_guard import assert_safe_test_database_url, ensure_database_exists
from tests.storage import InMemoryObjectStorage


@pytest.fixture(scope="session")
def engine():
    settings = get_settings()
    assert_safe_test_database_url(settings.database_url)
    ensure_database_exists(settings.database_url)
    eng = create_engine(settings.database_url, pool_pre_ping=True)
    Base.metadata.drop_all(bind=eng)
    Base.metadata.create_all(bind=eng)
    yield eng
    eng.dispose()


def pytest_report_header() -> list[str]:
    database = get_settings().database_url.rsplit("/", 1)[-1]
    return [f"test database: {database}"]


@pytest.fixture
def db_session(engine) -> Generator[Session]:
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())

    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        with engine.begin() as connection:
            for table in reversed(Base.metadata.sorted_tables):
                connection.execute(table.delete())


@pytest.fixture
def object_storage() -> InMemoryObjectStorage:
    return InMemoryObjectStorage()


@pytest.fixture
def client(db_session: Session, object_storage: InMemoryObjectStorage) -> Generator[TestClient]:
    application = create_app()

    def override_get_db() -> Generator[Session]:
        try:
            yield db_session
        finally:
            pass

    application.dependency_overrides[get_db] = override_get_db
    application.dependency_overrides[get_object_storage] = lambda: object_storage
    # Permissive limiter by default; tests that exercise rate limiting override
    # this on ``client.app.dependency_overrides``.
    application.dependency_overrides[get_rate_limiter] = lambda: InMemoryRateLimiter(limit=10_000, window_seconds=60)
    application.dependency_overrides[get_enroll_request_rate_limiter] = lambda: InMemoryRateLimiter(
        limit=10_000, window_seconds=60
    )
    application.dependency_overrides[get_enroll_poll_rate_limiter] = lambda: InMemoryRateLimiter(
        limit=10_000, window_seconds=60
    )
    with TestClient(application) as test_client:
        yield test_client
    application.dependency_overrides.clear()


def create_user(
    session: Session,
    *,
    email: str,
    full_name: str = "Test User",
    password: str = "strong-password",
    is_active: bool = True,
) -> User:
    user = User(
        email=email.lower(),
        full_name=full_name,
        password_hash=hash_password(password),
        is_active=is_active,
        is_superuser=False,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def auth_header(
    client: TestClient,
    email: str,
    password: str = "strong-password",
) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def create_org_with_owner(
    session: Session,
    owner: User,
    *,
    name: str = "Acme Energy",
    slug: str | None = None,
) -> tuple[Organization, OrganizationMembership]:
    organization = Organization(
        name=name,
        slug=slug or f"org-{uuid.uuid4().hex[:8]}",
        description="Test organization",
        created_by_user_id=owner.id,
    )
    session.add(organization)
    session.flush()
    membership = OrganizationMembership(
        organization_id=organization.id,
        user_id=owner.id,
        role=OrganizationRole.OWNER,
    )
    session.add(membership)
    session.commit()
    session.refresh(organization)
    session.refresh(membership)
    return organization, membership


def add_member(
    session: Session,
    organization: Organization,
    user: User,
    role: OrganizationRole,
) -> OrganizationMembership:
    membership = OrganizationMembership(
        organization_id=organization.id,
        user_id=user.id,
        role=role,
    )
    session.add(membership)
    session.commit()
    session.refresh(membership)
    return membership
