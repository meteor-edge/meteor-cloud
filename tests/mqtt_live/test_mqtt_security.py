"""MQTT authn/authz/TLS against the real broker (Mosquitto, EMQX, or VerneMQ).

Brokers answer a denied PUBLISH differently: EMQX and VerneMQ disconnect the
client, Mosquitto acknowledges and drops it (MQTT 3.1.1 has no "denied"
answer). So denial is proven the way that matters: a platform observer
subscribed to everything never sees the message.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

import paho.mqtt.client as mqtt
import pytest

from mqtt_live.broker import MqttSession
from mqtt_live.http import wait_until


def _session(live_config, device, **kwargs) -> MqttSession:
    params = {
        "host": live_config.mqtt_host,
        "port": live_config.mqtt_port,
        "ca_file": live_config.mqtt_ca_file,
        "username": device.mqtt_username,
        "password": device.mqtt_password,
        "device_id": device.device_id,
    }
    params.update(kwargs)
    return MqttSession(**params)


def _observer(live_config, topic_filter: str = "#") -> MqttSession:
    observer = MqttSession(
        host=live_config.mqtt_host,
        port=live_config.mqtt_port,
        ca_file=live_config.mqtt_ca_file,
        username=live_config.mqtt_platform_username,
        password=live_config.mqtt_platform_password,
        auto_subscribe=[topic_filter],
    )
    observer.start()
    assert observer.wait_connected() is True
    assert observer.wait_subscribed() is True
    return observer


def _assert_publish_blocked(
    observer: MqttSession, live_config, device, topic: str, *, retain: bool = False
) -> None:
    """Publish from a fresh, connected session and check nobody receives it.

    A fresh session per attempt: VerneMQ and EMQX close the connection of a
    client whose publish is denied, and a publish on a dead session would
    "pass" without the broker ever deciding anything.
    """
    marker = f"blocked-{uuid.uuid4().hex}"
    session = _session(live_config, device)
    session.start()
    try:
        assert session.wait_connected() is True, (
            "the publishing session never connected"
        )
        rc = session.submit(topic, json.dumps({"marker": marker}), retain=retain)
        assert rc == mqtt.MQTT_ERR_SUCCESS, (
            f"the publish to {topic} was never sent (rc={rc}), so the broker decided nothing"
        )
        leaked = observer.wait_message(
            lambda _topic, payload: marker in payload, timeout=3
        )
        assert leaked is None, (
            f"denied publish to {topic} reached the broker's subscribers"
        )
    finally:
        session.close()


def _assert_open_session_publish_blocked(
    observer: MqttSession, session: MqttSession, topic: str
) -> None:
    """For a session that was open before access was taken away (it may have been dropped)."""
    marker = f"blocked-{uuid.uuid4().hex}"
    session.publish(topic, json.dumps({"marker": marker}))
    leaked = observer.wait_message(lambda _topic, payload: marker in payload, timeout=3)
    assert leaked is None, (
        f"publish to {topic} from a revoked session reached subscribers"
    )


def _assert_publish_delivered(
    observer: MqttSession, session: MqttSession, topic: str, *, retain: bool = False
) -> None:
    marker = f"allowed-{uuid.uuid4().hex}"
    assert session.publish(topic, json.dumps({"marker": marker}), retain=retain)
    assert (
        observer.wait_message(
            lambda t, payload: t == topic and marker in payload, timeout=10
        )
        is not None
    )


def test_valid_device_connects(api, live_config):
    device = api.register_device("sec-ok")
    session = _session(live_config, device)
    session.start()
    try:
        assert session.wait_connected() is True
    finally:
        session.close()


def test_wrong_password_denied(api, live_config):
    device = api.register_device("sec-bad-pw")
    session = _session(live_config, device, password="wrong-password")
    session.start()
    try:
        assert session.wait_connected() is False
    finally:
        session.close()


def test_anonymous_denied(live_config):
    session = MqttSession(
        host=live_config.mqtt_host,
        port=live_config.mqtt_port,
        ca_file=live_config.mqtt_ca_file,
        username=None,
        password=None,
    )
    session.start()
    try:
        assert session.wait_connected() is False
    finally:
        session.close()


def test_disabled_device_denied(api, live_config):
    device = api.register_device("sec-disabled")
    api.disable_device(device.device_id)
    session = _session(live_config, device)
    session.start()
    try:
        assert session.wait_connected() is False
    finally:
        session.close()


def test_revoked_credential_denied(api, live_config):
    device = api.register_device("sec-revoked")
    api.revoke_mqtt(device.device_id)
    session = _session(live_config, device)
    session.start()
    try:
        assert session.wait_connected() is False
    finally:
        session.close()


def test_own_topics_allowed(api, live_config):
    device = api.register_device("sec-own")
    commands = f"devices/{device.device_id}/commands"
    observer = _observer(live_config)
    session = _session(live_config, device, auto_subscribe=[commands])
    session.start()
    try:
        assert session.wait_connected() is True
        assert session.wait_subscribed() is True
        _assert_publish_delivered(
            observer, session, f"devices/{device.device_id}/status", retain=True
        )
        _assert_publish_delivered(
            observer, session, f"devices/{device.device_id}/metrics"
        )
        _assert_publish_delivered(
            observer, session, f"devices/{device.device_id}/commands/result"
        )
    finally:
        session.close()
        observer.close()


def test_reserved_and_unknown_own_topics_denied(api, live_config):
    device = api.register_device("sec-reserved")
    observer = _observer(live_config)
    session = _session(live_config, device)
    session.start()
    try:
        assert session.wait_connected() is True
        for suffix in ("events", "telemetry", "custom", "commands"):
            _assert_publish_blocked(
                observer, live_config, device, f"devices/{device.device_id}/{suffix}"
            )
        # Only the status topic may be retained.
        _assert_publish_blocked(
            observer,
            live_config,
            device,
            f"devices/{device.device_id}/metrics",
            retain=True,
        )
    finally:
        session.close()
        observer.close()


@pytest.mark.parametrize(
    "protocol", [mqtt.MQTTv311, mqtt.MQTTv5], ids=["mqtt311", "mqtt5"]
)
def test_denied_subscriptions(api, live_config, protocol):
    """Brokers answer MQTT 3.1.1 and MQTT 5 subscribes on different paths: check both."""
    device_a = api.register_device("sec-sub-a")
    device_b = api.register_device("sec-sub-b")
    filters = [
        f"devices/{device_b.device_id}/commands",
        f"devices/{device_a.device_id}/status",
        f"devices/{device_a.device_id}/events",
        f"devices/{device_a.device_id}/#",
        "devices/+/commands",
        "devices/#",
        "#",
        "$SYS/#",
        # EMQX checks the topic behind $share, so only a foreign inbox is denied on every broker.
        f"$share/x/devices/{device_b.device_id}/commands",
        "meteorcloud/control/#",
    ]
    for topic_filter in filters:
        session = _session(live_config, device_a, protocol=protocol)
        session.start()
        try:
            assert session.wait_connected() is True
            session.subscribe(topic_filter)
            subscribed = session.wait_subscribed(6)
            # Either a failure SUBACK or a disconnect, depending on the broker.
            assert (not subscribed) or session.disconnect_event.is_set(), (
                f"{topic_filter} was accepted"
            )
        finally:
            session.close()


def test_other_device_topics_denied(api, live_config):
    device_a = api.register_device("sec-a")
    device_b = api.register_device("sec-b")
    observer = _observer(live_config)
    session = _session(live_config, device_a)
    session.start()
    try:
        assert session.wait_connected() is True
        for suffix in ("status", "metrics", "commands", "commands/result"):
            _assert_publish_blocked(
                observer,
                live_config,
                device_a,
                f"devices/{device_b.device_id}/{suffix}",
            )
        _assert_publish_blocked(
            observer, live_config, device_a, "meteorcloud/control/disconnect"
        )
    finally:
        session.close()
        observer.close()


def test_device_cannot_send_or_read_another_devices_commands(api, live_config):
    """The core rule: only the platform commands a device, and only that device hears it."""
    device_a = api.register_device("cmd-iso-a")
    device_b = api.register_device("cmd-iso-b")
    commands_a = f"devices/{device_a.device_id}/commands"
    session_a = _session(live_config, device_a, auto_subscribe=[commands_a])
    session_b = _session(
        live_config, device_b, auto_subscribe=[f"devices/{device_b.device_id}/commands"]
    )
    platform = _observer(live_config, commands_a)
    session_a.start()
    session_b.start()
    try:
        assert session_a.wait_connected() and session_a.wait_subscribed()
        assert session_b.wait_connected() and session_b.wait_subscribed()

        # Device B tries to command device A (and A tries to command itself). The
        # forgers use their own sessions: some brokers (VerneMQ) close the
        # connection of a client whose publish is denied.
        for forger, marker in ((device_b, "forged-by-b"), (device_a, "forged-by-a")):
            forging = _session(live_config, forger)
            forging.start()
            try:
                assert forging.wait_connected() is True
                forging.publish(
                    commands_a, json.dumps({"type": "reboot", "marker": marker})
                )
                assert (
                    session_a.wait_message(
                        lambda _t, payload: "forged-by" in payload, timeout=3
                    )
                    is None
                )
            finally:
                forging.close()

        # The platform commands A: A receives it, B does not.
        real = json.dumps({"type": "noop", "marker": "from-platform"})
        assert platform.publish(commands_a, real)
        assert (
            session_a.wait_message(
                lambda t, payload: t == commands_a and "from-platform" in payload
            )
            is not None
        )
        assert (
            session_b.wait_message(
                lambda _t, payload: "from-platform" in payload, timeout=3
            )
            is None
        )
    finally:
        session_a.close()
        session_b.close()
        platform.close()


def test_cross_tenant_isolation(api, live_config):
    """Two organizations on one broker cannot see or touch each other's devices."""
    from mqtt_live.api import PlatformApi

    other = PlatformApi(live_config)
    other.register_unique_owner()
    try:
        device_a = api.register_device("tenant-a")
        device_b = other.register_device("tenant-b")
        observer = _observer(live_config)
        session = _session(live_config, device_a)
        session.start()
        try:
            assert session.wait_connected() is True
            for suffix in ("status", "metrics", "commands", "commands/result"):
                _assert_publish_blocked(
                    observer,
                    live_config,
                    device_a,
                    f"devices/{device_b.device_id}/{suffix}",
                )
            session.subscribe(f"devices/{device_b.device_id}/commands")
            assert (not session.wait_subscribed(6)) or session.disconnect_event.is_set()
        finally:
            session.close()
            observer.close()
    finally:
        other.delete_organization()


