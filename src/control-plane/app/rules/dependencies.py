"""FastAPI dependencies for rule APIs."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.rules.service import RuleService


def get_rule_service(
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RuleService:
    return RuleService(session, settings=settings)


RuleSvc = Annotated[RuleService, Depends(get_rule_service)]
