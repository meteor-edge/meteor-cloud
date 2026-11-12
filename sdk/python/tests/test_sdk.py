"""Developer workflow against the real data plane: publish, subscribe, query, commands."""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

import meteorcloud
from meteorcloud import AsyncMeteorClient, MeteorClient

from .conftest import ORG, OTHER_ORG


def client_for(plane, slug, **kwargs) -> MeteorClient:
    token_kwargs = {k: kwargs.pop(k) for k in ("org", "device_id") if k in kwargs}
    return MeteorClient(endpoint=plane.endpoint, token=plane.token(slug, **token_kwargs), **{"retry_time": 5, **kwargs})


def wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_from_env_reads_token_file(dataplane, tmp_path, monkeypatch):
    token_file = tmp_path / "token"
    token_file.write_text(dataplane.token("meter", device_id="dev-1") + "\n")
    monkeypatch.setenv("METEORCLOUD_ENDPOINT", dataplane.endpoint)
    monkeypatch.setenv("METEORCLOUD_TOKEN_FILE", str(token_file))
    monkeypatch.delenv("METEORCLOUD_TOKEN", raising=False)
    with MeteorClient.from_env() as client:
        me = client.whoami()
        assert (me.organization_id, me.application, me.device_id, me.environment) == (ORG, "meter", "dev-1", "test")
        assert me.permissions["publish"] == ["energy.**", "alerts.*"]


def test_publish_query_and_identity_comes_from_token(dataplane):
    with client_for(dataplane, "meter", device_id="dev-1") as meter, client_for(dataplane, "dashboard") as dash:
        base = datetime.now(timezone.utc) - timedelta(minutes=10)
        for i in range(5):
            result = meter.telemetry.publish("energy.metrics", {"power": 1000 + i * 100, "phase": "l1"},
                                             time=base + timedelta(minutes=i), seq=i)
            assert result.status == "stored"
        page = dash.telemetry.query("energy.metrics", limit=3)
        assert [e.data["power"] for e in page] == [1000, 1100, 1200]
        first = page.events[0]
        assert (first.organization_id, first.application, first.device_id, first.kind) == (
            ORG, "meter", "dev-1", "telemetry")
        assert first.seq == 0 and first.time == base.replace(microsecond=base.microsecond)
        assert page.next_cursor
        rest = dash.telemetry.query("energy.metrics", limit=3, cursor=page.next_cursor)
        assert [e.data["power"] for e in rest] == [1300, 1400] and rest.next_cursor is None
        assert [e.data["power"] for e in dash.telemetry.query_all("energy.metrics", page_size=2)] == [
            1000, 1100, 1200, 1300, 1400]
        newest = dash.telemetry.query("energy.metrics", order="desc", limit=1)
        assert newest.events[0].data["power"] == 1400
        window = dash.telemetry.query("energy.metrics", start=base + timedelta(minutes=1),
                                      end=base + timedelta(minutes=3))
        assert [e.data["power"] for e in window] == [1100, 1200]
        assert len(dash.telemetry.query("energy.metrics", device_id="other")) == 0
        assert len(dash.telemetry.query("energy.metrics", where={"phase": "l1"})) == 5
        assert len(dash.telemetry.query("energy.metrics", where={"phase": "l2"})) == 0

        buckets = dash.telemetry.aggregate("energy.metrics", "power", function="avg", bucket="1h",
                                           start=base - timedelta(hours=1), end=base + timedelta(hours=1))
        filled = [b for b in buckets if b.count]
        assert sum(b.count for b in filled) == 5
        total = dash.telemetry.aggregate("energy.metrics", "power", function="sum", bucket="1d",
                                         start=base - timedelta(hours=1), end=base + timedelta(hours=1))
        assert sum(b.value or 0 for b in total) == 6000


