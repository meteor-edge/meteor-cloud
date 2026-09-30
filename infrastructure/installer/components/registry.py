"""Component registry for looking up platform components by name."""

from __future__ import annotations

from components.base import PlatformComponent
from components.postgres import PostgresComponent
from components.redis import RedisComponent
from components.traefik import TraefikComponent

_COMPONENTS: dict[str, type[PlatformComponent]] = {
    "postgres": PostgresComponent,
    "redis": RedisComponent,
    "reverse_proxy": TraefikComponent,
}


def get_component(name: str) -> PlatformComponent:
    """Return a new component instance for the given name."""
    try:
        component_cls = _COMPONENTS[name]
    except KeyError as exc:
        known = ", ".join(sorted(_COMPONENTS))
        raise ValueError(f"Unknown platform component '{name}'. Known components: {known}") from exc
    return component_cls()


def list_components() -> list[str]:
    """Return registered component names."""
    return sorted(_COMPONENTS)
