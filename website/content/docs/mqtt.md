---
title: MQTT
description: Device TLS MQTT, topics, and last-value ingest.
section: Platform
order: 13
---

Devices connect to EMQX on port **8883** with TLS. Each device gets username `device_<uuid>` and a one-time password at registration or claim. The control plane stores only a hash.

A device may:

- **Publish** `devices/<id>/status`, `devices/<id>/metrics`, `devices/<id>/commands/result`
- **Subscribe** `devices/<id>/commands`

The platform MQTT user is a superuser (console test publish, ping).

Inbound messages are ingested as last-value columns on `Device` (`mqtt_status`, `mqtt_metrics`, command results). Disable the device or revoke MQTT credentials to deny broker login.

mTLS device certificates are not implemented. AWS production Compose does not currently start EMQX; use local `make dev-data-plane` or run that stack on a host you control. GCP Cloud Run does not expose MQTT TCP.
