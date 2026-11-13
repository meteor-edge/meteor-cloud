"""An application MeteorCloud deploys to a device.

It publishes a reading every few seconds, raises an event when power is high and
accepts a command that changes how often it publishes.

Nothing here knows where it runs: MeteorCloud provides METEORCLOUD_ENDPOINT and the
token file. The same file runs unchanged on a laptop or in the cloud when you set
those two yourself.
"""

from __future__ import annotations

import logging
import os
import random
import threading

from meteorcloud import Command, MeteorClient, MeteorCloudError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("edge-app")

client = MeteorClient.from_env()
interval = float(os.environ.get("PUBLISH_INTERVAL_SECONDS", "5"))
stop = threading.Event()


@client.commands.handler("set-interval")
def set_interval(command: Command) -> dict[str, float]:
    """Sent from the console or another application: {"seconds": 1}."""
    global interval
    interval = max(0.2, float(command.payload["seconds"]))
    log.info("publishing every %s seconds", interval)
    return {"interval": interval}


def publish_readings() -> None:
    seq = 0
    while not stop.wait(interval):
        power = round(random.uniform(900, 1500), 1)
        try:
            client.telemetry.publish("energy.metrics", {"power": power, "voltage": 230}, seq=seq)
            if power > 1400:
                client.events.publish("alerts.high-power", {"power": power, "threshold": 1400})
            seq += 1
        except MeteorCloudError as error:
            # publish() already retried. Nothing was lost silently: decide here what to do.
            log.warning("reading not stored: %s", error)


if __name__ == "__main__":
    me = client.whoami()
    log.info("running as application %s on device %s", me.application, me.device_id)
    worker = threading.Thread(target=publish_readings, daemon=True)
    worker.start()
    try:
        client.run()  # handles commands until SIGTERM or Ctrl+C, then shuts down cleanly
    finally:
        stop.set()
