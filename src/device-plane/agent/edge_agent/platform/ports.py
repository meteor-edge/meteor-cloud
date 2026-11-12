"""Device-plane ports: same contracts for Raspberry Pi, NUC, Jetson, and later boards.

Implementors ship one agent binary and swap these adapters per OS/board family.
Control-plane and console keep consuming the same field names and action kinds.
"""

from __future__ import annotations

from typing import Any, Protocol


class InventoryCollector(Protocol):
    """Identity and hardware snapshot used at enroll / request-token.

    Required shape (all values optional at the wire level):
    ``serial_number``, ``mac_addresses``, ``hostname``, ``os_name``, ``os_version``,
    ``kernel_version``, ``architecture``, ``hardware_model``, ``cpu_model``,
    ``cpu_cores``, ``memory_mb``, ``local_ip``, ``agent_version``.
    """

    def collect(self) -> dict[str, Any]: ...


class MetricsCollector(Protocol):
    """Live health readings (MQTT ``devices/{id}/metrics`` today).

    Common keys: ``cpu_percent``, ``memory_percent``, ``disk_percent``.
    Optional: ``temperature_c``. Extra keys are allowed for a board (e.g. GPU).
    """

    def collect(self, *, sample_cpu: bool = True) -> dict[str, Any]: ...


class CapabilityProbe(Protocol):
    """Runtime features the control plane uses for deploy gating.

    Common booleans: ``systemd``, ``docker``, ``docker_compose``, ``wireguard``, ``ssh``.
    """

    def collect(self) -> dict[str, Any]: ...


class HostActions(Protocol):
    """Power and local user changes ordered from the console.

    Raise ``HostActionError`` (or a subclass) on failure. Do not log passwords.
    """

    def reboot(self) -> None: ...

    def shutdown(self) -> None: ...

    def set_password(self, username: str, password: str) -> None: ...

    def add_user(
        self,
        username: str,
        password: str,
        *,
        sudo: bool = False,
        authorized_key: str | None = None,
    ) -> None: ...


class HostActionError(Exception):
    """A host action could not be started or completed."""


class Platform(Protocol):
    """Bundle of adapters for one OS/board family."""

    @property
    def inventory(self) -> InventoryCollector: ...

    @property
    def metrics(self) -> MetricsCollector: ...

    @property
    def capabilities(self) -> CapabilityProbe: ...

    @property
    def host_actions(self) -> HostActions: ...
