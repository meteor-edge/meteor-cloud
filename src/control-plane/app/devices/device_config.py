"""The settings file a device reads on first boot (``.meteorenv``, version 2).

The same file Meteor Imager writes to a card: enrollment, Wi-Fi, hostname, timezone,
login user and SSH, as ``KEY=value`` lines that ``meteorcli-settings.sh`` applies on a
Meteor base image. The console offers it as a download, so a card can also be set up by
copying one file to its boot partition.

Keep the keys, their order and the validation in step with Meteor Imager
(``internal/bootfs/inject.go`` ``MeteorEnvV2`` and ``internal/profile/profile.go``).
"""

from __future__ import annotations

import re
import zoneinfo
from functools import cache

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.shacrypt import sha512_crypt

# Dot files are hidden on macOS and renamed by browsers, so the download has a plain name.
# meteorcli-settings.sh reads either.
DEVICE_CONFIG_FILE_NAME = "meteorenv.txt"

_COUNTRY = re.compile(r"^[A-Z]{2}$")
_HOSTNAME = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_DOMAIN = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:[0-9]{1,5})?$")
_SSH_KEY = re.compile(
    r"^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521)|sk-ssh-ed25519@openssh\.com"
    r"|sk-ecdsa-sha2-nistp256@openssh\.com) [A-Za-z0-9+/=]+( [^\r\n]*)?$"
)
_USER = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_TIMEZONE = re.compile(r"^[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+){0,2}$")
_LINE_BREAKS = ("\x00", "\r", "\n")


def _single_line(value: str, what: str) -> str:
    if any(char in value for char in _LINE_BREAKS):
        raise ValueError(f"{what} must not contain line breaks")
    return value


@cache
def _known_timezones() -> frozenset[str]:
    return frozenset(zoneinfo.available_timezones())


class DeviceConfigEnrollment(BaseModel):
    domain: str = Field(max_length=255, description="Host or host:port of this MeteorCloud, as the device reaches it")
    api_key: str = Field(max_length=255, description="Enrollment key (key_…)")
    device_name: str | None = Field(default=None, max_length=63)

    @field_validator("domain")
    @classmethod
    def _domain(cls, value: str) -> str:
        if not value:
            raise ValueError("is required")
        if "://" in value or any(char in value for char in "/?# "):
            raise ValueError("use only the host (and port), e.g. fleet.example.com or 192.168.0.10:8000")
        if value.split(":")[0].lower() == "localhost" or value.startswith("127."):
            raise ValueError("must be an address the device can reach, not localhost")
        if not _DOMAIN.fullmatch(value):
            raise ValueError("is not a valid host or host:port")
        return value

    @field_validator("api_key")
    @classmethod
    def _api_key(cls, value: str) -> str:
        if not value.startswith("key_") or len(value) < 8 or any(char in value for char in " \t\r\n=\"'"):
            raise ValueError("must be a MeteorCloud enrollment key (starts with key_)")
        return value

    @field_validator("device_name")
    @classmethod
    def _device_name(cls, value: str | None) -> str | None:
        if not value:
            return None
        if value.strip() != value or any(char in value for char in (*_LINE_BREAKS, "=")):
            raise ValueError("must be 1 to 63 characters, without line breaks, = or leading/trailing spaces")
        return value


class DeviceConfigWifi(BaseModel):
    ssid: str
    psk: str
    country: str
    hidden: bool = False

    @field_validator("ssid")
    @classmethod
    def _ssid(cls, value: str) -> str:
        if not 1 <= len(value.encode()) <= 32:
            raise ValueError("must be 1 to 32 bytes")
        return _single_line(value, "The Wi-Fi name")

    @field_validator("psk")
    @classmethod
    def _psk(cls, value: str) -> str:
        if not 8 <= len(value) <= 63:
            raise ValueError("must be 8 to 63 characters")
        return _single_line(value, "The Wi-Fi password")

    @field_validator("country")
    @classmethod
    def _country(cls, value: str) -> str:
        if not _COUNTRY.fullmatch(value):
            raise ValueError("must be two upper-case letters, e.g. DE; Wi-Fi stays off without it")
        return value


class DeviceConfigUser(BaseModel):
    name: str
    # Stored on the card only as a SHA-512 crypt hash. Empty: the user keeps the image's password.
    password: str = Field(default="", max_length=256)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not _USER.fullmatch(value) or value == "root":
            raise ValueError("must be a Linux user name (lower-case letters, digits, - or _; not root)")
        return value

    @field_validator("password")
    @classmethod
    def _password(cls, value: str) -> str:
        return _single_line(value, "The password")


