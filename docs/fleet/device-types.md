# Device types and groups

Device types and device groups are organization-scoped catalogs used to classify
and organize devices. They are optional: a device can register without either.

## Device types

A **device type** is a hardware model (e.g. "Raspberry Pi 4", "Jetson Orin").
Each type has:

- `name` — unique within the organization (case-insensitive)
- `slug` — unique within the organization; derived from the name when omitted
- `description`, `manufacturer`, `model`, `architecture` — optional text
- `capabilities` and `metadata` — optional JSON objects

Responses also include `device_count` and `artifact_count`. A device type owns
its [artifacts](artifacts.md) (OS images, firmware, …).

### API

All routes require an authenticated user who is a member of the organization.
Creating, updating, and deleting require the **owner** or **admin** role;
members and viewers have read-only access.

| Method | Path |
|--------|------|
| `GET` | `/api/v1/organizations/{org}/device-types` |
| `POST` | `/api/v1/organizations/{org}/device-types` |
| `GET` | `/api/v1/organizations/{org}/device-types/{id}` |
| `PATCH` | `/api/v1/organizations/{org}/device-types/{id}` |
| `DELETE` | `/api/v1/organizations/{org}/device-types/{id}` |

A device type that is still assigned to one or more devices cannot be deleted
(`device_type_in_use`); reassign or remove those devices first. A device type
that still has artifacts cannot be deleted either (`device_type_has_artifacts`).
An explicit slug that is already taken returns `device_type_slug_exists`.

## Device groups

A **device group** is a logical grouping (e.g. "Berlin", "Lab"), not hardware.
A device belongs to at most one group; groups are flat (no nesting). A group has
`name`, `slug`, `description`, `labels`, `metadata`, and a computed
`device_count`. The routes mirror device types under `/device-groups`. A group
that is still in use cannot be deleted (`device_group_in_use`).

## Assigning devices

Devices are assigned a type/group in three ways:

1. Bound to a registration token — every device that registers with the token
   inherits the token's type and group.
2. Manually from the device detail page or the update API.
3. Cleared via the update API using `clear_device_type` / `clear_device_group`.

## UI

Manage both from **Devices → Device Types** and **Devices → Device Groups** in
the organization menu. Each has a detail page: device types show Overview,
Devices, and Artifacts tabs; device groups show Overview and Devices. The
create/edit/delete controls are only shown to owners and admins.
