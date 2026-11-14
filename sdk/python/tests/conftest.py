"""Tests run the SDK against the real Go data plane (in-memory storage and bus).

The binary is built from src/data-plane/go, or taken from METEORCLOUD_DATAPLANE_BIN.
Without a Go toolchain and without that variable the integration tests are skipped.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
GO_DIR = REPO / "src" / "data-plane" / "go"
ORG = "11111111-1111-1111-1111-111111111111"
OTHER_ORG = "22222222-2222-2222-2222-222222222222"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def dataplane_bin(tmp_path_factory) -> str:
    preset = os.environ.get("METEORCLOUD_DATAPLANE_BIN")
    if preset:
        return preset
    if shutil.which("go") is None:
        pytest.skip("Go toolchain not available and METEORCLOUD_DATAPLANE_BIN not set")
    out = tmp_path_factory.mktemp("bin") / "dataplane"
    subprocess.run(["go", "build", "-o", str(out), "./cmd/dataplane"], cwd=GO_DIR, check=True)
    return str(out)


class DataPlane:
    """A running data plane with a policy file the test can change."""

    def __init__(self, binary: str, workdir: Path) -> None:
        self.binary = binary
        self.workdir = workdir
        self.policy_path = workdir / "policy.json"
        self.port = _free_port()
        self.ops_port = _free_port()
        self.endpoint = f"http://127.0.0.1:{self.port}"
        self.apps: dict[str, dict] = {}
        self.credentials: list[dict] = []
        self.process: subprocess.Popen | None = None
        self._n = 0

    def add_app(self, slug: str, *, org: str = ORG, publish=(), subscribe=(), read=(), receive_commands=False,
                send_commands=()) -> None:
        self._n += 1
        self.apps[f"{org}/{slug}"] = {
            "id": f"aaaaaaaa-0000-0000-0000-{self._n:012d}",
            "organization_id": org,
            "slug": slug,
            "permissions": {"publish": list(publish), "subscribe": list(subscribe), "read": list(read),
                            "receive_commands": receive_commands, "send_commands": list(send_commands)},
        }
        self.write()

    def token(self, slug: str, *, org: str = ORG, device_id: str | None = None, environment: str = "test",
              ttl: float = 3600) -> str:
        self._n += 1
        token = f"mca_test_{slug}_{self._n}"
        self.credentials.append({
            "id": f"cccccccc-0000-0000-0000-{self._n:012d}",
            "organization_id": org,
            "application_id": self.apps[f"{org}/{slug}"]["id"],
            "device_id": device_id,
            "environment": environment,
            "token_hash": hashlib.sha256(token.encode()).hexdigest(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat(),
        })
        self.write()
        return token

    def revoke(self, token: str) -> None:
        digest = hashlib.sha256(token.encode()).hexdigest()
        self.credentials = [c for c in self.credentials if c["token_hash"] != digest]
        self.write()

    def write(self) -> None:
        snapshot = {"generated_at": datetime.now(timezone.utc).isoformat(),
                    "applications": list(self.apps.values()), "credentials": self.credentials}
        tmp = self.policy_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(snapshot))
        tmp.replace(self.policy_path)
        if self.process is not None:
            time.sleep(0.35)  # one policy poll

    def start(self) -> None:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "DATAPLANE_HTTP_ADDR": f"127.0.0.1:{self.port}",
            "DATAPLANE_OPS_ADDR": f"127.0.0.1:{self.ops_port}",
            "DATAPLANE_STORAGE": "memory",
            "DATAPLANE_BUS": "memory",
            "DATAPLANE_POLICY_FILE": str(self.policy_path),
            "DATAPLANE_POLICY_INTERVAL": "150ms",
            "DATAPLANE_COMMAND_ACK_TIMEOUT": "400ms",
            "DATAPLANE_LIFECYCLE_EVENTS": "false",
            "LOG_LEVEL": "warn",
        }
        log = open(self.workdir / "dataplane.log", "ab")
        self.process = subprocess.Popen([self.binary], env=env, stdout=log, stderr=log)
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"{self.endpoint}/healthz", timeout=1) as response:
                    if response.status == 200:
                        time.sleep(0.3)  # first policy load
                        return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("data plane did not start: " + (self.workdir / "dataplane.log").read_text()[-2000:])

    def stop(self) -> None:
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(10)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None


@pytest.fixture()
def dataplane(dataplane_bin, tmp_path):
    plane = DataPlane(dataplane_bin, tmp_path)
    plane.add_app("meter", publish=["energy.**", "alerts.*"], subscribe=["energy.**"], read=["energy.**"],
                  receive_commands=True)
    plane.add_app("dashboard", subscribe=["energy.**", "alerts.*"], read=["energy.**", "alerts.*"],
                  send_commands=["meter"])
    plane.add_app("stranger", org=OTHER_ORG, publish=["energy.**"], subscribe=["energy.**"], read=["energy.**"],
                  send_commands=["meter"])
    plane.start()
    yield plane
    plane.stop()
