"""A cloud-hosted application: watches live data, reads history, sends a command.

    export METEORCLOUD_ENDPOINT=https://cloud.example.com
    export METEORCLOUD_TOKEN_FILE=./dashboard.token     # a credential created in the console or API
    python cloud_dashboard.py
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from meteorcloud import CommandError, MeteorClient

with MeteorClient.from_env() as client:
    # History: the last hour, then five-minute averages.
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    page = client.telemetry.query("energy.metrics", start=since, limit=10, order="desc")
    for event in page:
        print(event.time.isoformat(), event.device_id, event.data)
    for bucket in client.telemetry.aggregate("energy.metrics", "power", function="avg", bucket="5m", start=since):
        if bucket.count:
            print(bucket.start.isoformat(), round(bucket.value, 1), f"({bucket.count} readings)")

    # Live: the next five readings from any device.
    with client.telemetry.subscribe("energy.*") as live:
        for _, event in zip(range(5), live, strict=False):
            print("live", event.device_id, event.stream, event.data)
            device_id = event.device_id

    # Command: ask the meter application on that device to publish faster.
    try:
        result = client.commands.call("meter", "set-interval", {"seconds": 1}, device_id=device_id, timeout=30)
        print("meter answered", result)
    except CommandError as error:
        print("command did not succeed:", error)
