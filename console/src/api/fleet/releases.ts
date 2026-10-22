import { apiRequest } from "@/api/http";
import type { Page, Release, ReleaseCreatePayload, ReleaseListParams } from "@/api/fleet/types";

function buildQuery(params: ReleaseListParams): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      search.set(key, String(value));
    }
  }
  const query = search.toString();
  return query ? `?${query}` : "";
}

export function listReleases(
  token: string,
  organizationId: string,
  params: ReleaseListParams = {},
): Promise<Page<Release>> {
  return apiRequest<Page<Release>>(
    `/api/v1/organizations/${organizationId}/releases${buildQuery(params)}`,
    { token },
  );
}

export function createRelease(
  token: string,
  organizationId: string,
  payload: ReleaseCreatePayload,
): Promise<Release> {
  return apiRequest<Release>(`/api/v1/organizations/${organizationId}/releases`, {
    method: "POST",
    token,
    body: payload,
  });
}

export function deleteRelease(
  token: string,
  organizationId: string,
  releaseId: string,
): Promise<void> {
  return apiRequest<void>(`/api/v1/organizations/${organizationId}/releases/${releaseId}`, {
    method: "DELETE",
    token,
  });
}
