"""Provider registry for looking up infrastructure providers by name."""

from __future__ import annotations

from providers.aws import AwsProvider
from providers.base import InfrastructureProvider

_PROVIDERS: dict[str, type[InfrastructureProvider]] = {
    "aws": AwsProvider,
}


def get_provider(name: str) -> InfrastructureProvider:
    """Return a new provider instance for the given name."""
    try:
        provider_cls = _PROVIDERS[name]
    except KeyError as exc:
        known = ", ".join(sorted(_PROVIDERS))
        raise ValueError(
            f"Unknown infrastructure provider '{name}'. Known providers: {known}"
        ) from exc
    return provider_cls()


def list_providers() -> list[str]:
    """Return registered provider names."""
    return sorted(_PROVIDERS)
