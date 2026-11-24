"""Publish a reading every SECONDS (default 1) so the sink has something to store.

    METEORCLOUD_ENDPOINT=... METEORCLOUD_TOKEN_FILE=./meter.token python fake_meter.py [COUNT] [SECONDS]

In production this is your edge application on a device; here it is a stand-in.
"""

import math
import random
import sys
import time

from meteorcloud import MeteorClient

count = int(sys.argv[1]) if len(sys.argv) > 1 else 0  # 0 = forever
interval = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0

with MeteorClient.from_env() as client:
    n = 0
    while count == 0 or n < count:
        power = 1200 + 300 * math.sin(n / 10) + random.uniform(-20, 20)
        client.telemetry.publish("energy.metrics", {"power": round(power, 1), "voltage": 230}, seq=n)
        n += 1
        if n % 10 == 0:
            print(f"published {n}", flush=True)
        time.sleep(interval)
