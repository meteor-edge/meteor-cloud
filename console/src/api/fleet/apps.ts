import { apiRequest } from "@/api/http";
import type { Deployment } from "@/api/fleet/types";

export type AppResources = { memory_mb: number | null; cpu_millis: number | null };

type AppSettings = {
  image: string;
  ports: string[];
  env: Record<string, string>;
  volumes: string[];
  resources: AppResources | null;
};

export type AppRunPayload = AppSettings & {
  /** "meteorcloud" is the organization's own registry; null is a public image. */
  registry: "meteorcloud" | null;
  name?: string | null;
  secret_env: Record<string, string>;
  endpoint?: { hostname?: string | null; port?: number } | null;
};

/** What is left out stays as it is; a null secret is removed. */
export type AppRedeployPayload = Partial<AppSettings> & {
  secret_env?: Record<string, string | null>;
};

export type AppRun = { workload_key: string; release_id: string; deployment: Deployment };

export type AppSpec = {
  image?: string;
  registry?: string | null;
  ports?: string[] | null;
  env?: Record<string, string> | null;
  volumes?: string[] | null;
  resources?: Partial<AppResources> | null;
  /** Names only. */
  secret_env?: string[] | null;
};

export type DeployedApp = {
  workload_key: string;
  release_id: string;
  release_version: string;
  spec: AppSpec;
  health: string;
  sync_status: string;
  repository: string | null;
  tag: string | null;
  tag_moved: boolean;
  newer_tag: string | null;
};

const base = (organizationId: string, deviceId: string) =>
  `/api/v1/organizations/${organizationId}/devices/${deviceId}/apps`;

export function runApp(
  token: string,
  organizationId: string,
  deviceId: string,
  payload: AppRunPayload,
): Promise<AppRun> {
  return apiRequest<AppRun>(`${base(organizationId, deviceId)}/run`, { token, body: payload });
}

export function listDeployedApps(
  token: string,
  organizationId: string,
  deviceId: string,
): Promise<DeployedApp[]> {
  return apiRequest<DeployedApp[]>(`${base(organizationId, deviceId)}/deployed`, { token });
}

export function redeployApp(
  token: string,
  organizationId: string,
  deviceId: string,
  workloadKey: string,
  payload: AppRedeployPayload,
): Promise<AppRun> {
  return apiRequest<AppRun>(
    `${base(organizationId, deviceId)}/${encodeURIComponent(workloadKey)}/redeploy`,
    { token, body: payload },
  );
}