def test_publish_is_idempotent_and_reports_rejections(dataplane):
    with client_for(dataplane, "meter") as meter:
        assert meter.telemetry.publish("energy.metrics", {"v": 1}, id="reading-1").status == "stored"
        assert meter.telemetry.publish("energy.metrics", {"v": 1}, id="reading-1").status == "duplicate"
        assert len(meter.telemetry.query("energy.metrics")) == 1

        results = meter.telemetry.publish_many("energy.metrics", [{"v": 2}, {"data": {"v": 3}, "id": "reading-3"}])
        assert [r.status for r in results] == ["stored", "stored"] and results[1].id == "reading-3"

        with pytest.raises(meteorcloud.PublishError) as refused:
            meter.telemetry.publish("billing.invoices", {"v": 1})
        assert refused.value.code == "forbidden_stream"
        with pytest.raises(meteorcloud.PublishError) as bad:
            meter.telemetry.publish("Not A Stream", {"v": 1})
        assert bad.value.code == "invalid_stream"
        with pytest.raises(meteorcloud.ValidationError):
            meter.telemetry.publish("energy.metrics", ["not", "a", "dict"])  # type: ignore[arg-type]
        mixed = meter.telemetry.publish_many("energy.metrics", [{"v": 4}])
        assert all(r.ok for r in mixed)


def test_kinds_are_kept_apart(dataplane):
    with client_for(dataplane, "meter") as meter, client_for(dataplane, "dashboard") as dash:
        meter.telemetry.publish("energy.metrics", {"power": 1})
        meter.events.publish("alerts.overheat", {"temperature": 91}, type="overheat")
        meter.inference.publish("energy.forecast", {"label": "peak", "confidence": 0.93})
        assert len(dash.telemetry.query("energy.metrics")) == 1
        assert len(dash.events.query("energy.metrics")) == 0
        alert = dash.events.query("alerts.overheat").events[0]
        assert (alert.kind, alert.type, alert.data["temperature"]) == ("event", "overheat", 91)
        assert dash.inference.query("energy.forecast").events[0].data["label"] == "peak"


def test_permissions_and_tenant_isolation(dataplane):
    with client_for(dataplane, "meter") as meter, client_for(dataplane, "dashboard") as dash, \
            client_for(dataplane, "stranger", org=OTHER_ORG) as stranger:
        meter.telemetry.publish("energy.metrics", {"power": 1})
        with pytest.raises(meteorcloud.PublishError):
            dash.telemetry.publish("energy.metrics", {"power": 2})  # no publish permission
        with pytest.raises(meteorcloud.PermissionDeniedError):
            meter.events.query("alerts.overheat")  # no read permission
        with pytest.raises(meteorcloud.PermissionDeniedError):
            meter.events.subscribe("alerts.*")  # no subscribe permission
        with pytest.raises(meteorcloud.PermissionDeniedError):
            meter.commands.send("meter", "reboot")  # no send_commands permission
        # Another organization with the same stream names sees nothing and reaches nothing.
        assert len(stranger.telemetry.query("energy.metrics")) == 0
        with stranger.telemetry.subscribe("energy.**") as foreign:
            meter.telemetry.publish("energy.metrics", {"power": 3})
            with pytest.raises(TimeoutError):
                foreign.get(timeout=0.5)
        with pytest.raises(meteorcloud.ValidationError):
            stranger.commands.send("meter", "reboot")  # "meter" does not exist in that organization

    with MeteorClient(endpoint=dataplane.endpoint, token="mca_wrong", retry_time=2) as nobody:
        nobody._async._http._token_loaded_at = 0  # not a freshly issued token
        with pytest.raises(meteorcloud.AuthenticationError):
            nobody.telemetry.publish("energy.metrics", {"power": 1})


