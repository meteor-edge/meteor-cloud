import { apiRequest } from "@/api/http";
import type {
  DeviceEnrollmentRequest,
  EnrollmentBulkResult,
  EnrollmentStatus,
} from "@/api/fleet/types";

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

/** Approve up to 500 pending requests with one type and group; names are generated. */
export function bulkApproveEnrollmentRequests(
  token: string,
  organizationId: string,
  payload: { request_ids: string[]; device_type_id?: string; device_group_id?: string },
): Promise<EnrollmentBulkResult> {
  return apiRequest<EnrollmentBulkResult>(
    `/api/v1/organizations/${organizationId}/enrollment-requests/approve`,
    { method: "POST", token, body: payload },
  );
}

export function bulkRejectEnrollmentRequests(
  token: string,
  organizationId: string,
  payload: { request_ids: string[]; reason?: string },
): Promise<EnrollmentBulkResult> {
  return apiRequest<EnrollmentBulkResult>(
    `/api/v1/organizations/${organizationId}/enrollment-requests/reject`,
    { method: "POST", token, body: payload },
  );
}
