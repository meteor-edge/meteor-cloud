import { apiRequest } from "@/api/http";
import type { TelemetryRange } from "@/api/fleet/telemetry";

// Fleet and device history kept by the data plane (ClickHouse): devices online,
// online/offline changes, deployment status changes, apps, saved system warnings.

export type FleetOnlineHistory = {
  range: TelemetryRange;
  start: string;
  end: string;
  bucket_seconds: number;
  total_devices: number;
  online_now: number;
  buckets: { t: string; devices: number }[];
};

export type Connectivity = "online" | "offline" | "unknown";

export type DeviceAvailability = {
  device_id: string;
  range: TelemetryRange;
  start: string;
  end: string;
  uptime_percent: number | null;
  offline_count: number;
  segments: { start: string; end: string; status: Connectivity }[];
  changes: { t: string; status: "online" | "offline" }[];
};

export type DeploymentHistoryItem = {
  t: string;
  status: string;
  previous_status: string | null;
  deployment_id: string | null;
  deployment_name: string | null;
  release_id: string | null;
  release_name: string | null;
  release_version: string | null;
  seconds: number | null;
  error: string | null;
};

export type AppReading = {
  name: string;
  cpu_percent: number | null;
  memory_bytes: number | null;
  restarts: number | null;
  healthy: boolean | null;
  health_ms: number | null;
  running: boolean | null;
};

export type AppEvent = {
  t: string;
  type: string;
  app: string | null;
  exit_code: number | null;
  oom_killed: boolean | null;
  detail: string | null;
};

export type DeviceApps = {
  device_id: string;
  reported_at: string | null;
  apps: AppReading[];
  events: AppEvent[];
};

export type LogEntry = {
  t: string;
  message: string;
  priority: number | null;
  unit: string | null;
  source: string | null;
};

export type DeviceLogHistory = {
  device_id: string;
  range: TelemetryRange;
  query: string | null;
  entries: LogEntry[];
  next_cursor: string | null;
};

const base = (organizationId: string) => `/api/v1/organizations/${organizationId}`;

export function getFleetOnlineHistory(
  token: string,
  organizationId: string,
  range: TelemetryRange,
) {
  return apiRequest<FleetOnlineHistory>(
    `${base(organizationId)}/fleet/online-history?range=${range}`,
    {
      token,
    },
  );
}

export function getDeviceAvailability(
  token: string,
  organizationId: string,
  deviceId: string,
  range: TelemetryRange,
) {
  return apiRequest<DeviceAvailability>(
    `${base(organizationId)}/devices/${deviceId}/availability?range=${range}`,
    { token },
  );
}

export function getDeviceDeploymentHistory(
  token: string,
  organizationId: string,
  deviceId: string,
) {
  return apiRequest<{ device_id: string; items: DeploymentHistoryItem[] }>(
    `${base(organizationId)}/devices/${deviceId}/deployment-history?limit=50`,
    { token },
  );
}

export function getDeviceApps(token: string, organizationId: string, deviceId: string) {
  return apiRequest<DeviceApps>(`${base(organizationId)}/devices/${deviceId}/apps`, { token });
}

export function getDeviceLogHistory(
  token: string,
  organizationId: string,
  deviceId: string,
  params: { range: TelemetryRange; q?: string; cursor?: string },
) {
  const search = new URLSearchParams({ range: params.range, limit: "100" });
  if (params.q) search.set("q", params.q);
  if (params.cursor) search.set("cursor", params.cursor);
  return apiRequest<DeviceLogHistory>(
    `${base(organizationId)}/devices/${deviceId}/log-history?${search.toString()}`,
    { token },
  );
}
