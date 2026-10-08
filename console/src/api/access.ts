import { apiRequest } from "@/api/http";

export type PermissionDetail = {
  id: string;
  label: string;
  description: string;
  resource: string;
  action: string;
  granted: boolean;
  source?: string | null;
  role_key?: string | null;
  reason?: string | null;
  scope_mode?: string | null;
};

export type MemberAccess = {
  membership_id: string;
  user_id: string;
  email: string;
  full_name: string;
  organization_id: string;
  organization_name: string;
  role: { key: string; name: string; is_system: boolean };
  status: string;
  scope: {
    mode: string;
    device_group_ids: string[];
    device_group_names: string[];
  };
  permissions: string[];
  summary: {
    granted_count: number;
    denied_count: number;
    by_resource: Record<string, string[]>;
  };
  permissions_detail: PermissionDetail[];
};

export type AuthzCatalog = {
  roles: { key: string; name: string; description: string; permissions: string[] }[];
  permissions: {
    id: string;
    resource: string;
    action: string;
    label: string;
    description: string;
  }[];
};

export function getMemberAccess(
  token: string,
  organizationId: string,
  membershipId: string,
): Promise<MemberAccess> {
  return apiRequest<MemberAccess>(
    `/api/v1/organizations/${organizationId}/members/${membershipId}/access`,
    { token },
  );
}

export function getAuthzCatalog(token: string): Promise<AuthzCatalog> {
  return apiRequest<AuthzCatalog>("/api/v1/authz/catalog", { token });
}

export function getMyAccess(token: string) {
  return apiRequest<{ organizations: Omit<MemberAccess, "membership_id" | "user_id" | "email" | "full_name" | "permissions_detail">[] }>(
    "/api/v1/me/access",
    { token },
  );
}
