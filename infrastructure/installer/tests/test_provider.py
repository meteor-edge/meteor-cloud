"""Terraform integration tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from edge_installer.config.loader import load_configuration
from edge_installer.process.runner import ProcessResult, redact
from edge_installer.providers.aws.outputs import TerraformOutputs
from edge_installer.providers.aws.terraform import TerraformRunner

EXAMPLE = (
    Path(__file__).resolve().parent.parent
    / "edge_installer"
    / "config"
    / "examples"
    / "installation.yaml"
)


def test_terraform_variables_include_tags(tmp_path: Path) -> None:
    config = load_configuration(EXAMPLE)
    runner = TerraformRunner(config, tmp_path)
    variables = runner.variables()

    assert variables["installation_name"] == "production"
    assert variables["aws_region"] == "eu-central-1"
    assert variables["tags"]["ManagedBy"] == "edge-installer"


def test_terraform_output_parsing() -> None:
    raw = {
        "instance_id": {"value": "i-123"},
        "public_ip": {"value": "1.2.3.4"},
        "elastic_ip": {"value": "1.2.3.4"},
        "private_ip": {"value": "10.0.0.1"},
        "region": {"value": "eu-central-1"},
        "ssh_username": {"value": "ubuntu"},
        "security_group_id": {"value": "sg-abc"},
    }
    flattened = {key: value["value"] for key, value in raw.items()}
    outputs = TerraformOutputs.model_validate(flattened)

    assert outputs.instance_id == "i-123"
    assert outputs.connect_ip == "1.2.3.4"


def test_redact_secrets() -> None:
    text = "EDGE_PLATFORM_JWT_SECRET=abc123 password=secret"
    assert "[REDACTED]" in redact(text)
    assert "abc123" not in redact(text)


@patch("edge_installer.providers.aws.terraform.run_command")
def test_terraform_plan_invocation(mock_run: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = load_configuration(EXAMPLE)
    runner = TerraformRunner(config, tmp_path)
    mock_run.return_value = ProcessResult(
        command=["terraform", "plan"],
        returncode=0,
        stdout="Plan: 1 to add",
        stderr="",
    )

    output = runner.plan()

    assert "Plan: 1 to add" in output
    assert mock_run.called


def test_missing_output_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        TerraformOutputs.model_validate({"instance_id": "i-1"})
