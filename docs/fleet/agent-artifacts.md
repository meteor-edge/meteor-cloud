# Agent artifact download

Devices download release artifacts over HTTPS with their device token. The
control plane allows a download only when that artifact is on the device’s
**current desired** software (set by a [Deploy](deployments.md)).

## Endpoint

```
GET /api/v1/agent/artifacts/{artifact_id}/download
Authorization: Bearer dev_...
```

Response headers include:

| Header | Meaning |
|--------|---------|
| `X-Checksum-SHA256` | Expected SHA-256 of the body |
| `Content-Length` | Size in bytes |
| `Content-Disposition` | Filename |

The agent must verify the checksum after download. A mismatch is reported as
`sync_status=failed` with `last_error` set; **installed** software is not
changed.

While downloading, the agent reports `sync_status=updating` and
`progress_percent`. Operator console downloads under
`/organizations/{org}/artifacts/{id}/download` are unchanged.

## Related

- [Heartbeat](heartbeat.md) — `software_report` and `desired_software`
- [Artifacts](artifacts.md)
- [Deployments](deployments.md)
