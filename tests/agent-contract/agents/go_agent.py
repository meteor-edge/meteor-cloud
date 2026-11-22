"""Adapter that drives the Go meteorcli (src/device-plane/agent-go) through the scenarios.

The Go agent is a separate process: ``meteorcli --root <dir> agent`` runs for the whole
scenario, and the commands are the real CLI talking to it over its Unix socket, as on a
device. The CLI runs as the same user as the agent, so the socket's group policy lets it
through (the agent's own user is trusted).

How the device side is simulated:

- ``register`` stores the server with ``meteorcli config --api-base`` first: the Go CLI has no
  ``--server`` flag, the agent uses the configured server.
- ``claim`` waits in real time: the agent polls no faster than the contract allows (10 s) and
  honours ``Retry-After``, so ``enroll`` takes about 25 s.
- ``run`` lets the running agent beat and counts heartbeats at the fake control plane.
- Host tools are fake programs on PATH (``docker`` answers ``compose version``; ``wg``,
  ``wg-quick`` and ``sshd`` exist).
- Privileged work goes through the root helper's socket (``run/meteorcli/helper.sock``), as on a
  device. Here a fake helper (:class:`FakeHelper`) answers it: reboot, users, the Docker observer's
  commands and journal warnings go to the SimulatedDevice, with the same docker arguments the real
  helper uses. Metrics are published every second (``METEORCLI_METRICS_INTERVAL=1``).
- Covered: background enrollment (approval with no command, across restarts), the server's
  heartbeat interval, MQTT, actions, docker deploys (the fake helper turns ``docker.load``,
  ``docker.run`` and ``docker.health`` into the real helper's docker commands on the
  SimulatedDevice), Wi-Fi (``wifi.*`` become ``meteorcli-wifi`` commands on the
  SimulatedDevice) and WireGuard (``wireguard.*`` become ``wg-quick``/``wg`` calls there;
  ``ssh.authorized_keys`` keeps the managed lines in memory). Install outcomes go out at once in
  a heartbeat that carries only ``software_report``, like the progress reports.
- WireGuard and SSH facts are fixed so the recordings stay stable: one key pair in
  ``identity/wireguard``, the SSH user ``pi`` (``meteorcli config --ssh-user``) and the same host
  key in ``<root>/etc/ssh``.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from harness.device import SimulatedDevice
from harness.fake_cp import FakeControlPlane
from harness.script import ssh_key

SOURCE = Path(__file__).resolve().parents[3] / "src" / "device-plane" / "agent-go"
VERSION = "0.0.0"
HEARTBEAT = ("POST", "/api/v1/agent/heartbeat")
FAKE_TOOLS = {
    "docker": '#!/bin/sh\n[ "$1 $2" = "compose version" ] && { echo "Docker Compose version v2.29.0"; exit 0; }\nexit 1\n',
    "wg": "#!/bin/sh\nexit 0\n",
    "wg-quick": "#!/bin/sh\nexit 0\n",
    "sshd": "#!/bin/sh\nexit 0\n",
}

# The real helper's docker commands (internal/helper/docker.go).
INSPECT_TEMPLATE = (
    "{{.Name}}|{{.State.Status}}|{{.State.Running}}|{{.State.ExitCode}}|{{.State.OOMKilled}}|"
    "{{.RestartCount}}|{{.State.StartedAt}}|{{if .State.Health}}{{.State.Health.Status}}|"
    "{{range .State.Health.Log}}{{.Start.UnixNano}},{{.End.UnixNano}};{{end}}{{end}}"
)
STATS_FORMAT = "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}"
# The real helper's deploy health check (internal/helper/workloads.go).
HEALTH_TEMPLATE = "{{.State.Running}}|{{.State.Status}}|{{.State.ExitCode}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}"

# Fixed WireGuard key pair and SSH host key (the recordings expect them).
WG_PRIVATE = "yAnz5TF+lXXJte14tji3zlMNq+hd2rYUIgJBgB3fBmk="
WG_PUBLIC = "HIgo9xNzJMWLKASShiTqIybxZ0U3wGLiUeJ1PKf8ykw="  # X25519 public key of WG_PRIVATE
SSH_HOST_KEY_FILE = ssh_key("contract host key", "root@device") + "\n"
# What the real helper adds to wifi.status: the link traffic leaves on.
UPLINK = {"uplink_type": "wifi", "uplink_interface": "wlan0"}

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


class FakeHelper:
    """The root helper's socket protocol (one JSON request line, one JSON answer), with the
    host work done by the SimulatedDevice instead of the machine."""

    def __init__(self, path: Path, device: SimulatedDevice) -> None:
        self.device = device
        self.calls: list[str] = []
        self.authorized_keys: list[str] = []
        path.parent.mkdir(parents=True, exist_ok=True)
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(str(path))
        self._sock.listen(16)
        self._thread = threading.Thread(target=self._serve, name="fake-helper", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._sock.close()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            with conn:
                try:
                    line = conn.makefile("rb").readline()
                    request = json.loads(line)
                    answer = self._answer(request["method"], request.get("params") or {})
                except Exception as error:  # noqa: BLE001 - answer every request, as the real helper
                    answer = {"ok": False, "error": {"code": "failed", "message": str(error)}}
                conn.sendall(json.dumps(answer).encode() + b"\n")

    def _docker(self, args: list[str]) -> dict:
        done = self.device.docker(args)
        return {"output": done.stdout or "", "ok": done.returncode == 0}

    def _answer(self, method: str, params: dict) -> dict:
        self.calls.append(method)
        device = self.device
        if method == "power.reboot":
            device.reboot()
            result: dict = {"started": True}
        elif method == "power.shutdown":
            device.shutdown()
            result = {"started": True}
        elif method == "users.set_password":
            device.set_password(params["username"], params["password"])
            result = {"changed": True}
        elif method == "users.add":
            device.add_user(params["username"], params["password"], sudo=params.get("sudo") is True,
                            authorized_key=params.get("authorized_key") or None)
            result = {"created": True}
        elif method == "docker.ps":
            result = self._docker(["docker", "ps", "-a", "--format", "{{.Names}}"])
        elif method == "docker.inspect":
            result = self._docker(["docker", "inspect", "-f", INSPECT_TEMPLATE, *params["names"]])
        elif method == "docker.stats":
            result = self._docker(["docker", "stats", "--no-stream", "--format", STATS_FORMAT])
        elif method == "docker.load":
            result = self._docker(["docker", "load", "-i", params["path"]])
        elif method == "docker.run":
            device.docker(["docker", "rm", "-f", params["name"]])
            args = ["docker", "run", "-d", "--name", params["name"]]
            if params.get("pull_never"):
                args += ["--pull", "never"]
            if params.get("restart"):
                args += ["--restart", params["restart"]]
            for port in params.get("ports") or []:
                args += ["-p", port]
            for key, value in (params.get("env") or {}).items():
                args += ["-e", f"{key}={value}"]  # the real helper uses a private --env-file
            result = self._docker(args + [params["image"], *(params.get("command") or [])])
        elif method == "docker.health":
            result = self._docker(["docker", "inspect", "-f", HEALTH_TEMPLATE, params["name"]])
        elif method == "docker.remove":
            result = self._docker(["docker", "rm", "-f", params["name"]])
        elif method.startswith("wifi."):
            result = self._wifi(method.removeprefix("wifi."), params)
        elif method == "wireguard.apply":
            device.wireguard(["wg-quick", "up", "wg0"])
            result = {"changed": True}
        elif method == "wireguard.down":
            device.wireguard(["wg-quick", "down", "wg0"])
            result = {"down": True}
        elif method == "wireguard.show":
            result = {"up": True, "peers": []}
        elif method == "wireguard.endpoint":
            device.wireguard(["wg", "set", "wg0", "peer", params["public_key"], "endpoint", params["endpoint"]])
            result = {"changed": True}
        elif method == "ssh.authorized_keys":
            result = self._ssh_keys(params)
        elif method == "logs.warnings":
            out = device.journal_helper(["warnings"]).stdout
            result = {"entries": [json.loads(line) for line in out.splitlines() if line.strip()]}
        else:
            return {"ok": False, "error": {"code": "unknown_method", "message": f"unknown method {method!r}"}}
        return {"ok": True, "result": result}


    def _wifi(self, command: str, params: dict) -> dict:
        """The meteorcli-wifi commands on the SimulatedDevice (the request on stdin)."""
        stdin = json.dumps(params) if command in ("apply", "remove") else None
        done = self.device.wifi_helper([command], stdin)
        answer = json.loads(done.stdout or "{}")
        if done.returncode != 0:
            raise RuntimeError(answer.get("error") or f"wifi {command} failed")
        if command == "status":
            answer = {**answer, **UPLINK}
        return answer

    def _ssh_keys(self, params: dict) -> dict:
        """The managed authorized_keys lines (the CA line is what matters to the contract)."""
        ca, principal = params.get("ca_key") or "", params.get("principal") or ""
        if ca and principal:
            wanted = f'cert-authority,principals="{principal}" {" ".join(ca.split()[:2])} meteorcloud-ca'
            self.authorized_keys = [line for line in self.authorized_keys if not line.endswith(" meteorcloud-ca")]
            self.authorized_keys.append(wanted)
        trusted = next((" ".join(line.split()[1:3]) for line in self.authorized_keys if line.endswith(" meteorcloud-ca")), "")
        return {"user": "pi", "trusted_ca": trusted, "changed": bool(ca and principal)}


class GoAgent:
    name = "go"

    def __init__(self, workdir: Path, device: SimulatedDevice, plane: FakeControlPlane) -> None:
        self.device = device
        self.plane = plane
        self.bin = binary()
        # Unix socket paths are limited to 108 bytes; pytest's tmp_path can be longer.
        self.root = Path(tempfile.mkdtemp(prefix="mc"))
        tools = self.root / "tools"
        tools.mkdir()
        for name, script in FAKE_TOOLS.items():
            (tools / name).write_text(script, encoding="utf-8")
            (tools / name).chmod(0o755)
        self.env = {
            **os.environ, "PATH": f"{tools}{os.pathsep}{os.environ.get('PATH', '')}", "NO_COLOR": "1",
            "METEORCLI_METRICS_INTERVAL": "1",
        }
        for key in ("METEORCLI_TOKEN", "METEORCLI_API_KEY", "METEOR_BOOT_DIR"):
            self.env.pop(key, None)
        # Fixed WireGuard keys and SSH host key (the recordings expect them).
        wg_dir = self.root / "var" / "lib" / "meteorcli" / "identity" / "wireguard"
        wg_dir.mkdir(parents=True)
        (wg_dir / "private.key").write_text(WG_PRIVATE + "\n", encoding="utf-8")
        (wg_dir / "public.key").write_text(WG_PUBLIC + "\n", encoding="utf-8")
        ssh_dir = self.root / "etc" / "ssh"
        ssh_dir.mkdir(parents=True)
        (ssh_dir / "ssh_host_ed25519_key.pub").write_text(SSH_HOST_KEY_FILE, encoding="utf-8")
        self.log = workdir / "agent.log"
        self._proc: subprocess.Popen[bytes] | None = None
        self.helper = FakeHelper(self.root / "run" / "meteorcli" / "helper.sock", device)
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
        self.helper.close()
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
        code = self._cli("config", "--api-base", server, "--ssh-user", "pi")
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

    def run_until(self, done: Callable[[], bool], timeout: float = 90) -> int:
        """Let the running agent work until ``done()`` is true, then stop it."""
        deadline = time.monotonic() + timeout
        while not done():
            if self._proc is None or self._proc.poll() is not None:
                pytest.fail(f"the Go agent exited:\n{self.log.read_text(errors='replace')}")
            if time.monotonic() > deadline:
                pytest.fail(f"not done within {timeout:.0f} s:\n{self.log.read_text(errors='replace')}")
            time.sleep(0.02)
        return self.stop()

    @property
    def device_id(self) -> str | None:
        try:
            path = self.root / "var" / "lib" / "meteorcli" / "identity" / "device.json"
            return json.loads(path.read_text(encoding="utf-8")).get("device_id")
        except (OSError, ValueError):
            return None
