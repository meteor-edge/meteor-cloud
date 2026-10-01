import { resolveApiBaseUrl } from "@/lib/apiBase";

export type HealthResponse = {
  status: string;
  service: string;
  version: string;
};

export async function fetchHealth(): Promise<HealthResponse> {
  const response = await fetch(`${resolveApiBaseUrl()}/health`);
  if (!response.ok) {
    throw new Error(`Health check failed with status ${response.status}`);
  }
  return response.json() as Promise<HealthResponse>;
}
