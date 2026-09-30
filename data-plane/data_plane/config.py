"""Data-plane process settings."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    control_plane_url: str = Field(default="http://127.0.0.1:8000", alias="CONTROL_PLANE_URL")
    http_addr: str = Field(default=":8081", alias="HTTP_ADDR")
    mqtt_internal_token: str = Field(default="dev-mqtt-internal", alias="MQTT_INTERNAL_TOKEN")
    mqtt_broker_host: str = Field(default="localhost", alias="MQTT_BROKER_HOST")
    mqtt_broker_port: int = Field(default=8883, alias="MQTT_BROKER_PORT")
    mqtt_platform_username: str = Field(default="platform", alias="MQTT_PLATFORM_USERNAME")
    mqtt_platform_password: str = Field(default="dev-mqtt-platform", alias="MQTT_PLATFORM_PASSWORD")
    mqtt_ca_cert_path: str = Field(default="certs/ca.crt", alias="MQTT_CA_CERT_PATH")
    mqtt_provider: str = Field(default="emqx", alias="MQTT_PROVIDER")
    mqtt_enabled: bool = Field(default=True, alias="MQTT_ENABLED")

    def http_bind(self) -> tuple[str, int]:
        text = self.http_addr.strip()
        if text.startswith(":"):
            return "0.0.0.0", int(text[1:])
        host, _, port = text.rpartition(":")
        return (host or "0.0.0.0"), int(port)


@lru_cache
def get_settings() -> Settings:
    return Settings()