def test_client_id_of_another_device_denied(api, live_config):
    """A device cannot take over (and kick offline) another device's session."""
    device_a = api.register_device("hijack-a")
    device_b = api.register_device("hijack-b")
    victim = _session(live_config, device_b, client_id=f"device-{device_b.device_id}")
    victim.start()
    try:
        assert victim.wait_connected() is True
        for client_id in (f"device-{device_b.device_id}", "some-other-client"):
            attacker = _session(live_config, device_a, client_id=client_id)
            attacker.start()
            try:
                assert attacker.wait_connected() is False
            finally:
                attacker.close()
        assert not victim.disconnect_event.is_set()
        # meteorcli's own forms still work.
        for client_id in (
            f"device-{device_a.device_id}",
            f"device-{device_a.device_id}-mqtt-test-ab12cd34",
        ):
            own = _session(live_config, device_a, client_id=client_id)
            own.start()
            try:
                assert own.wait_connected() is True
            finally:
                own.close()
    finally:
        victim.close()


def test_revoke_cuts_a_connected_device(api, live_config):
    """Revoking ends access for a session that is already connected."""
    device = api.register_device("sec-live-revoke")
    status = f"devices/{device.device_id}/status"
    observer = _observer(live_config)
    session = _session(live_config, device)
    session.start()
    try:
        assert session.wait_connected() is True
        _assert_publish_delivered(observer, session, status)
        session.disconnect_event.clear()
        api.revoke_mqtt(device.device_id)
        if live_config.instant_disconnect:
            assert session.disconnect_event.wait(10), (
                "the broker did not drop the revoked device"
            )
        # Connected or not, nothing from it gets through any more.
        _assert_open_session_publish_blocked(observer, session, status)
        retry = _session(live_config, device)
        retry.start()
        try:
            assert retry.wait_connected() is False
        finally:
            retry.close()
    finally:
        session.close()
        observer.close()


