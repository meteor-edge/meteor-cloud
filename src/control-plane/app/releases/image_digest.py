"""Resolve a container image tag to its manifest digest (``sha256:…``).

Used when a docker release is saved, so every device runs exactly the image the
release was created with, even if the tag moves later or the registry is tampered
with. The digest is part of the signed desired software (docs/fleet/signed-updates.md).

Only public registries are contacted: HTTPS, and only hosts that resolve to public
addresses, so a release spec cannot make the server call internal services. Anonymous
pulls only; a private registry is skipped and the release keeps its tag.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_ACCEPT = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)
_TIMEOUT_SECONDS = 5.0
_DOCKER_HUB = "registry-1.docker.io"


class DigestResolutionError(Exception):
    """The digest could not be resolved; the message says why."""


@dataclass(frozen=True)
class ImageReference:
    registry: str
    repository: str
    tag: str
    digest: str | None = None

    @property
    def name(self) -> str:
        """The reference without tag or digest, as ``docker`` writes it."""
        if self.registry == _DOCKER_HUB:
            repo = self.repository.removeprefix("library/")
            return repo
        return f"{self.registry}/{self.repository}"


def parse_reference(image: str) -> ImageReference:
    """Split ``[registry/]repo[:tag][@digest]`` the way Docker does."""
    value = image.strip()
    digest = None
    if "@" in value:
        value, digest = value.split("@", 1)
        if not DIGEST_RE.match(digest):
            raise DigestResolutionError(f"Unsupported digest in {image!r}.")
    tag = "latest"
    last = value.rsplit("/", 1)[-1]
    if ":" in last:
        value, tag = value.rsplit(":", 1)
    first, _, rest = value.partition("/")
    if rest and ("." in first or ":" in first or first == "localhost"):
        registry, repository = first, rest
    else:
        registry, repository = _DOCKER_HUB, value
        if "/" not in repository:
            repository = f"library/{repository}"
    if registry in {"docker.io", "index.docker.io"}:
        registry = _DOCKER_HUB
    if not repository or not re.match(r"^[a-z0-9]+(?:[._/-][a-z0-9]+)*$", repository):
        raise DigestResolutionError(f"Not an image reference: {image!r}.")
    return ImageReference(registry=registry, repository=repository, tag=tag, digest=digest)


def _require_public_host(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise DigestResolutionError("Only HTTPS registries are resolved.")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise DigestResolutionError(f"Cannot resolve {parsed.hostname}.") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise DigestResolutionError(f"{parsed.hostname} is not a public registry.")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _request(url: str, *, method: str, headers: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
    _require_public_host(url)
    request = urllib.request.Request(url, method=method, headers={"User-Agent": "meteorcloud", **headers})
    try:
        with _opener.open(request, timeout=_TIMEOUT_SECONDS) as response:
            return response.status, {k.lower(): v for k, v in response.headers.items()}, response.read(65536)
    except urllib.error.HTTPError as exc:
        return exc.code, {k.lower(): v for k, v in exc.headers.items()}, b""
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise DigestResolutionError(f"Registry unreachable: {exc}") from exc


def _bearer_token(challenge: str, scope_default: str) -> str:
    params = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
    realm = params.get("realm")
    if not challenge.lower().startswith("bearer") or not realm:
        raise DigestResolutionError("Registry needs credentials; keeping the tag.")
    query = {"service": params.get("service", ""), "scope": params.get("scope", scope_default)}
    status, _, body = _request(f"{realm}?{urllib.parse.urlencode(query)}", method="GET", headers={})
    if status != 200:
        raise DigestResolutionError("Registry refused an anonymous token; keeping the tag.")
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise DigestResolutionError("Registry returned an unreadable token.") from exc
    token = data.get("token") or data.get("access_token")
    if not token:
        raise DigestResolutionError("Registry returned no token.")
    return str(token)


def resolve_image_digest(image: str) -> str:
    """Return ``sha256:…`` for ``image``, or raise DigestResolutionError."""
    ref = parse_reference(image)
    if ref.digest:
        return ref.digest
    url = f"https://{ref.registry}/v2/{ref.repository}/manifests/{urllib.parse.quote(ref.tag)}"
    headers = {"Accept": _ACCEPT}
    status, response_headers, _ = _request(url, method="HEAD", headers=headers)
    if status == 401:
        token = _bearer_token(response_headers.get("www-authenticate", ""), f"repository:{ref.repository}:pull")
        authorized = {**headers, "Authorization": f"Bearer {token}"}
        status, response_headers, _ = _request(url, method="HEAD", headers=authorized)
    if status == 404:
        raise DigestResolutionError(f"{image} was not found in the registry.")
    if status != 200:
        raise DigestResolutionError(f"Registry answered HTTP {status} for {image}.")
    digest = (response_headers.get("docker-content-digest") or "").strip()
    if not DIGEST_RE.match(digest):
        raise DigestResolutionError("Registry did not return a sha256 digest.")
    return digest
