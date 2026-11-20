"""Mosquitto with TLS for the contract suite, and an observer that plays the server side of MQTT.

The broker runs from the eclipse-mosquitto:2 image (EPL-2.0/EDL-1.0, test only) with a
throwaway CA made by openssl. It accepts any username and password: device MQTT auth
is the control plane's job (/internal/mqtt/*), not part of the agent contract.

Set AGENT_CONTRACT_BROKER=host:port and AGENT_CONTRACT_BROKER_CA=/path/ca.pem to use a
broker you started yourself.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import ssl
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt

IMAGE = "eclipse-mosquitto:2"
CONF = """\
listener 8883
cafile /mosquitto/config/ca.pem
certfile /mosquitto/config/server.pem
keyfile /mosquitto/config/server.key
allow_anonymous true
persistence false
"""


class BrokerUnavailable(RuntimeError):
    """Docker (or openssl) is missing, so no broker can be started here."""


@dataclass
class Broker:
    host: str
    port: int
    ca_pem: str
    ca_path: Path
    container: str | None = None

    def stop(self) -> None:
        if self.container:
            subprocess.run(["docker", "rm", "-f", self.container], capture_output=True, timeout=60, check=False)


def _openssl(*args: str, cwd: Path) -> None:
    subprocess.run(["openssl", *args], cwd=cwd, check=True, capture_output=True, timeout=60)


def make_certs(directory: Path) -> None:
    """A CA and a server certificate for 127.0.0.1 and localhost."""
    directory.mkdir(parents=True, exist_ok=True)
    # Python 3.13 verifies strictly: the CA needs key usage, the server cert key ids and SANs.
    _openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=agent-contract-ca",
             "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign",
             "-addext", "subjectKeyIdentifier=hash", "-keyout", "ca.key", "-out", "ca.pem", cwd=directory)
    _openssl("req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=localhost",
             "-keyout", "server.key", "-out", "server.csr", cwd=directory)
    (directory / "san.ext").write_text(
        "subjectAltName=IP:127.0.0.1,DNS:localhost\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\n"
        "authorityKeyIdentifier=keyid\n"
        "subjectKeyIdentifier=hash\n",
        encoding="utf-8",
    )
    _openssl("x509", "-req", "-in", "server.csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-CAcreateserial",
             "-days", "2", "-extfile", "san.ext", "-out", "server.pem", cwd=directory)
    for name in ("ca.pem", "server.pem", "server.key"):
        (directory / name).chmod(0o644)  # read by the mosquitto user in the container


def _wait_for_tls(host: str, port: int, ca_path: Path, timeout: float = 30.0) -> None:
    context = ssl.create_default_context(cafile=str(ca_path))
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    deadline = time.monotonic() + timeout
    while True:
        try:
            with socket.create_connection((host, port), timeout=2) as raw, context.wrap_socket(raw, server_hostname=host):
                return
        except ssl.SSLCertVerificationError:
            raise
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.3)


def start(workdir: Path) -> Broker:
    external = os.environ.get("AGENT_CONTRACT_BROKER")
    if external:
        host, _, port = external.rpartition(":")
        ca_path = Path(os.environ["AGENT_CONTRACT_BROKER_CA"])
        return Broker(host, int(port), ca_path.read_text(encoding="utf-8"), ca_path)
    if not shutil.which("docker") or not shutil.which("openssl"):
        raise BrokerUnavailable("docker and openssl are needed to start Mosquitto")
    certs = workdir / "mosquitto"
    make_certs(certs)
    (certs / "mosquitto.conf").write_text(CONF, encoding="utf-8")
    (certs / "mosquitto.conf").chmod(0o644)
    certs.chmod(0o755)
    name = f"agent-contract-{uuid.uuid4().hex[:8]}"
    started = subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", name, "-p", "127.0.0.1::8883",
         "-v", f"{certs}:/mosquitto/config:ro", IMAGE],
        capture_output=True, text=True, timeout=180, check=False,
    )
    if started.returncode != 0:
        raise BrokerUnavailable(f"docker run failed: {started.stderr.strip()}")
    mapped = subprocess.run(["docker", "port", name, "8883/tcp"], capture_output=True, text=True, timeout=30, check=True)
    port = int(mapped.stdout.strip().splitlines()[0].rsplit(":", 1)[1])
    broker = Broker("127.0.0.1", port, (certs / "ca.pem").read_text(encoding="utf-8"), certs / "ca.pem", name)
    try:
        _wait_for_tls(broker.host, broker.port, broker.ca_path)
    except OSError:
        broker.stop()
        raise
    return broker


@dataclass
class Message:
    topic: str
    qos: int
    retain: bool
    payload: Any


@dataclass
class Observer:
    """The server side of MQTT: watches devices/<id>/# and publishes commands."""

    broker: Broker
    device_id: str
    messages: list[Message] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._lock = threading.Condition()
        self._client = self._connect(f"contract-observer-{uuid.uuid4().hex[:8]}")
        self._client.on_message = self._on_message
        self._client.subscribe(f"devices/{self.device_id}/#", qos=1)

    def _connect(self, client_id: str) -> mqtt.Client:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, protocol=mqtt.MQTTv311)
        client.username_pw_set("platform", "contract")
        client.tls_set(ca_certs=str(self.broker.ca_path), tls_version=ssl.PROTOCOL_TLS_CLIENT)
        connected = threading.Event()
        client.on_connect = lambda *_a, **_k: connected.set()
        client.connect(self.broker.host, self.broker.port, 30)
        client.loop_start()
        if not connected.wait(15):
            raise RuntimeError("observer could not connect to the broker")
        return client

    def _on_message(self, _client: Any, _userdata: Any, message: mqtt.MQTTMessage) -> None:
        raw = message.payload.decode("utf-8", "replace")
        try:
            payload: Any = json.loads(raw)
        except ValueError:
            payload = raw
        with self._lock:
            self.messages.append(Message(message.topic, message.qos, bool(message.retain), payload))
            self._lock.notify_all()

    def publish(self, suffix: str, payload: dict[str, Any]) -> None:
        info = self._client.publish(f"devices/{self.device_id}/{suffix}", json.dumps(payload), qos=1, retain=False)
        info.wait_for_publish(timeout=10)

    def wait_for(self, predicate: Callable[[Message], bool], timeout: float = 20.0) -> Message:
        """The first message matching ``predicate``; waits for it up to ``timeout`` seconds."""
        deadline = time.monotonic() + timeout
        with self._lock:
            while True:
                for message in self.messages:
                    if predicate(message):
                        return message
                left = deadline - time.monotonic()
                if left <= 0:
                    seen = [(m.topic, m.payload) for m in self.messages]
                    raise AssertionError(f"no matching MQTT message within {timeout}s; saw {seen}")
                self._lock.wait(left)

    def retained(self, suffix: str, wait: float = 1.5) -> Any:
        """What a new subscriber gets as the retained message on devices/<id>/<suffix>."""
        got: list[Any] = []
        client = self._connect(f"contract-retained-{uuid.uuid4().hex[:8]}")
        client.on_message = lambda _c, _u, m: got.append(json.loads(m.payload)) if m.retain else None
        client.subscribe(f"devices/{self.device_id}/{suffix}", qos=1)
        time.sleep(wait)
        client.disconnect()
        client.loop_stop()
        return got[-1] if got else None

    def stop(self) -> None:
        self._client.disconnect()
        self._client.loop_stop()
