"""SSH gateway provider: argument building and host-key isolation."""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.adapters.ssh_gateway import SshGatewayProvider
from app.core.exceptions import ValidationAppError


def test_known_hosts_file_is_per_device(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(args):
        calls.append(list(args))
        return MagicMock()

    provider = SshGatewayProvider(known_hosts_dir=str(tmp_path / "kh"), runner=runner)
    first, second = uuid.uuid4(), uuid.uuid4()
    provider.connect(host="10.8.0.5", port=22, username="root", device_id=first)
    provider.connect(host="10.8.0.5", port=22, username="root", device_id=second)
    assert f"UserKnownHostsFile={tmp_path / 'kh' / f'{first}.known_hosts'}" in calls[0]
    assert f"UserKnownHostsFile={tmp_path / 'kh' / f'{second}.known_hosts'}" in calls[1]
    assert calls[0][-1] == "root@10.8.0.5"


def test_refuses_hosts_outside_wireguard_cidr() -> None:
    provider = SshGatewayProvider(runner=lambda args: MagicMock())
    with pytest.raises(ValidationAppError):
        provider.connect(host="192.168.1.1", port=22, username="root")
