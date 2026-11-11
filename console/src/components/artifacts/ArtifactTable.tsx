import { Download, Plus, Trash2 } from "lucide-react";
import { Link } from "react-router-dom";

import type { Artifact } from "@/api/fleet";
import { ARTIFACT_TYPE_LABELS } from "@/components/artifacts/artifactTypes";
import {
  createReleaseFromArtifactPath,
  releaseKindForArtifactType,
  releasesForArtifactPath,
} from "@/components/releases/releaseArtifacts";
import { Button } from "@/components/ui/button";
import { formatBytes, formatDateTime, formatRelativeTime } from "@/lib/utils";

type ArtifactTableProps = {
  artifacts: Artifact[];
  canManage: boolean;
  onDownload: (artifact: Artifact) => void;
  onDelete: (artifact: Artifact) => void;
  /** Set to show the Meteor Imager column (OS images listed publicly, no login). */
  onTogglePublished?: (artifact: Artifact) => void;
  /** Owners and admins only; others see the column read-only. */
  canPublish?: boolean;
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
  onTogglePublished,
  canPublish = false,
  showType = false,
  deviceTypeNames,
  emptyMessage = "No artifacts yet.",
  organizationId,
}: ArtifactTableProps) {
  const showReleases = Boolean(organizationId);
  const showImager = Boolean(onTogglePublished);
  // With both, the device type goes under the type label to keep the table narrow.
  const deviceTypeColumn = Boolean(deviceTypeNames) && !showType;
  const columnCount =
    6 +
    (showType ? 1 : 0) +
    (deviceTypeColumn ? 1 : 0) +
    (showReleases ? 1 : 0) +
    (showImager ? 1 : 0);
  const deviceTypeLabel = (artifact: Artifact) =>
    artifact.device_type_id ? (deviceTypeNames?.get(artifact.device_type_id) ?? "—") : "Any device";

  return (
    <div className="overflow-x-auto rounded-lg border border-border bg-card shadow-glow">
      <table className="min-w-full text-left text-sm">
        <thead className="border-b border-border bg-background text-xs uppercase tracking-wide text-muted-foreground">
          <tr>
            <th className="px-3 py-3 font-semibold">Name</th>
            <th className="px-3 py-3 font-semibold">Version</th>
            {showType && <th className="px-3 py-3 font-semibold">Type</th>}
            {deviceTypeColumn && <th className="px-3 py-3 font-semibold">Device Type</th>}
            <th className="px-3 py-3 font-semibold">Size</th>
            <th className="hidden px-3 py-3 font-semibold 2xl:table-cell">SHA-256</th>
            <th className="px-3 py-3 font-semibold">Uploaded</th>
            {showReleases && <th className="px-3 py-3 font-semibold">Used by</th>}
            {showImager && (
              <th className="px-3 py-3 font-semibold" title="Listed and downloadable without login">
                Meteor Imager
              </th>
            )}
            <th className="px-3 py-3 text-right font-semibold">Actions</th>
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
                <td className="min-w-[11rem] max-w-[15rem] px-3 py-3">
                  <p className="break-words font-medium">{artifact.name}</p>
                  {artifact.file_name !== artifact.name && (
                    <p
                      className="truncate text-xs text-muted-foreground"
                      title={artifact.file_name}
                    >
                      {artifact.file_name}
                    </p>
                  )}
                  {artifact.description && (
                    <p
                      className="mt-1 line-clamp-2 text-xs text-muted-foreground"
                      title={artifact.description}
                    >
                      {artifact.description}
                    </p>
                  )}
                </td>
                <td className="whitespace-nowrap px-3 py-3 font-mono text-xs">
                  {artifact.version}
                </td>
                {showType && (
                  <td className="whitespace-nowrap px-3 py-3 text-muted-foreground">
                    <p>{ARTIFACT_TYPE_LABELS[artifact.type]}</p>
                    {deviceTypeNames && <p className="text-xs">{deviceTypeLabel(artifact)}</p>}
                  </td>
                )}
                {deviceTypeColumn && (
                  <td className="whitespace-nowrap px-3 py-3 text-muted-foreground">
                    {deviceTypeLabel(artifact)}
                  </td>
                )}
                <td className="px-3 py-3 whitespace-nowrap text-muted-foreground">
                  {formatBytes(artifact.size_bytes)}
                </td>
                <td className="hidden px-3 py-3 2xl:table-cell">
                  <code className="font-mono text-xs" title={artifact.checksum_sha256}>
                    {artifact.checksum_sha256.slice(0, 12)}…
                  </code>
                </td>
                <td
                  className="px-3 py-3 whitespace-nowrap text-muted-foreground"
                  title={formatDateTime(artifact.created_at)}
                >
                  {formatRelativeTime(artifact.created_at)}
                </td>
                {organizationId && (
                  <td className="px-3 py-3 whitespace-nowrap">
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
                {onTogglePublished && (
                  <td className="px-3 py-3 whitespace-nowrap">
                    {artifact.type === "os_image" ? (
                      <label className="flex items-center gap-2 text-sm">
                        <input
                          type="checkbox"
                          checked={Boolean(artifact.published)}
                          disabled={!canPublish}
                          aria-label={`Show ${artifact.name} ${artifact.version} in Meteor Imager`}
                          onChange={() => onTogglePublished(artifact)}
                        />
                        <span className="text-muted-foreground">
                          {artifact.published ? "Shown" : "Hidden"}
                        </span>
                      </label>
                    ) : (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </td>
                )}
                <td className="px-3 py-3">
                  {/* Icon buttons stay right-aligned so they line up across rows. */}
                  <div className="flex items-center justify-end gap-1.5 whitespace-nowrap">
                    {canManage && organizationId && releaseKind && (
                      <Button variant="outline" size="sm" className="h-8 w-8 p-0" asChild>
                        <Link
                          to={createReleaseFromArtifactPath(organizationId, artifact.id)}
                          aria-label="Create release"
                          title="Create release"
                        >
                          <Plus className="h-4 w-4" aria-hidden />
                        </Link>
                      </Button>
                    )}
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-8 w-8 p-0"
                      aria-label={`Download ${artifact.name} ${artifact.version}`}
                      title="Download"
                      onClick={() => onDownload(artifact)}
                    >
                      <Download className="h-4 w-4" aria-hidden />
                    </Button>
                    {canManage && (
                      <Button
                        variant="outline"
                        size="sm"
                        className="h-8 w-8 p-0 text-muted-foreground hover:border-flare hover:text-flare"
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
