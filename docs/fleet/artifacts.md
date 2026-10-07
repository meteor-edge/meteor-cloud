# Artifacts

An **artifact** is a versioned file that can later be deployed to devices: an
OS image, firmware, a Docker Compose bundle, a systemd unit, a configuration
file, or something else. OS images are the MVP use case.

Artifacts are organization-scoped. Most belong to a [device type](device-types.md);
`device_type_id = null` means the artifact applies to any device type.

## Storage

The binary is stored in object storage behind the `ObjectStorage` port
(`app/ports/storage.py`). PostgreSQL keeps only metadata: name, version, type,
file name, content type, size, SHA-256 checksum, and the storage key.

Storage keys use IDs, not names, so renames never orphan content:

```text
organizations/{organization_id}/artifacts/{artifact_id}/{file_name}
```

The only adapter today is `s3` (`app/adapters/s3_storage.py`), which works with
any S3-compatible service. Local Compose runs MinIO
(`bitnamilegacy/minio:2024.12.18-debian-12-r1`): API on port 9000, browser
console on port 9001 (`meteorcloud` / `meteorcloud-dev-secret`). Override the
image with `MINIO_IMAGE` if needed.

| Setting | Default | Notes |
|---------|---------|-------|
| `OBJECT_STORAGE_PROVIDER` | `s3` | |
| `OBJECT_STORAGE_ENDPOINT_URL` | `http://localhost:9000` | Empty uses AWS S3 |
| `OBJECT_STORAGE_REGION` | `us-east-1` | |
| `OBJECT_STORAGE_BUCKET` | `meteorcloud-artifacts` | |
| `OBJECT_STORAGE_ACCESS_KEY_ID` / `OBJECT_STORAGE_SECRET_ACCESS_KEY` | empty | Empty uses the default AWS credential chain. Keep real values in your secret manager. |
| `OBJECT_STORAGE_AUTO_CREATE_BUCKET` | `true` | Disable when the bucket is provisioned by infrastructure |
| `ARTIFACT_MAX_UPLOAD_BYTES` | 8 GiB | Larger uploads return `413 artifact_too_large` |
| `ARTIFACT_DOWNLOAD_LINK_TTL_SECONDS` | `300` | Lifetime of browser download links |

## Uniqueness

The combination of device type, type, name, and version is unique per
organization (case-insensitive). Uploading it again returns
`409 artifact_exists`; publish a new version instead.

## API

Reading requires organization membership. Uploading and deleting require the
**owner** or **admin** role.

| Method | Path | Notes |
|--------|------|-------|
| `GET` | `/api/v1/organizations/{org}/artifacts` | Filters: `type`, `device_type_id`, `version` (substring), `search`, `page`, `page_size` |
| `POST` | `/api/v1/organizations/{org}/artifacts` | `multipart/form-data`: `file`, `name`, `version`, `type` (default `os_image`), optional `device_type_id`, `description`, `metadata` (JSON string) |
| `GET` | `/api/v1/organizations/{org}/artifacts/{id}` | |
| `DELETE` | `/api/v1/organizations/{org}/artifacts/{id}` | Removes the row, then the stored object |
| `GET` | `/api/v1/organizations/{org}/artifacts/{id}/download` | Bearer auth; streams the file |
| `POST` | `/api/v1/organizations/{org}/artifacts/{id}/download-link` | Returns `url` and a short-lived `ticket` for browser downloads |
| `POST` | `/api/v1/organizations/{org}/artifacts/{id}/download` | Form field `ticket`, no auth header. The ticket is bound to user, organization, and artifact, and membership is re-checked. It travels in the body so it never appears in URLs or request logs |

Types: `os_image`, `firmware`, `docker_compose`, `systemd`, `configuration`, `other`.

Downloads include `Content-Length`, `Content-Disposition`, and
`X-Checksum-SHA256` headers.

## Limitations

- Uploads pass through the control plane, which spools them to temporary disk
  before streaming them to storage. Very large images may later move to
  presigned direct-to-storage uploads.
- Deleting an organization removes artifact rows, but not their stored objects.
- Deployments and the container registry are not implemented yet. They will
  reference artifacts by ID.

## UI

- **Artifacts** in the organization menu lists everything, with filters for type,
  device type, and version.
- **Devices → Device Types → (type) → Artifacts** shows a device type's OS images
  and other artifacts, with upload, download, and delete.
