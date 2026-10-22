"""Traefik HTTP-provider document built from registered endpoints."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header

from app.core.config import Settings, get_settings
from app.core.exceptions import UnauthorizedError
from app.endpoints.dependencies import EndpointSvc

router = APIRouter(prefix="/internal/routing", tags=["internal-routing"], include_in_schema=False)


def _require_routing_token(
    settings: Annotated[Settings, Depends(get_settings)],
    x_routing_internal_token: Annotated[str | None, Header()] = None,
) -> None:
    expected = settings.routing_internal_token or settings.connectivity_internal_token
    if not expected or x_routing_internal_token != expected:
        raise UnauthorizedError("routing_internal_forbidden", "Invalid routing internal token.")


@router.get("/traefik")
def traefik_http_provider(
    service: EndpointSvc,
    settings: Annotated[Settings, Depends(get_settings)],
    _: Annotated[None, Depends(_require_routing_token)],
) -> dict[str, Any]:
    routers: dict[str, Any] = {}
    services: dict[str, Any] = {}
    apps_domain = settings.apps_base_domain.strip().lower().strip(".")
    tls_main = f"*.appx.{apps_domain}" if not apps_domain.startswith("appx.") else f"*.{apps_domain}"
    use_tls = bool(settings.apps_base_domain)
    for endpoint, upstream in service.routable_upstreams():
        name = f"app-{endpoint.id.hex}"
        router: dict[str, Any] = {
            "rule": f"Host(`{endpoint.hostname}`)",
            "service": name,
            "priority": 20,
            "entryPoints": ["websecure"] if use_tls else ["web"],
        }
        if use_tls:
            router["tls"] = {
                "certResolver": "letsencrypt-dns",
                "domains": [{"main": tls_main}],
            }
        routers[name] = router
        services[name] = {"loadBalancer": {"servers": [{"url": upstream}]}}
    return {"http": {"routers": routers, "services": services}}
