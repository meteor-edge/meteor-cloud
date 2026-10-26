"""Shared state for one suite run: hosts, API clients, and IDs created along the way."""

from __future__ import annotations

import base64
import json
import secrets
import shlex
import time
from dataclasses import dataclass, field
from typing import Any

from e2e.config import Config
from e2e.http import Api
from e2e.redact import redact
from e2e.ssh import Ssh, SshResult
from e2e.state import Host, State
from e2e.suite.framework import Blocked, Fail

COMPOSE_PROJECT = "meteorcloud"
METEORCLI = "METEORCLI_CONFIG_DIR=/etc/meteorcli /usr/local/bin/meteorcli"


def new_run_id() -> str:
    return time.strftime("%m%d%H%M", time.gmtime()) + secrets.token_hex(2)


@dataclass
class Context:
    cfg: Config
    state: State
    ssh: Ssh
    secrets: dict[str, str]
    run_id: str
    hosts: list[Host]
    data: dict[str, Any] = field(default_factory=dict)  # IDs and values shared between checks
    _api: Api | None = None

    # -- hosts --------------------------------------------------------------------
    @property
    def cp(self) -> Host:
        return next(h for h in self.hosts if h.role == "control_plane")

    @property
    def gw(self) -> Host:
        """Host that runs wg0 (the control plane itself when co-located)."""
        return next((h for h in self.hosts if h.role == "gateway"), self.cp)

    @property
    def devices(self) -> list[Host]:
        return [h for h in self.hosts if h.role == "device"]

    def device(self, index: int) -> Host:
        if index >= len(self.devices):
            raise Blocked(f"needs at least {index + 1} devices (E2E_DEVICE_COUNT={len(self.devices)})")
        return self.devices[index]

    # -- URLs ---------------------------------------------------------------------
    @property
    def api_url(self) -> str:
        return f"https://{self.cfg.fqdn}" if self.cfg.has_public_dns else f"http://{self.cp.address}"

    def app_hostname(self, label: str) -> str:
        """A custom app name: <name>.<apps domain>."""
        return f"e2e-{self.run_id}-{label}.{self.cfg.apps_base_domain}"

    def suggested_hostname(self, index: int, app: str) -> str:
        """The platform's suggestion for an app on device ``index``: <app>.<device>.<org>.<apps domain>."""
        info = self.data["devices"][index]
        return self.admin().call("GET", self.org_path(
            f"/endpoints/hostname-suggestion?device_id={info['id']}&app={app}"))["hostname"]

    @property
    def api(self) -> Api:
        if self._api is None:
            self._api = Api(self.api_url, self.cfg.ca_file)
        return self._api

    def admin(self) -> Api:
        if not self.api.token:
            self.api.login(self.secrets["EDGE_PLATFORM_ADMIN_EMAIL"], self.secrets["EDGE_PLATFORM_ADMIN_PASSWORD"])
            redact.add(self.api.token)
        return self.api

    def org_path(self, suffix: str = "", org: str | None = None) -> str:
        return f"/api/v1/organizations/{org or self.data['org_id']}{suffix}"

    # -- remote helpers -----------------------------------------------------------
    def sh(self, host: Host, command: str, *, sudo: bool = True, timeout: float = 300,
           check: bool = True) -> SshResult:
        result = self.ssh.run(host, command, sudo=sudo, timeout=timeout)
        if check and not result.ok:
            raise Fail(f"{host.name}: `{redact(command)[:200]}` exited {result.returncode}: {result.text()[-800:]}")
        return result

    @staticmethod
    def container(service: str) -> str:
        return ("$(docker ps -q -f label=com.docker.compose.project=" + COMPOSE_PROJECT
                + f" -f label=com.docker.compose.service={service} | head -n1)")

    def backend_python(self, code: str, *, timeout: float = 120) -> Any:
        """Run Python inside the API container (same network and credentials as the API) and parse its JSON output."""
        encoded = base64.b64encode(code.encode()).decode()
        result = self.sh(self.cp, f"echo {encoded} | base64 -d | docker exec -i {self.container('backend')} python -",
                         timeout=timeout)
        lines = [line for line in result.stdout.strip().splitlines() if line.strip()]
        if not lines:
            raise Fail("backend script printed nothing: " + result.text()[-500:])
        return json.loads(lines[-1])

    def meteorcli(self, device: Host, args: str, *, check: bool = True, timeout: float = 180) -> SshResult:
        return self.sh(device, f"{METEORCLI} {args}", check=check, timeout=timeout)

    def put_secret_file(self, host: Host, path: str, value: str) -> None:
        self.sh(host, f"umask 077 && printf %s {shlex.quote(value)} > {shlex.quote(path)}")
