"""Per-request facts that audit rows record: client address, user agent, who is calling.

RequestIdMiddleware opens one ``AuditRequestContext`` per HTTP request. It is a mutable
object held in a context variable, so a sync endpoint running in the threadpool (which
gets a *copy* of the context) still writes into the same object the middleware and the
exception handlers read. Outside a request (CLI, sweeper, tests calling services) there
is no context and every accessor returns ``None``.

Client addresses and user agents live only here, never in structlog contextvars: the
access log promises not to log them.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass

_MAX_USER_AGENT = 512
_MAX_IP = 64


@dataclass
class AuditRequestContext:
    ip: str | None = None
    user_agent: str | None = None
    actor_user_id: uuid.UUID | None = None
    actor_type: str | None = None
    actor_id: str | None = None
    # Set once a service wrote its own denied/failed audit row for this request, so the
    # generic "access.denied" row from the 403 handler is not a duplicate.
    denial_recorded: bool = False


_current: ContextVar[AuditRequestContext | None] = ContextVar("audit_request_context", default=None)


def open_request_context(*, ip: str | None, user_agent: str | None) -> Token[AuditRequestContext | None]:
    """Start a request's context; pass the returned token to ``close_request_context``."""
    context = AuditRequestContext(
        ip=(ip or None) and ip[:_MAX_IP],
        user_agent=(user_agent or None) and user_agent[:_MAX_USER_AGENT],
    )
    return _current.set(context)


def close_request_context(token: Token[AuditRequestContext | None]) -> None:
    _current.reset(token)


def current_request_context() -> AuditRequestContext | None:
    return _current.get()


def set_request_actor(*, actor_type: str, actor_id: uuid.UUID | str, user_id: uuid.UUID | None = None) -> None:
    """Remember who authenticated this request (a user, a device, an application...)."""
    context = _current.get()
    if context is None:
        return
    context.actor_type = actor_type
    context.actor_id = str(actor_id)
    if user_id is not None:
        context.actor_user_id = user_id
