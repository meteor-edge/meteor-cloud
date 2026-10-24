import { apiRequest } from "@/api/http";
import type { ApplicationEndpoint } from "@/api/fleet/types";

export function listEndpoints(
  token: string,
  organizationId: string,
  deviceId?: string,
): Promise<ApplicationEndpoint[]> {
  const query = deviceId ? `?device_id=${encodeURIComponent(deviceId)}` : "";
  return apiRequest<ApplicationEndpoint[]>(
    `/api/v1/organizations/${organizationId}/endpoints${query}`,
    { token },
  );
}

export function createEndpoint(
  token: string,
  organizationId: string,
  payload: {
    device_id: string;
    hostname: string;
    port: number;
    protocol?: string;
    enabled?: boolean;
    workload_key?: string | null;
  },
): Promise<ApplicationEndpoint> {
  return apiRequest<ApplicationEndpoint>(`/api/v1/organizations/${organizationId}/endpoints`, {
    token,
    body: payload,
  });
}

export function updateEndpoint(
  token: string,
  organizationId: string,
  endpointId: string,
  payload: { enabled?: boolean; hostname?: string; port?: number },
): Promise<ApplicationEndpoint> {
  return apiRequest<ApplicationEndpoint>(
    `/api/v1/organizations/${organizationId}/endpoints/${endpointId}`,
    { method: "PATCH", token, body: payload },
  );
}

export function deleteEndpoint(
  token: string,
  organizationId: string,
  endpointId: string,
): Promise<void> {
  return apiRequest<void>(`/api/v1/organizations/${organizationId}/endpoints/${endpointId}`, {
    method: "DELETE",
    token,
  });
}

export function getDeviceConnectivity(
  token: string,
  organizationId: string,
  deviceId: string,
): Promise<import("@/api/fleet/types").DeviceConnectivity> {
  return apiRequest(`/api/v1/organizations/${organizationId}/devices/${deviceId}/connectivity`, {
    token,
  });
}

export function revokeDeviceConnectivity(
  token: string,
  organizationId: string,
  deviceId: string,
): Promise<import("@/api/fleet/types").DeviceConnectivity> {
  return apiRequest(
    `/api/v1/organizations/${organizationId}/devices/${deviceId}/connectivity/revoke`,
    { method: "POST", token, body: {} },
  );
}

/** Forget the device's WireGuard key, address and SSH host key; it registers again on its own. */
export function reenrollDeviceConnectivity(
  token: string,
  organizationId: string,
  deviceId: string,
): Promise<import("@/api/fleet/types").DeviceConnectivity> {
  return apiRequest(
    `/api/v1/organizations/${organizationId}/devices/${deviceId}/connectivity/reenroll`,
    { method: "POST", token, body: {} },
  );
}

export function createShellSession(
  token: string,
  organizationId: string,
  deviceId: string,
): Promise<import("@/api/fleet/types").ShellSession> {
  return apiRequest(`/api/v1/organizations/${organizationId}/devices/${deviceId}/shell-sessions`, {
    method: "POST",
    token,
    body: {},
  });
}

export type HostnameCheck = {
  hostname: string;
  available: boolean;
  reason: "available" | "taken" | "reserved" | "invalid" | "not_yours";
  message: string;
};

export type HostnameSuggestion = { apps_domain: string; hostname: string; available: boolean };

export function suggestHostname(
  token: string,
  organizationId: string,
  deviceId: string,
  app: string,
): Promise<HostnameSuggestion> {
  const query = new URLSearchParams({ device_id: deviceId, app });
  return apiRequest<HostnameSuggestion>(
    `/api/v1/organizations/${organizationId}/endpoints/hostname-suggestion?${query}`,
    { token },
  );
}

export function checkHostname(
  token: string,
  organizationId: string,
  deviceId: string,
  hostname: string,
): Promise<HostnameCheck> {
  const query = new URLSearchParams({ device_id: deviceId, hostname });
  return apiRequest<HostnameCheck>(
    `/api/v1/organizations/${organizationId}/endpoints/hostname-check?${query}`,
    { token },
  );
}
