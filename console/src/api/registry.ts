import { apiRequest } from "@/api/http";

export type RegistryInfo = {
  enabled: boolean;
  host: string | null;
  namespace: string | null;
};

export type RegistryToken = {
  id: string;
  name: string;
  kind: "personal" | "robot";
  access: "pull" | "push";
  token_prefix: string;
  repository_prefix: string | null;
  user_id: string | null;
  created_by_user_id: string | null;
  created_at: string;
  expires_at: string | null;
  revoked_at: string | null;
  last_used_at: string | null;
};

export type RegistryTokenWithSecret = RegistryToken & { token: string };

export type RegistryTokenPayload = {
  name: string;
  kind: RegistryToken["kind"];
  access: RegistryToken["access"];
  repository_prefix?: string | null;
  expires_at?: string | null;
};

const base = (organizationId: string) => `/api/v1/organizations/${organizationId}/registry`;

export function getRegistry(token: string, organizationId: string): Promise<RegistryInfo> {
  return apiRequest<RegistryInfo>(base(organizationId), { token });
}

export function listRegistryTokens(
  token: string,
  organizationId: string,
): Promise<RegistryToken[]> {
  return apiRequest<RegistryToken[]>(`${base(organizationId)}/tokens`, { token });
}

export function createRegistryToken(
  token: string,
  organizationId: string,
  payload: RegistryTokenPayload,
): Promise<RegistryTokenWithSecret> {
  return apiRequest<RegistryTokenWithSecret>(`${base(organizationId)}/tokens`, {
    token,
    body: payload,
  });
}

export function revokeRegistryToken(
  token: string,
  organizationId: string,
  tokenId: string,
): Promise<RegistryToken> {
  return apiRequest<RegistryToken>(`${base(organizationId)}/tokens/${tokenId}/revoke`, {
    method: "POST",
    token,
  });
}
