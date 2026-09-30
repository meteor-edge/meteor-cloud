from __future__ import annotations

import os

os.environ["MQTT_ENABLED"] = "false"

from data_plane.config import get_settings

get_settings.cache_clear()
