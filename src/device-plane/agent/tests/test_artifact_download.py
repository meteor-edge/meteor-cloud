"""Agent artifact download and checksum verification."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from unittest.mock import patch

import pytest

from edge_agent.client import AgentApiError, EdgeClient
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

    with patch("urllib.request.urlopen", return_value=response):
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

    with patch("urllib.request.urlopen", return_value=response):
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
