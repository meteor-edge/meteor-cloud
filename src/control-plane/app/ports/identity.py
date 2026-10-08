"""Identity directory port: the accounts behind organization members.

The current adapter is local (``LocalIdentityDirectory``): accounts live in the
PostgreSQL ``users`` table with bcrypt passwords. A future external IdP
(OIDC/SCIM) implements the same protocol. Tenancy talks to members; it asks
this port only to find or create the account a member signs in with.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol


class IdentityAccountInactiveError(Exception):
    """Raised when an account exists for an email but is disabled."""


@dataclass(frozen=True)
class IdentityAccount:
    id: uuid.UUID
    email: str
    full_name: str
    is_active: bool


class IdentityDirectory(Protocol):
    def get_by_id(self, account_id: uuid.UUID) -> IdentityAccount | None:
        """Return the account, or None when it does not exist."""
        ...

    def get_by_email(self, email: str) -> IdentityAccount | None:
        """Return the account for a (case-insensitive) email, or None."""
        ...

    def ensure_account(self, *, email: str, full_name: str, password: str) -> IdentityAccount:
        """Create the account if missing; return an existing active one unchanged.

        Never overwrites the name or password of an existing account. Raises
        ``IdentityAccountInactiveError`` when the existing account is disabled.
        Joins the caller's unit of work; the caller commits.
        """
        ...
