import { ApiError, apiRequest, type ApiErrorBody } from "@/api/http";
import type { Artifact, ArtifactListParams, ArtifactType, Page } from "@/api/fleet/types";
import { resolveApiBaseUrl } from "@/lib/apiBase";

export type ArtifactUploadPayload = {
  file: File;
  name: string;
  version: string;
  type: ArtifactType;
  device_type_id?: string | null;
  description?: string | null;
};

function buildQuery(params: ArtifactListParams): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      search.set(key, String(value));
    }
  }
  const query = search.toString();
  return query ? `?${query}` : "";
}

export function listArtifacts(
  token: string,
  organizationId: string,
  params: ArtifactListParams = {},
): Promise<Page<Artifact>> {
  return apiRequest<Page<Artifact>>(
    `/api/v1/organizations/${organizationId}/artifacts${buildQuery(params)}`,
    { token },
  );
}

export function deleteArtifact(
  token: string,
  organizationId: string,
  artifactId: string,
): Promise<void> {
  return apiRequest<void>(`/api/v1/organizations/${organizationId}/artifacts/${artifactId}`, {
    method: "DELETE",
    token,
  });
}

/**
 * Uploads with XMLHttpRequest because fetch cannot report upload progress,
 * which matters for multi-gigabyte OS images.
 */
export function uploadArtifact(
  token: string,
  organizationId: string,
  payload: ArtifactUploadPayload,
  options: {
    onProgress?: (progress: { loaded: number; total: number }) => void;
    signal?: AbortSignal;
  } = {},
): Promise<Artifact> {
  const form = new FormData();
  form.set("name", payload.name);
  form.set("version", payload.version);
  form.set("type", payload.type);
  if (payload.device_type_id) {
    form.set("device_type_id", payload.device_type_id);
  }
  if (payload.description) {
    form.set("description", payload.description);
  }
  form.set("file", payload.file, payload.file.name);

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${resolveApiBaseUrl()}/api/v1/organizations/${organizationId}/artifacts`);
    xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.setRequestHeader("Accept", "application/json");

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        options.onProgress?.({ loaded: event.loaded, total: event.total });
      }
    };
    xhr.onload = () => {
      let data: unknown = null;
      try {
        data = xhr.responseText ? JSON.parse(xhr.responseText) : null;
      } catch {
        data = null;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(data as Artifact);
        return;
      }
      const body = data as ApiErrorBody | null;
      reject(
        new ApiError(
          xhr.status,
          body?.error?.code ?? "request_failed",
          body?.error?.message ?? `Upload failed with status ${xhr.status}`,
        ),
      );
    };
    xhr.onerror = () => reject(new ApiError(0, "network_error", "The upload could not be sent."));
    xhr.onabort = () => reject(new ApiError(0, "aborted", "The upload was cancelled."));
    if (options.signal?.aborted) {
      reject(new ApiError(0, "aborted", "The upload was cancelled."));
      return;
    }
    options.signal?.addEventListener("abort", () => xhr.abort(), { once: true });

    xhr.send(form);
  });
}

/**
 * Starts a browser download via a short-lived link, so large files stream to
 * disk instead of being buffered in memory.
 */
export async function downloadArtifact(
  token: string,
  organizationId: string,
  artifactId: string,
): Promise<void> {
  const link = await apiRequest<{ url: string; expires_at: string }>(
    `/api/v1/organizations/${organizationId}/artifacts/${artifactId}/download-link`,
    { method: "POST", token },
  );
  window.location.assign(`${resolveApiBaseUrl()}${link.url}`);
}
