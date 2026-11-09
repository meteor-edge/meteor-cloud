import { useQueryClient } from "@tanstack/react-query";

import { deleteArtifact, downloadArtifact, setArtifactPublished, type Artifact } from "@/api/fleet";
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

  async function onTogglePublished(artifact: Artifact) {
    const published = !artifact.published;
    if (
      published &&
      !window.confirm(
        `Show ${artifact.name} ${artifact.version} in Meteor Imager? Anyone who can reach this server can download it without signing in.`,
      )
    ) {
      return;
    }
    onError(null);
    try {
      await setArtifactPublished(token!, organizationId, artifact.id, published);
      await refresh();
    } catch (err) {
      onError(err instanceof ApiError ? err.message : "Could not change Meteor Imager visibility.");
    }
  }

  return { onDownload, onDelete, onTogglePublished, refresh };
}
