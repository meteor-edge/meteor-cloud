import { apiRequest } from "@/api/http";
import type { OrganizationRole } from "@/api/organizations";

export type TeamSummary = {
  id: string;
  organization_id: string;
  name: string;
  description: string | null;
  member_count: number;
  created_at: string;
  updated_at: string;
};

export type TeamMember = {
  membership_id: string;
  email: string;
  full_name: string;
  role: OrganizationRole;
  role_name: string;
};

export type Team = TeamSummary & { members: TeamMember[] };

const teamsPath = (organizationId: string) => `/api/v1/organizations/${organizationId}/teams`;

export function listTeams(token: string, organizationId: string): Promise<TeamSummary[]> {
  return apiRequest<TeamSummary[]>(teamsPath(organizationId), { token });
}

export function getTeam(token: string, organizationId: string, teamId: string): Promise<Team> {
  return apiRequest<Team>(`${teamsPath(organizationId)}/${teamId}`, { token });
}

export function createTeam(
  token: string,
  organizationId: string,
  payload: { name: string; description?: string; membership_ids?: string[] },
): Promise<Team> {
  return apiRequest<Team>(teamsPath(organizationId), { token, body: payload });
}

export function updateTeam(
  token: string,
  organizationId: string,
  teamId: string,
  payload: { name?: string; description?: string | null },
): Promise<Team> {
  return apiRequest<Team>(`${teamsPath(organizationId)}/${teamId}`, {
    method: "PATCH",
    token,
    body: payload,
  });
}

export function deleteTeam(token: string, organizationId: string, teamId: string): Promise<void> {
  return apiRequest<void>(`${teamsPath(organizationId)}/${teamId}`, { method: "DELETE", token });
}

export function addTeamMember(
  token: string,
  organizationId: string,
  teamId: string,
  membershipId: string,
): Promise<Team> {
  return apiRequest<Team>(`${teamsPath(organizationId)}/${teamId}/members`, {
    token,
    body: { membership_id: membershipId },
  });
}

export function removeTeamMember(
  token: string,
  organizationId: string,
  teamId: string,
  membershipId: string,
): Promise<Team> {
  return apiRequest<Team>(`${teamsPath(organizationId)}/${teamId}/members/${membershipId}`, {
    method: "DELETE",
    token,
  });
}
