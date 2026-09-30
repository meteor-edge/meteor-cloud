"""Persistence ports.

There is no generic DatabaseProvider. Domain modules own repository classes;
the current implementations are PostgreSQL/SQLAlchemy adapters:

- ``app.identity.repository``
- ``app.tenancy.repository``
- ``app.devices.repository``
- ``app.audit`` (SQLAlchemy models/service)

A different SQL store would add a new repository class behind the same methods.
"""
