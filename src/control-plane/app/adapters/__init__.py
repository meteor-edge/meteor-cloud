"""Select infrastructure adapters from configuration.

Unsupported provider names fail fast. Future providers are not implemented.
"""

from __future__ import annotations

from app.core.config import Settings
from app.devices.rate_limit import InMemoryRateLimiter, RedisRateLimiter
from app.mqtt.service import NoopPublisher
from app.ports.cache import RateLimiter
from app.ports.mqtt import MQTTGateway
from app.ports.ota import OTAProvider


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
    if settings.cache_provider != "redis":
        raise RuntimeError(f"Unsupported cache.provider={settings.cache_provider!r}; only 'redis' is implemented")
    import redis

    return RedisRateLimiter(
        redis.Redis.from_url(settings.redis_url),
        limit=limit,
        window_seconds=window_seconds,
        prefix=prefix,
    )


def ota_provider(settings: Settings) -> OTAProvider | None:
    if settings.ota_provider == "none":
        return None
    raise RuntimeError(f"Unsupported ota.provider={settings.ota_provider!r}; no OTA adapter is implemented")


__all__ = ["InMemoryRateLimiter", "mqtt_gateway", "ota_provider", "rate_limiter"]
