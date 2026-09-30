import { apiRequest } from "@/api/http";

export type OrganizationRole = "owner" | "admin" | "member" | "viewer";

export type Organization = {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  created_by_user_id: string;
  created_at: string;
  updated_at: string;
  current_user_role: OrganizationRole;
  member_count: number | null;
};

export type Member = {
  id: string;
  organization_id: string;
  user_id: string;
  email: string;
  full_name: string;
  role: OrganizationRole;
  created_at: string;
  updated_at: string;
};

export type OrganizationCreatePayload = {
  name: string;
  slug?: string;
  description?: string;
};

export type OrganizationUpdatePayload = {
  name?: string;
  slug?: string;
  description?: string | null;
};

export function listOrganizations(token: string): Promise<Organization[]> {
  return apiRequest<Organization[]>("/api/v1/organizations", { token });
}

export function createOrganization(
  token: string,
  payload: OrganizationCreatePayload,
): Promise<Organization> {
  return apiRequest<Organization>("/api/v1/organizations", { token, body: payload });
}

export function getOrganization(token: string, organizationId: string): Promise<Organization> {
  return apiRequest<Organization>(`/api/v1/organizations/${organizationId}`, { token });
}

export function updateOrganization(
  token: string,
  organizationId: string,
  payload: OrganizationUpdatePayload,
): Promise<Organization> {
  return apiRequest<Organization>(`/api/v1/organizations/${organizationId}`, {
    method: "PATCH",
    token,
    body: payload,
  });
}

export function deleteOrganization(token: string, organizationId: string): Promise<void> {
  return apiRequest<void>(`/api/v1/organizations/${organizationId}`, {
    method: "DELETE",
    token,
  });
}

export function listMembers(token: string, organizationId: string): Promise<Member[]> {
  return apiRequest<Member[]>(`/api/v1/organizations/${organizationId}/members`, { token });
}

export function addMember(
  token: string,
  organizationId: string,
  payload: { email: string; role: OrganizationRole },
): Promise<Member> {
  return apiRequest<Member>(`/api/v1/organizations/${organizationId}/members`, {
    token,
    body: payload,
  });
}

export function updateMemberRole(
  token: string,
  organizationId: string,
  membershipId: string,
  role: OrganizationRole,
): Promise<Member> {
  return apiRequest<Member>(`/api/v1/organizations/${organizationId}/members/${membershipId}`, {
    method: "PATCH",
    token,
    body: { role },
  });
}

export function removeMember(
  token: string,
  organizationId: string,
  membershipId: string,
): Promise<void> {
  return apiRequest<void>(`/api/v1/organizations/${organizationId}/members/${membershipId}`, {
    method: "DELETE",
    token,
  });
}

export function leaveOrganization(token: string, organizationId: string): Promise<void> {
  return apiRequest<void>(`/api/v1/organizations/${organizationId}/leave`, {
    method: "POST",
    token,
  });
}
