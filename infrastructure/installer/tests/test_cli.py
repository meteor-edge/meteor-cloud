"""Installer CLI command tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from edge_installer.cli.main import app
from edge_installer.providers.aws.outputs import TerraformOutputs

runner = CliRunner()
EXAMPLE = (
    Path(__file__).resolve().parent.parent
    / "edge_installer"
    / "config"
    / "examples"
    / "installation.yaml"
)


@pytest.fixture
def secrets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    key_path = tmp_path / "edge-platform.pem"
    key_path.write_text("fake-key", encoding="utf-8")
    monkeypatch.setenv("EDGE_PLATFORM_POSTGRES_PASSWORD", "postgres-secret")
    monkeypatch.setenv("EDGE_PLATFORM_JWT_SECRET", "jwt-secret")
    return key_path


@pytest.fixture
def config_path(tmp_path: Path, secrets: Path) -> Path:
    content = EXAMPLE.read_text(encoding="utf-8").replace(
        "~/.ssh/edge-platform.pem",
        str(secrets),
    )
    path = tmp_path / "installation.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_init_creates_sample_config(tmp_path: Path) -> None:
    output = tmp_path / "installation.yaml"

    result = runner.invoke(app, ["init", "--output", str(output)])

    assert result.exit_code == 0
    assert output.exists()
    assert "Created sample configuration" in result.stdout


@patch("edge_installer.cli.commands.validate_aws_credentials", return_value=[])
@patch("edge_installer.cli.commands.validate_dependencies", return_value=[])
def test_validate_succeeds(
    _deps: MagicMock,
    _aws: MagicMock,
    config_path: Path,
) -> None:
    result = runner.invoke(app, ["validate", "--config", str(config_path)])

    assert result.exit_code == 0, result.stdout
    assert "Configuration is valid" in result.stdout


def test_validate_fails_for_missing_secrets(
    config_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EDGE_PLATFORM_JWT_SECRET", raising=False)
    with patch("edge_installer.cli.commands.validate_dependencies", return_value=[]):
        with patch("edge_installer.cli.commands.validate_aws_credentials", return_value=[]):
            result = runner.invoke(app, ["validate", "--config", str(config_path)])

    assert result.exit_code == 1
    assert "EDGE_PLATFORM_JWT_SECRET is not set" in result.stdout


@patch("edge_installer.deployment.service.PlatformDeploymentService.plan")
def test_plan_does_not_apply(mock_plan: MagicMock, config_path: Path) -> None:
    mock_plan.return_value = {"plan_output": "Plan: 3 to add", "components": ["postgres"]}

    result = runner.invoke(app, ["plan", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "Plan: 3 to add" in result.stdout
    mock_plan.assert_called_once()


def test_apply_accepts_positional_config(config_path: Path) -> None:
    with patch(
        "edge_installer.deployment.service.PlatformDeploymentService.apply"
    ) as mock_apply:
        from edge_installer.deployment.service import ApplyResult
        from edge_installer.state.models import InstallationState

        outputs = TerraformOutputs(
            instance_id="i-abc123",
            public_ip="18.198.10.20",
            elastic_ip="18.198.10.20",
            private_ip="10.0.0.5",
            region="eu-central-1",
            ssh_username="ubuntu",
            security_group_id="sg-123",
        )
        state = InstallationState(
            name="production",
            provider="aws",
            environment="production",
            region="eu-central-1",
            platform_version="0.2.0",
            instance_id="i-abc123",
            public_ip="18.198.10.20",
            platform_url="http://18.198.10.20",
        )
        mock_apply.return_value = ApplyResult(
            state=state,
            outputs=outputs,
            health={
                "infrastructure": "healthy",
                "docker": "healthy",
                "postgres": "healthy",
                "redis": "healthy",
                "backend": "healthy",
                "frontend": "healthy",
                "reverse_proxy": "healthy",
            },
        )

        result = runner.invoke(app, ["apply", str(config_path)])

        assert result.exit_code == 0, result.stdout
        assert "Installation completed successfully" in result.stdout
        mock_apply.assert_called_once()



@patch("edge_installer.deployment.service.PlatformDeploymentService.apply")
def test_apply_stops_on_failure(mock_apply: MagicMock, config_path: Path) -> None:
    from edge_installer.exceptions import TerraformExecutionError

    mock_apply.side_effect = TerraformExecutionError("boom", stage="terraform_apply")

    result = runner.invoke(app, ["apply", "--config", str(config_path)])

    assert result.exit_code == 1
    assert "Apply failed" in result.stdout


def test_destroy_aborts_without_confirmation(config_path: Path) -> None:
    result = runner.invoke(app, ["destroy", "--config", str(config_path)], input="n\n")

    assert result.exit_code == 0
    assert "Aborted" in result.stdout


@patch("edge_installer.deployment.service.PlatformDeploymentService.destroy")
def test_destroy_yes_runs_destroy(mock_destroy: MagicMock, config_path: Path) -> None:
    result = runner.invoke(app, ["destroy", "--config", str(config_path), "--yes"])

    assert result.exit_code == 0
    assert "Installation destroyed" in result.stdout
    mock_destroy.assert_called_once()


def test_validate_fails_for_missing_config(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"

    result = runner.invoke(app, ["validate", "--config", str(missing)])

    assert result.exit_code == 1
    assert "Configuration file not found" in result.stdout
