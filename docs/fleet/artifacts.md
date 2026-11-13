# Artifacts

An **artifact** is a versioned file that can later be deployed to devices: an
OS image, firmware, a Docker Compose bundle, a systemd unit, a configuration
file, or something else. OS images are the MVP use case.

Artifacts are organization-scoped. An artifact is for one or several
[device types](device-types.md) (`device_type_ids`), for example one OS image for
Raspberry Pi 3, 4 and 5. An empty list means it applies to any device type. The
types can be changed after upload (**Device types** in the artifact's row).
`device_type_id` is still returned (the only type, or null) for older clients.

Name and version are unique among the artifacts of one kind that share a device
type: uploading "Meteor OS 1.0" twice for Raspberry Pi 5 is refused, also when the
second upload lists more types.

## Storage

The binary is stored in object storage behind the `ObjectStorage` port
(`app/ports/storage.py`). PostgreSQL keeps only metadata: name, version, type,
file name, content type, size, SHA-256 checksum, and the storage key.

Storage keys use IDs, not names, so renames never orphan content:

```text
organizations/{organization_id}/artifacts/{artifact_id}/{file_name}
```

Two adapters exist, selected with `OBJECT_STORAGE_PROVIDER`:

- `s3` (`app/adapters/s3_storage.py`) works with AWS S3 and any S3-compatible
  service: MinIO, Ceph, Cloudflare R2, Google Cloud Storage interoperability
  (`https://storage.googleapis.com` with HMAC keys), and customer storage.
- `filesystem` (`app/adapters/filesystem_storage.py`) stores files under
  `OBJECT_STORAGE_PATH` on a local or shared volume. Writes are atomic.

Local development Compose runs MinIO (profile `minio`,
`bitnamilegacy/minio:2024.12.18-debian-12-r1`): API on port 9000, browser
console on port 9001 (`meteorcloud` / `meteorcloud-dev-secret`). Override the
image with `MINIO_IMAGE` if needed. Production installs do not need MinIO:
Ansible and the Helm chart default to `filesystem`. Upstream MinIO is AGPLv3;
prefer filesystem or cloud S3 in commercial production — see [Licensing](../licensing.md).

| Setting | Default | Notes |
|---------|---------|-------|
| `OBJECT_STORAGE_PROVIDER` | `s3` | `s3` or `filesystem` |
| `OBJECT_STORAGE_PATH` | `/data/artifacts` | `filesystem` only |
| `OBJECT_STORAGE_ENDPOINT_URL` | empty | Empty uses AWS S3; `http://minio:9000` for bundled MinIO |
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

Reading requires organization membership. Uploading, changing and deleting need
`artifact.create` / `artifact.update` / `artifact.delete` (owner, admin, developer).
Publishing an OS image (`published`, at upload or with `PATCH`) also needs
`artifact.publish`: **owner** or **admin** only.

| Method | Path | Notes |
|--------|------|-------|
| `GET` | `/api/v1/organizations/{org}/artifacts` | Filters: `type`, `device_type_id`, `version` (substring), `search`, `page`, `page_size` |
| `POST` | `/api/v1/organizations/{org}/artifacts` | `multipart/form-data`: `file`, `name`, `version`, `type` (default `os_image`), optional `device_type_ids` (repeat the field, or separate IDs with commas; `device_type_id` is still accepted), `description`, `metadata` (JSON string), `published` (OS images only) |
| `GET` | `/api/v1/organizations/{org}/artifacts/{id}` | |
| `PATCH` | `/api/v1/organizations/{org}/artifacts/{id}` | JSON `{"published": true}`: show or hide an OS image in [Meteor Imager](#meteor-imager-public-os-images). `{"device_type_ids": [...]}`: change the device types |
| `DELETE` | `/api/v1/organizations/{org}/artifacts/{id}` | Removes the row, then the stored object |
| `GET` | `/api/v1/organizations/{org}/artifacts/{id}/download` | Bearer auth; streams the file |
| `POST` | `/api/v1/organizations/{org}/artifacts/{id}/download-link` | Returns `url` and a short-lived `ticket` for browser downloads |
| `POST` | `/api/v1/organizations/{org}/artifacts/{id}/download` | Form field `ticket`, no auth header. The ticket is bound to user, organization, and artifact, and membership is re-checked. It travels in the body so it never appears in URLs or request logs |

Types: `os_image`, `firmware`, `docker_compose`, `systemd`, `configuration`, `other`.

Downloads include `Content-Length`, `Content-Disposition`, and
`X-Checksum-SHA256` headers.

## Meteor Imager (public OS images)

[Meteor Imager](https://github.com/meteor-edge/meteor-imager) is a desktop and CLI tool
that writes an OS image to an SD card and adds Wi-Fi and enrollment settings to its boot
partition. It lists and downloads images **without signing in**, so only OS images an
owner or admin marked **Show in Meteor Imager** (`published`) are served. The imager asks
for the MeteorCloud domain, enrollment API key and Wi-Fi after the image is chosen and
writes them to the card; it never sends them to these routes.

| Method | Path | Notes |
|--------|------|-------|
| `GET` | `/api/v1/os-images` | No auth. Published OS images of every organization on this server, newest first: `id`, `name`, `version`, `description`, `device_type` (name of the first type), `device_types` (`id`, `name`, `slug` of each), `file_name`, `size_bytes`, `checksum_sha256`, `download_url` |
| `GET` | `/api/v1/os-images/{id}/download` | No auth. Streams the image from storage (MinIO, S3 or filesystem) with `Content-Length` and `X-Checksum-SHA256`. Unpublished or unknown IDs return 404 |

Both routes are rate-limited per source IP: `PUBLIC_OS_IMAGE_RATE_LIMIT_REQUESTS`
(default 60) per `PUBLIC_OS_IMAGE_RATE_LIMIT_WINDOW_SECONDS` (default 60).

Publish only images that are safe to share: anyone who can reach the server can download
them. On a multi-organization server every organization's published images appear in one
list (organization IDs are not shown). Only `os_image` artifacts can be published; the
database enforces it.

### Your own OS image (Docker and meteorcli preinstalled)

1. Write a plain OS (for example Raspberry Pi OS Lite Bookworm), boot it and install Docker,
   packages and `meteorcli`. Do not enroll it.
2. On the device: `sudo bash meteorcli-prepare-image.sh` (agent release asset). It installs
   first boot and removes the device's identity, Wi-Fi, SSH host keys and machine-id, then
   powers off. Details: [first-boot README](../../src/device-plane/agent/packaging/firstboot/README.md#build-an-os-image-golden-image).
3. Back up the card with Meteor Imager (**Back up a card**, or
   `meteor-imager backup --device /dev/sdX --out golden.img.gz`). The root filesystem is
   shrunk so the image fits smaller cards.
4. Upload the file here as an `os_image` with **Show in Meteor Imager**.
5. Write cards with Meteor Imager: the **General** tab sets hostname, user and password, Wi-Fi
   and SSH; the **MeteorCloud** tab sets the domain and an enrollment key. On Linux the imager
   extends the root partition to the card and grows the filesystem when `e2fsprogs` is installed.
   Each device gets new host keys and sends a token request that shows up under pending
   enrollments for approval. A prepared image still grows the filesystem on first boot if the
   computer that wrote the card could not.

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
- The **Meteor Imager** column (OS images) and the upload checkbox **Show in Meteor
  Imager** control which images the public list serves.
- **Devices → Device Types → (type) → Artifacts** shows a device type's OS images
  and other artifacts, with upload, download, and delete.
