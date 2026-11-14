"""Smallest possible program: publish one reading and read it back."""

from meteorcloud import MeteorClient

client = MeteorClient.from_env()
client.telemetry.publish("demo.metrics", {"power": 1200, "voltage": 230})
for event in client.telemetry.query("demo.metrics", limit=5, order="desc"):
    print(event.time, event.data)
client.close()
