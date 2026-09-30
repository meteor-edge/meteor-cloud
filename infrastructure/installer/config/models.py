"""Pydantic models for installer configuration."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class InstallationSettings(BaseModel):
    """Top-level installation identity and provider selection."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, description="Human-readable installation name.")
    provider: Literal["aws"] = Field(description="Infrastructure provider identifier.")
    environment: Literal["development", "staging", "production"] = Field(
        description="Deployment environment."
    )


class PlatformSettings(BaseModel):
    """Platform software version to install."""

    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1, description="Platform version string.")


class ComponentSettings(BaseModel):
    """Enable or disable a single platform component."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True


class ComponentsSettings(BaseModel):
    """Platform components that may be installed."""

    model_config = ConfigDict(extra="forbid")

    postgres: ComponentSettings = Field(default_factory=ComponentSettings)
    redis: ComponentSettings = Field(default_factory=ComponentSettings)
    reverse_proxy: ComponentSettings = Field(default_factory=ComponentSettings)


class InstallationConfig(BaseModel):
    """Root configuration document for an Edge Platform installation."""

    model_config = ConfigDict(extra="forbid")

    installation: InstallationSettings
    platform: PlatformSettings
    components: ComponentsSettings = Field(default_factory=ComponentsSettings)
