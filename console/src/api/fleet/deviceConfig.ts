import { apiRequest } from "@/api/http";

/** First-boot settings for a card: the same fields Meteor Imager offers. */
export type DeviceConfigPayload = {
  enrollment?: { domain: string; api_key: string; device_name?: string };
  hostname?: string;
  timezone?: string;
  wifi?: { ssid: string; psk: string; country: string; hidden: boolean };
  user?: { name: string; password: string };
  ssh?: { enabled: boolean; password_authentication: boolean; authorized_key?: string };
};

export type DeviceConfigFile = { file_name: string; content: string };

/** Render the settings file (`.meteorenv` version 2). The server stores nothing. */
export function createDeviceConfig(
  token: string,
  organizationId: string,
  payload: DeviceConfigPayload,
): Promise<DeviceConfigFile> {
  return apiRequest<DeviceConfigFile>(`/api/v1/organizations/${organizationId}/device-config`, {
    token,
    body: payload,
  });
}
