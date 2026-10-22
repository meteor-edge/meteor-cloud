"""Agent client helpers for desired software."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from edge_agent.client import EdgeClient


def test_get_software_returns_list() -> None:
    client = EdgeClient("http://example.test")
    payload = [{"workload_key": "cam.service", "release_version": "1.0"}]
    response = MagicMock()
    response.read.return_value = json.dumps(payload).encode()
    response.__enter__.return_value = response
    response.__exit__.return_value = False

    with patch("urllib.request.urlopen", return_value=response):
        result = client.get_software(device_token="dev_test")

    assert result == payload