class DeviceConfigSsh(BaseModel):
    enabled: bool | None = None
    password_authentication: bool | None = None
    authorized_key: str | None = None
    # Who gets the key: this user, else the login user above, else the image's first user.
    user: str | None = None

    @field_validator("authorized_key")
    @classmethod
    def _authorized_key(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        if not value:
            return None
        if not _SSH_KEY.fullmatch(value):
            raise ValueError("must be an OpenSSH public key (ssh-ed25519 AAAA… comment)")
        return value

    @field_validator("user")
    @classmethod
    def _user(cls, value: str | None) -> str | None:
        if not value:
            return None
        if not _USER.fullmatch(value):
            raise ValueError("must be a Linux user name")
        return value


class DeviceConfigRequest(BaseModel):
    enrollment: DeviceConfigEnrollment | None = None
    hostname: str | None = None
    timezone: str | None = None
    wifi: DeviceConfigWifi | None = None
    user: DeviceConfigUser | None = None
    ssh: DeviceConfigSsh | None = None

    @field_validator("hostname")
    @classmethod
    def _hostname(cls, value: str | None) -> str | None:
        if not value:
            return None
        if not _HOSTNAME.fullmatch(value):
            raise ValueError("must be 1 to 63 lower-case letters, digits or hyphens")
        return value

    @field_validator("timezone")
    @classmethod
    def _timezone(cls, value: str | None) -> str | None:
        if not value:
            return None
        known = _known_timezones()
        if value == "Local" or not _TIMEZONE.fullmatch(value) or (known and value not in known):
            raise ValueError("must be an IANA name such as Europe/Berlin")
        return value

    @model_validator(mode="after")
    def _not_empty(self) -> DeviceConfigRequest:
        ssh = self.ssh
        ssh_set = ssh is not None and (
            ssh.enabled is not None or ssh.password_authentication is not None or ssh.authorized_key is not None
        )
        if not (self.enrollment or self.hostname or self.timezone or self.wifi or self.user or ssh_set):
            raise ValueError("Nothing to write: set enrollment, Wi-Fi, hostname, timezone, user or SSH")
        return self


class DeviceConfigResponse(BaseModel):
    file_name: str
    content: str


def render_device_config(config: DeviceConfigRequest, *, password_hash: str | None = None) -> str:
    """``.meteorenv`` version 2 for ``config``. ``password_hash`` is for tests; else a fresh hash is made."""
    lines: list[str] = []

    def kv(key: str, value: str) -> None:
        lines.append(f"{key}={value}")

    def flag(value: bool) -> str:
        return "true" if value else "false"

    kv("METEOR_ENV_VERSION", "2")
    if config.enrollment is not None:
        kv("METEOR_DOMAIN", config.enrollment.domain)
        kv("METEOR_API_KEY", config.enrollment.api_key)
        if config.enrollment.device_name:
            kv("METEOR_NAME", config.enrollment.device_name)
    if config.hostname:
        kv("METEOR_HOSTNAME", config.hostname)
    if config.timezone:
        kv("METEOR_TIMEZONE", config.timezone)
    if config.wifi is not None:
        kv("METEOR_WIFI_SSID", config.wifi.ssid)
        kv("METEOR_WIFI_PSK", config.wifi.psk)
        kv("METEOR_WIFI_COUNTRY", config.wifi.country)
        kv("METEOR_WIFI_HIDDEN", flag(config.wifi.hidden))
    if config.user is not None:
        kv("METEOR_USER", config.user.name)
        if config.user.password:
            kv("METEOR_USER_PASSWORD_HASH", password_hash or sha512_crypt(config.user.password))
    if config.ssh is not None:
        if config.ssh.enabled is not None:
            kv("METEOR_SSH_ENABLED", flag(config.ssh.enabled))
        if config.ssh.password_authentication is not None:
            kv("METEOR_SSH_PASSWORD_AUTH", flag(config.ssh.password_authentication))
        if config.ssh.authorized_key:
            kv("METEOR_SSH_AUTHORIZED_KEY", config.ssh.authorized_key)
            if config.ssh.user:
                kv("METEOR_SSH_USER", config.ssh.user)
    return "\n".join(lines) + "\n"
