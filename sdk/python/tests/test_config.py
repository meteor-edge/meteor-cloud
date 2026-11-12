from __future__ import annotations

import os

import httpx
import pytest

import meteorcloud
from meteorcloud import Config, MeteorClient
from meteorcloud.config import DEFAULT_TOKEN_FILE, TokenSource
from meteorcloud.models import format_time, parse_time


def test_from_env_defaults_and_overrides():
    config = Config.from_env({"METEORCLOUD_ENDPOINT": "https://data.example.com/", "METEORCLOUD_APP_ID": "meter",
                              "METEORCLOUD_DEVICE_ID": "dev-1", "METEORCLOUD_TIMEOUT": "3"})
    assert config.endpoint == "https://data.example.com"
    assert config.token is None and config.token_file == DEFAULT_TOKEN_FILE
    assert (config.app_id, config.device_id, config.timeout) == ("meter", "dev-1", 3.0)
    assert Config.from_env({"METEORCLOUD_ENDPOINT": "http://x", "METEORCLOUD_TOKEN": "t"}).token_file is None


def test_configuration_errors(tmp_path):
    with pytest.raises(meteorcloud.ConfigurationError, match="METEORCLOUD_ENDPOINT"):
        Config.from_env({})
    with pytest.raises(meteorcloud.ConfigurationError):
        Config(endpoint="data.example.com", token="t")
    with pytest.raises(meteorcloud.ConfigurationError):
        Config.from_env({"METEORCLOUD_ENDPOINT": "http://x", "METEORCLOUD_TIMEOUT": "soon"})
    missing = TokenSource(Config(endpoint="http://x", token_file=str(tmp_path / "nope")))
    with pytest.raises(meteorcloud.ConfigurationError, match="token file"):
        missing.get()


def test_token_file_is_reread_when_it_changes(tmp_path):
    path = tmp_path / "token"
    path.write_text("one\n")
    source = TokenSource(Config(endpoint="http://x", token_file=str(path)))
    assert source.get() == "one"
    path.write_text("two-longer\n")
    assert source.get() == "two-longer"
    os.remove(path)
    assert source.get() == "two-longer"  # keeps the last token while the file is being replaced
    assert source.refresh() is False


def test_times_round_trip():
    parsed = parse_time("2026-11-11T10:00:00.123456789Z")
    assert parsed.microsecond == 123456 and parsed.utcoffset().total_seconds() == 0
    assert format_time(parsed) == "2026-11-11T10:00:00.123456Z"
    assert format_time("2026-01-01T00:00:00Z") == "2026-01-01T00:00:00Z"


def test_retries_overload_then_succeeds_and_never_logs_the_token(caplog):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers["Authorization"])
        if len(calls) < 3:
            return httpx.Response(503, headers={"Retry-After": "0"},
                                  json={"error": {"code": "overloaded", "message": "busy"}})
        sent = __import__("json").loads(request.content)["messages"]
        return httpx.Response(200, json={"results": [{"index": 0, "id": sent[0]["id"], "status": "stored"}]})

    with caplog.at_level("DEBUG"):
        with MeteorClient(endpoint="http://dataplane.test", token="mca_secret_value",
                          transport=httpx.MockTransport(handler)) as client:
            assert client.telemetry.publish("energy.metrics", {"power": 1, "note": "private-payload"}).ok
    assert calls == ["Bearer mca_secret_value"] * 3
    assert "mca_secret_value" not in caplog.text and "private-payload" not in caplog.text


def test_gives_up_when_the_retry_budget_runs_out():
    transport = httpx.MockTransport(lambda request: httpx.Response(
        429, headers={"Retry-After": "1"}, json={"error": {"code": "rate_limited", "message": "slow down"}}))
    with MeteorClient(endpoint="http://dataplane.test", token="t", retry_time=0.3, transport=transport) as client:
        with pytest.raises(meteorcloud.RateLimitedError) as err:
            client.telemetry.publish("energy.metrics", {"power": 1})
    assert err.value.code == "rate_limited" and err.value.status == 429
