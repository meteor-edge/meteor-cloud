import { apiRequest } from "@/api/http";
import type { DeviceSoftware } from "@/api/fleet/types";

export function listDeviceSoftware(
  token: string,
  organizationId: string,
  deviceId: string,
): Promise<DeviceSoftware[]> {
  return apiRequest<DeviceSoftware[]>(
    `/api/v1/organizations/${organizationId}/devices/${deviceId}/software`,
    { token },
  );
}
