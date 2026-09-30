import { apiRequest } from "@/api/http";
import type { RegistrationToken, RegistrationTokenWithSecret } from "@/api/fleet/types";

export type RegistrationTokenPayload = {
  name: string;
  device_type_id?: string | null;
  device_group_id?: string | null;
  expires_at?: string | null;
  max_uses?: number | null;
};

export function listRegistrationTokens(
  token: string,
  organizationId: string,
): Promise<RegistrationToken[]> {
  return apiRequest<RegistrationToken[]>(
    `/api/v1/organizations/${organizationId}/registration-tokens`,
    { token },
  );
}

export function createRegistrationToken(
  token: string,
  organizationId: string,
  payload: RegistrationTokenPayload,
): Promise<RegistrationTokenWithSecret> {
  return apiRequest<RegistrationTokenWithSecret>(
    `/api/v1/organizations/${organizationId}/registration-tokens`,
    { token, body: payload },
  );
}

export function revokeRegistrationToken(
  token: string,
  organizationId: string,
  tokenId: string,
): Promise<RegistrationToken> {
  return apiRequest<RegistrationToken>(
    `/api/v1/organizations/${organizationId}/registration-tokens/${tokenId}/revoke`,
    { method: "POST", token },
  );
}
