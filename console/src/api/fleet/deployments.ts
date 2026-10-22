import { apiRequest } from "@/api/http";
import type {
  Deployment,
  DeploymentCreatePayload,
  DeploymentListParams,
  Page,
} from "@/api/fleet/types";

function buildQuery(params: DeploymentListParams): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      search.set(key, String(value));
    }
  }
  const query = search.toString();
  return query ? `?${query}` : "";
}

export function listDeployments(
  token: string,
  organizationId: string,
  params: DeploymentListParams = {},
): Promise<Page<Deployment>> {
  return apiRequest<Page<Deployment>>(
    `/api/v1/organizations/${organizationId}/deployments${buildQuery(params)}`,
    { token },
  );
}

export function getDeployment(
  token: string,
  organizationId: string,
  deploymentId: string,
): Promise<Deployment> {
  return apiRequest<Deployment>(
    `/api/v1/organizations/${organizationId}/deployments/${deploymentId}`,
    { token },
  );
}

export function createDeployment(
  token: string,
  organizationId: string,
  payload: DeploymentCreatePayload,
): Promise<Deployment> {
  return apiRequest<Deployment>(`/api/v1/organizations/${organizationId}/deployments`, {
    method: "POST",
    token,
    body: payload,
  });
}

export function cancelDeployment(
  token: string,
  organizationId: string,
  deploymentId: string,
): Promise<Deployment> {
  return apiRequest<Deployment>(
    `/api/v1/organizations/${organizationId}/deployments/${deploymentId}/cancel`,
    { method: "POST", token },
  );
}

export function retryFailedDeployment(
  token: string,
  organizationId: string,
  deploymentId: string,
): Promise<Deployment> {
  return apiRequest<Deployment>(
    `/api/v1/organizations/${organizationId}/deployments/${deploymentId}/retry-failed`,
    { method: "POST", token },
  );
}

export function rollbackDeployment(
  token: string,
  organizationId: string,
  deploymentId: string,
): Promise<Deployment> {
  return apiRequest<Deployment>(
    `/api/v1/organizations/${organizationId}/deployments/${deploymentId}/rollback`,
    { method: "POST", token },
  );
}
