"""Client configuration, usually read from the environment.

MeteorCloud sets these variables for applications it deploys to a device. A
cloud-hosted application sets the same variables itself, so the code is the same
in both places.

===============================  ==============================================
``METEORCLOUD_ENDPOINT``         Base URL of the data API, e.g. https://data.example.com
``METEORCLOUD_TOKEN_FILE``       File holding the application token
                                 (default ``/run/secrets/meteorcloud-token``)
``METEORCLOUD_TOKEN``            The token itself; prefer the file
``METEORCLOUD_APP_ID``           Application name (informational)
``METEORCLOUD_DEVICE_ID``        Device the application runs on (informational)
``METEORCLOUD_ENVIRONMENT``      Environment name (informational)
``METEORCLOUD_CA_FILE``          CA bundle for a private certificate authority
``METEORCLOUD_TIMEOUT``          Seconds for one HTTP request (default 10)
``METEORCLOUD_RETRY_TIME``       Seconds to keep retrying a failed call (default 30)
===============================  ==============================================

The identity the service acts on always comes from the token. ``APP_ID`` and
``DEVICE_ID`` are only used for logging and for convenient defaults.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from .exceptions import ConfigurationError

DEFAULT_TOKEN_FILE = "/run/secrets/meteorcloud-token"


@dataclass(frozen=True)
class Config:
    endpoint: str
    token: str | None = None
    token_file: str | None = None
    app_id: str | None = None
    device_id: str | None = None
    environment: str | None = None
    ca_file: str | None = None
    #: Seconds allowed for one HTTP request.
    timeout: float = 10.0
    #: Total seconds a call keeps retrying on network errors, overload or rate limits.
    retry_time: float = 30.0
    #: Longest pause between reconnect attempts of the live connection.
    reconnect_max_delay: float = 30.0
    #: Events buffered per subscription before the SDK applies backpressure.
    subscription_buffer: int = 1000

    def __post_init__(self) -> None:
        endpoint = (self.endpoint or "").strip().rstrip("/")
        if not endpoint:
            raise ConfigurationError(
                "No endpoint. Set METEORCLOUD_ENDPOINT, for example https://data.example.com", code="no_endpoint"
            )
        if not endpoint.startswith(("https://", "http://")):
            raise ConfigurationError("METEORCLOUD_ENDPOINT must start with https:// or http://", code="bad_endpoint")
        object.__setattr__(self, "endpoint", endpoint)
        if not self.token and not self.token_file:
            raise ConfigurationError(
                "No credentials. Set METEORCLOUD_TOKEN_FILE (or METEORCLOUD_TOKEN).", code="no_credentials"
            )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, **overrides: object) -> Config:
        env = os.environ if env is None else env

        def get(name: str) -> str | None:
            value = env.get(name, "").strip()
            return value or None

        token = get("METEORCLOUD_TOKEN")
        token_file = get("METEORCLOUD_TOKEN_FILE")
        if not token and not token_file:
            token_file = DEFAULT_TOKEN_FILE
        values: dict[str, object] = {
            "endpoint": get("METEORCLOUD_ENDPOINT") or "",
            "token": token,
            "token_file": token_file,
            "app_id": get("METEORCLOUD_APP_ID"),
            "device_id": get("METEORCLOUD_DEVICE_ID"),
            "environment": get("METEORCLOUD_ENVIRONMENT"),
            "ca_file": get("METEORCLOUD_CA_FILE"),
        }
        for name, key in (("METEORCLOUD_TIMEOUT", "timeout"), ("METEORCLOUD_RETRY_TIME", "retry_time")):
            raw = get(name)
            if raw is not None:
                try:
                    values[key] = float(raw)
                except ValueError as exc:
                    raise ConfigurationError(f"{name} must be a number of seconds", code="bad_setting") from exc
        values.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**values)  # type: ignore[arg-type]


class TokenSource:
    """Supplies the current token.

    With a token file, the file is read again whenever it changes, so a rotated
    credential is picked up without restarting the application.
    """

    def __init__(self, config: Config) -> None:
        self._static = config.token
        self._path = None if config.token else config.token_file
        self._cached: str | None = None
        self._stamp: tuple[int, int] | None = None

    def get(self) -> str:
        if self._path is None:
            return self._static or ""
        try:
            stat = os.stat(self._path)
            stamp = (stat.st_mtime_ns, stat.st_size)
            if self._cached is None or stamp != self._stamp:
                with open(self._path, encoding="utf-8") as fh:
                    token = fh.read().strip()
                if not token:
                    raise ConfigurationError(f"Token file {self._path} is empty", code="no_credentials")
                self._cached, self._stamp = token, stamp
        except OSError as exc:
            if self._cached is not None:
                # A rotation may be replacing the file right now: keep the last token.
                return self._cached
            raise ConfigurationError(
                f"Cannot read the token file {self._path}: {exc.strerror or exc}", code="no_credentials"
            ) from exc
        return self._cached

    def refresh(self) -> bool:
        """Read the file again. True when the token changed."""
        if self._path is None:
            return False
        before = self._cached
        self._stamp = None
        try:
            return self.get() != before
        except ConfigurationError:
            return False
