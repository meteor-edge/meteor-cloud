import { apiRequest } from "@/api/http";
import type { DeviceType } from "@/api/fleet/types";

export type DeviceTypePayload = {
  name: string;
  slug?: string;
  description?: string | null;
  manufacturer?: string | null;
  model?: string | null;
  architecture?: string | null;
  capabilities?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
};

export function listDeviceTypes(token: string, organizationId: string): Promise<DeviceType[]> {
  return apiRequest<DeviceType[]>(`/api/v1/organizations/${organizationId}/device-types`, {
    token,
  });
}

/** Fetch type metadata and device/artifact counts; reject on API, network, or parsing errors. */
export function getDeviceType(
  token: string,
  organizationId: string,
  typeId: string,
): Promise<DeviceType> {
  return apiRequest<DeviceType>(`/api/v1/organizations/${organizationId}/device-types/${typeId}`, {
    token,
  });
}

export function createDeviceType(
  token: string,
  organizationId: string,
  payload: DeviceTypePayload,
): Promise<DeviceType> {
  return apiRequest<DeviceType>(`/api/v1/organizations/${organizationId}/device-types`, {
    token,
    body: payload,
  });
}

export function updateDeviceType(
  token: string,
  organizationId: string,
  typeId: string,
  payload: Partial<DeviceTypePayload>,
): Promise<DeviceType> {
  return apiRequest<DeviceType>(
    `/api/v1/organizations/${organizationId}/device-types/${typeId}`,
    { method: "PATCH", token, body: payload },
  );
}

export function deleteDeviceType(
  token: string,
  organizationId: string,
  typeId: string,
): Promise<void> {
  return apiRequest<void>(
    `/api/v1/organizations/${organizationId}/device-types/${typeId}`,
    { method: "DELETE", token },
  );
}
