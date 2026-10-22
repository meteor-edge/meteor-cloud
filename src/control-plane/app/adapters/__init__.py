"""Select infrastructure adapters from configuration.

Unsupported provider names fail fast. Future providers are not implemented.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.devices.rate_limit import InMemoryRateLimiter, RedisRateLimiter
from app.mqtt.service import NoopPublisher
from app.ports.cache import RateLimiter
from app.ports.deployment import DeploymentProvider
from app.ports.identity import IdentityDirectory
from app.ports.mqtt import MQTTGateway
from app.ports.storage import ObjectStorage


def mqtt_gateway(settings: Settings) -> MQTTGateway:
    if not settings.mqtt_enabled:
        return NoopPublisher()
    if settings.mqtt_provider != "emqx":
        raise RuntimeError(f"Unsupported mqtt.provider={settings.mqtt_provider!r}; only 'emqx' is implemented")
    if settings.telemetry_provider != "postgresql":
        raise RuntimeError(
            f"Unsupported telemetry.provider={settings.telemetry_provider!r}; only 'postgresql' is implemented"
        )
    from app.adapters.data_plane_mqtt import DataPlaneMQTTGateway

    return DataPlaneMQTTGateway(settings)


def rate_limiter(settings: Settings, *, limit: int, window_seconds: int, prefix: str) -> RateLimiter:
    if settings.cache_provider == "memory":
        return InMemoryRateLimiter(limit=limit, window_seconds=window_seconds)
    if settings.cache_provider != "redis":
        raise RuntimeError(
            f"Unsupported cache.provider={settings.cache_provider!r}; only 'redis' and 'memory' are implemented"
        )
    import redis

    return RedisRateLimiter(
        redis.Redis.from_url(settings.redis_url),
        limit=limit,
        window_seconds=window_seconds,
        prefix=prefix,
    )


def deployment_provider(settings: Settings, session: Session) -> DeploymentProvider | None:
    """Return the configured deployment *execution* engine, or None when disabled.

    Releases and Deploy history/UI always stay in MeteorCloud. The provider only
    carries out (or cancels) a Deploy — native agents today, Mender later.
    """
    if settings.deployment_provider == "none":
        return None
    if settings.deployment_provider == "native":
        from app.adapters.native_deployment import NativeDeploymentProvider

        return NativeDeploymentProvider(session)
    if settings.deployment_provider == "mender":
        from app.adapters.mender_deployment import MenderDeploymentProvider

        return MenderDeploymentProvider(session)
    raise RuntimeError(
        f"Unsupported deployment.provider={settings.deployment_provider!r}; "
        "only 'native', 'none', and 'mender' (stub) are known"
    )


def ota_provider(settings: Settings) -> DeploymentProvider | None:
    """Deprecated: returns None when deploys are off; otherwise requires deployment_provider(session)."""
    if settings.deployment_provider == "none":
        return None
    raise RuntimeError(
        "Use deployment_provider(settings, session) for a live DeploymentProvider; "
        f"deployment.provider={settings.deployment_provider!r}"
    )


def identity_directory(settings: Settings, session: Session) -> IdentityDirectory:
    if settings.identity_provider != "local":
        raise RuntimeError(
            f"Unsupported identity.provider={settings.identity_provider!r}; only 'local' is implemented"
        )
    from app.adapters.local_identity import LocalIdentityDirectory

    return LocalIdentityDirectory(session)


def object_storage(settings: Settings) -> ObjectStorage:
    """Build the configured storage adapter (S3-compatible or local filesystem).

    Raise RuntimeError for an unsupported provider; adapter initialization errors
    propagate to the caller.
    """
    if settings.object_storage_provider == "filesystem":
        from app.adapters.filesystem_storage import FilesystemObjectStorage

        return FilesystemObjectStorage(settings)
    if settings.object_storage_provider != "s3":
        raise RuntimeError(
            f"Unsupported object_storage.provider={settings.object_storage_provider!r}; "
            "only 's3' and 'filesystem' are implemented"
        )
    from app.adapters.s3_storage import S3ObjectStorage

    return S3ObjectStorage(settings)


__all__ = [
    "InMemoryRateLimiter",
    "deployment_provider",
    "identity_directory",
    "mqtt_gateway",
    "object_storage",
    "ota_provider",
    "rate_limiter",
]
