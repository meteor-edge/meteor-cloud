import { apiRequest } from "@/api/http";

export type WifiSecurity = "wpa-psk" | "open";

/** A Wi-Fi network ordered for a device from the console. Never carries a password. */
export type DeviceWifiNetwork = {
  id: string;
  ssid: string;
  security: WifiSecurity;
  hidden: boolean;
  connect_now: boolean;
  action: "apply" | "remove";
  state: "pending" | "applied" | "rolled_back" | "failed";
  last_error: string | null;
  reported_at: string | null;
  updated_at: string;
  /** The password is still waiting on the server for the device to collect. */
  password_pending: boolean;
};

export type DeviceWifiStatus = {
  supported: boolean;
  unsupported_reason: string | null;
  connected_ssid: string | null;
  signal_percent: number | null;
  interface: string | null;
  wired_connected: boolean;
  saved_networks: { ssid: string; managed: boolean }[];
  reported_at: string;
};

export type DeviceWifi = {
  /** null: the device has not reported its Wi-Fi (agent older than 0.3.0, or not online since). */
  status: DeviceWifiStatus | null;
  networks: DeviceWifiNetwork[];
};

export type WifiNetworkPayload = {
  ssid: string;
  security: WifiSecurity;
  password?: string;
  hidden: boolean;
  connect_now: boolean;
};

function wifiPath(organizationId: string, deviceId: string): string {
  return `/api/v1/organizations/${organizationId}/devices/${deviceId}/wifi`;
}

export function getDeviceWifi(
  token: string,
  organizationId: string,
  deviceId: string,
): Promise<DeviceWifi> {
  return apiRequest<DeviceWifi>(wifiPath(organizationId, deviceId), { token });
}

/** Add a network, or change the password of one the device already has. */
export function setDeviceWifiNetwork(
  token: string,
  organizationId: string,
  deviceId: string,
  payload: WifiNetworkPayload,
): Promise<DeviceWifi> {
  return apiRequest<DeviceWifi>(`${wifiPath(organizationId, deviceId)}/networks`, {
    token,
    method: "PUT",
    body: payload,
  });
}

export function removeDeviceWifiNetwork(
  token: string,
  organizationId: string,
  deviceId: string,
  ssid: string,
): Promise<DeviceWifi> {
  return apiRequest<DeviceWifi>(`${wifiPath(organizationId, deviceId)}/networks/remove`, {
    token,
    body: { ssid },
  });
}
