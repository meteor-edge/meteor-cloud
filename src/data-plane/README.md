# MeteorCloud data plane

| Path | What |
| --- | --- |
| [`go/`](go/README.md) | Go services: the MQTT forwarder (`cmd/mqtt-forwarder`, Compose service `data-plane`; [docs](../../docs/data-plane/mqtt-forwarder.md)) and the application data API (`cmd/dataplane`; [docs](../../docs/data-plane/README.md)) |
| [`broker/mosquitto/`](broker/mosquitto/) | Mosquitto image with the `meteorcloud_auth` plugin |

The previous Python MQTT gateway (`data_plane`) was removed; the forwarder replaced it.
Contracts: [`contracts/mqtt-http.md`](../../contracts/mqtt-http.md),
[`contracts/data-plane-v1.md`](../../contracts/data-plane-v1.md).
