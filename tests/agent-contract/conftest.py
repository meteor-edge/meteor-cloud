"""Fixtures for the device agent contract suite. See README.md.

AGENT_CONTRACT_MODE:
  verify (default)  scripted fake control plane; the transcript must equal recordings/<scenario>.json
  record            same, but (re)write recordings/<scenario>.json
  replay            the fake control plane answers from the recording instead of the script
AGENT_CONTRACT_AGENT: which adapter drives the agent (only ``python`` today).
"""

from __future__ import annotations

import base64
import difflib
import json
import os
import secrets
import uuid
from collections import deque
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from harness import schemas, transcript
from harness.broker import Broker, BrokerUnavailable, Observer
from harness.broker import start as start_broker
from harness.device import SimulatedDevice
from harness.ed25519 import sign_desired
from harness.fake_cp import FakeControlPlane, Reply, Request, recorded_http

if TYPE_CHECKING:
    from agents.python_agent import PythonAgent

MODE = os.environ.get("AGENT_CONTRACT_MODE", "verify")
SIGNING_SEED = bytes(range(32))  # test-only organization signing key


@pytest.fixture
def device() -> SimulatedDevice:
    return SimulatedDevice()


@pytest.fixture
def agent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, device: SimulatedDevice, contract: Contract
) -> PythonAgent:
    name = os.environ.get("AGENT_CONTRACT_AGENT", "python")
    if name != "python":
        pytest.fail(f"no adapter for agent {name!r}")
    from agents.python_agent import PythonAgent

    adapter = PythonAgent(monkeypatch, tmp_path, device)
    contract.bind(adapter)  # replay re-signs desired software for this agent's device id
    return adapter


@pytest.fixture(scope="session")
def broker(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Broker]:
    try:
        started = start_broker(tmp_path_factory.mktemp("broker"))
    except BrokerUnavailable as error:
        if os.environ.get("CI"):
            pytest.fail(f"Mosquitto is required in CI: {error}")
        pytest.skip(f"no MQTT broker: {error}")
    yield started
    started.stop()


