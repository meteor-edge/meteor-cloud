import { apiRequest } from "@/api/http";
import type { AppEvent, DeploymentHistoryItem } from "@/api/fleet/history";
import type { TelemetryRange, TelemetrySeries } from "@/api/fleet/telemetry";
import type { DeviceActionState } from "@/api/fleet/actions";
import type { DeviceSoftware, ReleaseKind } from "@/api/fleet/types";

export type SnapshotContainer = {
  id: string;
  name: string;
  metric: string;
  service: string;
  release: string;
  state: string;
  running: boolean;
  health: string;
  restarts: number;
  exit_code: number;
  oom_killed: boolean;
  started_at: string;
  finished_at: string;
  image: string;
  image_id: string;
  ports: { host_ip: string; host_port: string; container: string }[];
  mounts: { type: string; name: string; source: string; destination: string; rw: boolean }[];
  networks: string[];
};

/** What a workload is made of, as the device last saw it. */
export type WorkloadSnapshot = {
  workload_key: string;
  kind: ReleaseKind;
  taken_at: string;
  managed: boolean;
  note: string;
  truncated: boolean;
  stopped_services: string[];
  containers: SnapshotContainer[];
  images: { id: string; tags: string[]; digests: string[]; size: number; created: string }[];
  volumes: { name: string; driver: string }[];
  networks: { name: string; driver: string; scope: string }[];
  unit: {
    name: string;
    load_state: string;
    active_state: string;
    sub_state: string;
    enabled: string;
    result: string;
    restarts: number;
    exit_status: number;
    memory_bytes: number | null;
    cpu_seconds: number | null;
    active_since: string;
    stopped_at: string;
  } | null;
};

export type DeviceSoftwareDetail = DeviceSoftware & {
  snapshot: WorkloadSnapshot | null;
  snapshot_at: string | null;
  supported_actions: string[];
  can_manage: boolean;
};

export type SoftwareActionKind =
  "workload_restart" | "workload_stop" | "workload_start" | "workload_snapshot";

export type SoftwareAction = {
  id: string;
  kind: SoftwareActionKind;
  state: DeviceActionState;
  service: string | null;
  last_error: string | null;
  requested_by: string | null;
  created_at: string;
  sent_at: string | null;
  reported_at: string | null;
};

export type WorkloadHealth = "healthy" | "unhealthy" | "stopped" | "unknown";
export type SoftwareRange = Extract<TelemetryRange, "24h" | "7d" | "30d">;

export type SoftwareHistory = {
  software_id: string;
  range: SoftwareRange;
  start: string;
  end: string;
  healthy_percent: number | null;
  segments: { start: string; end: string; status: WorkloadHealth }[];
  changes: { t: string; status: WorkloadHealth; reason: string | null }[];
  events: AppEvent[];
  deployments: DeploymentHistoryItem[];
  bucket_seconds: number;
  /** `<container>.cpu` in percent and `<container>.mem` in bytes. */
  series: TelemetrySeries[];
};

const base = (organizationId: string, deviceId: string) =>
  `/api/v1/organizations/${organizationId}/devices/${deviceId}/software`;

export function listDeviceSoftware(
  token: string,
  organizationId: string,
  deviceId: string,
): Promise<DeviceSoftware[]> {
  return apiRequest<DeviceSoftware[]>(base(organizationId, deviceId), { token });
}

export function getDeviceSoftware(
  token: string,
  organizationId: string,
  deviceId: string,
  softwareId: string,
): Promise<DeviceSoftwareDetail> {
  return apiRequest<DeviceSoftwareDetail>(`${base(organizationId, deviceId)}/${softwareId}`, {
    token,
  });
}

export function getSoftwareHistory(
  token: string,
  organizationId: string,
  deviceId: string,
  softwareId: string,
  range: SoftwareRange,
): Promise<SoftwareHistory> {
  return apiRequest<SoftwareHistory>(
    `${base(organizationId, deviceId)}/${softwareId}/history?range=${range}`,
    { token },
  );
}

export function listSoftwareActions(
  token: string,
  organizationId: string,
  deviceId: string,
  softwareId: string,
): Promise<SoftwareAction[]> {
  return apiRequest<SoftwareAction[]>(`${base(organizationId, deviceId)}/${softwareId}/actions`, {
    token,
  });
}

export function orderSoftwareAction(
  token: string,
  organizationId: string,
  deviceId: string,
  softwareId: string,
  payload: { kind: SoftwareActionKind; service?: string },
): Promise<SoftwareAction> {
  return apiRequest<SoftwareAction>(`${base(organizationId, deviceId)}/${softwareId}/actions`, {
    token,
    body: payload,
  });
}
