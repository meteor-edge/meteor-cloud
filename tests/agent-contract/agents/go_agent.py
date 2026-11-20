"""Adapter that drives the Go meteorcli (src/device-plane/agent-go) through the scenarios.

The Go agent is a separate process: ``meteorcli --root <dir> agent`` runs for the whole
scenario, and the commands are the real CLI talking to it over its Unix socket, as on a
device. The CLI runs as the same user as the agent, so the socket's group policy lets it
through (the agent's own user is trusted).

Differences from the Python adapter, all on the device side of the contract:

- ``register`` stores the server with ``meteorcli config --api-base`` first: the Go CLI has no
  ``--server`` flag, the agent uses the configured server.
- ``claim`` waits in real time: the agent polls no faster than the contract allows (10 s) and
  honours ``Retry-After``, so ``enroll`` takes about 25 s.
- ``run`` lets the running agent beat and counts heartbeats at the fake control plane.
- Host tools are fake programs on PATH (``docker`` answers ``compose version``; ``wg``,
  ``wg-quick`` and ``sshd`` exist); the agent touches nothing else on the host yet.
- Features: background enrollment (approval with no command, across restarts) and the server's
  heartbeat interval. MQTT, actions, Wi-Fi, WireGuard and deploys come in later pull requests.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from harness.device import SimulatedDevice
from harness.fake_cp import FakeControlPlane

SOURCE = Path(__file__).resolve().parents[3] / "src" / "device-plane" / "agent-go"
VERSION = "0.0.0"
HEARTBEAT = ("POST", "/api/v1/agent/heartbeat")
FAKE_TOOLS = {
    "docker": '#!/bin/sh\n[ "$1 $2" = "compose version" ] && { echo "Docker Compose version v2.29.0"; exit 0; }\nexit 1\n',
    "wg": "#!/bin/sh\nexit 0\n",
    "wg-quick": "#!/bin/sh\nexit 0\n",
    "sshd": "#!/bin/sh\nexit 0\n",
}

_binary: Path | None = None


def binary() -> Path:
    """Build meteorcli once per test session."""
    global _binary
    if _binary is None:
        go = shutil.which("go")
        if go is None:
            if os.environ.get("CI"):
                pytest.fail("Go is required for AGENT_CONTRACT_AGENT=go in CI")
            pytest.skip("Go is not installed")
        out = Path(tempfile.mkdtemp(prefix="meteorcli-go-")) / "meteorcli"
        subprocess.run(
            [go, "build", "-trimpath", "-ldflags", f"-X main.version={VERSION}", "-o", str(out), "./cmd/meteorcli"],
            cwd=SOURCE, check=True, timeout=600, env={**os.environ, "CGO_ENABLED": "0"},
        )
        _binary = out
    return _binary


class GoAgent:
    name = "go"
    features = frozenset({"background_enrollment", "server_heartbeat_interval"})

    def __init__(self, workdir: Path, device: SimulatedDevice, plane: FakeControlPlane) -> None:
        self.device = device  # unused until host work moves to the Go agent
        self.plane = plane
        self.bin = binary()
        # Unix socket paths are limited to 108 bytes; pytest's tmp_path can be longer.
        self.root = Path(tempfile.mkdtemp(prefix="mc"))
        tools = self.root / "tools"
        tools.mkdir()
        for name, script in FAKE_TOOLS.items():
            (tools / name).write_text(script, encoding="utf-8")
            (tools / name).chmod(0o755)
        self.env = {**os.environ, "PATH": f"{tools}{os.pathsep}{os.environ.get('PATH', '')}", "NO_COLOR": "1"}
        for key in ("METEORCLI_TOKEN", "METEORCLI_API_KEY", "METEOR_BOOT_DIR"):
            self.env.pop(key, None)
        self.log = workdir / "agent.log"
        self._proc: subprocess.Popen[bytes] | None = None
        try:
            self.start()
        except BaseException:
            # The fixture never gets the adapter, so it cannot close() it.
            self.close()
            raise

    # ---------------------------------------------------------------- the agent
    @property
    def socket(self) -> Path:
        return self.root / "run" / "meteorcli" / "agent.sock"

    def start(self) -> None:
        with self.log.open("ab") as log:
            self._proc = subprocess.Popen(
                [str(self.bin), "--root", str(self.root), "agent"], env=self.env, stdout=log, stderr=subprocess.STDOUT,
            )
        deadline = time.monotonic() + 10
        while not self.socket.exists():
            if self._proc.poll() is not None or time.monotonic() > deadline:
                log = self.log.read_text(errors="replace")
                self.stop()
                pytest.fail(f"the Go agent did not start:\n{log}")
            time.sleep(0.02)

    def stop(self) -> int:
        """Stop the agent like systemd does (SIGTERM) and return its exit code."""
        if self._proc is None:
            return 0
        proc, self._proc = self._proc, None
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                pytest.fail("the Go agent did not stop within 15 s of SIGTERM")
        return proc.returncode

    def close(self) -> None:
        self.stop()
        shutil.rmtree(self.root, ignore_errors=True)

    # ------------------------------------------------------------------ commands
    def _cli(self, *args: str, timeout: float = 120) -> int:
        result = subprocess.run(
            [str(self.bin), "--root", str(self.root), *args],
            check=False, env=self.env, capture_output=True, text=True, timeout=timeout,
        )
        with self.log.open("a", encoding="utf-8") as log:
            log.write(f"$ meteorcli {args[0]} -> {result.returncode}\n{result.stdout}{result.stderr}")
        return result.returncode

    def configure(self, server: str, api_key: str) -> int:
        return self._cli("config", "--api-base", server, "--api-key", api_key)

    def register(self, server: str, token: str) -> int:
        code = self._cli("config", "--api-base", server)
        return code or self._cli("register", "--token", token)

    def test(self) -> int:
        return self._cli("test")

    def request_token(self) -> int:
        return self._cli("request-token")

    def claim(self) -> int:
        return self._cli("claim", "--wait", "120", timeout=240)

    def run(self, heartbeats: int, between: Callable[[int], None] | None = None, timeout: float = 90) -> int:
        """Let the running agent send ``heartbeats`` heartbeats, then stop it."""
        for beat in range(1, heartbeats + 1):
            deadline = time.monotonic() + timeout
            while len(self.plane.calls(*HEARTBEAT)) < beat:
                if self._proc is None or self._proc.poll() is not None:
                    pytest.fail(f"the Go agent exited:\n{self.log.read_text(errors='replace')}")
                if time.monotonic() > deadline:
                    pytest.fail(f"heartbeat {beat} did not arrive within {timeout:.0f} s")
                time.sleep(0.02)
            if between is not None:
                between(beat)
        return self.stop()

    @property
    def device_id(self) -> str | None:
        try:
            path = self.root / "var" / "lib" / "meteorcli" / "identity" / "device.json"
            return json.loads(path.read_text(encoding="utf-8")).get("device_id")
        except (OSError, ValueError):
            return None
