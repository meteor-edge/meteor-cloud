import { apiRequest } from "@/api/http";

export type TelemetryRange = "1h" | "6h" | "24h" | "7d" | "30d";

export type TelemetryPoint = {
  t: string;
  avg: number;
  min: number;
  max: number;
  count: number;
};

export type TelemetrySeries = {
  name: string;
  points: TelemetryPoint[];
};

export type DeviceTelemetry = {
  device_id: string;
  range: TelemetryRange;
  start: string;
  end: string;
  bucket_seconds: number;
  series: TelemetrySeries[];
};

/** CPU, memory, disk, temperature and load history (stored in ClickHouse by the data plane). */
export function getDeviceTelemetry(
  token: string,
  organizationId: string,
  deviceId: string,
  range: TelemetryRange,
): Promise<DeviceTelemetry> {
  return apiRequest<DeviceTelemetry>(
    `/api/v1/organizations/${organizationId}/devices/${deviceId}/telemetry?range=${range}`,
    { token },
  );
}
