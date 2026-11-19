import { apiRequest } from "@/api/http";
import type {
  EnrollmentApiKey,
  EnrollmentApiKeyWithSecret,
  EnrollmentAutoApproveRule,
} from "@/api/fleet/types";

export type EnrollmentApiKeyPayload = {
  name: string;
  expires_at?: string | null;
  auto_approve?: EnrollmentAutoApproveRule | null;
};

export function listEnrollmentKeys(
  token: string,
  organizationId: string,
): Promise<EnrollmentApiKey[]> {
  return apiRequest<EnrollmentApiKey[]>(`/api/v1/organizations/${organizationId}/enrollment-keys`, {
    token,
  });
}

export function createEnrollmentKey(
  token: string,
  organizationId: string,
  payload: EnrollmentApiKeyPayload,
): Promise<EnrollmentApiKeyWithSecret> {
  return apiRequest<EnrollmentApiKeyWithSecret>(
    `/api/v1/organizations/${organizationId}/enrollment-keys`,
    { token, body: payload },
  );
}

export function revokeEnrollmentKey(
  token: string,
  organizationId: string,
  keyId: string,
): Promise<EnrollmentApiKey> {
  return apiRequest<EnrollmentApiKey>(
    `/api/v1/organizations/${organizationId}/enrollment-keys/${keyId}/revoke`,
    { method: "POST", token },
  );
}

/** Turn on or replace the key's auto-approve rule. */
export function setEnrollmentKeyAutoApprove(
  token: string,
  organizationId: string,
  keyId: string,
  rule: EnrollmentAutoApproveRule,
): Promise<EnrollmentApiKey> {
  return apiRequest<EnrollmentApiKey>(
    `/api/v1/organizations/${organizationId}/enrollment-keys/${keyId}/auto-approve`,
    { method: "PUT", token, body: rule },
  );
}

export function clearEnrollmentKeyAutoApprove(
  token: string,
  organizationId: string,
  keyId: string,
): Promise<EnrollmentApiKey> {
  return apiRequest<EnrollmentApiKey>(
    `/api/v1/organizations/${organizationId}/enrollment-keys/${keyId}/auto-approve`,
    { method: "DELETE", token },
  );
}
