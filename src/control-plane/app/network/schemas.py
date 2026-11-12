"""Wi-Fi management request and response schemas. No response ever carries a password."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

WifiSecurity = Literal["wpa-psk", "open"]
UplinkType = Literal["ethernet", "wifi", "cellular", "other"]
UPLINK_TYPES: tuple[str, ...] = ("ethernet", "wifi", "cellular", "other")
_LINE_BREAKS = ("\x00", "\r", "\n")


def _ssid(value: str) -> str:
    if not 1 <= len(value.encode()) <= 32:
        raise ValueError("The network name must be 1 to 32 bytes")
    if any(char in value for char in _LINE_BREAKS):
        raise ValueError("The network name must not contain line breaks")
    return value


class WifiNetworkSetRequest(BaseModel):
    """Add a network to a device, or change the password of one it has."""

    ssid: str
    security: WifiSecurity = "wpa-psk"
    password: str | None = Field(default=None, description="8 to 63 characters; not for open networks")
    hidden: bool = False
    # True: connect now and go back to the old settings if MeteorCloud cannot be reached.
    # False: save it; the device uses it the next time it connects to that network.
    connect_now: bool = False

    @field_validator("ssid")
    @classmethod
    def _valid_ssid(cls, value: str) -> str:
        return _ssid(value)

    @model_validator(mode="after")
    def _password_fits_security(self) -> WifiNetworkSetRequest:
        if self.security == "open":
            if self.password:
                raise ValueError("An open network has no password")
            return self
        password = self.password or ""
        if not 8 <= len(password) <= 63:
            raise ValueError("The Wi-Fi password must be 8 to 63 characters")
        if any(char in password for char in _LINE_BREAKS):
            raise ValueError("The Wi-Fi password must not contain line breaks")
        return self


class WifiNetworkRemoveRequest(BaseModel):
    ssid: str

    @field_validator("ssid")
    @classmethod
    def _valid_ssid(cls, value: str) -> str:
        return _ssid(value)


class WifiNetworkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ssid: str
    security: str
    hidden: bool
    connect_now: bool
    action: str
    state: str
    last_error: str | None
    reported_at: datetime | None
    updated_at: datetime
    # A password is waiting on the server for the device. It is erased when the device reports.
    password_pending: bool = False


class WifiSavedNetwork(BaseModel):
    ssid: str = Field(max_length=64)
    # Managed from this console (as opposed to written at image time or by hand).
    managed: bool = False


class WifiStatusResponse(BaseModel):
    supported: bool
    unsupported_reason: str | None
    connected_ssid: str | None
    signal_percent: int | None
    interface: str | None
    wired_connected: bool
    saved_networks: list[WifiSavedNetwork]
    # None from agents before the uplink was reported.
    uplink_type: UplinkType | None = None
    uplink_interface: str | None = None
    reported_at: datetime


class DeviceWifiResponse(BaseModel):
    # None: the device has not reported its Wi-Fi yet (older agent, or never online since).
    status: WifiStatusResponse | None
    networks: list[WifiNetworkResponse]


# ------------------------------------------------------------------ agent side
class AgentWifiChange(BaseModel):
    id: uuid.UUID
    revision: int
    action: Literal["apply", "remove"]
    ssid: str
    security: str
    hidden: bool
    connect_now: bool
    password: str | None = None


class AgentWifiChangesResponse(BaseModel):
    changes: list[AgentWifiChange]


class AgentWifiStatus(BaseModel):
    supported: bool = False
    unsupported_reason: str | None = Field(default=None, max_length=255)
    connected_ssid: str | None = Field(default=None, max_length=64)
    signal_percent: int | None = Field(default=None, ge=0, le=100)
    interface: str | None = Field(default=None, max_length=32)
    wired_connected: bool = False
    saved_networks: list[WifiSavedNetwork] = Field(default_factory=list, max_length=64)
    # The link traffic leaves on (default route). A kind this server does not know is stored as "other".
    uplink_type: str | None = Field(default=None, max_length=16)
    uplink_interface: str | None = Field(default=None, max_length=32)

    @field_validator("uplink_type")
    @classmethod
    def _known_uplink(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value if value in UPLINK_TYPES else "other"


class AgentWifiResult(BaseModel):
    id: uuid.UUID
    revision: int
    state: Literal["applied", "removed", "rolled_back", "failed"]
    error: str | None = Field(default=None, max_length=500)


class AgentWifiReportRequest(BaseModel):
    status: AgentWifiStatus | None = None
    results: list[AgentWifiResult] = Field(default_factory=list, max_length=64)


class AgentWifiReportResponse(BaseModel):
    accepted: int
