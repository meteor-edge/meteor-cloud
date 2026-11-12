import { apiRequest } from "@/api/http";

export type DeviceActionKind = "reboot" | "shutdown" | "set_password" | "add_user";
export type DeviceActionState = "pending" | "sent" | "done" | "failed";

export type DeviceAction = {
  id: string;
  kind: DeviceActionKind;
  state: DeviceActionState;
  username: string | null;
  sudo: boolean;
  last_error: string | null;
  created_at: string;
  sent_at: string | null;
  reported_at: string | null;
};

export type DeviceActions = { actions: DeviceAction[] };

function path(organizationId: string, deviceId: string, suffix = ""): string {
  return `/api/v1/organizations/${organizationId}/devices/${deviceId}/actions${suffix}`;
}

export function listDeviceActions(
  token: string,
  organizationId: string,
  deviceId: string,
): Promise<DeviceActions> {
  return apiRequest<DeviceActions>(path(organizationId, deviceId), { token });
}

export function rebootDevice(
  token: string,
  organizationId: string,
  deviceId: string,
): Promise<DeviceActions> {
  return apiRequest<DeviceActions>(path(organizationId, deviceId, "/reboot"), {
    token,
    method: "POST",
  });
}

export function shutdownDevice(
  token: string,
  organizationId: string,
  deviceId: string,
): Promise<DeviceActions> {
  return apiRequest<DeviceActions>(path(organizationId, deviceId, "/shutdown"), {
    token,
    method: "POST",
  });
}

export function setDevicePassword(
  token: string,
  organizationId: string,
  deviceId: string,
  payload: { username: string; password: string },
): Promise<DeviceActions> {
  return apiRequest<DeviceActions>(path(organizationId, deviceId, "/set-password"), {
    token,
    body: payload,
  });
}

export function addDeviceUser(
  token: string,
  organizationId: string,
  deviceId: string,
  payload: { username: string; password: string; sudo?: boolean; authorized_key?: string },
): Promise<DeviceActions> {
  return apiRequest<DeviceActions>(path(organizationId, deviceId, "/add-user"), {
    token,
    body: payload,
  });
}
