"""Workload snapshots: what one deployed workload is made of, as the device last saw it.

The device sends fixed fields only (never environment variables, commands or foreign
labels); unknown fields are dropped, and every list and text is bounded.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field

from app.releases.kinds import ReleaseKind

# Longest snapshot body the API accepts.
MAX_SNAPSHOT_BYTES = 80 * 1024

Text = Annotated[str, Field(max_length=512)]
Short = Annotated[str, Field(max_length=128)]


class SnapshotPort(BaseModel):
    host_ip: Short = ""
    host_port: Short = ""
    container: Short


class SnapshotMount(BaseModel):
    type: Short
    name: Text = ""
    source: Text = ""
    destination: Text
    rw: bool = False


class SnapshotContainer(BaseModel):
    id: Short = ""
    name: Text
    # The name in app.<metric>.* device metrics.
    metric: Short = ""
    service: Text = ""
    release: Text = ""
    state: Short
    running: bool = False
    health: Short = ""
    restarts: int = 0
    exit_code: int = 0
    oom_killed: bool = False
    started_at: Short = ""
    finished_at: Short = ""
    image: Text = ""
    image_id: Short = ""
    ports: list[SnapshotPort] = Field(default_factory=list, max_length=64)
    mounts: list[SnapshotMount] = Field(default_factory=list, max_length=64)
    networks: list[Text] = Field(default_factory=list, max_length=32)


class SnapshotImage(BaseModel):
    id: Short
    tags: list[Text] = Field(default_factory=list, max_length=32)
    digests: list[Text] = Field(default_factory=list, max_length=32)
    size: int = 0
    created: Short = ""


class SnapshotVolume(BaseModel):
    name: Text
    driver: Short = ""


class SnapshotNetwork(BaseModel):
    name: Text
    driver: Short = ""
    scope: Short = ""


class SnapshotUnit(BaseModel):
    name: Text
    load_state: Short = ""
    active_state: Short = ""
    sub_state: Short = ""
    enabled: Short = ""
    result: Short = ""
    restarts: int = 0
    exit_status: int = 0
    memory_bytes: int | None = None
    cpu_seconds: float | None = None
    active_since: Short = ""
    stopped_at: Short = ""


class WorkloadSnapshot(BaseModel):
    workload_key: str = Field(min_length=1, max_length=255)
    kind: ReleaseKind
    taken_at: datetime
    # False: restart, stop and start would be refused on the device (``note`` says why).
    managed: bool = False
    note: Text = ""
    truncated: bool = False
    stopped_services: list[Text] = Field(default_factory=list, max_length=64)
    containers: list[SnapshotContainer] = Field(default_factory=list, max_length=20)
    images: list[SnapshotImage] = Field(default_factory=list, max_length=64)
    volumes: list[SnapshotVolume] = Field(default_factory=list, max_length=64)
    networks: list[SnapshotNetwork] = Field(default_factory=list, max_length=64)
    unit: SnapshotUnit | None = None


class SoftwareSnapshotRequest(BaseModel):
    snapshot: WorkloadSnapshot


class SoftwareSnapshotResponse(BaseModel):
    # False: the device has no such workload (any more).
    accepted: bool