def test_disable_cuts_a_connected_device(api, live_config):
    device = api.register_device("sec-live-disable")
    status = f"devices/{device.device_id}/status"
    observer = _observer(live_config)
    session = _session(live_config, device)
    session.start()
    try:
        assert session.wait_connected() is True
        session.disconnect_event.clear()
        api.disable_device(device.device_id)
        if live_config.instant_disconnect:
            assert session.disconnect_event.wait(10), (
                "the broker did not drop the disabled device"
            )
        _assert_open_session_publish_blocked(observer, session, status)
    finally:
        session.close()
        observer.close()


def test_platform_needs_its_password(live_config):
    session = MqttSession(
        host=live_config.mqtt_host,
        port=live_config.mqtt_port,
        ca_file=live_config.mqtt_ca_file,
        username=live_config.mqtt_platform_username,
        password="not-the-platform-password",
    )
    session.start()
    try:
        assert session.wait_connected() is False
    finally:
        session.close()


def test_old_tls_versions_refused(live_config):
    """Only TLS 1.2 and newer reach the broker."""
    import ssl

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        context.set_ciphers("DEFAULT:@SECLEVEL=0")  # let the client offer legacy TLS
        context.minimum_version = ssl.TLSVersion.TLSv1
        context.maximum_version = ssl.TLSVersion.TLSv1_1
    except (ValueError, ssl.SSLError):
        pytest.skip("This OpenSSL build cannot offer TLS 1.1")
    import socket

    # Connect first and let TCP errors fail the test: only a failure during the
    # TLS handshake counts as the broker refusing old TLS.
    raw = socket.create_connection(
        (live_config.mqtt_host, live_config.mqtt_port), timeout=8
    )
    refused = False
    try:
        with context.wrap_socket(raw):
            pass
    except ssl.SSLError as exc:
        # Only a refusal by the broker counts. If this OpenSSL cannot offer
        # TLS 1.0/1.1 at all, the handshake fails locally and proves nothing.
        if "NO_PROTOCOLS_AVAILABLE" in str(exc).upper().replace(" ", "_"):
            pytest.skip("This OpenSSL build cannot offer TLS 1.0 or 1.1")
        refused = True
    except (ConnectionResetError, BrokenPipeError, ssl.SSLEOFError):
        refused = True  # the broker closed the connection during the handshake
    finally:
        raw.close()
    assert refused


