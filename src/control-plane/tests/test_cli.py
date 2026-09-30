"""create-admin CLI tests."""

from __future__ import annotations

from unittest.mock import patch

from sqlalchemy.orm import Session

from app.cli.main import create_admin
from app.identity.repository import UserRepository


def test_create_admin_skips_without_credentials(db_session: Session) -> None:
    with patch.dict("os.environ", {}, clear=True):
        with patch("app.cli.main.SessionLocal", return_value=db_session):
            assert create_admin() == 0
    assert UserRepository(db_session).get_by_email("admin@example.com") is None


def test_create_admin_creates_user_once(db_session: Session) -> None:
    env = {
        "EDGE_PLATFORM_ADMIN_EMAIL": "admin@example.com",
        "EDGE_PLATFORM_ADMIN_PASSWORD": "StrongPassword123!",
    }
    with patch.dict("os.environ", env, clear=False):
        with patch("app.cli.main.SessionLocal", return_value=db_session):
            assert create_admin() == 0
            assert create_admin() == 0

    user = UserRepository(db_session).get_by_email("admin@example.com")
    assert user is not None
    assert user.is_superuser is True
