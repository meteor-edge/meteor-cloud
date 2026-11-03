import { Download, Trash2 } from "lucide-react";
import { Link } from "react-router-dom";

import type { Artifact } from "@/api/fleet";
import { ARTIFACT_TYPE_LABELS } from "@/components/artifacts/artifactTypes";
import {
  createReleaseFromArtifactPath,
  releaseKindForArtifactType,
  releasesForArtifactPath,
} from "@/components/releases/releaseArtifacts";
import { Button } from "@/components/ui/button";
import { formatBytes, formatDateTime } from "@/lib/utils";

type ArtifactTableProps = {
  artifacts: Artifact[];
  canManage: boolean;
  onDownload: (artifact: Artifact) => void;
  onDelete: (artifact: Artifact) => void;
  /** Omit on pages already scoped to one type / one device type. */
  showType?: boolean;
  deviceTypeNames?: Map<string, string>;
  emptyMessage?: string;
  /** Set to show which releases use each artifact and a "Create release" action. */
  organizationId?: string;
};

export function ArtifactTable({
  artifacts,
  canManage,
  onDownload,
  onDelete,
  showType = false,
  deviceTypeNames,
  emptyMessage = "No artifacts yet.",
  organizationId,
}: ArtifactTableProps) {
  const showReleases = Boolean(organizationId);
  const columnCount = 6 + (showType ? 1 : 0) + (deviceTypeNames ? 1 : 0) + (showReleases ? 1 : 0);

  return (
    <div className="overflow-x-auto rounded-lg border border-border bg-card shadow-glow">
      <table className="min-w-full text-left text-sm">
        <thead className="border-b border-border bg-background text-xs uppercase tracking-wide text-muted-foreground">
          <tr>
            <th className="px-4 py-3 font-semibold">Name</th>
            <th className="px-4 py-3 font-semibold">Version</th>
            {showType && <th className="px-4 py-3 font-semibold">Type</th>}
            {deviceTypeNames && <th className="px-4 py-3 font-semibold">Device Type</th>}
            <th className="px-4 py-3 font-semibold">Size</th>
            <th className="px-4 py-3 font-semibold">SHA-256</th>
            <th className="px-4 py-3 font-semibold">Uploaded</th>
            {showReleases && <th className="px-4 py-3 font-semibold">Used by</th>}
            <th className="px-4 py-3 font-semibold">Actions</th>
          </tr>
        </thead>
        <tbody>
          {artifacts.length === 0 && (
            <tr>
              <td className="px-4 py-6 text-center text-muted-foreground" colSpan={columnCount}>
                {emptyMessage}
              </td>
            </tr>
          )}
          {artifacts.map((artifact) => {
            const releaseCount = artifact.release_count ?? 0;
            const releaseKind = releaseKindForArtifactType(artifact.type);
            return (
              <tr key={artifact.id} className="border-b border-border align-top">
                <td className="px-4 py-3">
                  <p className="font-medium">{artifact.name}</p>
                  <p className="text-xs text-muted-foreground">{artifact.file_name}</p>
                  {artifact.description && (
                    <p className="mt-1 text-xs text-muted-foreground">{artifact.description}</p>
                  )}
                </td>
                <td className="px-4 py-3 font-mono text-xs">{artifact.version}</td>
                {showType && (
                  <td className="px-4 py-3 text-muted-foreground">
                    {ARTIFACT_TYPE_LABELS[artifact.type]}
                  </td>
                )}
                {deviceTypeNames && (
                  <td className="px-4 py-3 text-muted-foreground">
                    {artifact.device_type_id
                      ? (deviceTypeNames.get(artifact.device_type_id) ?? "—")
                      : "Any"}
                  </td>
                )}
                <td className="px-4 py-3 whitespace-nowrap text-muted-foreground">
                  {formatBytes(artifact.size_bytes)}
                </td>
                <td className="px-4 py-3">
                  <code className="font-mono text-xs" title={artifact.checksum_sha256}>
                    {artifact.checksum_sha256.slice(0, 12)}…
                  </code>
                </td>
                <td className="px-4 py-3 whitespace-nowrap text-muted-foreground">
                  {formatDateTime(artifact.created_at)}
                </td>
                {organizationId && (
                  <td className="px-4 py-3 whitespace-nowrap">
                    {releaseCount > 0 ? (
                      <Link
                        to={releasesForArtifactPath(organizationId, artifact.id)}
                        className="text-link hover:underline"
                      >
                        {releaseCount} release{releaseCount === 1 ? "" : "s"}
                      </Link>
                    ) : (
                      <span className="text-muted-foreground">
                        {releaseKind ? "No releases" : "—"}
                      </span>
                    )}
                  </td>
                )}
                <td className="px-4 py-3">
                  <div className="flex items-center gap-1.5 whitespace-nowrap">
                  <Button
                    variant="secondary"
                    size="sm"
                    className="h-8 w-8 p-0"
                    aria-label={`Download ${artifact.name} ${artifact.version}`}
                    title="Download"
                    onClick={() => onDownload(artifact)}
                  >
                    <Download className="h-4 w-4" aria-hidden />
                  </Button>
                  {canManage && organizationId && releaseKind && (
                    <Button variant="outline" size="sm" className="h-8 px-2.5 text-xs" asChild>
                      <Link to={createReleaseFromArtifactPath(organizationId, artifact.id)}>
                        Create release
                      </Link>
                    </Button>
                  )}
                  {canManage && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-8 w-8 bg-ember p-0 text-midnight hover:bg-ember hover:text-midnight disabled:opacity-50"
                      disabled={releaseCount > 0}
                      aria-label={`Delete ${artifact.name} ${artifact.version}`}
                      title={
                        releaseCount > 0
                          ? "Releases still use this artifact. Delete those releases first."
                          : "Delete"
                      }
                      onClick={() => onDelete(artifact)}
                    >
                      <Trash2 className="h-4 w-4" aria-hidden />
                    </Button>
                  )}
                </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
