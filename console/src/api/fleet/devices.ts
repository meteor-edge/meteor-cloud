import { apiRequest } from "@/api/http";
import type { Device, DeviceCredential, DeviceListParams, Page } from "@/api/fleet/types";

export type DeviceUpdatePayload = {
  name?: string;
  device_type_id?: string | null;
  device_group_id?: string | null;
  labels?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
  clear_device_type?: boolean;
  clear_device_group?: boolean;
};

function buildQuery(params: DeviceListParams): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      search.set(key, String(value));
    }
  }
  const query = search.toString();
  return query ? `?${query}` : "";
}

export function listDevices(
  token: string,
  organizationId: string,
  params: DeviceListParams = {},
): Promise<Page<Device>> {
  return apiRequest<Page<Device>>(
    `/api/v1/organizations/${organizationId}/devices${buildQuery(params)}`,
    { token },
  );
}

export function getDevice(
  token: string,
  organizationId: string,
  deviceId: string,
): Promise<Device> {
  return apiRequest<Device>(
    `/api/v1/organizations/${organizationId}/devices/${deviceId}`,
    { token },
  );
}

export function updateDevice(
  token: string,
  organizationId: string,
  deviceId: string,
  payload: DeviceUpdatePayload,
): Promise<Device> {
  return apiRequest<Device>(
    `/api/v1/organizations/${organizationId}/devices/${deviceId}`,
    { method: "PATCH", token, body: payload },
  );
}

export function setDeviceEnabled(
  token: string,
  organizationId: string,
  deviceId: string,
  enabled: boolean,
): Promise<Device> {
  const action = enabled ? "enable" : "disable";
  return apiRequest<Device>(
    `/api/v1/organizations/${organizationId}/devices/${deviceId}/${action}`,
    { method: "POST", token },
  );
}

export function rotateDeviceCredential(
  token: string,
  organizationId: string,
  deviceId: string,
): Promise<DeviceCredential> {
  return apiRequest<DeviceCredential>(
    `/api/v1/organizations/${organizationId}/devices/${deviceId}/rotate-credential`,
    { method: "POST", token },
  );
}

export function revokeDeviceCredential(
  token: string,
  organizationId: string,
  deviceId: string,
): Promise<Device> {
  return apiRequest<Device>(
    `/api/v1/organizations/${organizationId}/devices/${deviceId}/revoke-credential`,
    { method: "POST", token },
  );
}

export function pingDevice(
  token: string,
  organizationId: string,
  deviceId: string,
): Promise<DevicePingResult> {
  return apiRequest<DevicePingResult>(
    `/api/v1/organizations/${organizationId}/devices/${deviceId}/commands/ping`,
    { method: "POST", token },
  );
}

export type DevicePingResult = {
  command_id: string;
  status: string;
  round_trip_ms: number | null;
  result: Record<string, unknown> | null;
  message: string | null;
};

export function deleteDevice(
  token: string,
  organizationId: string,
  deviceId: string,
): Promise<void> {
  return apiRequest<void>(
    `/api/v1/organizations/${organizationId}/devices/${deviceId}`,
    { method: "DELETE", token },
  );
}
