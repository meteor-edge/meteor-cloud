import { apiRequest } from "@/api/http";

export type DeviceWifiSettings = {
  device_id: string;
  ssid: string;
  interface: string;
  hidden: boolean;
  psk_set: boolean;
  revision: number;
  applied_ssid: string | null;
  applied_revision: number | null;
  sync_status: string | null;
  sync_error: string | null;
};

export type DeviceWifiUpdate = {
  ssid: string;
  psk: string;
  interface?: string;
  hidden?: boolean;
};

export async function getDeviceWifi(
  token: string,
  organizationId: string,
  deviceId: string,
): Promise<DeviceWifiSettings | null> {
  return apiRequest(`/api/v1/organizations/${organizationId}/devices/${deviceId}/wifi`, {
    token,
  });
}

export async function putDeviceWifi(
  token: string,
  organizationId: string,
  deviceId: string,
  body: DeviceWifiUpdate,
): Promise<DeviceWifiSettings> {
  return apiRequest(`/api/v1/organizations/${organizationId}/devices/${deviceId}/wifi`, {
    method: "PUT",
    token,
    body,
  });
}

export async function deleteDeviceWifi(
  token: string,
  organizationId: string,
  deviceId: string,
): Promise<void> {
  await apiRequest(`/api/v1/organizations/${organizationId}/devices/${deviceId}/wifi`, {
    method: "DELETE",
    token,
  });
}
