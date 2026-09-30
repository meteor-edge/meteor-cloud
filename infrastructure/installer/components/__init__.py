"""Platform component interfaces and implementations."""

from components.base import PlatformComponent
from components.registry import get_component, list_components

__all__ = ["PlatformComponent", "get_component", "list_components"]
