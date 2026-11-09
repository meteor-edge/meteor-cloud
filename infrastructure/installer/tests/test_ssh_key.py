"""EC2 SSH key pair must match the local PEM before apply."""

from __future__ import annotations

import json
from pathlib import Path

from edge_installer.providers.aws.ssh_key import (
    normalize_openssh_public_key,
    validate_ssh_key_matches_ec2,
)


def test_normalize_drops_comment() -> None:
    assert (
        normalize_openssh_public_key("ssh-rsa AAAAkey comment-here")
        == "ssh-rsa AAAAkey"
    )


def test_validate_ssh_key_match(monkeypatch, tmp_path: Path) -> None:
    pem = tmp_path / "key.pem"
    pem.write_text("PRIVATE", encoding="utf-8")
    local = "ssh-rsa AAAALOCAL"
    remote = "ssh-rsa AAAALOCAL comment"

    def fake_run(command, **kwargs):  # noqa: ANN001
        from edge_installer.process.runner import ProcessResult

        if command[0] == "ssh-keygen":
            return ProcessResult(list(command), 0, local + "\n", "")
        if command[0] == "aws":
            body = json.dumps({"KeyPairs": [{"PublicKey": remote + "\n"}]})
            return ProcessResult(list(command), 0, body, "")
        raise AssertionError(command)

    monkeypatch.setattr(
        "edge_installer.providers.aws.ssh_key.command_exists", lambda _name: True
    )
    monkeypatch.setattr("edge_installer.providers.aws.ssh_key.run_command", fake_run)

    assert (
        validate_ssh_key_matches_ec2(
            private_key_path=pem, key_name="edge-platform", region="eu-central-1"
        )
        == []
    )


def test_validate_ssh_key_mismatch(monkeypatch, tmp_path: Path) -> None:
    pem = tmp_path / "key.pem"
    pem.write_text("PRIVATE", encoding="utf-8")

    def fake_run(command, **kwargs):  # noqa: ANN001
        from edge_installer.process.runner import ProcessResult

        if command[0] == "ssh-keygen":
            return ProcessResult(list(command), 0, "ssh-rsa AAAALOCAL\n", "")
        body = json.dumps({"KeyPairs": [{"PublicKey": "ssh-rsa AAAAREMOTE\n"}]})
        return ProcessResult(list(command), 0, body, "")

    monkeypatch.setattr(
        "edge_installer.providers.aws.ssh_key.command_exists", lambda _name: True
    )
    monkeypatch.setattr("edge_installer.providers.aws.ssh_key.run_command", fake_run)

    errors = validate_ssh_key_matches_ec2(
        private_key_path=pem, key_name="edge-platform", region="eu-central-1"
    )
    assert len(errors) == 1
    assert "does not match EC2 key pair" in errors[0]


def test_validate_ssh_key_missing_pair(monkeypatch, tmp_path: Path) -> None:
    pem = tmp_path / "key.pem"
    pem.write_text("PRIVATE", encoding="utf-8")

    def fake_run(command, **kwargs):  # noqa: ANN001
        from edge_installer.process.runner import ProcessResult

        if command[0] == "ssh-keygen":
            return ProcessResult(list(command), 0, "ssh-rsa AAAALOCAL\n", "")
        return ProcessResult(list(command), 254, "", "InvalidKeyPair.NotFound")

    monkeypatch.setattr(
        "edge_installer.providers.aws.ssh_key.command_exists", lambda _name: True
    )
    monkeypatch.setattr("edge_installer.providers.aws.ssh_key.run_command", fake_run)

    errors = validate_ssh_key_matches_ec2(
        private_key_path=pem, key_name="missing", region="eu-central-1"
    )
    assert errors
    assert "missing" in errors[0]
