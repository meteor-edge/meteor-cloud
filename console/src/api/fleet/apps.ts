import { apiRequest } from "@/api/http";
import type { Deployment } from "@/api/fleet/types";

export type AppRunPayload = {
  image: string;
  /** "meteorcloud" is the organization's own registry; null is a public image. */
  registry: "meteorcloud" | null;
  name?: string | null;
  ports: string[];
  env: Record<string, string>;
  command?: string | null;
  endpoint?: { hostname?: string | null; port?: number } | null;
};

export type AppRun = { workload_key: string; release_id: string; deployment: Deployment };

export function runApp(
  token: string,
  organizationId: string,
  deviceId: string,
  payload: AppRunPayload,
): Promise<AppRun> {
  return apiRequest<AppRun>(
    `/api/v1/organizations/${organizationId}/devices/${deviceId}/apps/run`,
    { token, body: payload },
  );
}