def test_subscribe_iterator_callback_and_filters(dataplane):
    with client_for(dataplane, "meter", device_id="dev-1") as meter, \
            client_for(dataplane, "meter", device_id="dev-2") as meter2, client_for(dataplane, "dashboard") as dash:
        seen: list[str] = []
        dash.telemetry.subscribe("energy.*", lambda event: seen.append(event.data["n"]))
        only_dev2 = dash.telemetry.subscribe("energy.**", device_id="dev-2")
        with dash.telemetry.subscribe("energy.metrics") as sub:
            meter.telemetry.publish("energy.metrics", {"n": "a"})
            meter2.telemetry.publish("energy.metrics", {"n": "b"})
            meter.telemetry.publish("energy.other", {"n": "c"})
            meter.events.publish("energy.metrics", {"n": "event-kind"})
            got = [sub.get(timeout=5), sub.get(timeout=5)]
            assert [e.data["n"] for e in got] == ["a", "b"]
            assert got[0].device_id == "dev-1" and got[0].application == "meter"
        assert wait_for(lambda: seen == ["a", "b", "c"]), seen
        event = only_dev2.get(timeout=5)
        assert (event.data["n"], event.device_id) == ("b", "dev-2")
        with pytest.raises(TimeoutError):
            only_dev2.get(timeout=0.3)
        only_dev2.close()
        assert list(only_dev2) == []  # a closed subscription ends iteration


def test_decorators_and_run(dataplane):
    meter = client_for(dataplane, "meter", device_id="dev-1")
    dash = client_for(dataplane, "dashboard")
    seen = []

    @dash.telemetry.on("energy.**")
    def on_energy(event):
        seen.append(event.data)

    @meter.commands.handler("set-interval")
    def set_interval(command):
        return {"interval": command.payload["seconds"], "device": command.device_id}

    runner = threading.Thread(target=lambda: dash.run(handle_signals=False))
    runner.start()
    meter.start()
    try:
        assert wait_for(lambda: dash.connected and meter.connected)
        meter.telemetry.publish("energy.metrics", {"power": 7})
        assert wait_for(lambda: seen == [{"power": 7}])
        with client_for(dataplane, "dashboard") as sender:
            assert sender.commands.call("meter", "set-interval", {"seconds": 5}, device_id="dev-1", timeout=10) == {
                "interval": 5, "device": "dev-1"}
    finally:
        dash.stop()
        runner.join(10)
        meter.close()
    assert not runner.is_alive()
    with pytest.raises(meteorcloud.ClientClosedError):
        dash.telemetry.publish("energy.metrics", {"power": 1})


def test_commands_lifecycle(dataplane):
    with client_for(dataplane, "meter", device_id="dev-1") as meter, client_for(dataplane, "dashboard") as dash:
        calls = []

        @meter.commands.handler("reboot")
        def reboot(command):
            calls.append(command.id)
            return {"rebooted": True}

        @meter.commands.handler("explode")
        def explode(command):
            raise RuntimeError("cannot do that")

        # Sent before the target is connected: delivered once it connects.
        early = dash.commands.send("meter", "reboot", {"delay": 1}, device_id="dev-1")
        assert early.state in ("PENDING", "DISPATCHED")
        meter.start()
        done = dash.commands.wait(early.id, timeout=10)
        assert (done.state, done.result, done.succeeded) == ("SUCCEEDED", {"rebooted": True}, True)
        assert calls == [early.id]

        with pytest.raises(meteorcloud.CommandError) as failed:
            dash.commands.call("meter", "explode", device_id="dev-1", timeout=10)
        assert "cannot do that" in str(failed.value) and failed.value.command.state == "FAILED"
        with pytest.raises(meteorcloud.CommandError) as unknown:
            dash.commands.call("meter", "no-such-command", device_id="dev-1", timeout=10)
        assert "no handler" in str(unknown.value)

        # The same idempotency key creates one command.
        first = dash.commands.send("meter", "reboot", device_id="dev-1", idempotency_key="k-1")
        again = dash.commands.send("meter", "reboot", device_id="dev-1", idempotency_key="k-1")
        assert first.id == again.id
        dash.commands.wait(first.id, timeout=10)
        assert calls.count(first.id) == 1

        # A command for a device where the application is not running times out waiting.
        lost = dash.commands.send("meter", "reboot", device_id="dev-404", expires_in=1)
        with pytest.raises(meteorcloud.CommandTimeoutError):
            dash.commands.wait(lost.id, timeout=0.5)
        assert dash.commands.wait(lost.id, timeout=10).state == "TIMEOUT"
        # The sender of a command can read it; another application cannot.
        with pytest.raises(meteorcloud.NotFoundError):
            meter2 = client_for(dataplane, "stranger", org=OTHER_ORG)
            try:
                meter2.commands.get(first.id)
            finally:
                meter2.close()


