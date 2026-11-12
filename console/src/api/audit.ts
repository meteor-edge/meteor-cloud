import { apiRequest } from "@/api/http";

export type AuditEvent = {
  id: string;
  organization_id: string | null;
  actor_user_id: string | null;
  actor_type: string | null;
  actor_id: string | null;
  actor_email: string | null;
  action: string;
  resource_type: string;
  resource_id: string | null;
  request_id: string | null;
  ip: string | null;
  user_agent: string | null;
  outcome: string;
  metadata: Record<string, unknown>;
  created_at: string;
};

export type AuditEventFilters = {
  action?: string;
  resource_type?: string;
  resource_id?: string;
  actor_type?: string;
  outcome?: string;
  before?: string;
  before_id?: string;
  limit?: number;
};

export function listAuditEvents(
  token: string,
  organizationId: string,
  filters: AuditEventFilters = {},
): Promise<AuditEvent[]> {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== "") search.set(key, String(value));
  }
  const query = search.toString();
  return apiRequest<AuditEvent[]>(
    `/api/v1/organizations/${organizationId}/audit-events${query ? `?${query}` : ""}`,
    { token },
  );
}
