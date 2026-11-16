"""Application settings loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_CONNECTIVITY_INTERNAL_TOKEN = "dev-connectivity-internal"

# Defaults and example values that are public (this repository, .env.example). Production
# refuses them: a known JWT secret lets anyone sign in as any user.
KNOWN_DEFAULT_SECRETS = frozenset(
    {
        "change-me",
        "change-me-jwt",
        "change-me-to-a-long-random-secret-key",
        "change-me-jwt-secret-key-must-be-long",
        "dev-mqtt-internal",
        "dev-mqtt-platform",
        DEV_CONNECTIVITY_INTERNAL_TOKEN,
    }
)
# Shortest secret production accepts. The installers generate 32+ characters.
MIN_SECRET_LENGTH = 16


class Settings(BaseSettings):
    """Runtime configuration for the Edge Platform backend."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = Field(default="edge-platform", alias="APP_NAME")
    app_version: str = "0.1.0"
    app_env: str = Field(default="development", alias="APP_ENV")
    app_debug: bool = Field(default=False, alias="APP_DEBUG")
    app_secret_key: str = Field(default="change-me", alias="APP_SECRET_KEY")
    # Encrypts secrets held for devices (a Wi-Fi password until the device has it).
    # Empty: derived from APP_SECRET_KEY.
    secrets_encryption_key: str = Field(default="", alias="SECRETS_ENCRYPTION_KEY")
    # How long an undelivered Wi-Fi password is kept before it is erased.
    wifi_password_ttl_seconds: int = Field(default=7 * 24 * 3600, alias="WIFI_PASSWORD_TTL_SECONDS", ge=60)
    # How long a waiting restart, shut-down or SSH-user change is kept before it is cancelled.
    # A restart ordered for a device that was offline must not run hours later.
    device_action_ttl_seconds: int = Field(default=3600, alias="DEVICE_ACTION_TTL_SECONDS", ge=60)

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
    # Whether anyone who reaches the API may create an account (POST /api/v1/auth/register).
    # Off: people join only when an administrator adds them to an organization.
    allow_signup: bool = Field(default=True, alias="ALLOW_SIGNUP")
    # Sign-in attempts per fixed window, counted per email address and per source IP.
    login_rate_limit_requests: int = Field(default=10, ge=0, alias="LOGIN_RATE_LIMIT_REQUESTS")
    login_ip_rate_limit_requests: int = Field(default=60, ge=0, alias="LOGIN_IP_RATE_LIMIT_REQUESTS")
    login_rate_limit_window_seconds: int = Field(default=300, ge=1, alias="LOGIN_RATE_LIMIT_WINDOW_SECONDS")

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
    # How long "Allow re-registration" lets known hardware register again without its
    # current device token (e.g. after the card was re-flashed).
    # Docker releases: resolve the image tag to a digest when the release is saved, so
    # devices pull exactly that image (docs/fleet/signed-updates.md). Public registries only.
    release_resolve_image_digests: bool = True
    # Refuse a docker release whose tag cannot be resolved (otherwise it keeps the tag).
    release_require_image_digests: bool = False
    device_reregistration_window_seconds: int = Field(
        default=24 * 3600,
        ge=60,
        alias="DEVICE_REREGISTRATION_WINDOW_SECONDS",
    )
    # How long the previous device credential still proves identity after a rotation,
    # so a lost register response can be retried with the old token.
    device_registration_retry_seconds: int = Field(
        default=300,
        ge=30,
        le=3600,
        alias="DEVICE_REGISTRATION_RETRY_SECONDS",
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

    # Public OS image list and download (Meteor Imager), per source IP.
    public_os_image_rate_limit_requests: int = Field(
        default=60,
        alias="PUBLIC_OS_IMAGE_RATE_LIMIT_REQUESTS",
    )
    public_os_image_rate_limit_window_seconds: int = Field(
        default=60,
        alias="PUBLIC_OS_IMAGE_RATE_LIMIT_WINDOW_SECONDS",
    )

    # Device-initiated enrollment (request -> approve -> claim).
    # Per source IP and key. Sites flash a batch of devices behind one NAT with one key,
    # and devices back off on 429, so this allows a burst rather than a trickle.
    enrollment_request_rate_limit_requests: int = Field(
        default=30,
        alias="ENROLLMENT_REQUEST_RATE_LIMIT_REQUESTS",
    )
    # Per source IP, checked before the key is looked up (so wrong keys are limited too).
    enrollment_ip_rate_limit_requests: int = Field(
        default=120,
        alias="ENROLLMENT_IP_RATE_LIMIT_REQUESTS",
    )
    # Pending (unanswered) requests one key may have at a time; 0 means no limit.
    # Stops a leaked key from filling the approval queue.
    enrollment_max_pending_per_key: int = Field(
        default=100,
        alias="ENROLLMENT_MAX_PENDING_PER_KEY",
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
    # Cap per source IP across all request ids; high enough for a site's devices behind one NAT.
    enrollment_poll_ip_rate_limit_requests: int = Field(
        default=600,
        alias="ENROLLMENT_POLL_IP_RATE_LIMIT_REQUESTS",
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
    data_plane_url: str = Field(default="http://127.0.0.1:8081", alias="DATA_PLANE_URL")
    telemetry_provider: str = Field(default="postgresql", alias="TELEMETRY_PROVIDER")
    # Application data plane (Go service, contracts/data-plane-v1.md).
    # Internal URL the control plane calls (commands); empty disables command forwarding.
    data_plane_api_url: str = Field(default="", alias="DATA_PLANE_API_URL")
    # Public URL handed to applications as METEORCLOUD_ENDPOINT.
    data_plane_public_url: str = Field(default="", alias="DATA_PLANE_PUBLIC_URL")
    # Shared secret for /internal/data-plane/* (policy snapshot) and data-plane /internal/v1/*.
    data_plane_internal_token: str = Field(default="", alias="DATA_PLANE_INTERNAL_TOKEN")
    # Seconds between sends of the platform history outbox (deployment changes) to the data plane.
    history_relay_interval_seconds: float = Field(default=5.0, gt=0, le=300, alias="HISTORY_RELAY_INTERVAL_SECONDS")
    # Lifetime of credentials the device agent mints for deployed workloads.
    app_credential_ttl_hours: int = Field(default=168, ge=1, le=8760, alias="APP_CREDENTIAL_TTL_HOURS")

    database_provider: Literal["postgresql"] = Field(default="postgresql", alias="DATABASE_PROVIDER")
    # "memory" keeps rate-limit counters per process: fine for one API replica, no Redis needed.
    cache_provider: Literal["redis", "memory"] = Field(default="redis", alias="CACHE_PROVIDER")
    mqtt_provider: Literal["mosquitto", "emqx", "vernemq"] = Field(default="mosquitto", alias="MQTT_PROVIDER")
    # Who runs fleet deploys. "native" = MeteorCloud engine; "none" disables
    # deploy routes; "mender" reserved (fail fast until implemented).
    deployment_provider: Literal["native", "none", "mender"] = Field(
        default="native",
        alias="DEPLOYMENT_PROVIDER",
    )
    # Seconds between Deploy sweeps (batched delays, phased soak, pending timeouts
    # without waiting for a heartbeat). 0 disables the sweeper.
    deploy_tick_interval_seconds: int = Field(
        default=60,
        ge=0,
        alias="DEPLOY_TICK_INTERVAL_SECONDS",
    )
    # Deprecated alias; prefer DEPLOYMENT_PROVIDER. Kept so existing env files load.
    ota_provider: Literal["none"] = Field(default="none", alias="OTA_PROVIDER")
    # Private device network. "none" disables peer APIs; "wireguard" is the v1 transport.
    connectivity_provider: Literal["wireguard", "none"] = Field(
        default="none",
        alias="CONNECTIVITY_PROVIDER",
    )
    wireguard_server_public_key: str = Field(default="", alias="WIREGUARD_SERVER_PUBLIC_KEY")
    wireguard_server_endpoint: str = Field(default="", alias="WIREGUARD_SERVER_ENDPOINT")
    wireguard_server_address: str = Field(default="10.8.0.1/16", alias="WIREGUARD_SERVER_ADDRESS")
    wireguard_client_cidr: str = Field(default="10.8.0.0/16", alias="WIREGUARD_CLIENT_CIDR")
    wireguard_client_dns: str = Field(default="", alias="WIREGUARD_CLIENT_DNS")
    wireguard_persistent_keepalive: int = Field(default=25, alias="WIREGUARD_PERSISTENT_KEEPALIVE")
    connectivity_internal_token: str = Field(
        default=DEV_CONNECTIVITY_INTERNAL_TOKEN, alias="CONNECTIVITY_INTERNAL_TOKEN"
    )
    connectivity_handshake_stale_seconds: int = Field(
        default=180,
        alias="CONNECTIVITY_HANDSHAKE_STALE_SECONDS",
    )
    connectivity_rate_limit_requests: int = Field(default=20, alias="CONNECTIVITY_RATE_LIMIT_REQUESTS")
    connectivity_rate_limit_window_seconds: int = Field(
        default=60,
        alias="CONNECTIVITY_RATE_LIMIT_WINDOW_SECONDS",
    )
    # Public app hostnames: <app>.<device>.<org>.{APPS_BASE_DOMAIN} (suggested) or <name>.<org>.{APPS_BASE_DOMAIN}
    # (custom). APPS_RESERVED_NAMES: comma-separated names that already exist under that domain.
    # APPS_ALLOW_SHORT_NAMES: also allow <name>.{APPS_BASE_DOMAIN}, shared by all organizations.
    apps_base_domain: str = Field(default="meteor-edge.com", alias="APPS_BASE_DOMAIN")
    apps_reserved_names: str = Field(default="", alias="APPS_RESERVED_NAMES")
    apps_allow_short_names: bool = Field(default=False, alias="APPS_ALLOW_SHORT_NAMES")
    routing_internal_token: str = Field(default="", alias="ROUTING_INTERNAL_TOKEN")
    # Browser SSH. "echo" is for tests only.
    shell_provider: Literal["ssh_gateway", "echo", "none"] = Field(default="none", alias="SHELL_PROVIDER")
    shell_ssh_user: str = Field(default="root", alias="SHELL_SSH_USER")
    shell_ssh_port: int = Field(default=22, alias="SHELL_SSH_PORT")
    shell_ssh_identity_file: str = Field(default="", alias="SHELL_SSH_IDENTITY_FILE")
    shell_gateway_public_key: str = Field(default="", alias="SHELL_GATEWAY_PUBLIC_KEY")
    # Refuse SSH to devices that never reported a host key (older agents) instead of trusting on first use.
    shell_require_host_key: bool = Field(default=False, alias="SHELL_REQUIRE_HOST_KEY")
    # SSH user CA: each session signs a fresh key for one device (principal meteorcloud-device-<id>).
    shell_ssh_ca_file: str = Field(default="", alias="SHELL_SSH_CA_FILE")
    shell_ssh_ca_public_key: str = Field(default="", alias="SHELL_SSH_CA_PUBLIC_KEY")
    shell_ssh_cert_ttl_seconds: int = Field(default=120, ge=30, le=3600, alias="SHELL_SSH_CERT_TTL_SECONDS")
    # Once every device reports ssh_auth=ca: stop handing out the shared gateway key.
    shell_gateway_key_retired: bool = Field(default=False, alias="SHELL_GATEWAY_KEY_RETIRED")
    shell_ticket_ttl_seconds: int = Field(default=60, alias="SHELL_TICKET_TTL_SECONDS")
    shell_idle_timeout_seconds: int = Field(default=600, alias="SHELL_IDLE_TIMEOUT_SECONDS")
    shell_max_duration_seconds: int = Field(default=3600, alias="SHELL_MAX_DURATION_SECONDS")
    shell_max_sessions_per_user: int = Field(default=4, alias="SHELL_MAX_SESSIONS_PER_USER")
    # How often an open shell re-checks it is still allowed (revoke, disable, lost permission).
    shell_recheck_seconds: int = Field(default=10, ge=1, alias="SHELL_RECHECK_SECONDS")
    # "local" = accounts in the PostgreSQL users table.
    identity_provider: Literal["local"] = Field(default="local", alias="IDENTITY_PROVIDER")

    # Object storage for artifacts. "s3" covers AWS S3 and any S3-compatible API (MinIO,
    # Ceph, GCS interoperability, ...). "filesystem" stores files under object_storage_path.
    object_storage_provider: Literal["s3", "filesystem"] = Field(default="s3", alias="OBJECT_STORAGE_PROVIDER")
    # Empty endpoint means the AWS S3 default; other S3-compatible services need an explicit URL.
    object_storage_endpoint_url: str = Field(default="", alias="OBJECT_STORAGE_ENDPOINT_URL")
    object_storage_path: str = Field(default="/data/artifacts", alias="OBJECT_STORAGE_PATH")
    object_storage_region: str = Field(default="us-east-1", alias="OBJECT_STORAGE_REGION")
    object_storage_bucket: str = Field(default="meteorcloud-artifacts", alias="OBJECT_STORAGE_BUCKET")
    # Empty credentials fall back to the provider's default chain (e.g. an instance role).
    object_storage_access_key_id: str = Field(default="", alias="OBJECT_STORAGE_ACCESS_KEY_ID")
    object_storage_secret_access_key: str = Field(default="", alias="OBJECT_STORAGE_SECRET_ACCESS_KEY")
    object_storage_auto_create_bucket: bool = Field(
        default=True,
        alias="OBJECT_STORAGE_AUTO_CREATE_BUCKET",
    )
    # Endpoint devices use to reach the bucket, when it differs from the API's own
    # (e.g. MinIO at http://minio:9000 inside Compose but https://s3.example.com outside).
    object_storage_public_endpoint_url: str = Field(default="", alias="OBJECT_STORAGE_PUBLIC_ENDPOINT_URL")

    artifact_max_upload_bytes: int = Field(
        default=8 * 1024 * 1024 * 1024,
        alias="ARTIFACT_MAX_UPLOAD_BYTES",
    )
    artifact_download_link_ttl_seconds: int = Field(
        default=300,
        alias="ARTIFACT_DOWNLOAD_LINK_TTL_SECONDS",
    )
    # Agents download artifacts straight from S3-compatible storage through a short-lived
    # presigned URL instead of through the API (docs/fleet/artifacts.md).
    artifact_download_redirect: bool = Field(default=True, alias="ARTIFACT_DOWNLOAD_REDIRECT")
    artifact_download_url_ttl_seconds: int = Field(
        default=900, ge=60, le=7 * 24 * 3600, alias="ARTIFACT_DOWNLOAD_URL_TTL_SECONDS"
    )

    # When True, agent registration over plain HTTP is rejected. Left False for
    # now (Milestone 4) but available so HTTPS can be enforced later.
    registration_require_https: bool = Field(
        default=False,
        alias="REGISTRATION_REQUIRE_HTTPS",
    )

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_format: Literal["console", "json"] = Field(default="console", alias="LOG_FORMAT")
    # Observability switches. The app never connects to a metrics or log system; these only
    # control what it exposes on /metrics and writes to stdout.
    metrics_enabled: bool = Field(default=True, alias="METRICS_ENABLED")
    # Fleet gauges (devices online/offline, waiting on deploys) computed on scrape, cached.
    fleet_metrics_enabled: bool = Field(default=True, alias="FLEET_METRICS_ENABLED")
    fleet_metrics_cache_seconds: float = Field(default=30.0, ge=0, alias="FLEET_METRICS_CACHE_SECONDS")
    # One JSON line per request. None (unset) = on when LOG_FORMAT=json, off otherwise.
    access_log: bool | None = Field(default=None, alias="ACCESS_LOG")
    observability_backend: Literal["prometheus", "cloudwatch"] = Field(
        default="prometheus",
        alias="OBSERVABILITY_BACKEND",
        description="prometheus (Loki/Grafana) or cloudwatch (reserved, not implemented).",
    )

    @property
    def access_log_enabled(self) -> bool:
        if self.access_log is not None:
            return self.access_log
        return self.log_format == "json"

    @property
    def reserved_app_names(self) -> frozenset[str]:
        return frozenset(name.strip().lower() for name in self.apps_reserved_names.split(",") if name.strip())

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.backend_cors_origins.split(",") if origin.strip()]

    @field_validator("app_env")
    @classmethod
    def normalize_env(cls, value: str) -> str:
        return value.lower()

    @field_validator("telemetry_provider")
    @classmethod
    def require_postgresql_telemetry(cls, value: str) -> str:
        provider = value.strip().lower()
        if provider != "postgresql":
            raise ValueError(
                f"Unsupported telemetry.provider={value!r}; only 'postgresql' is implemented "
                "(timescale and clickhouse are reserved)"
            )
        return provider

    @field_validator("access_log", mode="before")
    @classmethod
    def empty_access_log_means_default(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("log_format", mode="before")
    @classmethod
    def normalize_log_format(cls, value: object) -> str:
        text = str(value).strip().lower() if value is not None else ""
        if text in {"", "none", "false", "0", "no", "off"}:
            return "console"
        if text in {"true", "1", "yes", "on"}:
            return "json"
        return text

    @model_validator(mode="after")
    def _require_real_connectivity_token(self) -> Settings:
        # The gateway agent and Traefik authenticate with this token; a known default would let
        # anything that reaches the API list peers and post handshakes.
        if (
            self.app_env == "production"
            and self.connectivity_provider != "none"
            and self.connectivity_internal_token in {"", DEV_CONNECTIVITY_INTERNAL_TOKEN}
        ):
            raise ValueError(
                "CONNECTIVITY_INTERNAL_TOKEN must be set to a non-default secret when "
                "CONNECTIVITY_PROVIDER is enabled and APP_ENV=production"
            )
        return self


    @model_validator(mode="after")
    def _require_real_secrets_in_production(self) -> Settings:
        if self.app_env != "production":
            return self
        required: dict[str, str] = {
            "JWT_SECRET_KEY": self.jwt_secret_key,
            "APP_SECRET_KEY": self.app_secret_key,
        }
        # Internal MQTT routes are mounted even when MQTT_ENABLED is false.
        required["MQTT_INTERNAL_TOKEN"] = self.mqtt_internal_token
        if self.mqtt_enabled:
            required["MQTT_PLATFORM_PASSWORD"] = self.mqtt_platform_password
        optional: dict[str, str] = {
            "SECRETS_ENCRYPTION_KEY": self.secrets_encryption_key,
            "DATA_PLANE_INTERNAL_TOKEN": self.data_plane_internal_token,
        }
        weak = [name for name, value in required.items() if _is_weak_secret(value)]
        weak += [name for name, value in optional.items() if value and _is_weak_secret(value)]
        if weak:
            raise ValueError(
                f"APP_ENV=production refuses default, example or short secrets: {', '.join(sorted(weak))}. "
                f"Set each to a random value of at least {MIN_SECRET_LENGTH} characters "
                "(for example: openssl rand -hex 32)."
            )
        return self


def _is_weak_secret(value: str) -> bool:
    return value in KNOWN_DEFAULT_SECRETS or len(value) < MIN_SECRET_LENGTH


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
