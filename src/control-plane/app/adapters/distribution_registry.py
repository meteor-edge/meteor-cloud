"""CNCF Distribution v3 (``registry:3``): configuration rendered from our settings."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import Settings
from app.ports.registry import ImageManifest, Platform, RegistryUnavailableError
from app.registry.access import TOKEN_ISSUER, events_secret

CONFIG_DIR = "/etc/distribution"
DATA_DIR = "/var/lib/registry"
_TIMEOUT_SECONDS = 10.0
_MAX_BODY = 4 * 1024 * 1024  # the registry's own limit for a manifest
_MAX_PLATFORMS = 20
_INDEX_TYPES = (
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
)
_ACCEPT = ", ".join(
    (
        *_INDEX_TYPES,
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)
_DIGEST_RE = re.compile(r"sha256:[a-f0-9]{64}")

Access = list[dict[str, Any]]


class _DropAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """Blobs may redirect to a presigned S3 URL, which refuses a second credential."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            redirected.remove_header("Authorization")
        return redirected


_opener = urllib.request.build_opener(_DropAuthOnRedirect, urllib.request.ProxyHandler({}))


class DistributionRegistry:
    def __init__(self, settings: Settings, sign: Callable[[Access], str] | None = None) -> None:
        self.settings = settings
        self._sign = sign

    # --- client -------------------------------------------------------------

    def _request(
        self, method: str, path: str, access: Access, *, accept: str = "*/*"
    ) -> tuple[int, dict[str, str], bytes]:
        if self._sign is None:
            raise RuntimeError("this registry was built without credentials")
        request = urllib.request.Request(
            self.settings.registry_internal_url.rstrip("/") + path,
            method=method,
            headers={"Authorization": f"Bearer {self._sign(access)}", "Accept": accept},
        )
        try:
            with _opener.open(request, timeout=_TIMEOUT_SECONDS) as response:
                body = response.read(_MAX_BODY + 1)
                if len(body) > _MAX_BODY:
                    raise RegistryUnavailableError("The registry sent an answer that is too large.")
                return response.status, {k.lower(): v for k, v in response.headers.items()}, body
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return 404, {}, b""
            raise RegistryUnavailableError(f"The registry answered HTTP {exc.code}.") from exc
        except OSError as exc:  # URLError and timeouts are OSErrors
            raise RegistryUnavailableError(f"The registry is unreachable: {exc}") from exc

    def _json(self, path: str, access: Access) -> dict[str, Any]:
        return _parse(self._request("GET", path, access)[2])

    def _pages(self, path: str, key: str, access: Access) -> list[str]:
        items: list[str] = []
        last = ""
        while True:
            query = urllib.parse.urlencode({"n": 1000, **({"last": last} if last else {})})
            page = self._json(f"{path}?{query}", access).get(key) or []
            items += page
            if len(page) < 1000:
                return items
            last = page[-1]

    def list_repositories(self) -> list[str]:
        return self._pages("/v2/_catalog", "repositories", [{"type": "registry", "name": "catalog", "actions": ["*"]}])

    def list_tags(self, repository: str) -> list[str]:
        return self._pages(f"/v2/{repository}/tags/list", "tags", _pull(repository))

    def digest(self, repository: str, reference: str) -> str | None:
        status, headers, _ = self._request(
            "HEAD", f"/v2/{repository}/manifests/{reference}", _pull(repository), accept=_ACCEPT
        )
        return None if status == 404 else _digest(headers)

    def manifest(self, repository: str, reference: str) -> ImageManifest | None:
        status, headers, body = self._request(
            "GET", f"/v2/{repository}/manifests/{reference}", _pull(repository), accept=_ACCEPT
        )
        if status == 404:
            return None
        document = _parse(body)
        media_type = str(document.get("mediaType") or headers.get("content-type") or "")[:255]
        if media_type in _INDEX_TYPES or "manifests" in document:
            platforms = self._index_platforms(repository, document)
        else:
            config = document.get("config")
            config_digest = config.get("digest") if isinstance(config, dict) else ""
            platforms = [self._image_platform(repository, document, str(config_digest))]
        return ImageManifest(
            digest=_digest(headers), media_type=media_type, platforms=tuple(p for p in platforms if p is not None)
        )

    def _index_platforms(self, repository: str, index: dict[str, Any]) -> list[Platform | None]:
        platforms: list[Platform | None] = []
        entries = index.get("manifests")
        for entry in (entries if isinstance(entries, list) else [])[:_MAX_PLATFORMS]:
            described = entry.get("platform") if isinstance(entry, dict) else None
            if not isinstance(described, dict):
                continue
            # Build attestations are listed as platform unknown/unknown.
            if described.get("os") in (None, "unknown") or not _DIGEST_RE.fullmatch(str(entry.get("digest"))):
                continue
            _, _, body = self._request(
                "GET", f"/v2/{repository}/manifests/{entry['digest']}", _pull(repository), accept=_ACCEPT
            )
            platforms.append(_platform(described, _parse(body)))
        return platforms

    def _image_platform(self, repository: str, document: dict[str, Any], config_digest: str) -> Platform | None:
        if not _DIGEST_RE.fullmatch(config_digest):
            return None
        _, _, body = self._request("GET", f"/v2/{repository}/blobs/{config_digest}", _pull(repository))
        return _platform(_parse(body), document)

    def delete_tag(self, repository: str, tag: str) -> None:
        self._request(
            "DELETE",
            f"/v2/{repository}/manifests/{tag}",
            [{"type": "repository", "name": repository, "actions": ["pull", "delete"]}],
        )

    # --- configuration ------------------------------------------------------

    def _storage(self) -> dict[str, Any]:
        settings = self.settings
        storage: dict[str, Any] = {"delete": {"enabled": True}, "cache": {"blobdescriptor": "inmemory"}}
        if settings.object_storage_provider == "filesystem":
            storage["filesystem"] = {"rootdirectory": DATA_DIR}
            return storage
        endpoint = settings.object_storage_endpoint_url
        # Keys come from AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY, else the instance or pod role.
        s3: dict[str, Any] = {
            "region": settings.object_storage_region,
            "bucket": settings.registry_storage_bucket or settings.object_storage_bucket,
            "rootdirectory": f"/{settings.registry_storage_prefix.strip('/')}",
        }
        if endpoint:
            s3 |= {"regionendpoint": endpoint, "forcepathstyle": True, "secure": endpoint.startswith("https://")}
        storage["s3"] = s3
        # Layers are downloaded straight from the bucket, when clients can reach that address.
        reachable = not endpoint or endpoint == settings.object_storage_public_endpoint_url
        storage["redirect"] = {"disable": not reachable}
        return storage

    def config_files(self, jwks: dict[str, Any]) -> dict[str, str]:
        settings = self.settings
        # Signs upload state; the same for every replica, and not any other key we use.
        http_secret = HKDF(
            algorithm=hashes.SHA256(), length=32, salt=None, info=b"meteorcloud registry http secret v1"
        ).derive(settings.app_secret_key.encode())
        config = {
            "version": 0.1,
            "log": {"level": "info", "accesslog": {"disabled": True}},
            "storage": self._storage(),
            "http": {
                "addr": ":5000",
                "secret": http_secret.hex(),
                # Absolute upload URLs would carry the proxy's internal scheme.
                "relativeurls": True,
            },
            "auth": {
                "token": {
                    "realm": settings.registry_token_realm,
                    "service": settings.registry_public_host,
                    "issuer": TOKEN_ISSUER,
                    "jwks": f"{CONFIG_DIR}/jwks.json",
                    "signingalgorithms": ["ES256"],
                }
            },
            # Pushes and deletes, so the image list follows at once.
            "notifications": {
                "endpoints": [
                    {
                        "name": "meteorcloud",
                        "url": settings.registry_events_url,
                        "headers": {"X-Registry-Events-Token": [events_secret(settings)]},
                        "timeout": "30s",
                        "threshold": 5,
                        "backoff": "5s",
                        "ignore": {"actions": ["pull"]},
                    }
                ]
            },
            "health": {"storagedriver": {"enabled": True, "interval": "10s", "threshold": 3}},
        }
        # JSON is valid YAML, so no YAML library is needed.
        return {"config.yml": json.dumps(config, indent=2) + "\n", "jwks.json": json.dumps(jwks, indent=2) + "\n"}


