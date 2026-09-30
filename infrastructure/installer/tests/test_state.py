"""State, inventory, and locking tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from edge_installer.config.loader import load_configuration
from edge_installer.deployment.inventory import write_inventory
from edge_installer.exceptions import InstallationLockedError
from edge_installer.providers.aws.outputs import TerraformOutputs
from edge_installer.state.models import InstallationState
from edge_installer.state.store import InstallationLock, load_state, save_state

EXAMPLE = (
    Path(__file__).resolve().parent.parent
    / "edge_installer"
    / "config"
    / "examples"
    / "installation.yaml"
)


@pytest.fixture
def state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / ".installer-state"
    monkeypatch.setattr("edge_installer.state.paths.state_root", lambda: root)
    monkeypatch.setattr(
        "edge_installer.state.store.installation_state_file",
        lambda name: root / name / "installation.json",
    )
    monkeypatch.setattr(
        "edge_installer.state.store.lock_file",
        lambda name: root / name / "install.lock",
    )
    return root


def test_state_round_trip(state_root: Path) -> None:
    state = InstallationState(
        name="production",
        provider="aws",
        environment="production",
        region="eu-central-1",
        platform_version="0.2.0",
        public_ip="1.2.3.4",
    )
    save_state(state)
    loaded = load_state("production")

    assert loaded is not None
    assert loaded.public_ip == "1.2.3.4"
    assert loaded.installation_id == state.installation_id


def test_installation_lock_blocks_second_acquire(state_root: Path) -> None:
    lock_path = state_root / "production" / "install.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(str(os.getpid()), encoding="utf-8")

    with pytest.raises(InstallationLockedError):
        with InstallationLock("production"):
            pass


def test_inventory_generation(tmp_path: Path) -> None:
    config = load_configuration(EXAMPLE)
    config.aws.ssh_private_key_path = str(tmp_path / "key.pem")
    (tmp_path / "key.pem").write_text("key", encoding="utf-8")
    outputs = TerraformOutputs(
        instance_id="i-1",
        public_ip="1.2.3.4",
        elastic_ip="",
        private_ip="10.0.0.1",
        region="eu-central-1",
        ssh_username="ubuntu",
        security_group_id="sg-1",
    )
    inventory = tmp_path / "inventory.ini"
    write_inventory(path=inventory, outputs=outputs, config=config)
    content = inventory.read_text(encoding="utf-8")

    assert "ansible_host=1.2.3.4" in content
    assert "ansible_user=ubuntu" in content
    assert content.startswith("[platform]\nedge ")
