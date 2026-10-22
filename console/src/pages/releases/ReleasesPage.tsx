import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import {
  deleteRelease,
  listReleases,
  type ReleaseKind,
  type ReleaseListParams,
} from "@/api/fleet";
import { getOrganization } from "@/api/organizations";
import { ApiError } from "@/api/http";
import { useAuth } from "@/auth/AuthContext";
import { CreateReleaseDialog } from "@/components/releases/CreateReleaseDialog";
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
  const [search, setSearch] = useState("");
  const [kind, setKind] = useState<ReleaseKind | "">("");
  const [page, setPage] = useState(1);
  const [showCreate, setShowCreate] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const orgQuery = useQuery({
    queryKey: ["organization", organizationId, token],
    queryFn: () => getOrganization(token!, organizationId),
    enabled: Boolean(token && organizationId),
  });

  const params: ReleaseListParams = {
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

  async function onDelete(releaseId: string, label: string) {
    if (!token || !window.confirm(`Delete release ${label}?`)) {
      return;
    }
    setError(null);
    try {
      await deleteRelease(token, organizationId, releaseId);
      await queryClient.invalidateQueries({ queryKey: ["releases", organizationId] });
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
                  colSpan={canManage || canDeploy ? 5 : 4}
                  className="px-4 py-8 text-center text-muted-foreground"
                >
                  No releases yet.
                  {canManage ? " Create one to get started." : ""}
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

      {showCreate && token && (
        <CreateReleaseDialog
          token={token}
          organizationId={organizationId}
          onClose={() => setShowCreate(false)}
          onCreated={async () => {
            setShowCreate(false);
            await queryClient.invalidateQueries({ queryKey: ["releases", organizationId] });
          }}
        />
      )}
    </section>
  );
}