def _pull(repository: str) -> Access:
    return [{"type": "repository", "name": repository, "actions": ["pull"]}]


def _parse(body: bytes) -> dict[str, Any]:
    """Image content is written by tenants: anything that is not a JSON object reads as empty."""
    try:
        document = json.loads(body)
    except ValueError:
        return {}
    return document if isinstance(document, dict) else {}


def _digest(headers: dict[str, str]) -> str:
    digest = headers.get("docker-content-digest", "")
    if not _DIGEST_RE.fullmatch(digest):
        raise RegistryUnavailableError("The registry did not return a digest.")
    return digest


def _size(descriptor: Any) -> int:
    size = descriptor.get("size") if isinstance(descriptor, dict) else 0
    return size if isinstance(size, int) and 0 <= size < 2**50 else 0


def _platform(described: dict[str, Any], manifest: dict[str, Any]) -> Platform | None:
    name, architecture = described.get("os"), described.get("architecture")
    if not isinstance(name, str) or not isinstance(architecture, str) or not name or not architecture:
        return None
    variant = described.get("variant")
    layers = manifest.get("layers")
    return Platform(
        os=name[:32],
        architecture=architecture[:32],
        variant=variant[:16] if isinstance(variant, str) and variant else None,
        size_bytes=sum(map(_size, layers if isinstance(layers, list) else [])) + _size(manifest.get("config")),
    )
