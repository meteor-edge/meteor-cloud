import { apiRequest } from "@/api/http";

export type DeviceActionKind =
  | "reboot"
  | "shutdown"
  | "set_password"
  | "add_user"
  | "update_agent"
  | "restart_module"
  | "run_diagnostics"
  | "workload_restart"
  | "workload_stop"
  | "workload_start"
  | "workload_snapshot";
export type DeviceActionState = "pending" | "sent" | "done" | "failed";

export type DeviceAction = {
  id: string;
  kind: DeviceActionKind;
  state: DeviceActionState;
  username: string | null;
  sudo: boolean;
  from_version?: string | null;
  /** restart_module: the agent module. */
  module?: string | null;
  last_error: string | null;
  /** run_diagnostics: the device's doctor report. */
  output?: string | null;
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

export function updateDeviceAgent(
  token: string,
  organizationId: string,
  deviceId: string,
): Promise<DeviceActions> {
  return apiRequest<DeviceActions>(path(organizationId, deviceId, "/update-agent"), {
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

export function restartDeviceModule(
  token: string,
  organizationId: string,
  deviceId: string,
  module: string,
): Promise<DeviceActions> {
  return apiRequest<DeviceActions>(path(organizationId, deviceId, "/restart-module"), {
    token,
    body: { module },
  });
}

export function runDeviceDiagnostics(
  token: string,
  organizationId: string,
  deviceId: string,
): Promise<DeviceActions> {
  return apiRequest<DeviceActions>(path(organizationId, deviceId, "/run-diagnostics"), {
    token,
    method: "POST",
  });
}
