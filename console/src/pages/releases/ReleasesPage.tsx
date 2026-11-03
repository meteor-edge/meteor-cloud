import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams, useSearchParams } from "react-router-dom";

import {
  deleteRelease,
  getArtifact,
  listReleases,
  type ReleaseKind,
  type ReleaseListParams,
} from "@/api/fleet";
import { getOrganization } from "@/api/organizations";
import { ApiError } from "@/api/http";
import { useAuth } from "@/auth/AuthContext";
import { CreateReleaseDialog } from "@/components/releases/CreateReleaseDialog";
import {
  artifactPath,
  deploymentsForReleasePath,
  releaseKindForArtifactType,
} from "@/components/releases/releaseArtifacts";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { canManageArtifacts, canManageDeployments } from "@/lib/permissions";

const PAGE_SIZE = 20;

const KIND_LABELS: Record<string, string> = {
  systemd: "systemd service",
  docker: "Single container",
  docker_compose: "Compose application",
};

const SELECT_CLASS = "h-10 rounded-md border border-input bg-field text-foreground px-3 text-sm";

export function ReleasesPage() {
  const { organizationId = "" } = useParams();
  const { token } = useAuth();
  const queryClient = useQueryClient();
  const [kind, setKind] = useState<ReleaseKind | "">("");
  const [page, setPage] = useState(1);
  const [searchParams, setSearchParams] = useSearchParams();
  // `?search=` lets a Deploy's release link land on that release.
  const [search, setSearch] = useState(searchParams.get("search") ?? "");
  // `?artifact_id=` lists the releases that use one artifact; with `create=1` it opens the
  // create dialog starting from that artifact (the "Create release" action on Artifacts).
  const artifactId = searchParams.get("artifact_id") ?? "";
  const [showCreate, setShowCreate] = useState(searchParams.get("create") === "1");
  const [error, setError] = useState<string | null>(null);

  const orgQuery = useQuery({
    queryKey: ["organization", organizationId, token],
    queryFn: () => getOrganization(token!, organizationId),
    enabled: Boolean(token && organizationId),
  });

  const artifactQuery = useQuery({
    queryKey: ["artifact", organizationId, artifactId, token],
    queryFn: () => getArtifact(token!, organizationId, artifactId),
    enabled: Boolean(token && organizationId && artifactId),
  });
  const filterArtifact = artifactQuery.data;
  const initialKind = filterArtifact ? releaseKindForArtifactType(filterArtifact.type) : null;

  const params: ReleaseListParams = {
    artifact_id: artifactId || undefined,
    kind: kind || undefined,
    search: search || undefined,
    page,
    page_size: PAGE_SIZE,
  };
  const releasesQuery = useQuery({
    queryKey: ["releases", organizationId, params, token],
    queryFn: () => listReleases(token!, organizationId, params),
    enabled: Boolean(token && organizationId),
  });

  const canManage = canManageArtifacts(orgQuery.data?.current_user_role);
  const canDeploy = canManageDeployments(orgQuery.data?.current_user_role);
  const total = releasesQuery.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const items = releasesQuery.data?.items ?? [];

  /** Releases changed, so artifact "used by" counts did too. */
  async function refreshLists() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["releases", organizationId] }),
      queryClient.invalidateQueries({ queryKey: ["artifacts", organizationId] }),
      queryClient.invalidateQueries({ queryKey: ["artifact", organizationId] }),
    ]);
  }

  function clearArtifactFilter() {
    setPage(1);
    const next = new URLSearchParams(searchParams);
    next.delete("artifact_id");
    next.delete("create");
    setSearchParams(next, { replace: true });
  }

  function closeCreate() {
    setShowCreate(false);
    if (searchParams.get("create")) {
      const next = new URLSearchParams(searchParams);
      next.delete("create");
      setSearchParams(next, { replace: true });
    }
  }

  async function onDelete(releaseId: string, label: string) {
    if (!token || !window.confirm(`Delete release ${label}?`)) {
      return;
    }
    setError(null);
    try {
      await deleteRelease(token, organizationId, releaseId);
      await refreshLists();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not delete the release.");
    }
  }

  return (
    <section className="mx-auto max-w-6xl space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">Releases</h1>
          <p className="mt-2 text-muted-foreground">
            Versions of software you can deploy to devices — systemd services, single containers,
            Compose applications, and more kinds later.
          </p>
        </div>
        {canManage && (
          <Button type="button" onClick={() => setShowCreate(true)}>
            Create release
          </Button>
        )}
      </div>

      <div className="flex flex-wrap gap-3">
        <Input
          className="max-w-xs"
          placeholder="Search by name"
          value={search}
          onChange={(event) => {
            setPage(1);
            setSearch(event.target.value);
          }}
        />
        <select
          className={SELECT_CLASS}
          value={kind}
          aria-label="Filter by deployment type"
          onChange={(event) => {
            setPage(1);
            setKind(event.target.value as ReleaseKind | "");
          }}
        >
          <option value="">All types</option>
          <option value="systemd">systemd service</option>
          <option value="docker">Single container</option>
          <option value="docker_compose">Compose application</option>
        </select>
      </div>

      {artifactId && (
        <div className="flex flex-wrap items-center gap-3 rounded-md border border-border bg-muted/40 px-3 py-2 text-sm">
          <span>
            Releases using artifact{" "}
            <strong>
              {filterArtifact ? `${filterArtifact.name} ${filterArtifact.version}` : "…"}
            </strong>
          </span>
          <Button type="button" variant="outline" size="sm" onClick={clearArtifactFilter}>
            Show all releases
          </Button>
        </div>
      )}

      {error && <p className="text-sm text-destructive">{error}</p>}
      {releasesQuery.isError && (
        <p className="text-sm text-destructive">Could not load releases.</p>
      )}

      <div className="overflow-x-auto rounded-md border border-border">
        <table className="w-full min-w-[640px] text-left text-sm">
          <thead className="border-b border-border bg-muted/40 text-muted-foreground">
            <tr>
              <th className="px-4 py-3 font-medium">Name</th>
              <th className="px-4 py-3 font-medium">Version</th>
              <th className="px-4 py-3 font-medium">Kind</th>
              <th className="px-4 py-3 font-medium">Artifact</th>
              <th className="px-4 py-3 font-medium">Deploys</th>
              <th className="px-4 py-3 font-medium">Created</th>
              {(canManage || canDeploy) && (
                <th className="px-4 py-3 font-medium">Actions</th>
              )}
            </tr>
          </thead>
          <tbody>
            {items.map((release) => (
              <tr key={release.id} className="border-b border-border last:border-0">
                <td className="px-4 py-3 font-medium">{release.name}</td>
                <td className="px-4 py-3">{release.version}</td>
                <td className="px-4 py-3">{KIND_LABELS[release.kind] ?? release.kind}</td>
                <td className="px-4 py-3">
                  {release.artifact ? (
                    <>
                      <Link
                        to={artifactPath(organizationId, release.artifact)}
                        className="text-link hover:underline"
                      >
                        {release.artifact.name} {release.artifact.version}
                      </Link>
                      <p className="text-xs text-muted-foreground">{release.artifact.file_name}</p>
                    </>
                  ) : (
                    <span className="text-muted-foreground">
                      {typeof release.spec?.image === "string"
                        ? `Image ${release.spec.image}`
                        : "—"}
                    </span>
                  )}
                </td>
                <td className="px-4 py-3 whitespace-nowrap">
                  {(release.deployment_count ?? 0) > 0 ? (
                    <Link
                      to={deploymentsForReleasePath(organizationId, release.id)}
                      className="text-link hover:underline"
                    >
                      {release.deployment_count} Deploy{release.deployment_count === 1 ? "" : "s"}
                    </Link>
                  ) : (
                    <span className="text-muted-foreground">Not deployed</span>
                  )}
                </td>
                <td className="px-4 py-3 text-muted-foreground">
                  {new Date(release.created_at).toLocaleString()}
                </td>
                {(canManage || canDeploy) && (
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-2">
                      {canDeploy && (
                        <Button type="button" variant="outline" size="sm" asChild>
                          <Link
                            to={`/organizations/${organizationId}/deployments?deploy=1&release_id=${release.id}`}
                          >
                            Deploy
                          </Link>
                        </Button>
                      )}
                      {canManage && (
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          disabled={(release.deployment_count ?? 0) > 0}
                          title={
                            (release.deployment_count ?? 0) > 0
                              ? "This release has Deploys, which keep its history. It cannot be deleted."
                              : undefined
                          }
                          onClick={() =>
                            onDelete(release.id, `${release.name} ${release.version}`)
                          }
                        >
                          Delete
                        </Button>
                      )}
                    </div>
                  </td>
                )}
              </tr>
            ))}
            {!releasesQuery.isLoading && items.length === 0 && (
              <tr>
                <td
                  colSpan={canManage || canDeploy ? 7 : 6}
                  className="px-4 py-8 text-center text-muted-foreground"
                >
                  {artifactId
                    ? "No release uses this artifact yet."
                    : `No releases yet.${canManage ? " Create one to get started." : ""}`}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {totalPages > 1 && (
        <div className="flex items-center gap-3">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={page <= 1}
            onClick={() => setPage((value) => value - 1)}
          >
            Previous
          </Button>
          <span className="text-sm text-muted-foreground">
            Page {page} of {totalPages}
          </span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={page >= totalPages}
            onClick={() => setPage((value) => value + 1)}
          >
            Next
          </Button>
        </div>
      )}

      {/* Wait for the artifact so the dialog opens with its kind preselected. */}
      {showCreate && token && (!artifactId || !artifactQuery.isLoading) && (
        <CreateReleaseDialog
          token={token}
          organizationId={organizationId}
          initialArtifact={
            filterArtifact && initialKind
              ? {
                  id: filterArtifact.id,
                  kind: initialKind,
                  name: filterArtifact.name,
                  version: filterArtifact.version,
                }
              : null
          }
          onClose={closeCreate}
          onCreated={async () => {
            closeCreate();
            await refreshLists();
          }}
        />
      )}
    </section>
  );
}
