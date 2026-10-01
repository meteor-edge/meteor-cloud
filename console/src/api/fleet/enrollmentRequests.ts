import { apiRequest } from "@/api/http";
import type { DeviceEnrollmentRequest, EnrollmentStatus } from "@/api/fleet/types";

export function listEnrollmentRequests(
  token: string,
  organizationId: string,
  status?: EnrollmentStatus,
): Promise<DeviceEnrollmentRequest[]> {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  return apiRequest<DeviceEnrollmentRequest[]>(
    `/api/v1/organizations/${organizationId}/enrollment-requests${query}`,
    { token },
  );
}

export function approveEnrollmentRequest(
  token: string,
  organizationId: string,
  requestId: string,
  payload: {
    name?: string;
    device_type_id?: string;
    device_group_id?: string;
  },
): Promise<DeviceEnrollmentRequest> {
  return apiRequest<DeviceEnrollmentRequest>(
    `/api/v1/organizations/${organizationId}/enrollment-requests/${requestId}/approve`,
    { method: "POST", token, body: payload },
  );
}

export function rejectEnrollmentRequest(
  token: string,
  organizationId: string,
  requestId: string,
  payload: { reason?: string } = {},
): Promise<DeviceEnrollmentRequest> {
  return apiRequest<DeviceEnrollmentRequest>(
    `/api/v1/organizations/${organizationId}/enrollment-requests/${requestId}/reject`,
    { method: "POST", token, body: payload },
  );
}
