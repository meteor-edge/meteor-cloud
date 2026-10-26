"""Stage 2: public DNS and verified HTTPS on the platform domain."""

from __future__ import annotations

import json

from e2e.http import peer_certificate, request, resolve_ipv4
from e2e.suite.framework import NotSupported, check, expect, wait_for

STAGE = "2 dns and tls"


def _require_domain(ctx) -> str:
    if not ctx.cfg.has_public_dns:
        raise NotSupported("no public domain configured (E2E_DOMAIN with E2E_DNS_PROVIDER=route53|manual)")
    return ctx.cfg.fqdn


@check("dns.platform_record", STAGE, "Platform name resolves to the ingress")
def platform_record(ctx):
    fqdn = _require_domain(ctx)
    found = wait_for(lambda: resolve_ipv4(fqdn) == {ctx.cp.address} and resolve_ipv4(fqdn),
                     timeout=600, interval=10, what=f"{fqdn} -> {ctx.cp.address}")
    return {"name": fqdn, "addresses": sorted(found)}


@check("dns.wildcard_record", STAGE, "Custom and app.device.org names resolve to the ingress")
def wildcard_record(ctx):
    _require_domain(ctx)
    found = {}
    for name in (ctx.app_hostname("dnsprobe"), f"dnsprobe.device.org.{ctx.cfg.apps_base_domain}"):
        found[name] = sorted(wait_for(lambda n=name: resolve_ipv4(n) == {ctx.cp.address} and resolve_ipv4(n),
                                      timeout=600, interval=10, what=f"{name} -> {ctx.cp.address}"))
    return {"addresses": found}


@check("tls.platform_https", STAGE, "Platform serves /health over verified HTTPS",
       needs=("dns.platform_record",))
def platform_https(ctx):
    fqdn = _require_domain(ctx)

    def probe():
        response = request("GET", f"https://{fqdn}/health", ca_file=ctx.cfg.ca_file, timeout=15)
        return response if response.status == 200 else None

    # Let's Encrypt HTTP-01 issuance happens on the first TLS request; allow a few minutes.
    response = wait_for(probe, timeout=600, interval=15, what=f"https://{fqdn}/health with a trusted certificate")
    body = response.json()
    expect(str(body.get("status", "")).lower() in ("ok", "healthy"), f"unexpected health body {body}")
    cert = peer_certificate(fqdn, ca_file=ctx.cfg.ca_file)
    expect(fqdn in cert["san"], f"certificate SAN {cert['san']} does not cover {fqdn}")
    return {"health": body, "certificate": cert}


@check("tls.http_redirect", STAGE, "Plain HTTP redirects to HTTPS", needs=("dns.platform_record",))
def http_redirect(ctx):
    fqdn = _require_domain(ctx)
    response = request("GET", f"http://{fqdn}/health", timeout=15)
    location = response.headers.get("location", "")
    expect(response.status in (301, 302, 307, 308) and location.startswith(f"https://{fqdn}"),
           f"http://{fqdn}/health -> {response.status} {location!r}")
    return {"status": response.status, "location": location}


@check("tls.internal_not_public", STAGE, "/internal APIs are not reachable through the public proxy",
       needs=("tls.platform_https",))
def internal_not_public(ctx):
    fqdn = _require_domain(ctx)
    seen = {}
    for path in ("/internal/routing/traefik", "/internal/connectivity/peers"):
        response = request("GET", f"https://{fqdn}{path}", ca_file=ctx.cfg.ca_file, timeout=15)
        try:
            body = response.json()
        except (ValueError, json.JSONDecodeError):
            body = None
        leaked = isinstance(body, dict) and ("http" in body or "peers" in body)
        expect(not leaked, f"{path} returned internal data through the public proxy")
        seen[path] = {"status": response.status, "content_type": response.headers.get("content-type", "")}
    return seen