class Contract:
    """One scenario run: scripts the fake control plane, then checks and records the transcript."""

    def __init__(self, scenario: str, plane: FakeControlPlane) -> None:
        self.scenario = scenario
        self.plane = plane
        self.normalizer = transcript.Normalizer()
        self.normalizer.literal("<server>", plane.url)
        self.replaying = MODE == "replay"
        self.mqtt_problems: list[str] = []
        self._agent: Any = None
        if self.replaying:
            self._start_replay()

    # ----------------------------------------------------------- scripting
    def script(self, method: str, path: str, *replies: Reply | Any) -> None:
        """Script an answer. Ignored in replay mode, where the recording answers."""
        if not self.replaying:
            self.plane.on(method, path, *replies)

    def secret(self, name: str, value: str | None = None) -> str:
        """A secret value (made up when ``value`` is None) that shows as <name> in recordings."""
        value = value or f"{name.split('_')[0]}_{secrets.token_urlsafe(18)}"
        self.normalizer.secret(name, value)
        return value

    def broker(self, broker: Broker) -> dict[str, Any]:
        """MQTT connect info for this broker, as the server hands it out."""
        self.use_broker_literals(broker)
        return {
            "host": broker.host,
            "port": broker.port,
            "username": f"device_{uuid.uuid4()}",
            "password": self.secret("mqtt_password"),
            "tls": True,
            "ca_cert": broker.ca_pem,
        }

    def signed(self, item: dict[str, Any], device_id: str) -> dict[str, Any]:
        signature = sign_desired(SIGNING_SEED, device_id, item)
        self.normalizer.secret("signature", signature)
        return {**item, "key_id": "org-key-1", "signature": signature}

    def bind(self, agent: Any) -> None:
        self._agent = agent

    # -------------------------------------------------------------- replay
    def _start_replay(self) -> None:
        recording = transcript.load(self.scenario)
        literals: dict[str, Any] = {"<server>": self.plane.url}
        self.materializer = transcript.Materializer(literals)
        self._replies: dict[tuple[str, str], deque[dict[str, Any]]] = {}
        for exchange in recording["http"]:
            key = (exchange["request"]["method"], exchange["request"]["path"])
            self._replies.setdefault(key, deque()).append(exchange["response"])
        self.plane.responder = self._replay_answer

    def use_broker_literals(self, broker: Broker) -> None:
        """Broker address and CA: placeholders in recordings, real values in replies."""
        if self.replaying:
            self.materializer.literals.update({"<mqtt_host>": broker.host, "<mqtt_port>": broker.port, "<pem>": broker.ca_pem})
        self.normalizer.literal("<mqtt_host>", broker.host)
        self.normalizer.literal("<mqtt_port>", broker.port)

    def _replay_answer(self, request: Request) -> Reply:
        path = transcript.UUID_RE.sub(lambda m: self.materializer.placeholder_for(m.group(0)) or m.group(0), request.path)
        path = path.replace(self.plane.url, "<server>")
        queue = self._replies.get((request.method, path))
        if not queue:
            return Reply(404, {"error": {"code": "not_in_recording", "message": f"{request.method} {path}"}})
        recorded = queue.popleft() if len(queue) > 1 else queue[0]
        body = recorded["body"]
        if isinstance(body, dict) and set(body) == {"base64"}:
            content: Any = base64.b64decode(body["base64"])
        else:
            content = self.materializer.value(body)
        if request.path == "/api/v1/agent/software" and isinstance(content, list) and self._agent is not None:
            content = [self.signed({k: v for k, v in item.items() if k not in ("key_id", "signature")}, self._agent.device_id)
                       if item.get("signature") else item for item in content]
        for placeholder, value in self.materializer.concrete.items():
            if not placeholder.startswith("<uuid:"):
                self.normalizer.secret(placeholder.strip("<>"), value)
        headers = {k: self.materializer.text(v) for k, v in recorded["headers"].items()}
        return Reply(recorded["status"], content, headers, recorded.get("off_contract"))

    # -------------------------------------------------------------- finish
    def check_mqtt(self, observer: Observer) -> list[dict[str, Any]]:
        seen: dict[str, dict[str, Any]] = {}
        for message in observer.messages:
            entry = schemas.mqtt_topic(message.topic)
            if entry is None:
                self.mqtt_problems.append(f"MQTT topic {message.topic} is not in the contract")
                continue
            self.mqtt_problems += [f"{message.topic}: {p}" for p in schemas.problems(entry["schema"], message.payload)]
            if message.qos != entry["qos"]:
                self.mqtt_problems.append(f"{message.topic}: published with QoS {message.qos}, contract says {entry['qos']}")
            suffix = message.topic.split("/", 2)[2]
            normalized = {
                "topic": self.normalizer.text(message.topic),
                "qos": message.qos,
                "payload": self.normalizer.mqtt_payload(suffix, message.payload),
            }
            seen[json.dumps(normalized, sort_keys=True)] = normalized
        return [seen[key] for key in sorted(seen)]

    def finish(self, observer: Observer | None = None, retained: dict[str, Any] | None = None) -> dict[str, Any]:
        problems = list(self.plane.violations)
        result: dict[str, Any] = {
            "scenario": self.scenario,
            "contract": "contracts/device-agent-v1.md",
            "http": recorded_http(self.plane, self.normalizer),
        }
        if observer is not None:
            result["mqtt"] = self.check_mqtt(observer)
            problems += self.mqtt_problems
        if retained is not None:
            result["retained"] = {topic: self.normalizer.mqtt_payload("status", payload) for topic, payload in retained.items()}
        assert not problems, "contract violations:\n  " + "\n  ".join(problems)
        if MODE == "record":
            transcript.save(self.scenario, result)
            return result
        try:
            golden = transcript.load(self.scenario)
        except FileNotFoundError:
            pytest.fail(f"no recording for {self.scenario}; run with AGENT_CONTRACT_MODE=record")
        if golden != result:
            diff = difflib.unified_diff(
                json.dumps(golden, indent=2).splitlines(), json.dumps(result, indent=2).splitlines(),
                f"recordings/{self.scenario}.json", "this run", lineterm="", n=4,
            )
            pytest.fail("transcript differs from the recording:\n" + "\n".join(diff))
        return result


@pytest.fixture
def contract(request: pytest.FixtureRequest) -> Iterator[Contract]:
    plane = FakeControlPlane().start()
    scenario = request.node.name.removeprefix("test_")
    yield Contract(scenario, plane)
    plane.stop()
