"""Placeholder AWS infrastructure provider."""

from __future__ import annotations

import logging
from typing import Any

from providers.base import InfrastructureProvider

logger = logging.getLogger(__name__)


class AwsProvider(InfrastructureProvider):
    """AWS infrastructure provider placeholder.

    Terraform-backed implementation arrives in a later milestone.
    """

    name = "aws"

    def validate(self) -> None:
        logger.info("Validating AWS provider credentials and configuration")
        raise NotImplementedError("AWS provider validate() is not implemented yet")

    def plan(self) -> dict[str, Any]:
        logger.info("Planning AWS infrastructure changes")
        raise NotImplementedError("AWS provider plan() is not implemented yet")

    def apply(self) -> dict[str, Any]:
        logger.info("Applying AWS infrastructure changes")
        raise NotImplementedError("AWS provider apply() is not implemented yet")

    def inspect(self) -> dict[str, Any]:
        logger.info("Inspecting AWS infrastructure")
        raise NotImplementedError("AWS provider inspect() is not implemented yet")

    def destroy(self) -> dict[str, Any]:
        logger.info("Destroying AWS infrastructure")
        raise NotImplementedError("AWS provider destroy() is not implemented yet")
