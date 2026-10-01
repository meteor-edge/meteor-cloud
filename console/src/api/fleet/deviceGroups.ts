import { apiRequest } from "@/api/http";
import type { DeviceGroup } from "@/api/fleet/types";

export type DeviceGroupPayload = {
  name: string;
  description?: string | null;
  labels?: Record<string, unknown>;
};

export function listDeviceGroups(token: string, organizationId: string): Promise<DeviceGroup[]> {
  return apiRequest<DeviceGroup[]>(`/api/v1/organizations/${organizationId}/device-groups`, {
    token,
  });
}

export function createDeviceGroup(
  token: string,
  organizationId: string,
  payload: DeviceGroupPayload,
): Promise<DeviceGroup> {
  return apiRequest<DeviceGroup>(`/api/v1/organizations/${organizationId}/device-groups`, {
    token,
    body: payload,
  });
}

export function updateDeviceGroup(
  token: string,
  organizationId: string,
  groupId: string,
  payload: Partial<DeviceGroupPayload>,
): Promise<DeviceGroup> {
  return apiRequest<DeviceGroup>(
    `/api/v1/organizations/${organizationId}/device-groups/${groupId}`,
    { method: "PATCH", token, body: payload },
  );
}

export function deleteDeviceGroup(
  token: string,
  organizationId: string,
  groupId: string,
): Promise<void> {
  return apiRequest<void>(
    `/api/v1/organizations/${organizationId}/device-groups/${groupId}`,
    { method: "DELETE", token },
  );
}
