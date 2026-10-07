import { useQueryClient } from "@tanstack/react-query";

import { deleteArtifact, downloadArtifact, type Artifact } from "@/api/fleet";
import { ApiError } from "@/api/http";

/** Download/delete handlers shared by every artifact list. */
export function useArtifactActions(
  token: string | null,
  organizationId: string,
  onError: (message: string | null) => void,
) {
  const queryClient = useQueryClient();

  /** Invalidate artifact lists and type metadata so artifact counts refresh after mutations. */
  async function refresh() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["artifacts", organizationId] }),
      queryClient.invalidateQueries({ queryKey: ["device-types", organizationId] }),
      queryClient.invalidateQueries({ queryKey: ["device-type", organizationId] }),
    ]);
  }

  async function onDownload(artifact: Artifact) {
    onError(null);
    try {
      await downloadArtifact(token!, organizationId, artifact.id);
    } catch (err) {
      onError(err instanceof ApiError ? err.message : "Could not start the download.");
    }
  }

  async function onDelete(artifact: Artifact) {
    if (!window.confirm(`Delete ${artifact.name} ${artifact.version}? This cannot be undone.`)) {
      return;
    }
    onError(null);
    try {
      await deleteArtifact(token!, organizationId, artifact.id);
      await refresh();
    } catch (err) {
      onError(err instanceof ApiError ? err.message : "Could not delete the artifact.");
    }
  }

  return { onDownload, onDelete, refresh };
}