def test_token_rotation_and_revocation(dataplane, tmp_path):
    token_file = tmp_path / "token"
    old = dataplane.token("meter", device_id="dev-1")
    token_file.write_text(old)
    with MeteorClient(endpoint=dataplane.endpoint, token_file=str(token_file), retry_time=5) as meter, \
            client_for(dataplane, "dashboard") as dash:
        handled = []
        meter.commands.register("ping", lambda command: handled.append(command.id) or {"pong": True})
        meter.start()
        assert wait_for(lambda: meter.connected)
        meter.telemetry.publish("energy.metrics", {"n": 1})

        # The agent rotates the credential: new file content, old token revoked.
        token_file.write_text(dataplane.token("meter", device_id="dev-1"))
        dataplane.revoke(old)
        meter.telemetry.publish("energy.metrics", {"n": 2})  # picks up the new token by itself
        assert dash.commands.call("meter", "ping", device_id="dev-1", timeout=20) == {"pong": True}
        assert wait_for(lambda: meter.connected, 15)  # the live connection came back with the new token
        assert len(dash.telemetry.query("energy.metrics")) == 2


def test_reconnects_and_resubscribes_after_an_outage(dataplane):
    with client_for(dataplane, "meter") as meter, client_for(dataplane, "dashboard", retry_time=20) as dash:
        sub = dash.telemetry.subscribe("energy.**")
        meter.telemetry.publish("energy.metrics", {"n": 1})
        assert sub.get(timeout=5).data == {"n": 1}

        dataplane.stop()
        assert wait_for(lambda: not dash.connected)
        with pytest.raises(meteorcloud.UnavailableError):
            MeteorClient(endpoint=dataplane.endpoint, token="x", retry_time=0.5).telemetry.publish("energy.a", {})
        threading.Timer(1.0, dataplane.start).start()
        # A publish during the outage is retried until the service is back.
        assert meter.telemetry.publish("energy.metrics", {"n": 2}).status == "stored"
        assert wait_for(lambda: dash.connected, 20)
        time.sleep(0.3)
        meter.telemetry.publish("energy.metrics", {"n": 3})
        # The retried publish may or may not have been seen live, depending on who came back first.
        received = [sub.get(timeout=10).data["n"]]
        if received == [2]:
            received.append(sub.get(timeout=10).data["n"])
        assert received[-1] == 3


def test_async_client(dataplane):
    async def scenario():
        async with AsyncMeteorClient(endpoint=dataplane.endpoint, token=dataplane.token("meter", device_id="d1"),
                                     retry_time=5) as meter, \
                AsyncMeteorClient(endpoint=dataplane.endpoint, token=dataplane.token("dashboard"),
                                  retry_time=5) as dash:
            got_async, got_sync = [], []

            async def on_async(event):
                got_async.append(event.data["n"])

            await dash.telemetry.subscribe("energy.**", on_async)
            await dash.telemetry.subscribe("energy.**", lambda event: got_sync.append(event.data["n"]))

            @meter.commands.handler("double")
            async def double(command):
                return {"value": command.payload["value"] * 2}

            await meter.start()
            async with await dash.telemetry.subscribe("energy.metrics") as sub:
                await asyncio.gather(*(meter.telemetry.publish("energy.metrics", {"n": i}) for i in range(20)))
                received = [(await sub.get(timeout=5)).data["n"] for _ in range(20)]
            assert sorted(received) == list(range(20))
            for _ in range(100):
                if len(got_async) == 20 and len(got_sync) == 20:
                    break
                await asyncio.sleep(0.05)
            assert sorted(got_async) == list(range(20)) and sorted(got_sync) == list(range(20))
            assert len(await dash.telemetry.query("energy.metrics")) == 20
            assert len([e async for e in dash.telemetry.query_all("energy.metrics", page_size=7)]) == 20
            assert await dash.commands.call("meter", "double", {"value": 21}, device_id="d1", timeout=10) == {
                "value": 42}
            assert (await meter.whoami()).device_id == "d1"

    asyncio.run(scenario())
