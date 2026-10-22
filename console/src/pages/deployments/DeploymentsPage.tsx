import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { listDeployments } from "@/api/fleet";
import { getOrganization } from "@/api/organizations";
import { useAuth } from "@/auth/AuthContext";
import { DeployWizard } from "@/components/deployments/DeployWizard";
import { Button } from "@/components/ui/button";
import { canManageDeployments } from "@/lib/permissions";

const PAGE_SIZE = 20;

const STATUS_LABELS: Record<string, string> = {
  in_progress: "In progress",
  completed: "Completed",
  cancelled: "Cancelled",
  failed: "Failed",
};

export function DeploymentsPage() {
  const { organizationId = "" } = useParams();
  const { token } = useAuth();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [page, setPage] = useState(1);
  const [showWizard, setShowWizard] = useState(searchParams.get("deploy") === "1");

  const initialReleaseId = searchParams.get("release_id");
  const initialDeviceId = searchParams.get("device_id");

  const orgQuery = useQuery({
    queryKey: ["organization", organizationId, token],
    queryFn: () => getOrganization(token!, organizationId),
    enabled: Boolean(token && organizationId),
  });

  const deploymentsQuery = useQuery({
    queryKey: ["deployments", organizationId, page, token],
    queryFn: () =>
      listDeployments(token!, organizationId, { page, page_size: PAGE_SIZE }),
    enabled: Boolean(token && organizationId),
  });

  const canManage = canManageDeployments(orgQuery.data?.current_user_role);
  const total = deploymentsQuery.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const items = deploymentsQuery.data?.items ?? [];

  function openWizard() {
    setShowWizard(true);
  }

  function closeWizard() {
    setShowWizard(false);
    const next = new URLSearchParams(searchParams);
    next.delete("deploy");
    next.delete("release_id");
    next.delete("device_id");
    setSearchParams(next, { replace: true });
  }

  return (
    <section className="mx-auto max-w-6xl space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">Deployments</h1>
          <p className="mt-2 text-muted-foreground">
            History of Deploy actions: which release went to which devices, and how far each
            device got.
          </p>
        </div>
        {canManage && (
          <Button type="button" onClick={openWizard}>
            Deploy
          </Button>
        )}
      </div>

      {deploymentsQuery.isError && (
        <p className="text-sm text-destructive">Could not load deployments.</p>
      )}

      <div className="overflow-x-auto rounded-md border border-border">
        <table className="w-full min-w-[720px] text-left text-sm">
          <thead className="border-b border-border bg-muted/40 text-muted-foreground">
            <tr>
              <th className="px-4 py-3 font-medium">Name / Release</th>
              <th className="px-4 py-3 font-medium">Status</th>
              <th className="px-4 py-3 font-medium">Progress</th>
              <th className="px-4 py-3 font-medium">Created</th>
            </tr>
          </thead>
          <tbody>
            {items.map((deployment) => {
              const releaseLabel = deployment.release
                ? `${deployment.release.name} ${deployment.release.version}`
                : deployment.release_id;
              const title = deployment.name || releaseLabel;
              return (
                <tr key={deployment.id} className="border-b border-border last:border-0">
                  <td className="px-4 py-3">
                    <Link
                      to={`/organizations/${organizationId}/deployments/${deployment.id}`}
                      className="font-medium text-link hover:underline"
                    >
                      {title}
                    </Link>
                    {deployment.name && (
                      <p className="text-xs text-muted-foreground">{releaseLabel}</p>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    {STATUS_LABELS[deployment.status] ?? deployment.status}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {deployment.counts.succeeded}/{deployment.counts.total} succeeded
                    {deployment.counts.pending > 0
                      ? ` · ${deployment.counts.pending} pending`
                      : ""}
                    {deployment.counts.failed > 0
                      ? ` · ${deployment.counts.failed} failed`
                      : ""}
                  </td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {new Date(deployment.created_at).toLocaleString()}
                  </td>
                </tr>
              );
            })}
            {!deploymentsQuery.isLoading && items.length === 0 && (
              <tr>
                <td colSpan={4} className="px-4 py-8 text-center text-muted-foreground">
                  No deployments yet.
                  {canManage ? " Click Deploy to send a release to devices." : ""}
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

      {showWizard && token && (
        <DeployWizard
          token={token}
          organizationId={organizationId}
          initialReleaseId={initialReleaseId}
          initialDeviceId={initialDeviceId}
          onClose={closeWizard}
          onCreated={(deploymentId) => {
            closeWizard();
            void navigate(`/organizations/${organizationId}/deployments/${deploymentId}`);
          }}
        />
      )}
    </section>
  );
}