def test_plain_tcp_is_not_mqtt(live_config):
    """The public port speaks TLS only: a plain MQTT CONNECT gets no CONNACK."""
    import socket

    connect = bytes.fromhex("101000044d5154540402003c000474657374")
    with socket.create_connection(
        (live_config.mqtt_host, live_config.mqtt_port), timeout=8
    ) as raw:
        raw.settimeout(4)
        raw.sendall(connect)
        try:
            answer = raw.recv(4)
        except (TimeoutError, OSError):
            answer = b""
    assert not answer.startswith(b"\x20")


def test_valid_tls_connects(api, live_config):
    device = api.register_device("sec-tls")
    session = _session(live_config, device)
    session.start()
    try:
        assert session.wait_connected() is True
    finally:
        session.close()


def test_untrusted_ca_denied(api, live_config, tmp_path: Path):
    key = tmp_path / "wrong.key"
    cert = tmp_path / "wrong.crt"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-nodes",
            "-subj",
            "/CN=untrusted-mqtt-ca",
        ],
        check=True,
        capture_output=True,
    )
    device = api.register_device("sec-bad-ca")
    session = _session(live_config, device, untrusted_ca=str(cert))
    session.start()
    try:
        assert session.wait_connected() is False
    finally:
        session.close()


def test_device_reports_online(api, live_config):
    device = api.register_device("sec-online")
    session = _session(live_config, device)
    session.start()
    try:
        assert session.wait_connected() is True
        session.publish(
            f"devices/{device.device_id}/status",
            json.dumps({"status": "online"}),
            retain=True,
        )
        assert wait_until(
            lambda: api.get_device(device.device_id).get("mqtt_status") == "online",
            timeout_seconds=20,
        )
    finally:
        session.close()
