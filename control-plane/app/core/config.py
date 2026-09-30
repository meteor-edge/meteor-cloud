"""Application settings loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the Edge Platform backend."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="edge-platform", alias="APP_NAME")
    app_version: str = "0.1.0"
    app_env: str = Field(default="development", alias="APP_ENV")
    app_debug: bool = Field(default=False, alias="APP_DEBUG")
    app_secret_key: str = Field(default="change-me", alias="APP_SECRET_KEY")

    backend_host: str = Field(default="0.0.0.0", alias="BACKEND_HOST")
    backend_port: int = Field(default=8000, alias="BACKEND_PORT")
    backend_cors_origins: str = Field(
        default="http://localhost:5173",
        alias="BACKEND_CORS_ORIGINS",
    )

    database_url: str = Field(
        default="postgresql+psycopg://edge:edge@localhost:5432/edge_platform",
        alias="DATABASE_URL",
    )

    jwt_secret_key: str = Field(default="change-me-jwt", alias="JWT_SECRET_KEY")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_access_token_expire_minutes: int = Field(
        default=30,
        alias="JWT_ACCESS_TOKEN_EXPIRE_MINUTES",
    )

    redis_url: str = Field(
        default="redis://localhost:6379/0",
        alias="REDIS_URL",
    )

    # Fleet / device connectivity settings.
    device_heartbeat_interval_seconds: int = Field(
        default=60,
        alias="DEVICE_HEARTBEAT_INTERVAL_SECONDS",
    )
    device_offline_threshold_seconds: int = Field(
        default=150,
        alias="DEVICE_OFFLINE_THRESHOLD_SECONDS",
    )

    # Device registration rate limiting (fixed window per source IP).
    registration_rate_limit_requests: int = Field(
        default=10,
        alias="REGISTRATION_RATE_LIMIT_REQUESTS",
    )
    registration_rate_limit_window_seconds: int = Field(
        default=60,
        alias="REGISTRATION_RATE_LIMIT_WINDOW_SECONDS",
    )

    # Device-initiated enrollment (request -> approve -> claim).
    enrollment_request_rate_limit_requests: int = Field(
        default=5,
        alias="ENROLLMENT_REQUEST_RATE_LIMIT_REQUESTS",
    )
    enrollment_request_rate_limit_window_seconds: int = Field(
        default=60,
        alias="ENROLLMENT_REQUEST_RATE_LIMIT_WINDOW_SECONDS",
    )
    enrollment_poll_rate_limit_requests: int = Field(
        default=30,
        alias="ENROLLMENT_POLL_RATE_LIMIT_REQUESTS",
    )
    enrollment_poll_rate_limit_window_seconds: int = Field(
        default=60,
        alias="ENROLLMENT_POLL_RATE_LIMIT_WINDOW_SECONDS",
    )
    # Interval the device should wait between poll attempts (returned to the CLI).
    enrollment_poll_interval_seconds: int = Field(
        default=10,
        alias="ENROLLMENT_POLL_INTERVAL_SECONDS",
    )
    # How long a pending enrollment request stays valid before it expires.
    enrollment_request_ttl_seconds: int = Field(
        default=3600,
        alias="ENROLLMENT_REQUEST_TTL_SECONDS",
    )

    mqtt_enabled: bool = Field(default=False, alias="MQTT_ENABLED")
    mqtt_broker_host: str = Field(default="localhost", alias="MQTT_BROKER_HOST")
    mqtt_public_host: str = Field(default="localhost", alias="MQTT_PUBLIC_HOST")
    mqtt_broker_port: int = Field(default=8883, alias="MQTT_BROKER_PORT")
    mqtt_platform_username: str = Field(default="platform", alias="MQTT_PLATFORM_USERNAME")
    mqtt_platform_password: str = Field(default="dev-mqtt-platform", alias="MQTT_PLATFORM_PASSWORD")
    mqtt_internal_token: str = Field(default="dev-mqtt-internal", alias="MQTT_INTERNAL_TOKEN")
    mqtt_ca_cert_path: str = Field(default="certs/ca.crt", alias="MQTT_CA_CERT_PATH")
    mqtt_ping_timeout_seconds: float = Field(default=8.0, alias="MQTT_PING_TIMEOUT_SECONDS")

    database_provider: Literal["postgresql"] = Field(default="postgresql", alias="DATABASE_PROVIDER")
    cache_provider: Literal["redis"] = Field(default="redis", alias="CACHE_PROVIDER")
    mqtt_provider: Literal["emqx"] = Field(default="emqx", alias="MQTT_PROVIDER")
    ota_provider: Literal["none"] = Field(default="none", alias="OTA_PROVIDER")

    # When True, agent registration over plain HTTP is rejected. Left False for
    # now (Milestone 4) but available so HTTPS can be enforced later.
    registration_require_https: bool = Field(
        default=False,
        alias="REGISTRATION_REQUIRE_HTTPS",
    )

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_format: Literal["console", "json"] = Field(default="console", alias="LOG_FORMAT")
    observability_backend: Literal["prometheus", "cloudwatch"] = Field(
        default="prometheus",
        alias="OBSERVABILITY_BACKEND",
        description="prometheus (Loki/Grafana) or cloudwatch (reserved, not implemented).",
    )

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.backend_cors_origins.split(",") if origin.strip()]

    @field_validator("app_env")
    @classmethod
    def normalize_env(cls, value: str) -> str:
        return value.lower()

    @field_validator("log_format", mode="before")
    @classmethod
    def normalize_log_format(cls, value: object) -> str:
        text = str(value).strip().lower() if value is not None else ""
        if text in {"", "none", "false", "0", "no", "off"}:
            return "console"
        if text in {"true", "1", "yes", "on"}:
            return "json"
        return text


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
