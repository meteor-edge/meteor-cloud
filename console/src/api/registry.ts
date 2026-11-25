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

export type ImageRepository = { repository: string; tag_count: number; pushed_at: string };

export type ImageTag = {
  repository: string;
  tag: string;
  digest: string;
  media_type: string;
  size_bytes: number;
  platforms: { os: string; architecture: string; variant: string | null; size_bytes: number }[];
  pushed_at: string;
  pushed_by: string | null;
};

export type ImagePage<T> = { items: T[]; total: number; page: number; page_size: number };

export function listImages(
  token: string,
  organizationId: string,
  params: { search?: string; page?: number; page_size?: number } = {},
): Promise<ImagePage<ImageRepository>> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value) query.set(key, String(value));
  }
  return apiRequest(`${base(organizationId)}/images?${query}`, { token });
}

export function listImageTags(
  token: string,
  organizationId: string,
  repository: string,
  page = 1,
  pageSize = 20,
): Promise<ImagePage<ImageTag>> {
  const query = new URLSearchParams({ repository, page: String(page), page_size: String(pageSize) });
  return apiRequest(`${base(organizationId)}/images/tags?${query}`, { token });
}

export function deleteImage(
  token: string,
  organizationId: string,
  repository: string,
  tag?: string,
): Promise<void> {
  const query = new URLSearchParams({ repository, ...(tag ? { tag } : {}) });
  return apiRequest(`${base(organizationId)}/images?${query}`, { method: "DELETE", token });
}
