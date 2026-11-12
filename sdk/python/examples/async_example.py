"""The same API with asyncio."""

from __future__ import annotations

import asyncio

from meteorcloud import AsyncMeteorClient


async def main() -> None:
    async with AsyncMeteorClient.from_env() as client:
        await client.telemetry.publish("energy.metrics", {"power": 1200, "voltage": 230})

        async def on_reading(event):
            print(event.stream, event.data)

        await client.telemetry.subscribe("energy.*", on_reading)

        @client.commands.handler("ping")
        async def ping(command):
            return {"pong": True}

        await client.run()  # until SIGTERM or Ctrl+C


asyncio.run(main())
