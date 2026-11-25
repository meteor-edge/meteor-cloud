"""CNCF Distribution v3 (``registry:3``): configuration rendered from our settings."""

from __future__ import annotations

import json
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import Settings
from app.registry.service import TOKEN_ISSUER

CONFIG_DIR = "/etc/distribution"
DATA_DIR = "/var/lib/registry"


class DistributionRegistry:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

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
            "health": {"storagedriver": {"enabled": True, "interval": "10s", "threshold": 3}},
        }
        # JSON is valid YAML, so no YAML library is needed.
        return {"config.yml": json.dumps(config, indent=2) + "\n", "jwks.json": json.dumps(jwks, indent=2) + "\n"}
