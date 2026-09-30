"""Typed installer exceptions."""

from __future__ import annotations


class InstallerError(Exception):
    """Base installer error."""

    def __init__(self, message: str, *, stage: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.stage = stage


class ConfigurationError(InstallerError):
    """Invalid configuration or missing secrets."""


class DependencyMissingError(InstallerError):
    """Required external tool is not available."""


class TerraformExecutionError(InstallerError):
    """Terraform command failed."""


class InfrastructureProvisioningError(InstallerError):
    """Infrastructure could not be provisioned."""


class SshConnectionError(InstallerError):
    """SSH connection to the server failed."""


class AnsibleExecutionError(InstallerError):
    """Ansible playbook failed."""


class MigrationError(InstallerError):
    """Database migration failed."""


class HealthCheckError(InstallerError):
    """Post-deployment health verification failed."""


class StateError(InstallerError):
    """Installer state could not be read or written."""


class InstallationLockedError(InstallerError):
    """Another installer operation is in progress."""
