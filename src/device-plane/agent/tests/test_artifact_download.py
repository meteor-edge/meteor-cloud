"""Agent artifact download and checksum verification."""

from __future__ import annotations

import email.message
import hashlib
import io
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pytest

from edge_agent.client import _DOWNLOAD_OPENER, AgentApiError, EdgeClient
from edge_agent.software import reconcile_downloads


class _FakeResponse:
    def __init__(self, body: bytes, headers: dict[str, str], status: int = 200) -> None:
        self._body = io.BytesIO(body)
        self.headers = headers
        self.status = status

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: object) -> bool:
        return False


def test_download_artifact_writes_file_and_verifies_checksum(tmp_path: Path) -> None:
    content = b"unit-file-bytes"
    digest = hashlib.sha256(content).hexdigest()
    dest = tmp_path / "art-1"
    client = EdgeClient("http://example.test")
    response = _FakeResponse(
        content,
        {"X-Checksum-SHA256": digest, "Content-Length": str(len(content))},
    )

    with patch.object(_DOWNLOAD_OPENER, "open", return_value=response):
        result = client.download_artifact(
            device_token="dev_test",
            artifact_id="art-1",
            destination=dest,
        )

    assert result == digest
    assert dest.read_bytes() == content


def test_download_artifact_rejects_checksum_mismatch(tmp_path: Path) -> None:
    content = b"unit-file-bytes"
    dest = tmp_path / "art-1"
    client = EdgeClient("http://example.test")
    response = _FakeResponse(
        content,
        {"X-Checksum-SHA256": "0" * 64, "Content-Length": str(len(content))},
    )

    with patch.object(_DOWNLOAD_OPENER, "open", return_value=response):
        with pytest.raises(AgentApiError) as exc:
            client.download_artifact(
                device_token="dev_test",
                artifact_id="art-1",
                destination=dest,
            )

    assert exc.value.code == "checksum_mismatch"
    assert not dest.exists()
    assert not list(tmp_path.glob("*.partial"))


def test_reconcile_reports_failed_on_checksum_mismatch(tmp_path: Path) -> None:
    client = EdgeClient("http://example.test")
    desired = [
        {
            "workload_key": "camera-agent.service",
            "kind": "systemd",
            "artifact_id": "art-1",
            "release_id": "rel-1",
            "spec": {"unit_name": "camera-agent.service"},
        }
    ]

    with (
        patch.object(client, "get_software", return_value=desired),
        patch("edge_agent.software.send_heartbeat"),
        patch.object(
            client,
            "download_artifact",
            side_effect=AgentApiError(0, "checksum_mismatch", "Downloaded artifact failed SHA-256 verification."),
        ),
    ):
        reports = reconcile_downloads(client, "dev_test", tmp_path)

    assert len(reports) == 1
    assert reports[0]["sync_status"] == "failed"
    assert "SHA-256" in (reports[0]["last_error"] or "")
    assert reports[0].get("installed_release_id") is None


def _redirect(location: str, checksum: str | None) -> urllib.error.HTTPError:
    headers = email.message.Message()
    headers["Location"] = location
    if checksum is not None:
        headers["X-Checksum-SHA256"] = checksum
    return urllib.error.HTTPError("https://cloud.test/x", 302, "Found", headers, io.BytesIO(b""))


def test_download_follows_a_storage_redirect_without_the_device_token(tmp_path: Path) -> None:
    content = b"image-bytes"
    digest = hashlib.sha256(content).hexdigest()
    requests: list[urllib.request.Request] = []

    def open_(request: urllib.request.Request, timeout: float):
        requests.append(request)
        if len(requests) == 1:
            raise _redirect("https://bucket.test/obj?X-Amz-Signature=abc", digest)
        return _FakeResponse(content, {"Content-Length": str(len(content))})

    client = EdgeClient("https://cloud.test")
    with patch.object(_DOWNLOAD_OPENER, "open", side_effect=open_):
        result = client.download_artifact(device_token="dev_secret", artifact_id="a1", destination=tmp_path / "a1")

    assert result == digest
    assert (tmp_path / "a1").read_bytes() == content
    assert requests[0].get_header("X-meteorcli-redirect") == "1"
    assert requests[1].full_url == "https://bucket.test/obj?X-Amz-Signature=abc"
    assert requests[1].get_header("Authorization") is None


def test_storage_copy_is_checked_against_the_redirect_checksum(tmp_path: Path) -> None:
    responses = iter([_redirect("https://bucket.test/obj", "0" * 64)])

    def open_(request: urllib.request.Request, timeout: float):
        for redirect in responses:
            raise redirect
        return _FakeResponse(b"tampered", {"X-Checksum-SHA256": hashlib.sha256(b"tampered").hexdigest()})

    client = EdgeClient("https://cloud.test")
    with patch.object(_DOWNLOAD_OPENER, "open", side_effect=open_), pytest.raises(AgentApiError) as exc:
        client.download_artifact(device_token="dev", artifact_id="a1", destination=tmp_path / "a1")
    assert exc.value.code == "checksum_mismatch"
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    ("location", "checksum", "code"),
    [
        ("https://bucket.test/obj", None, "checksum_missing"),
        ("http://bucket.test/obj", "a" * 64, "download_redirect_insecure"),
        ("file:///etc/passwd", "a" * 64, "download_redirect_insecure"),
    ],
)
def test_unsafe_redirects_are_refused(tmp_path: Path, location: str, checksum: str | None, code: str) -> None:
    client = EdgeClient("https://cloud.test")
    with (
        patch.object(_DOWNLOAD_OPENER, "open", side_effect=_redirect(location, checksum)),
        pytest.raises(AgentApiError) as exc,
    ):
        client.download_artifact(device_token="dev", artifact_id="a1", destination=tmp_path / "a1")
    assert exc.value.code == code
